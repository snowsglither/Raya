"""Groupe C — Concurrency : plusieurs tâches concurrentes, limite de
concurrence, priorité, fairness/no-starvation, progression indépendante."""

from __future__ import annotations

import time

from raya.contracts import ErrorInfo, TaskOwner
from raya.event_bus import EventBus
from raya.harness.scheduler import TaskScheduler
from raya.persistence import InMemoryBackend
from raya.safety import AuditTrail, SafetyService, StopController
from raya.tasks import TaskRegistry, priority as prio


def _setup(max_concurrent: int = 2):
    bus = EventBus()
    tasks = TaskRegistry(InMemoryBackend(), bus)
    safety = SafetyService(StopController(bus), AuditTrail())
    scheduler = TaskScheduler(tasks, safety, max_concurrent_tasks=max_concurrent)
    return tasks, safety, scheduler


def _counting_step_fn(tasks: TaskRegistry, counter: dict, key: str, total_steps: int, delay: float = 0.01):
    """Le scheduler ne complète JAMAIS une tâche lui-même — c'est la
    responsabilité du step_fn (seul à connaître le résultat), exactement
    comme le démonstrateur réel du Harness (_make_simulated_step_fn)."""

    def step(task_id: str) -> bool:
        time.sleep(delay)
        counter[key] = counter.get(key, 0) + 1
        done = counter[key] >= total_steps
        if done:
            tasks.complete(task_id, {"summary": f"{key} terminé"})
        return done

    return step


def _make_and_submit(tasks, scheduler, counter, key, priority=prio.NORMAL, total_steps=5, delay=0.01):
    task = tasks.create(key, TaskOwner(channel="cli", session_id="s1"), correlation_id=key, priority=priority)
    scheduler.submit(task.id, _counting_step_fn(tasks, counter, key, total_steps, delay), priority=priority)
    return task


def test_two_tasks_progress_independently():
    tasks, safety, scheduler = _setup(max_concurrent=2)
    counter: dict = {}
    task_a = _make_and_submit(tasks, scheduler, counter, "A")
    task_b = _make_and_submit(tasks, scheduler, counter, "B")
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            from raya.contracts import TaskState

            if tasks.get(task_a.id).state == TaskState.COMPLETED and tasks.get(task_b.id).state == TaskState.COMPLETED:
                break
            time.sleep(0.02)
        from raya.contracts import TaskState

        assert tasks.get(task_a.id).state == TaskState.COMPLETED
        assert tasks.get(task_b.id).state == TaskState.COMPLETED
        assert counter["A"] == 5
        assert counter["B"] == 5
    finally:
        scheduler.shutdown()


def test_max_concurrency_never_exceeded():
    """Instrumente step_fn pour observer combien de tâches sont EN VOL simultanément."""
    tasks, safety, scheduler = _setup(max_concurrent=2)
    in_flight = {"current": 0, "max_seen": 0}
    import threading

    lock = threading.Lock()

    def make_step(total_steps):
        def step(task_id: str) -> bool:
            with lock:
                in_flight["current"] += 1
                in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["current"])
            time.sleep(0.02)
            with lock:
                in_flight["current"] -= 1
            state = tasks.get(task_id)
            step_count = (state.checkpoint or {}).get("n", 0) + 1
            tasks.checkpoint(task_id, {"n": step_count})
            done = step_count >= total_steps
            if done:
                tasks.complete(task_id, {"summary": "ok"})
            return done

        return step

    task_ids = []
    for i in range(5):
        task = tasks.create(f"task_{i}", TaskOwner(channel="cli", session_id="s1"), correlation_id=f"c{i}")
        task_ids.append(task.id)
        scheduler.submit(task.id, make_step(3), priority=prio.NORMAL)

    try:
        from raya.contracts import TaskState

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if all(tasks.get(tid).state == TaskState.COMPLETED for tid in task_ids):
                break
            time.sleep(0.02)
        assert all(tasks.get(tid).state == TaskState.COMPLETED for tid in task_ids)
        assert in_flight["max_seen"] <= 2  # jamais dépassé max_concurrent_tasks=2
    finally:
        scheduler.shutdown()


