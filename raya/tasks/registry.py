"""TaskRegistry — persistant (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.9, §6).

Phase 2 : reste STRICTEMENT la couche données/lifecycle (CRUD + persistance +
validation de transition). L'orchestration/concurrence (scheduler) vit dans
`raya/harness/scheduler.py` — Harness est le subsystem explicitement chargé
de "concurrent task execution" (consigne Phase 2 §27), pas Tasks. Ce choix
évite d'ajouter `safety`/`attention` aux dépendances autorisées de `tasks/`
(RAYA_V2_REPOSITORY_STRUCTURE.md §20 inchangé pour `tasks`).

Recovery (§11.3 Phase 1, §31 Phase 2) : un Task retrouvé RUNNING après un
redémarrage n'est JAMAIS considéré comme terminé avec succès — il est
transitionné vers PAUSED, resumable explicitement, jamais réactivé
silencieusement en RUNNING. Événement dédié `task.recovered` (Phase 2) pour
le distinguer d'une pause volontaire.
"""

from __future__ import annotations

import threading

from raya.contracts import (
    ErrorInfo,
    Task,
    TaskEvent,
    TaskEventPayload,
    TaskOwner,
    TaskProgress,
    TaskState,
    from_dict,
    to_dict,
    utc_now_iso,
)
from raya.event_bus import EventBus
from raya.persistence import PersistenceBackend

_COLLECTION = "tasks"


