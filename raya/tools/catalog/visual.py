"""Tools de perception visuelle — vision.observe_screen, vision.observe_browser,
vision.observe_image, vision.find_on_screen, vision.find_in_browser.

RÈGLE ARCHITECTURALE : ces tools sont autorisés à appeler le Model Layer via
l'injection `observe_fn` fournie par bootstrap — la séparation architecturale
est préservée par injection de callable (même pattern que register_task_control_tools
qui injecte harness.create_long_horizon_task). Les Device Agents restent
inchangés. Aucun import direct de raya.models depuis ce module.

DOM-FIRST pour le browser : vision.observe_browser et vision.find_in_browser
sont des FALLBACKS. Le modèle doit d'abord tenter browser.read_page / browser.click.
Vision intervient uniquement si le DOM est insuffisant ou ambigu.

PRIVACY : les screenshots contenant potentiellement des données sensibles
(password fields, données bancaires) ne sont JAMAIS envoyés au cloud
automatiquement — l'option prefer_local=True force le modèle local si disponible.
"""

from __future__ import annotations

import hashlib as _hashlib
import time as _time
from pathlib import Path
from typing import Callable

from raya.contracts import (
    Confidence,
    ErrorInfo,
    ObservationSpec,
    PermissionLevel,
    Tool,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    ViewportInfo,
    VisualObservation,
)

# Promotion WorldState : description textuelle + entités de l'observation écran
_SCREEN_OBS_SPEC = (
    ObservationSpec(
        domain="visual",
        key="screen_state",
        evidence_field="visual_observation",
        confidence=Confidence.INFERRED,
        freshness_ttl_s=30,
    ),
)

# Promotion WorldState : observation browser
_BROWSER_OBS_SPEC = (
    ObservationSpec(
        domain="visual",
        key="browser_visual_state",
        evidence_field="visual_observation",
        confidence=Confidence.INFERRED,
        freshness_ttl_s=30,
    ),
)

# Promotion WorldState : cible visuellement localisée dans le browser, avec
# contexte de fraîcheur (url + fingerprint + timestamp) pour permettre à
# Cognition de raisonner sur la staleness avant d'utiliser les coords.
_BROWSER_LAST_TARGET_SPEC = ObservationSpec(
    domain="visual",
    key="browser_last_target",
    evidence_field="browser_last_target",
    confidence=Confidence.INFERRED,
    freshness_ttl_s=30,
)

# Pour vision.find_in_browser : description visuelle générale + cible localisée.
_BROWSER_FIND_OBS_SPEC = (
    ObservationSpec(
        domain="visual",
        key="browser_visual_state",
        evidence_field="visual_observation",
        confidence=Confidence.INFERRED,
        freshness_ttl_s=30,
    ),
    _BROWSER_LAST_TARGET_SPEC,
)

# Promotion WorldState : observation image (fichier existant)
_IMAGE_OBS_SPEC = (
    ObservationSpec(
        domain="visual",
        key="last_image_observation",
        evidence_field="visual_observation",
        confidence=Confidence.INFERRED,
        freshness_ttl_s=60,
    ),
)

ObserveFn = Callable[[str | Path, str, str, str, bool, ViewportInfo | None, str], VisualObservation | None]
CaptureFn = Callable[[], dict]  # retourne {"path": str, "width": int, "height": int}


def _ok(call: ToolCall, output: dict, evidence: dict) -> ToolResult:
    return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
                      output=output, evidence=evidence)


def _fail(call: ToolCall, code: str, message: str) -> ToolResult:
    return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                      error=ErrorInfo(code=code, message=message, retryable=False))


def _obs_to_evidence(obs: VisualObservation) -> dict:
    """Construit le dict evidence utilisable par ObservationSpec pour promouvoir
    l'observation dans WorldState. visual_observation = valeur WorldState (sans
    artifact_ref, sans raw_model_output — uniquement les faits dérivés)."""
    return {
        "visual_observation": obs.to_world_state_value(),
        "observation_id": obs.observation_id,
        "description": obs.description,
    }


