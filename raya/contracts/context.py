"""Context (RAYA_V2_CONTRACTS.md §14) — sortie de context_engine.assemble().

POINT CRITIQUE (RAYA_V2_ARCHITECTURE_FINAL_REVIEW.md §6, corrigé) : toute
section kind=world_state DOIT porter un `freshness` non-null. Un
WorldStateFact stale ne peut structurellement jamais être présenté au modèle
comme s'il était active — la validation lève ValueError sinon.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ._base import utc_now_iso
from .world_state import FactStatus


class SectionKind(str, enum.Enum):
    MEMORY = "memory"
    WORLD_STATE = "world_state"
    CONVERSATION_HISTORY = "conversation_history"
    TASK_STATE = "task_state"
    TOOL_SCHEMAS = "tool_schemas"
    SYSTEM_RULES = "system_rules"
    # Chantier 16 (Contextualisation, "Task context") : distinct de
    # TASK_STATE — TASK_STATE reste LA tâche dont l'étape est en train de
    # s'exécuter (mandatory, toujours une seule, jamais rognée par le
    # budget) ; ACTIVE_TASKS liste les AUTRES tâches actives de l'utilisateur
    # (rappels programmés, téléchargements en fond...), visibles même
    # pendant une conversation normale (sans task= en cours) — optionnelle,
    # soumise au même tri par budget que WORLD_STATE/MEMORY (jamais un dump
    # inconditionnel de toutes les tâches, consigne §22 "contexte pertinent
    # et limité").
    ACTIVE_TASKS = "active_tasks"


@dataclass
class Freshness:
    status: FactStatus
    as_of: str


@dataclass
class ContextSection:
    kind: SectionKind
    content: object
    provenance: str
    rank_score: float = 0.0
    freshness: Freshness | None = None

    def __post_init__(self) -> None:
        if self.kind == SectionKind.WORLD_STATE and self.freshness is None:
            raise ValueError(
                "ContextSection: kind=world_state exige freshness non-null "
                "(RAYA_V2_CONTRACTS.md §14) — un fait World State ne peut jamais "
                "être transmis au modèle sans son statut de fraîcheur."
            )


@dataclass
class Context:
    session_id: str
    budget_tokens: int
    sections: list[ContextSection] = field(default_factory=list)
    task_id: str | None = None
    assembled_at: str = field(default_factory=utc_now_iso)
    used_tokens_estimate: int = 0
    cache_hit: bool = False

    def __post_init__(self) -> None:
        if self.used_tokens_estimate > self.budget_tokens:
            raise ValueError(
                f"Context: used_tokens_estimate ({self.used_tokens_estimate}) "
                f"dépasse budget_tokens ({self.budget_tokens})"
            )
