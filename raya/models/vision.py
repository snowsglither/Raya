"""Vision utilities — fonctions haut niveau pour l'observation visuelle.

Ces fonctions encapsulent le pipeline complet :
    image_path → ModelRequest(VISION) → OllamaAdapter → ModelResponse → VisualObservation

RÈGLES ARCHITECTURALES :
- Pas d'orchestration : le Harness reste décisionnaire.
- Ces fonctions sont de pures I/O Model Layer, appelables depuis
  tools/catalog/visual.py par injection (jamais import direct depuis un Device
  Agent ou un LightSensor).
- confidence = INFERRED TOUJOURS (jamais KNOWN_FACT pour une réponse Vision).
- Le fichier image est lu LOCALEMENT — jamais transmis hors du process
  sauf si la config l'autorise explicitement (cloud provider).
- Si le modèle Vision est indisponible → retourne None ou VisualObservation
  vide avec description="VISION_UNAVAILABLE" (jamais un crash).

GROUNDING PROMPT :
  Pour localiser un élément précis dans l'image, on demande au modèle
  de retourner les coordonnées dans un format parseable :
      FOUND: bbox=[x_min,y_min,x_max,y_max] label="..."
  Si les coordonnées sont hors range ou malformées, VisualTarget reste None.
"""

from __future__ import annotations

import re
from pathlib import Path

from raya.contracts import (
    BoundingBox,
    Confidence,
    ContentPart,
    FinishReason,
    Message,
    ModelCapability,
    ModelConstraints,
    ModelRequest,
    VisualArtifact,
    VisualObservation,
    VisualTarget,
    ViewportInfo,
    new_id,
)
from raya.models.registry import ModelRegistry
from raya.models.router import route
from raya.observability import log

# Prompt par défaut pour l'observation de scène générale
_SCENE_PROMPT = (
    "Describe what you see in this image. Focus on: "
    "(1) the main content or application visible, "
    "(2) any important UI elements, text, or controls, "
    "(3) any people or objects present. "
    "Be specific and concise (2-4 sentences)."
)

# Prompt de grounding — demande au modèle de localiser un élément précis.
# Format de réponse strict parseable.
_GROUNDING_PROMPT_TEMPLATE = (
    "In this image, find the element described as: '{target}'\n\n"
    "If found, respond with EXACTLY this format on one line:\n"
    "FOUND: bbox=[x_min,y_min,x_max,y_max] label=\"{target}\" confidence=0.N\n"
    "where x_min,y_min,x_max,y_max are normalized coordinates in range [0.0, 1.0]\n"
    "(0,0 = top-left corner, 1,1 = bottom-right corner)\n"
    "Example: FOUND: bbox=[0.25,0.10,0.75,0.20] label=\"{target}\" confidence=0.9\n"
    "Do NOT return pixel values — values must be between 0.0 and 1.0.\n\n"
    "If not found, respond with:\n"
    "NOT_FOUND: reason=\"brief reason\"\n\n"
    "After this line, you may add a brief description of the image."
)

# Regex pour parser la réponse FOUND du modèle.
# Tolère les espaces autour des virgules dans bbox (observé en production).
# Utilise (.+?) non-greedy avec terminateur "\s+confidence= pour gérer les
# guillemets internes dans le label (ex: label="bouton "Ajouter au panier"").
_FOUND_PATTERN = re.compile(
    r'FOUND:\s*bbox=\[\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\]'
    r'\s+label="(.+?)"\s+confidence=([0-9.]+)',
    re.IGNORECASE,
)


def _parse_grounding(response_text: str, observation_id: str, viewport: ViewportInfo | None) -> VisualTarget | None:
    """Parse la réponse du modèle pour extraire un VisualTarget grounded.
    Retourne None si :
    - la réponse contient NOT_FOUND
    - le format FOUND est malformé
    - les coordonnées sont pixel (>1.0) mais viewport absent (ne peut pas normaliser)
    - x_min >= x_max ou y_min >= y_max (bbox dégénérée)
    Jamais un crash : une réponse malformée produit None, pas une exception.
    """
    m = _FOUND_PATTERN.search(response_text)
    if m is None:
        return None
    try:
        x_min, y_min, x_max, y_max = float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))
        label = m.group(5).strip()
        confidence = float(m.group(6))
    except (ValueError, Exception):
        return None

    # Normalize pixel coordinates if the model returned values > 1.0
    if max(x_min, y_min, x_max, y_max) > 1.0:
        if viewport is None or viewport.image_width <= 0 or viewport.image_height <= 0:
            return None
        x_min = x_min / viewport.image_width
        y_min = y_min / viewport.image_height
        x_max = x_max / viewport.image_width
        y_max = y_max / viewport.image_height

    try:
        bbox = BoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)
    except (ValueError, Exception):
        return None

    vp = viewport or ViewportInfo(image_width=1, image_height=1)
    return VisualTarget(
        label=label,
        bbox=bbox,
        viewport=vp,
        observation_id=observation_id,
        confidence=min(1.0, max(0.0, confidence)),
    )