def _obs_to_output(obs: VisualObservation) -> dict:
    """Sortie complète retournée au modèle — inclut description, entités,
    grounding si disponible, mais JAMAIS le chemin d'image brut."""
    out: dict = {
        "observation_id": obs.observation_id,
        "description": obs.description,
        "semantic_entities": obs.semantic_entities,
        "model_used": obs.model_used,
        "confidence": obs.confidence.value,
    }
    if obs.target is not None:
        sx, sy = obs.target.screen_coordinates()
        out["target"] = {
            "label": obs.target.label,
            "confidence": obs.target.confidence,
            "screen_x": sx,
            "screen_y": sy,
            "bbox": {
                "x_min": obs.target.bbox.x_min,
                "y_min": obs.target.bbox.y_min,
                "x_max": obs.target.bbox.x_max,
                "y_max": obs.target.bbox.y_max,
            },
            "observation_id": obs.target.observation_id,
        }
    return out


def register_visual_tools(
    registry,
    observe_fn: ObserveFn,
    capture_screen_fn: CaptureFn,
    capture_browser_fn: CaptureFn,
    *,
    prefer_local: bool = False,
) -> None:
    """Enregistre les tools de perception visuelle.

    Args:
        registry          : ToolRegistry
        observe_fn        : callable(path, prompt, find_target, corr_id,
                             prefer_local, viewport, source) → VisualObservation | None
                             (fourni par bootstrap, wraps raya.models.vision.observe_image)
        capture_screen_fn : callable() → {"path": str, "width": int, "height": int}
                             (fourni par bootstrap, wraps pyautogui.screenshot)
        capture_browser_fn: callable() → {"path": str}
                             (fourni par bootstrap, wraps BrowserController.screenshot)
        prefer_local      : préférer le modèle local pour privacy (configurable)
    """
    # ─────────────────────────────────────────────────────────────────────────
    # vision.observe_screen
    # ─────────────────────────────────────────────────────────────────────────
    def _handle_observe_screen(call: ToolCall) -> ToolResult:
        prompt = call.arguments.get("prompt", "")
        try:
            cap = capture_screen_fn()
        except Exception as exc:
            return _fail(call, "SCREEN_CAPTURE_FAILED", str(exc))

        path = cap.get("path", "")
        w, h = cap.get("width", 0), cap.get("height", 0)
        viewport = ViewportInfo(image_width=w, image_height=h) if w and h else None

        obs = observe_fn(path, prompt, "", call.correlation_id, prefer_local, viewport, "perception:screen")
        if obs is None:
            return _fail(call, "VISION_UNAVAILABLE",
                         "Aucun provider Vision disponible — vérifier RAYA_MODEL_POOL et OLLAMA_API_KEY")
        if not obs.description:
            return _fail(call, "VISION_ERROR",
                         f"Le modèle Vision a retourné une réponse vide ({obs.raw_model_output[:100]})")
        return _ok(call, _obs_to_output(obs), _obs_to_evidence(obs))

    registry.register(
        Tool(
            name="vision.observe_screen",
            description=(
                "Capture l'écran et demande au modèle Vision de décrire ce qu'il voit. "
                "Retourne une description textuelle de la scène, les éléments UI visibles, "
                "et les entités sémantiques. À utiliser pour comprendre l'état visuel de "
                "l'écran quand le DOM ou l'UIA ne suffisent pas. NE PAS appeler après chaque "
                "action — uniquement quand une compréhension visuelle est nécessaire."
            ),
            capability_tags=["vision", "pc.read"],
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Instruction spécifique (ex: 'Décris les boutons visibles'). Optionnel.",
                    }
                },
            },
            output_schema={"type": "object"},
            permission_level=PermissionLevel.SAFE,
            idempotent=False,
            observation=_SCREEN_OBS_SPEC,
        ),
        _handle_observe_screen,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # vision.find_on_screen
    # ─────────────────────────────────────────────────────────────────────────
    def _handle_find_on_screen(call: ToolCall) -> ToolResult:
        target = call.arguments.get("target", "").strip()
        if not target:
            return _fail(call, "MISSING_TARGET", "L'argument 'target' est requis")
        try:
            cap = capture_screen_fn()
        except Exception as exc:
            return _fail(call, "SCREEN_CAPTURE_FAILED", str(exc))

        path = cap.get("path", "")
        w, h = cap.get("width", 0), cap.get("height", 0)
        viewport = ViewportInfo(image_width=w, image_height=h) if w and h else None

        obs = observe_fn(path, "", target, call.correlation_id, prefer_local, viewport, "perception:screen")
        if obs is None:
            return _fail(call, "VISION_UNAVAILABLE",
                         "Aucun provider Vision disponible")
        if obs.target is None:
            return _fail(call, "TARGET_NOT_FOUND",
                         f"Le modèle Vision n'a pas trouvé '{target}' dans l'écran. "
                         f"Description de la scène : {obs.description[:200]}")
        return _ok(call, _obs_to_output(obs), _obs_to_evidence(obs))

    registry.register(
        Tool(
            name="vision.find_on_screen",
            description=(
                "Capture l'écran et demande au modèle Vision de localiser un élément précis. "
                "Retourne les coordonnées (screen_x, screen_y) utilisables avec pc.mouse.click, "
                "et une bounding box normalisée. Les coordonnées sont liées à l'observation "
                "(observation_id) pour garantir la traçabilité — jamais de coordonnées inventées. "
                "Utiliser UNIQUEMENT quand pc.ui.click a échoué et que l'élément est visuellement "
                "identifiable. NE PAS utiliser si l'élément est accessible par UIA/DOM."
            ),
            capability_tags=["vision", "pc.read"],
            input_schema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Description de l'élément à trouver (ex: 'bouton Play', 'champ de recherche').",
                    }
                },
                "required": ["target"],
            },
            output_schema={"type": "object"},
            permission_level=PermissionLevel.SAFE,
            idempotent=False,
            observation=_SCREEN_OBS_SPEC,
        ),
        _handle_find_on_screen,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # vision.observe_browser
    # ─────────────────────────────────────────────────────────────────────────
    def _handle_observe_browser(call: ToolCall) -> ToolResult:
        prompt = call.arguments.get("prompt", "")
        try:
            cap = capture_browser_fn()
        except Exception as exc:
            return _fail(call, "BROWSER_CAPTURE_FAILED", str(exc))

        path = cap.get("path", "")
        if not path:
            return _fail(call, "BROWSER_CAPTURE_FAILED", "Pas de chemin d'image retourné par le browser")

        obs = observe_fn(path, prompt, "", call.correlation_id, prefer_local, None, "perception:browser")
        if obs is None:
            return _fail(call, "VISION_UNAVAILABLE", "Aucun provider Vision disponible")
        if not obs.description:
            return _fail(call, "VISION_ERROR",
                         f"Le modèle Vision a retourné une réponse vide ({obs.raw_model_output[:100]})")
        return _ok(call, _obs_to_output(obs), _obs_to_evidence(obs))

    registry.register(
        Tool(
            name="vision.observe_browser",
            description=(
                "Capture le browser et demande au modèle Vision de décrire le contenu visuel. "
                "DOM-FIRST : utiliser browser.read_page en premier. Ce tool est un FALLBACK "
                "quand le DOM ne suffit pas (contenus canvas, images, éléments CSS non accessibles). "
                "Retourne une description de la scène visible dans le browser."
            ),
            capability_tags=["vision", "browser.read"],
            input_schema={
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Instruction spécifique optionnelle.",
                    }
                },
            },
            output_schema={"type": "object"},
            permission_level=PermissionLevel.SAFE,
            idempotent=False,
            observation=_BROWSER_OBS_SPEC,
        ),
        _handle_observe_browser,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # vision.find_in_browser
    # ─────────────────────────────────────────────────────────────────────────
    def _handle_find_in_browser(call: ToolCall) -> ToolResult:
        target = call.arguments.get("target", "").strip()
        if not target:
            return _fail(call, "MISSING_TARGET", "L'argument 'target' est requis")
        try:
            cap = capture_browser_fn()
        except Exception as exc:
            return _fail(call, "BROWSER_CAPTURE_FAILED", str(exc))

        path = cap.get("path", "")
        if not path:
            return _fail(call, "BROWSER_CAPTURE_FAILED", "Pas de chemin d'image retourné")

        w, h = cap.get("width", 0), cap.get("height", 0)
        vp = ViewportInfo(image_width=w, image_height=h) if (w and h) else None
        obs = observe_fn(path, "", target, call.correlation_id, prefer_local, vp, "perception:browser")
        if obs is None:
            return _fail(call, "VISION_UNAVAILABLE", "Aucun provider Vision disponible")
        if obs.target is None:
            return _fail(call, "TARGET_NOT_FOUND",
                         f"'{target}' non trouvé dans le browser via Vision. "
                         f"Scène : {obs.description[:200]}")
        evidence = _obs_to_evidence(obs)
        observed_url = cap.get("url", "")
        observed_title = cap.get("title", "")
        fp = _hashlib.md5(f"{observed_url}|{observed_title}".encode()).hexdigest()[:12]
        sx, sy = obs.target.screen_coordinates()
        evidence["browser_last_target"] = {
            "label": obs.target.label,
            "screen_x": sx,
            "screen_y": sy,
            "bbox": {
                "x_min": obs.target.bbox.x_min,
                "y_min": obs.target.bbox.y_min,
                "x_max": obs.target.bbox.x_max,
                "y_max": obs.target.bbox.y_max,
            },
            "confidence": obs.target.confidence,
            "observation_id": obs.target.observation_id,
            "observed_url": observed_url,
            "observed_page_fingerprint": fp,
            "observed_at": _time.time(),
        }
        return _ok(call, _obs_to_output(obs), evidence)

    registry.register(
        Tool(
            name="vision.find_in_browser",
            description=(
                "Capture le browser et demande au modèle Vision de localiser un élément précis. "
                "Retourne (screen_x, screen_y) utilisables avec browser.click_at_position. "
                "DOM-FIRST : utiliser browser.click en premier. Ce tool est un FALLBACK pour "
                "les contrôles visuels non accessibles par DOM (lecteurs vidéo, canvas, "
                "éléments dynamiques). Les coordonnées retournées sont liées à l'observation "
                "(observation_id) — jamais inventées."
            ),
            capability_tags=["vision", "browser.read"],
            input_schema={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Description de l'élément à trouver (ex: 'bouton Play', 'barre de progression').",
                    }
                },
                "required": ["target"],
            },
            output_schema={"type": "object"},
            permission_level=PermissionLevel.SAFE,
            idempotent=False,
            observation=_BROWSER_FIND_OBS_SPEC,
        ),
        _handle_find_in_browser,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # vision.observe_image
    # ─────────────────────────────────────────────────────────────────────────
    def _handle_observe_image(call: ToolCall) -> ToolResult:
        path = call.arguments.get("path", "").strip()
        if not path:
            return _fail(call, "MISSING_PATH", "L'argument 'path' est requis")
        prompt = call.arguments.get("prompt", "")

        obs = observe_fn(path, prompt, "", call.correlation_id, prefer_local, None, "perception:image")
        if obs is None:
            return _fail(call, "VISION_UNAVAILABLE", "Aucun provider Vision disponible")
        if not obs.description:
            return _fail(call, "VISION_ERROR",
                         f"Le modèle Vision a retourné une réponse vide ({obs.raw_model_output[:100]})")
        return _ok(call, _obs_to_output(obs), _obs_to_evidence(obs))

    registry.register(
        Tool(
            name="vision.observe_image",
            description=(
                "Analyse une image existante via le modèle Vision et retourne sa description. "
                "path doit être un chemin de fichier local accessible (PNG, JPEG). "
                "Utile pour analyser une image partagée ou un screenshot préalablement capturé. "
                "Pour capturer ET analyser l'écran en une seule étape, utiliser vision.observe_screen."
            ),
            capability_tags=["vision", "pc.read"],
            input_schema={
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Chemin local du fichier image à analyser.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Instruction spécifique optionnelle.",
                    },
                },
                "required": ["path"],
            },
            output_schema={"type": "object"},
            permission_level=PermissionLevel.SAFE,
            idempotent=True,
            observation=_IMAGE_OBS_SPEC,
        ),
        _handle_observe_image,
    )