def test_high_priority_runs_before_low_priority():
    tasks, safety, scheduler = _setup(max_concurrent=1)  # 1 seul worker -> ordre strictement observable
    order: list = []

    def make_step(name):
        def step(task_id: str) -> bool:
            order.append(name)
            time.sleep(0.01)
            return True

        return step

    task_low = tasks.create("low", TaskOwner(channel="cli", session_id="s1"), correlation_id="c1", priority=prio.LOW)
    task_high = tasks.create("high", TaskOwner(channel="cli", session_id="s1"), correlation_id="c2", priority=prio.HIGH)
    # Soumission dans l'ordre LOW puis HIGH -> HIGH doit quand même passer avant
    # si LOW n'a pas encore commencé (le worker unique n'a pas encore piscé).
    scheduler.submit(task_low.id, make_step("low"), priority=prio.LOW)
    scheduler.submit(task_high.id, make_step("high"), priority=prio.HIGH)

    try:
        deadline = time.monotonic() + 2.0
        while len(order) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert order[0] == "high"
    finally:
        scheduler.shutdown()


def test_fairness_low_priority_eventually_runs_no_starvation():
    """§14 : une tâche basse priorité ne doit jamais être privée d'exécution
    indéfiniment — le mécanisme d'aging doit la faire progresser."""
    tasks, safety, scheduler = _setup(max_concurrent=1)
    counter: dict = {}

    task_low = _make_and_submit(tasks, scheduler, counter, "low", priority=prio.LOW, total_steps=1, delay=0.01)

    # Alimente continuellement des tâches HIGH pour créer de la contention.
    def spam_high():
        for i in range(15):
            t = tasks.create(f"high_{i}", TaskOwner(channel="cli", session_id="s1"), correlation_id=f"h{i}", priority=prio.HIGH)
            scheduler.submit(t.id, _counting_step_fn(tasks, counter, f"high_{i}", 1, delay=0.005), priority=prio.HIGH)
            time.sleep(0.02)

    import threading

    spammer = threading.Thread(target=spam_high, daemon=True)
    spammer.start()

    try:
        from raya.contracts import TaskState

        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if tasks.get(task_low.id).state == TaskState.COMPLETED:
                break
            time.sleep(0.02)
        spammer.join(timeout=2.0)
        assert tasks.get(task_low.id).state == TaskState.COMPLETED, "la tâche LOW n'a jamais pu s'exécuter (starvation)"
    finally:
        scheduler.shutdown()


