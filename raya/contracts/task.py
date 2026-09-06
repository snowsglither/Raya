"""Task, TaskEvent (RAYA_V2_CONTRACTS.md §5-6) — Task Actors persistants.

Plan/PlanStep (Phase 10, Long-Horizon Autonomy) : structure de données
EXPLICITE représentant la décomposition d'un objectif en étapes — jamais un
second système de persistance (elle vit dans `Task.checkpoint["plan"]`,
sérialisée/désérialisée via les contrats génériques `to_dict`/`from_dict`,
au même titre que n'importe quel autre champ de checkpoint). Le planner
(raya/cognition/planning.py) PRODUIT un `Plan`, il ne l'exécute jamais —
c'est le Harness qui avance `current_step_id` au fil des ticks du scheduler."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ._base import new_id, utc_now_iso
from .errors import ErrorInfo
from .errors import ErrorInfo


class TaskState(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    # Chantier 15 (Agentic Reliability, Axe D) : distinct de FAILED —
    # l'exécution ne peut PAS continuer sans une action externe (permission,
    # confirmation, information manquante), jamais un échec technique.
    # Avant ce chantier, une tâche de fond ayant besoin d'une confirmation
    # (CONFIRMATION_REQUIRED_IN_BACKGROUND) finissait FAILED comme une
    # tâche réellement cassée — les deux étaient indiscernables sans lire
    # `task.error.code`. Résumable (comme PAUSED) une fois la situation
    # résolue, jamais un état terminal.
    BLOCKED = "BLOCKED"


TERMINAL_STATES = frozenset({TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED})

# RAYA_V2_CONTRACTS.md §5 : transition stricte, un état terminal ne redevient jamais actif.
_ALLOWED_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PENDING: frozenset({TaskState.RUNNING, TaskState.CANCELLED}),
    TaskState.RUNNING: frozenset(
        {TaskState.PAUSED, TaskState.BLOCKED, TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.PAUSED: frozenset({TaskState.RUNNING, TaskState.CANCELLED}),
    # Résumable exactement comme PAUSED (`TaskRegistry.resume()`, inchangé,
    # fonctionne déjà pour n'importe quel état source autorisé ici) —
    # jamais une deuxième méthode dupliquée pour "débloquer".
    TaskState.BLOCKED: frozenset({TaskState.RUNNING, TaskState.CANCELLED}),
    TaskState.COMPLETED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
}


def can_transition(current: TaskState, target: TaskState) -> bool:
    return target in _ALLOWED_TRANSITIONS[current]


@dataclass
class TaskProgress:
    current_step: str = ""
    percent: float | None = None


@dataclass
class TaskOwner:
    channel: str
    session_id: str


@dataclass
class Task:
    objective: str
    owner: TaskOwner
    correlation_id: str
    id: str = field(default_factory=lambda: new_id("task"))
    state: TaskState = TaskState.PENDING
    priority: int = 0
    context: dict = field(default_factory=dict)
    progress: TaskProgress = field(default_factory=TaskProgress)
    dependencies: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    checkpoint: dict | None = None
    cancellation_requested: bool = False
    result: dict | None = None
    error: ErrorInfo | None = None
    # Chantier 12 §B (Persistent Scheduling) — additif, jamais un second champ
    # de persistance : ISO8601 UTC, ou `None` (comportement inchangé,
    # exécution dès que le scheduler a un worker libre). Un Task en attente
    # de son échéance reste PENDING, jamais un TaskState distinct — le
    # scheduler seul décide QUAND le rendre exécutable (voir harness/scheduler.py).
    not_before: str | None = None

    def transition_to(self, target: TaskState) -> None:
        if not can_transition(self.state, target):
            raise ValueError(f"Transition Task illégale : {self.state.value} -> {target.value}")
        self.state = target
        self.updated_at = utc_now_iso()


_TASK_EVENT_TYPES = frozenset(
    {
        "task.created",
        "task.ready",  # AJOUTÉ Phase 2 — accepté par le scheduler, en file (pas un TaskState distinct)
        "task.started",
        "task.progress",
        "task.checkpoint",
        "task.paused",
        "task.resumed",
        "task.cancel_requested",  # AJOUTÉ Phase 2 — signal coopératif, distinct de task.cancelled
        "task.cancelled",
        "task.completed",
        "task.failed",
        "task.recovered",  # AJOUTÉ Phase 2 — RUNNING->PAUSED spécifiquement causé par un crash/recovery
        "task.replanned",  # AJOUTÉ Phase 10 — une étape a échoué, une étape alternative a été insérée
        "task.blocked",  # AJOUTÉ Chantier 15 — RUNNING->BLOCKED, distinct de task.failed (voir TaskState.BLOCKED)
    }
)


class StepState(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"  # contourné par un replanning — jamais retenté


@dataclass
class PlanStep:
    id: str
    objective: str
    status: StepState = StepState.PENDING
    dependencies: list[str] = field(default_factory=list)
    attempts: int = 0
    result: dict | None = None
    evidence: dict | None = None
    error: ErrorInfo | None = None


@dataclass
class Plan:
    steps: list[PlanStep] = field(default_factory=list)
    current_step_id: str | None = None


def next_runnable_step(plan: Plan) -> PlanStep | None:
    """Le premier step PENDING dont toutes les dépendances sont COMPLETED —
    jamais un step dont une dépendance a échoué (FAILED n'est jamais
    considéré satisfait, évite d'exécuter une étape sur une base invalide)."""
    completed_ids = {s.id for s in plan.steps if s.status in (StepState.COMPLETED, StepState.SKIPPED)}
    for step in plan.steps:
        if step.status == StepState.PENDING and all(d in completed_ids for d in step.dependencies):
            return step
    return None


def plan_is_complete(plan: Plan) -> bool:
    return bool(plan.steps) and all(s.status in (StepState.COMPLETED, StepState.SKIPPED) for s in plan.steps)


def plan_is_stuck(plan: Plan) -> bool:
    """Aucune étape PENDING/RUNNING n'est exécutable et le plan n'est pas
    complet — typiquement une étape FAILED bloque toutes celles qui en
    dépendent, sans alternative proposée par le replanning."""
    if plan_is_complete(plan):
        return False
    return next_runnable_step(plan) is None and not any(s.status == StepState.RUNNING for s in plan.steps)


@dataclass
class TaskEventPayload:
    task_id: str
    new_state: str
    previous_state: str | None = None
    detail: dict | None = None


@dataclass
class TaskEvent:
    type: str
    payload: TaskEventPayload
    source: str = "tasks"
    id: str = field(default_factory=lambda: new_id("evt"))
    timestamp: str = field(default_factory=utc_now_iso)
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if self.type not in _TASK_EVENT_TYPES:
            raise ValueError(f"TaskEvent.type inconnu : {self.type!r} (catalogue fermé)")