def observe_image(
    model_registry: ModelRegistry,
    image_path: str | Path,
    prompt: str = "",
    find_target: str = "",
    correlation_id: str = "",
    prefer_local: bool = False,
    viewport: ViewportInfo | None = None,
    source: str = "perception:screen",
) -> VisualObservation | None:
    """Analyse une image via le modèle Vision et retourne une VisualObservation.

    Args:
        model_registry  : registre des providers (doit avoir un provider VISION)
        image_path      : chemin LOCAL du fichier image (PNG/JPEG)
        prompt          : prompt texte optionnel (défaut = scène générale)
        find_target     : si non vide, demande le grounding de cet élément
        correlation_id  : correlation ID pour le traçage
        prefer_local    : préférer le modèle local (privacy-first)
        viewport        : info coordonnées pour le grounding
        source          : "perception:screen" | "perception:browser" | etc.

    Returns:
        VisualObservation ou None si aucun provider Vision disponible.
        En cas d'erreur modèle : VisualObservation avec description vide
        et raw_model_output contenant le code d'erreur (jamais un crash).
    """
    image_path = Path(image_path)
    if not image_path.exists():
        log("warning", "vision.observe_image: fichier image introuvable", path=str(image_path))
        return None

    if not correlation_id:
        correlation_id = new_id("vcorr")

    # Sélectionner le prompt selon le mode
    if find_target:
        text_prompt = _GROUNDING_PROMPT_TEMPLATE.format(target=find_target)
    else:
        text_prompt = prompt or _SCENE_PROMPT

    req = ModelRequest(
        capability=ModelCapability.VISION,
        messages=[
            Message(
                role="user",
                content=[
                    ContentPart(type="image_ref", value=str(image_path)),
                    ContentPart(type="text", value=text_prompt),
                ],
            )
        ],
        correlation_id=correlation_id,
        context_budget_tokens=1024,
        constraints=ModelConstraints(require_local=prefer_local),
    )

    response = route(model_registry, req, prefer_local=prefer_local)

    obs_id = new_id("vobs")
    model_used = response.provider_used or ""
    raw_text = "".join(p.value for p in response.content if p.type == "text")

    if response.finish_reason == FinishReason.ERROR:
        error_code = response.error.code if response.error else "UNKNOWN"
        log("warning", "vision.observe_image: modèle Vision indisponible",
            code=error_code, path=str(image_path))
        return VisualObservation(
            source=source,
            artifact_ref=str(image_path),
            description="",
            raw_model_output=f"ERROR:{error_code}",
            model_used=model_used,
            observation_id=obs_id,
            viewport=viewport,
        )

    # Extraire les entités sémantiques : mots-clés entre guillemets ou après ":"
    # Heuristique légère — pas de NLP, juste les noms propres/concepts en majuscule
    entities = list({
        w.strip(".,;:\"'()") for w in raw_text.split()
        if len(w) > 3 and (w[0].isupper() or w.lower() in _COMMON_UI_ENTITIES)
    })[:20]  # borné à 20 entités maximum

    # Grounding si demandé
    target = None
    if find_target:
        target = _parse_grounding(raw_text, obs_id, viewport)

    log("info", "vision.observe_image: observation produite",
        model=model_used, entities_count=len(entities),
        grounding=target is not None, correlation_id=correlation_id)

    return VisualObservation(
        source=source,
        artifact_ref=str(image_path),
        description=raw_text,
        semantic_entities=entities,
        target=target,
        raw_model_output=raw_text,
        model_used=model_used,
        observation_id=obs_id,
        viewport=viewport,
    )


# Entités UI communes — incluses dans les entités sémantiques même en minuscule
_COMMON_UI_ENTITIES = frozenset({
    "button", "menu", "toolbar", "window", "dialog", "tab", "input", "search",
    "form", "link", "icon", "image", "video", "player", "browser", "notepad",
    "calculator", "calendar", "settings", "notification", "taskbar", "desktop",
    "person", "face", "screen", "text", "title", "header",
})
