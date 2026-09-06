"""TaskScheduler — concurrence des Task Actors (consigne Phase 2 §11-§19, §27-§28).

Vit dans `harness/` et pas `tasks/` : la consigne Phase 2 §27 confie
explicitement au Harness la responsabilité de "concurrent task execution" —
`tasks/` (TaskRegistry) reste la couche données/lifecycle pure (comme en
Phase 1), sans dépendance à `safety`. Le scheduler, lui, a besoin de
`safety.should_stop()` (§16) — Harness a déjà cette dépendance autorisée
(RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.5), donc aucune extension du graphe de
dépendance n'est nécessaire pour `tasks/`.

Modèle de concurrence choisi : pool fixe de N threads "worker" (pattern déjà
utilisé en Phase 0/1 — pas d'asyncio introduit, pas de nouvelle dépendance).
Documenté ici plutôt que juste déclaré : un pool à N threads + une file de
priorité en mémoire est suffisant pour quelques tâches concurrentes locales ;
un vrai besoin de scale changerait cette implémentation sans changer le
contrat `Task`/`TaskEvent`.

Modèle d'exécution : COOPÉRATIF PAR STEP. Un "runnable" ne représente PAS une
boucle agentique (§28) — c'est une fonction qui exécute EXACTEMENT une unité
de travail et retourne si la tâche est terminée. Le scheduler la rappelle au
tour suivant si ce n'est pas fini. Ceci garantit qu'aucun thread ne "possède"
une tâche indéfiniment (respecte max_concurrent_tasks) et que la
cancellation/pause est observée entre deux steps, jamais au milieu d'un.
"""

from __future__ import annotations

import heapq
import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from raya.contracts import ErrorInfo, TaskState, parse_iso, utc_now_iso
from raya.observability import log
from raya.safety import SafetyService
from raya.tasks import TaskRegistry

StepFn = Callable[[str], bool]  # (task_id) -> terminé ?

_DEFAULT_MAX_CONCURRENT = 2
_AGING_RATE_PER_SECOND = 0.5  # priorité effective += 0.5/s d'attente (fairness, §14)
_MAX_AGING_BOOST = 50.0
_POLL_TIMEOUT_S = 0.1


@dataclass(order=True)
class _QueueItem:
    sort_key: float
    seq: int
    task_id: str = field(compare=False)


@dataclass(order=True)
class _DelayedItem:
    """Chantier 12 §B (Persistent Scheduling) : une tâche dont `not_before`
    est dans le futur n'entre JAMAIS dans `_queue` avant son échéance — sinon
    un step_fn qui retourne `done=False` serait immédiatement ré-enfilé PUIS
    immédiatement re-dépilé (aucune attente entre les deux, `_queue` n'étant
    jamais vide), un worker tournerait en boucle serrée pendant toute la
    durée du délai. `deadline` est en `time.monotonic()` (jamais une horloge
    murale, insensible à un changement d'heure système) — converti UNE fois
    depuis `Task.not_before` (UTC ISO) au moment de `submit()`."""

    deadline: float
    seq: int
    task_id: str = field(compare=False)
    priority: int = field(compare=False)


