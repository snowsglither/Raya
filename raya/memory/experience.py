"""Distillation d'expériences réutilisables depuis les tâches complétées.

Principe : un task COMPLETED avec un plan multi-étapes ayant nécessité des
retries peut produire une MemoryEntry(type=EXPERIENCE, layer=EXPERIENCE).

Ce qui est stocké : stratégie abstraite, preuve de succès, confiance.
Ce qui n'est PAS stocké : coordonnées, URLs, sélecteurs DOM, secrets.

Architecture invariant :
- Ce module écrit en mémoire, jamais en World State ni en EventBus.
- Il ne fait aucun appel modèle (distillation déterministe).
- Il est appelé par le Harness uniquement après task COMPLETED, jamais
  depuis un Tool ou un Device Agent.
- LOG ≠ MEMORY : ne convertit pas les traces brutes en mémoire.
"""

from __future__ import annotations

import re

from raya.contracts import (
    ChannelScope,
    Confidence,
    MemoryEntry,
    MemoryLayer,
    MemoryLifecycle,
    MemoryType,
    Plan,
    StepState,
    Task,
)
from raya.memory.store import MemoryStore

# Mots trivials : une tâche dont l'objectif ne contient que ces mots
# ne produit pas d'expérience (ex: "Bonjour", "Merci").
_TRIVIAL_PATTERNS = re.compile(
    r"^(bonjour|salut|merci|ok|oui|non|hello|hi|bye|test|essai)\s*[.!?]?$",
    re.IGNORECASE,
)

# Familles de tools par préfixe — pour dériver l'interface utilisée.
_TOOL_FAMILY_MAP = {
    "browser": "browser",
    "pc.mouse": "pc_mouse",
    "pc.ui": "pc_ui",
    "pc.keyboard": "pc_keyboard",
    "pc.application": "pc_application",
    "pc.window": "pc_window",
    "pc.file": "pc_file",
    "pc.clipboard": "pc_clipboard",
}

# Patterns fragiles à ne jamais stocker comme stratégie.
# Note : #id et .class ne peuvent pas utiliser \b au début (# et . sont non-word).
_FRAGILE_PATTERNS = re.compile(
    r"x=\d+|y=\d+|#[\w-]+|(?<!\w)\.[\w-]+|\d{3,}px|https?://\S+"
    r"|(?:selector|locator|xpath|password|passwd|token|secret|key|apikey)=\S*",
    re.IGNORECASE,
)

_MIN_STEPS = 2  # un plan trivial à 1 étape ne produit pas d'expérience


def maybe_distill_experience(task: Task, plan: Plan, memory: MemoryStore) -> MemoryEntry | None:
    """Distille une expérience réutilisable depuis un plan complété.

    Retourne l'entrée créée ou None si la tâche n'est pas digne d'une expérience.
    """
    if not _is_experience_worthy(task, plan):
        return None

    content = _extract_experience(task, plan)
    if content is None:
        return None

    channel_scope = _infer_channel_scope(task)
    entry = MemoryEntry(
        type=MemoryType.EXPERIENCE,
        layer=MemoryLayer.EXPERIENCE,
        channel_scope=channel_scope,
        content=content,
        provenance=f"harness:task_completion:{task.id}",
        lifecycle=MemoryLifecycle.CONFIRMED,
        confidence=Confidence.INFERRED,
        related_task_id=task.id,
    )
    memory.write(entry)
    return entry


def _is_experience_worthy(task: Task, plan: Plan) -> bool:
    """Conditions pour qu'une tâche mérite une expérience mémorisable."""
    if not plan.steps or len(plan.steps) < _MIN_STEPS:
        return False
    objective = (task.objective or "").strip()
    if not objective or _TRIVIAL_PATTERNS.match(objective):
        return False
    completed_steps = [s for s in plan.steps if s.status == StepState.COMPLETED]
    if not completed_steps:
        return False
    # Intéressant si au moins une étape a nécessité un retry (signe que RAYA
    # a dû adapter sa stratégie) — une tâche réussie du premier coup ne
    # livre pas d'apprentissage structural supplémentaire.
    steps_with_retry = [s for s in completed_steps if (s.attempts or 0) > 1]
    return len(steps_with_retry) > 0


