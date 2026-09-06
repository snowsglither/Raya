"""Planning / Replanning (RAYA V2 Phase 10, consigne §3/§6).

Cognition PRODUIT un plan, il ne l'exécute JAMAIS — c'est le Harness qui
avance `Plan.current_step_id` au fil des ticks du `TaskScheduler` (consigne
§3 : "Le planner ne doit PAS exécuter les Tools"). Utilise le Model Layer
EXISTANT (`raya.models.route`), la capability `ModelCapability.PLANNING`
déjà définie depuis la Phase 0 mais jamais utilisée avant cette phase —
aucun provider spécifique Long-Horizon, aucun appel direct à un provider
(consigne §16).

Repli honnête (jamais un plan vide, jamais une exception) : si le modèle
n'est pas disponible, renvoie une erreur, ou une réponse non exploitable, le
plan retombe sur une seule étape correspondant à l'objectif brut tel quel —
jamais un objectif inventé, jamais une décomposition fabriquée sans base
réelle."""

from __future__ import annotations

import json

from raya.contracts import (
    ContentPart,
    Message,
    ModelCapability,
    ModelRequest,
    Plan,
    PlanStep,
    new_id,
)
from raya.models import ModelRegistry
from raya.models import route as model_route

_MAX_STEPS = 5

_PLAN_SYSTEM_PROMPT = (
    "Break the following objective into 1 to "
    f"{_MAX_STEPS} concrete, ordered, actionable steps. "
    "If one of the tools made available to you can achieve the ENTIRE objective "
    "by itself in one call (e.g. a messaging tool that sends a message directly), "
    "respond with a SINGLE step describing exactly that direct action — never invent "
    "a longer manual procedure (e.g. operating a device/app by hand) when a tool "
    "already covers the objective directly. "
    "Respond ONLY with a JSON array of short strings, one per step, "
    "no prose, no markdown code fence. If the objective is already a "
    "single concrete action, respond with a JSON array containing "
    "exactly that one string."
)

_REPLAN_INSTRUCTION = (
    "Propose ONE alternative concrete next step that could still achieve "
    "the objective, given the failure described above. Respond with a "
    "single short sentence describing that step, or the single word NONE "
    "if no alternative is possible. No prose beyond that."
)


def _extract_text(response) -> str:
    return "".join(p.value for p in response.content if p.type == "text").strip()


def _parse_step_list(text: str) -> list[str]:
    candidates = [text]
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, list):
            steps = [str(item).strip() for item in data if str(item).strip()]
            if steps:
                return steps[:_MAX_STEPS]
    return []


def build_plan(objective: str, model_registry: ModelRegistry, correlation_id: str,
                available_tools: list[dict] | None = None) -> Plan:
    """Un seul appel modèle borné (jamais une chaîne de planification
    récursive) — reproduit exactement le même `ModelRequest`/`route()` que
    n'importe quel autre appel du Model Layer (consigne §16).

    `available_tools` (Chantier 14, additif — `None` préserve le
    comportement précédent) : SANS ceci, ce modèle de planification n'a
    aucune idée des Tools réellement disponibles et peut halluciner une
    procédure manuelle plausible (ex: "ouvrir l'app Telegram sur le
    téléphone, taper le message, appuyer sur envoyer") alors qu'un Tool
    direct existe déjà (`telegram.send_message`) — constaté en réel
    (Chantier 14, validation E2E) : un rappel "envoie-moi ça sur Telegram"
    était planifié comme une séquence de contrôle physique du téléphone au
    lieu d'un unique appel du Tool d'envoi, ce qui bloquait la tâche sur sa
    toute première étape (téléphone non connecté) et faisait boucler le
    modèle en RETENTATIVES, chacune renvoyant malgré tout un vrai message
    Telegram réel à chaque tentative (le modèle avait accès au Tool à
    l'EXÉCUTION, juste pas un plan qui l'utilisait directement). Passer les
    mêmes schémas de Tools qu'à l'exécution (`Harness._discover_tool_schemas()`)
    permet au modèle de PLANIFICATION de préférer un plan à une seule étape
    quand un Tool couvre déjà tout l'objectif — jamais un appel de Tool
    RÉELLEMENT exécuté ici (`model_route()` retourne au plus une INTENTION
    de tool call, jamais exécutée par `build_plan()`, qui ne lit que le
    texte de la réponse — voir `_extract_text`/`_parse_step_list`)."""
    request = ModelRequest(
        capability=ModelCapability.PLANNING,
        messages=[
            Message(role="system", content=[ContentPart(type="text", value=_PLAN_SYSTEM_PROMPT)]),
            Message(role="user", content=[ContentPart(type="text", value=objective)]),
        ],
        correlation_id=correlation_id,
        available_tools=available_tools or None,
    )
    response = model_route(model_registry, request)
    step_texts = _parse_step_list(_extract_text(response)) if response.finish_reason.value != "error" else []
    if not step_texts:
        step_texts = [objective]

    steps = [PlanStep(id=new_id("step"), objective=text) for text in step_texts]
    for i in range(1, len(steps)):
        steps[i].dependencies = [steps[i - 1].id]  # séquentiel par défaut, jamais un ordre inventé plus complexe
    return Plan(steps=steps, current_step_id=steps[0].id if steps else None)


def replan_step(
    objective: str, failed_step: PlanStep, evidence: dict | None, model_registry: ModelRegistry, correlation_id: str,
) -> PlanStep | None:
    """Retourne `None` si aucune alternative n'est proposée/exploitable —
    jamais une étape fabriquée sans base réelle du modèle (le Harness
    échoue alors honnêtement la tâche plutôt que d'inventer une suite)."""
    prompt = (
        f"Global objective: {objective!r}\n"
        f"Failed step: {failed_step.objective!r}\n"
        f"Evidence/error: {evidence!r}\n\n{_REPLAN_INSTRUCTION}"
    )
    request = ModelRequest(
        capability=ModelCapability.PLANNING,
        messages=[Message(role="user", content=[ContentPart(type="text", value=prompt)])],
        correlation_id=correlation_id,
    )
    response = model_route(model_registry, request)
    if response.finish_reason.value == "error":
        return None
    text = _extract_text(response)
    if not text or text.strip().upper() == "NONE":
        return None
    return PlanStep(id=new_id("step"), objective=text)