class TaskScheduler:
    def __init__(
        self,
        tasks: TaskRegistry,
        safety: SafetyService,
        max_concurrent_tasks: int = _DEFAULT_MAX_CONCURRENT,
    ) -> None:
        self._tasks = tasks
        self._safety = safety
        self._max_concurrent = max_concurrent_tasks
        self._runnables: dict[str, StepFn] = {}
        self._queue: list[_QueueItem] = []
        self._queued_ids: set[str] = set()
        self._enqueued_at: dict[str, float] = {}
        self._delayed: list[_DelayedItem] = []  # Chantier 12 §B — tâches en attente de leur échéance
        self._seq = itertools.count()
        self._cv = threading.Condition()
        self._shutting_down = threading.Event()
        self._workers = [
            threading.Thread(target=self._worker_loop, daemon=True, name=f"scheduler-worker-{i}")
            for i in range(max_concurrent_tasks)
        ]
        for w in self._workers:
            w.start()

    # ------------------------------------------------------------------

    def submit(self, task_id: str, step_fn: StepFn, priority: int, not_before: str | None = None) -> None:
        """Enregistre une tâche et la place en file — équivalent de
        CREATED/READY (consigne §9) : aucun TaskState "READY" n'existe dans
        le contrat figé, donc "prêt" est un état du SCHEDULER, pas du Task.

        `not_before` (Chantier 12 §B, additif) : ISO8601 UTC. Si dans le
        futur, la tâche entre dans `_delayed` (jamais `_queue`) et n'est
        migrée vers la file exécutable qu'à échéance — voir `_pop_next`."""
        with self._cv:
            self._runnables[task_id] = step_fn
            deadline = self._deadline_monotonic(not_before)
            if deadline is None:
                self._enqueue_locked(task_id, priority)
            else:
                heapq.heappush(self._delayed, _DelayedItem(deadline, next(self._seq), task_id, priority))
            self._cv.notify()
        self._tasks.mark_ready(task_id)

    @staticmethod
    def _deadline_monotonic(not_before: str | None) -> float | None:
        """Convertit `not_before` (horloge murale UTC) en échéance
        `time.monotonic()` UNE fois — jamais recomparé à une horloge murale
        ensuite (insensible à un changement d'heure système pendant l'attente).
        `None` si déjà échu ou absent : exécution immédiate, comportement
        inchangé pour tout appelant existant qui ne passe pas `not_before`."""
        if not not_before:
            return None
        remaining = (parse_iso(not_before) - parse_iso(utc_now_iso())).total_seconds()
        if remaining <= 0:
            return None
        return time.monotonic() + remaining

    def resume(self, task_id: str, priority: int) -> None:
        """Ré-enfile une tâche PAUSED après resume() — reprend au prochain
        step à partir du dernier checkpoint persistant (jamais depuis zéro,
        consigne §18) : c'est le step_fn lui-même qui relit le checkpoint."""
        with self._cv:
            if task_id not in self._runnables:
                return  # tâche inconnue du scheduler (déjà terminée/jamais soumise)
            self._enqueue_locked(task_id, priority)
            self._cv.notify()

    def wake(self) -> None:
        with self._cv:
            self._cv.notify_all()

    def active_task_ids(self) -> list[str]:
        return list(self._runnables.keys())

    def is_managed(self, task_id: str) -> bool:
        return task_id in self._runnables

    # ------------------------------------------------------------------

    def _enqueue_locked(self, task_id: str, priority: int) -> None:
        self._enqueued_at.setdefault(task_id, time.monotonic())
        sort_key = self._priority_key(task_id, priority)
        item = _QueueItem(sort_key, next(self._seq), task_id)
        heapq.heappush(self._queue, item)
        self._queued_ids.add(task_id)

    def _priority_key(self, task_id: str, priority: int) -> float:
        waited = time.monotonic() - self._enqueued_at.get(task_id, time.monotonic())
        boost = min(waited * _AGING_RATE_PER_SECOND, _MAX_AGING_BOOST)  # fairness anti-starvation (§14)
        return -(priority + boost)  # heap = min-first ; on veut la priorité effective la plus haute d'abord

    def _migrate_due_locked(self) -> None:
        """Chantier 12 §B : fait passer dans `_queue` toute tâche dont
        l'échéance est atteinte — DOIT être appelé avec `self._cv` déjà tenu
        (courant dans `_pop_next`, jamais un lock séparé)."""
        now = time.monotonic()
        while self._delayed and self._delayed[0].deadline <= now:
            item = heapq.heappop(self._delayed)
            self._enqueue_locked(item.task_id, item.priority)

    def _pop_next(self) -> str | None:
        """N'exclut PAS les tâches en file quand should_stop() est actif :
        §16 exige à la fois "ne pas démarrer de nouvelle tâche" ET "les
        tâches en cours doivent recevoir la cancellation" — les deux sont
        gérés uniformément par `_process_one_step` (qui vérifie should_stop()
        AVANT d'appeler step_fn et finalise en CANCELLED sinon). Un filtre ICI
        empêcherait justement les tâches déjà en file d'être dépilées pour
        être annulées, les laissant RUNNING indéfiniment après un STOP.

        Chantier 12 §B : migre les tâches différées échues AVANT de tester
        `_queue` — jamais de busy-spin pendant l'attente (le `cv.wait`
        ci-dessous est borné par la prochaine échéance connue, pas seulement
        par `_POLL_TIMEOUT_S`)."""
        with self._cv:
            while True:
                if self._shutting_down.is_set():
                    return None
                self._migrate_due_locked()
                if self._queue:
                    item = heapq.heappop(self._queue)
                    self._queued_ids.discard(item.task_id)
                    return item.task_id
                timeout = _POLL_TIMEOUT_S
                if self._delayed:
                    timeout = min(timeout, max(0.0, self._delayed[0].deadline - time.monotonic()))
                self._cv.wait(timeout=timeout)

    def _worker_loop(self) -> None:
        while not self._shutting_down.is_set():
            task_id = self._pop_next()
            if task_id is None:
                continue
            self._process_one_step(task_id)

    def _process_one_step(self, task_id: str) -> None:
        task = self._tasks.get(task_id)
        if task is None or task.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
            self._forget(task_id)
            return
        if task.state == TaskState.PAUSED:
            return  # ne pas ré-enfiler : resume() le fera explicitement (§17)

        if self._safety.should_stop() or task.cancellation_requested:
            self._finalize_cancel(task_id, task)
            return

        if task.state == TaskState.PENDING:
            self._tasks.start(task_id)

        step_fn = self._runnables.get(task_id)
        if step_fn is None:
            return
        try:
            done = step_fn(task_id)
        except Exception as exc:  # un step qui plante ne doit jamais tuer le worker
            try:
                self._tasks.fail(task_id, ErrorInfo(code="TASK_STEP_ERROR", message=str(exc)))
            except (KeyError, ValueError):
                pass
            self._forget(task_id)
            log("error", "task step exception", task_id=task_id, error=str(exc))
            return

        current = self._tasks.get(task_id)
        if current is None or current.state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
            self._forget(task_id)
            return
        if current.state == TaskState.PAUSED:
            return  # pausé pendant le step (course bénigne) : pas de ré-enfilage

        if done:
            self._forget(task_id)
            return

        with self._cv:
            if task_id in self._runnables:
                self._enqueue_locked(task_id, current.priority)
                self._cv.notify()

    def _finalize_cancel(self, task_id: str, task) -> None:
        try:
            if task.state in (TaskState.RUNNING, TaskState.PENDING, TaskState.PAUSED):
                self._tasks.cancel(task_id)
        except (KeyError, ValueError):
            pass
        self._forget(task_id)

    def _forget(self, task_id: str) -> None:
        self._runnables.pop(task_id, None)
        self._enqueued_at.pop(task_id, None)

    # ------------------------------------------------------------------

    def shutdown(self, timeout_s: float = 2.0) -> None:
        """Ordre garanti (consigne §33, bug Phase 1 corrigé) :
        1. arrêter d'accepter du nouveau travail
        2. demander la cancellation des tâches actives
        3. attendre les workers
        4. SEULEMENT ENSUITE l'appelant peut fermer la persistence."""
        self._shutting_down.set()
        with self._cv:
            self._cv.notify_all()
        for task_id in list(self._runnables.keys()):
            try:
                task = self._tasks.get(task_id)
                if task is not None and task.state in (TaskState.RUNNING, TaskState.PENDING, TaskState.PAUSED):
                    self._tasks.cancel(task_id)
            except (KeyError, ValueError):
                pass
        for w in self._workers:
            w.join(timeout=timeout_s)
        log("info", "scheduler shutdown complete")