def _extract_experience(task: Task, plan: Plan) -> dict | None:
    """Extrait la stratégie abstraite depuis le plan.

    Ne conserve PAS : URLs, coordonnées, sélecteurs DOM, valeurs numériques
    brutes, secrets.
    """
    completed_steps = [s for s in plan.steps if s.status == StepState.COMPLETED]
    retry_steps = [s for s in completed_steps if (s.attempts or 0) > 1]

    # Dériver la famille de tools dominante.
    tool_calls = _collect_tool_calls(plan)
    tool_family = _dominant_tool_family(tool_calls)

    # Construire la stratégie : objectifs des étapes complétées, sans données
    # fragiles. On garde uniquement la DESCRIPTION des étapes.
    step_summaries = []
    for step in completed_steps:
        obj = _sanitize_text(step.objective or "")
        if obj:
            result_text = ""
            if step.result:
                rt = _sanitize_text(str(step.result.get("text", "")))
                if rt and len(rt) < 120:
                    result_text = f" → {rt}"
            step_summaries.append(f"{obj}{result_text}")

    if not step_summaries:
        return None

    # Preuve de succès depuis les étapes avec retry (l'étape difficile).
    proof_texts = []
    for step in retry_steps:
        if step.result:
            rt = _sanitize_text(str(step.result.get("text", "")))
            if rt and len(rt) < 200:
                proof_texts.append(rt)

    return {
        "objective_type": _sanitize_text(task.objective or ""),
        "interface": tool_family,
        "strategy": _build_strategy_text(step_summaries, tool_family),
        "proof": "; ".join(proof_texts) if proof_texts else "task completed successfully",
        "steps_count": len(completed_steps),
        "attempts_before_success": max((s.attempts or 1) for s in retry_steps) if retry_steps else 1,
    }


def _sanitize_text(text: str) -> str:
    """Supprime les patterns fragiles (URLs, coordonnées, sélecteurs DOM)."""
    return _FRAGILE_PATTERNS.sub("[ref]", text).strip()


def _collect_tool_calls(plan: Plan) -> list[str]:
    """Collecte les noms de tools appelés dans les résultats d'étapes."""
    tool_names = []
    for step in plan.steps:
        if step.result:
            tool = step.result.get("tool")
            if tool and isinstance(tool, str):
                tool_names.append(tool)
    return tool_names


def _dominant_tool_family(tool_calls: list[str]) -> str:
    """Détermine la famille de tools dominante (browser, pc_ui, etc.)."""
    counts: dict[str, int] = {}
    for call in tool_calls:
        for prefix, family in _TOOL_FAMILY_MAP.items():
            if call.startswith(prefix):
                counts[family] = counts.get(family, 0) + 1
                break
    if not counts:
        return "general"
    return max(counts, key=lambda k: counts[k])


def _build_strategy_text(step_summaries: list[str], tool_family: str) -> str:
    """Construit un texte de stratégie lisible depuis les étapes."""
    base = "; ".join(step_summaries)
    if tool_family == "browser":
        return f"Browser-based: {base}"
    if tool_family in ("pc_ui", "pc_application"):
        return f"Desktop UI: {base}"
    return base


_CHANNEL_TO_SCOPE = {
    "voice": ChannelScope.VOICE,
    "mobile": ChannelScope.IOS,
}


def _infer_channel_scope(task: Task) -> ChannelScope:
    if not task.owner:
        return ChannelScope.SHARED
    return _CHANNEL_TO_SCOPE.get(task.owner.channel.value if hasattr(task.owner.channel, "value") else str(task.owner.channel), ChannelScope.CHAT)