class TaskRegistry:
    def __init__(self, backend: PersistenceBackend, bus: EventBus | None = None) -> None:
        self._backend = backend
        self._bus = bus
        self._lock = threading.Lock()
        # Cache "progress" en mémoire uniquement — task.progress est publié à
        # CHAQUE step (bon marché, EventBus only) sans écrire en SQLite à
        # chaque tick (consigne Phase 2 §19/§46). La persistance réelle de la
        # progression se fait via checkpoint(), appelé aux moments significatifs.
        self._progress_cache: dict[str, TaskProgress] = {}

    def _emit(self, event_type: str, task: Task, previous_state: str | None, detail: dict | None = None) -> None:
        if self._bus is None:
            return
        self._bus.publish(
            TaskEvent(
                type=event_type,
                source="tasks",
                correlation_id=task.correlation_id,
                payload=TaskEventPayload(
                    task_id=task.id, new_state=task.state.value, previous_state=previous_state, detail=detail
                ),
            )
        )

    def _save(self, task: Task) -> None:
        self._backend.save(_COLLECTION, task.id, to_dict(task))

    def create(self, objective: str, owner: TaskOwner, correlation_id: str, priority: int = 0,
               not_before: str | None = None) -> Task:
        task = Task(objective=objective, owner=owner, correlation_id=correlation_id, priority=priority,
                    not_before=not_before)
        with self._lock:
            self._save(task)
        self._emit("task.created", task, previous_state=None)
        return task

    def get(self, task_id: str) -> Task | None:
        raw = self._backend.load(_COLLECTION, task_id)
        if raw is None:
            return None
        task = from_dict(Task, raw)
        cached = self._progress_cache.get(task_id)
        if cached is not None:
            task.progress = cached
        return task

    def list(self, **filters: object) -> list[Task]:
        raw = self._backend.query(_COLLECTION, **filters)
        tasks = [from_dict(Task, r) for r in raw]
        for task in tasks:
            cached = self._progress_cache.get(task.id)
            if cached is not None:
                task.progress = cached
        return tasks

    def _transition(self, task_id: str, target: TaskState, event_type: str) -> Task:
        with self._lock:
            task = self.get(task_id)
            if task is None:
                raise KeyError(f"Task inconnu : {task_id!r}")
            previous = task.state
            task.transition_to(target)
            self._save(task)
        self._emit(event_type, task, previous_state=previous.value)
        return task

    def mark_ready(self, task_id: str) -> None:
        """Purement informatif (Phase 2 §21) — 'ready' n'est PAS un TaskState
        distinct (le contrat Task figé n'en définit pas), c'est un événement
        signalant que le scheduler a mis la tâche en file d'attente."""
        task = self.get(task_id)
        if task is None:
            return
        self._emit("task.ready", task, previous_state=task.state.value)

    def start(self, task_id: str) -> Task:
        return self._transition(task_id, TaskState.RUNNING, "task.started")

    def report_progress(self, task_id: str, current_step: str, percent: float | None) -> None:
        """Événement FRÉQUENT et bon marché — jamais d'écriture SQLite ici
        (consigne Phase 2 §19 : "ne pas écrire à chaque micro-instruction").
        Attention filtre le bruit (§49) ; checkpoint() reste responsable de
        la persistance réelle aux moments significatifs."""
        task = self.get(task_id)
        if task is None:
            return
        progress = TaskProgress(current_step=current_step, percent=percent)
        self._progress_cache[task_id] = progress
        if self._bus is None:
            return
        self._bus.publish(
            TaskEvent(
                type="task.progress",
                source="tasks",
                correlation_id=task.correlation_id,
                payload=TaskEventPayload(
                    task_id=task_id, new_state=task.state.value, previous_state=task.state.value,
                    detail={"current_step": current_step, "percent": percent},
                ),
            )
        )

    def checkpoint(self, task_id: str, checkpoint: dict) -> Task:
        """Suffisant pour reprendre : état, progression, référence de contexte,
        état d'exécution courant, timestamp (RAYA_V2_MIGRATION_PLAN.md §11.1).
        Jamais de chain-of-thought stocké ici. Écriture SQLite réelle — appelée
        aux moments significatifs seulement (avant pause/shutdown/completion/
        échec récupérable, ou tous les N steps), pas à chaque micro-étape."""
        with self._lock:
            task = self.get(task_id)
            if task is None:
                raise KeyError(f"Task inconnu : {task_id!r}")
            task.checkpoint = checkpoint
            task.updated_at = utc_now_iso()
            self._save(task)
        self._progress_cache.pop(task_id, None)  # désormais durable, le cache n'est plus nécessaire
        self._emit("task.checkpoint", task, previous_state=task.state.value)
        return task

    def pause(self, task_id: str) -> Task:
        return self._transition(task_id, TaskState.PAUSED, "task.paused")

    def resume(self, task_id: str) -> Task:
        return self._transition(task_id, TaskState.RUNNING, "task.resumed")

    def request_cancellation(self, task_id: str) -> Task:
        """Signal COOPÉRATIF (consigne Phase 2 §15) : marque l'intention sans
        transitionner immédiatement — utile quand un worker du scheduler est
        potentiellement en train d'exécuter un step. `cancel()` reste la
        finalisation immédiate (état terminal), utilisée directement pour les
        tâches PENDING/PAUSED où rien n'est activement en cours."""
        with self._lock:
            task = self.get(task_id)
            if task is None:
                raise KeyError(f"Task inconnu : {task_id!r}")
            task.cancellation_requested = True
            self._save(task)
        self._emit("task.cancel_requested", task, previous_state=task.state.value)
        return task

    def cancel(self, task_id: str) -> Task:
        with self._lock:
            task = self.get(task_id)
            if task is None:
                raise KeyError(f"Task inconnu : {task_id!r}")
            task.cancellation_requested = True
            self._save(task)
        result = self._transition(task_id, TaskState.CANCELLED, "task.cancelled")
        self._progress_cache.pop(task_id, None)
        return result

    def complete(self, task_id: str, result: dict) -> Task:
        with self._lock:
            task = self.get(task_id)
            if task is None:
                raise KeyError(f"Task inconnu : {task_id!r}")
            task.result = result
            self._save(task)
        outcome = self._transition(task_id, TaskState.COMPLETED, "task.completed")
        self._progress_cache.pop(task_id, None)
        return outcome

    def block(self, task_id: str, error: ErrorInfo) -> Task:
        """Chantier 15 (Axe D) : distinct de `fail()` — l'exécution ne peut
        pas continuer sans une action externe (permission/confirmation/
        information manquante), jamais un échec technique. Résumable via
        `resume()` (inchangé, générique à l'état source) une fois la
        situation résolue — jamais un état terminal comme FAILED."""
        with self._lock:
            task = self.get(task_id)
            if task is None:
                raise KeyError(f"Task inconnu : {task_id!r}")
            task.error = error
            self._save(task)
        outcome = self._transition(task_id, TaskState.BLOCKED, "task.blocked")
        self._progress_cache.pop(task_id, None)
        return outcome

    def fail(self, task_id: str, error: ErrorInfo) -> Task:
        with self._lock:
            task = self.get(task_id)
            if task is None:
                raise KeyError(f"Task inconnu : {task_id!r}")
            task.error = error
            self._save(task)
        outcome = self._transition(task_id, TaskState.FAILED, "task.failed")
        self._progress_cache.pop(task_id, None)
        return outcome

    def recover_after_restart(self) -> list[Task]:
        """RAYA_V2_MIGRATION_PLAN.md §11.3, consigne Phase 2 §31 : un Task
        RUNNING au crash devient PAUSED au redémarrage — jamais réputé
        terminé avec succès, jamais réactivé silencieusement en RUNNING.
        Événement `task.recovered` (Phase 2) pour distinguer d'une pause
        volontaire — utile à Attention pour décider de le signaler."""
        recovered: list[Task] = []
        for task in self.list(state=TaskState.RUNNING.value):
            with self._lock:
                task.transition_to(TaskState.PAUSED)
                checkpoint = dict(task.checkpoint or {})
                checkpoint["recovered_after_restart"] = True
                task.checkpoint = checkpoint
                self._save(task)
            self._emit("task.recovered", task, previous_state=TaskState.RUNNING.value)
            recovered.append(task)
        return recovered