def test_queued_task_waits_when_at_concurrency_limit():
    """Synchronisation par threading.Event plutôt que par sleep() fixe — un
    délai fixe est intrinsèquement fragile sous charge (suite complète, CI
    partagée) : la version précédente de ce test a été observée flaky."""
    import threading

    tasks, safety, scheduler = _setup(max_concurrent=1)
    started = []
    a_started = threading.Event()
    a_may_finish = threading.Event()

    def step_a(task_id: str) -> bool:
        started.append("A")
        a_started.set()
        a_may_finish.wait(timeout=2.0)  # bloque A tant que le test n'a pas vérifié B
        return True

    def step_b(task_id: str) -> bool:
        started.append("B")
        return True

    task_a = tasks.create("A", TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    task_b = tasks.create("B", TaskOwner(channel="cli", session_id="s1"), correlation_id="c2")
    scheduler.submit(task_a.id, step_a, priority=prio.NORMAL)
    scheduler.submit(task_b.id, step_b, priority=prio.NORMAL)

    assert a_started.wait(timeout=2.0), "A n'a jamais démarré"
    assert "B" not in started  # un seul worker : B ne peut pas avoir démarré tant que A bloque
    a_may_finish.set()
    scheduler.shutdown()


def test_step_exception_marks_task_failed_not_stuck():
    tasks, safety, scheduler = _setup(max_concurrent=1)

    def failing_step(task_id: str) -> bool:
        raise RuntimeError("boom")

    task = tasks.create("obj", TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    scheduler.submit(task.id, failing_step, priority=prio.NORMAL)

    try:
        from raya.contracts import TaskState

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if tasks.get(task.id).state == TaskState.FAILED:
                break
            time.sleep(0.02)
        assert tasks.get(task.id).state == TaskState.FAILED
        assert tasks.get(task.id).error.code == "TASK_STEP_ERROR"
    finally:
        scheduler.shutdown()


# --- Chantier 12 §B (Persistent Scheduling) : not_before ---

def test_not_before_delays_execution_until_due():
    from raya.contracts import utc_now_iso, parse_iso
    from datetime import timedelta

    tasks, safety, scheduler = _setup(max_concurrent=1)
    ran_at: dict = {}

    def step(task_id: str) -> bool:
        ran_at["t"] = time.monotonic()
        tasks.complete(task_id, {"summary": "ok"})
        return True

    task = tasks.create("rappel", TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    due = parse_iso(utc_now_iso()) + timedelta(seconds=0.3)
    not_before = due.strftime("%Y-%m-%dT%H:%M:%S.") + f"{due.microsecond // 1000:03d}Z"
    submitted_at = time.monotonic()
    scheduler.submit(task.id, step, priority=prio.NORMAL, not_before=not_before)
    try:
        # bien avant l'échéance : jamais exécutée prématurément
        time.sleep(0.1)
        assert "t" not in ran_at
        from raya.contracts import TaskState

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if tasks.get(task.id).state == TaskState.COMPLETED:
                break
            time.sleep(0.01)
        assert tasks.get(task.id).state == TaskState.COMPLETED
        assert ran_at["t"] - submitted_at >= 0.25  # marge : jamais avant l'échéance
    finally:
        scheduler.shutdown()


def test_not_before_in_the_past_runs_immediately():
    from raya.contracts import utc_now_iso, parse_iso
    from datetime import timedelta

    tasks, safety, scheduler = _setup(max_concurrent=1)

    def step(task_id: str) -> bool:
        tasks.complete(task_id, {"summary": "ok"})
        return True

    task = tasks.create("en retard", TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    past = parse_iso(utc_now_iso()) - timedelta(seconds=5)
    not_before = past.strftime("%Y-%m-%dT%H:%M:%S.") + f"{past.microsecond // 1000:03d}Z"
    scheduler.submit(task.id, step, priority=prio.NORMAL, not_before=not_before)
    try:
        from raya.contracts import TaskState

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if tasks.get(task.id).state == TaskState.COMPLETED:
                break
            time.sleep(0.01)
        assert tasks.get(task.id).state == TaskState.COMPLETED
    finally:
        scheduler.shutdown()


def test_no_busy_spin_while_task_not_yet_due():
    """La tâche différée ne doit JAMAIS repasser par step_fn avant son
    échéance — si `_pop_next` ré-enfilait/re-dépilait en boucle serrée
    (busy-spin), step_fn serait appelée des centaines de fois avant l'échéance."""
    from raya.contracts import utc_now_iso, parse_iso
    from datetime import timedelta

    tasks, safety, scheduler = _setup(max_concurrent=1)
    call_count = {"n": 0}

    def step(task_id: str) -> bool:
        call_count["n"] += 1
        tasks.complete(task_id, {"summary": "ok"})
        return True

    task = tasks.create("pas de busy-spin", TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    due = parse_iso(utc_now_iso()) + timedelta(seconds=0.3)
    not_before = due.strftime("%Y-%m-%dT%H:%M:%S.") + f"{due.microsecond // 1000:03d}Z"
    scheduler.submit(task.id, step, priority=prio.NORMAL, not_before=not_before)
    try:
        time.sleep(0.15)
        assert call_count["n"] == 0
    finally:
        scheduler.shutdown()


def test_cancelled_delayed_task_never_executes():
    from raya.contracts import utc_now_iso, parse_iso
    from datetime import timedelta

    tasks, safety, scheduler = _setup(max_concurrent=1)
    call_count = {"n": 0}

    def step(task_id: str) -> bool:
        call_count["n"] += 1
        return True

    task = tasks.create("annulée avant échéance", TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    due = parse_iso(utc_now_iso()) + timedelta(seconds=0.2)
    not_before = due.strftime("%Y-%m-%dT%H:%M:%S.") + f"{due.microsecond // 1000:03d}Z"
    scheduler.submit(task.id, step, priority=prio.NORMAL, not_before=not_before)
    try:
        tasks.cancel(task.id)  # annulation directe (chemin PENDING de Harness.cancel_task)
        time.sleep(0.4)  # laisse largement le temps de dépasser l'échéance
        from raya.contracts import TaskState

        assert tasks.get(task.id).state == TaskState.CANCELLED
        assert call_count["n"] == 0
    finally:
        scheduler.shutdown()


def test_active_task_ids_reflects_managed_tasks():
    tasks, safety, scheduler = _setup(max_concurrent=1)
    counter: dict = {}
    task = _make_and_submit(tasks, scheduler, counter, "A", total_steps=100, delay=0.05)
    try:
        assert task.id in scheduler.active_task_ids()
    finally:
        scheduler.shutdown()
