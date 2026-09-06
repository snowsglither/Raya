"""Les 8 scénarios d'intégration explicitement requis (consigne Phase 2 §44).

Système réel de bout en bout (§45) : vrai SQLite, vrai EventBus, vraie Safety,
vrai TaskScheduler, vraie persistance Task, vraie Attention, vrai Harness —
seul le TRAVAIL de la tâche de fond est simulé (démonstrateur, pas un vrai
Device Agent/Tool, hors scope Phase 2).
"""

from __future__ import annotations

import time

from raya.contracts import Channel, Event, HarnessRequest, InterfaceInput, TaskState
from raya.persistence import SqliteBackend
from raya.runtime.bootstrap import bootstrap
from raya.runtime.config import load_config
from raya.tasks import priority as prio


def _handles(tmp_path, name: str, max_concurrent: int = 2):
    cfg = load_config()
    cfg.db_path = tmp_path / name
    cfg.max_concurrent_tasks = max_concurrent
    return bootstrap(config=cfg, backend=SqliteBackend(cfg.db_path))


def _wait_for(predicate, timeout_s: float = 3.0, interval_s: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


# --- 1. TWO CONCURRENT TASKS ---

def test_1_two_concurrent_tasks_both_progress_and_complete(tmp_path):
    handles = _handles(tmp_path, "s1.sqlite3")
    try:
        task_a = handles.harness.start_background_task("task A", total_steps=5)
        task_b = handles.harness.start_background_task("task B", total_steps=5, set_as_focus=False)

        assert _wait_for(lambda: handles.tasks.get(task_a.id).state == TaskState.COMPLETED)
        assert _wait_for(lambda: handles.tasks.get(task_b.id).state == TaskState.COMPLETED)
        # Persistance réelle des deux résultats.
        assert handles.tasks.get(task_a.id).result is not None
        assert handles.tasks.get(task_b.id).result is not None
    finally:
        handles.shutdown()


# --- 2. CONVERSATION DURING TASK ---

def test_2_conversation_answered_immediately_during_task(tmp_path):
    handles = _handles(tmp_path, "s2.sqlite3")
    try:
        handles.harness.start_background_task("tâche longue", total_steps=20)
        start = time.monotonic()
        req = HarnessRequest(channel=Channel.CLI, session_id="s1", input=InterfaceInput(text="salut"))
        state = handles.harness.handle_request(req)
        elapsed = time.monotonic() - start
        # Phase 3 : NullProvider (pas de clé Ollama dans ce test) -> FAILED
        # honnête plutôt que COMPLETED. Ce qui est testé ICI est l'absence de
        # blocage, pas la nature de la réponse.
        assert state.status.value in ("COMPLETED", "FAILED")
        assert elapsed < 0.2  # bien avant la fin des 20 steps de la tâche de fond
    finally:
        handles.shutdown()


# --- 3. PRIORITY ---

def test_3_high_priority_preferred_low_eventually_runs(tmp_path):
    handles = _handles(tmp_path, "s3.sqlite3", max_concurrent=1)
    try:
        low = handles.harness.start_background_task("low", priority=prio.LOW, total_steps=2, set_as_focus=False)
        high = handles.harness.start_background_task("high", priority=prio.HIGH, total_steps=2, set_as_focus=False)

        assert _wait_for(lambda: handles.tasks.get(high.id).state == TaskState.COMPLETED)
        assert _wait_for(lambda: handles.tasks.get(low.id).state == TaskState.COMPLETED)  # jamais starvé
    finally:
        handles.shutdown()


# --- 4. PAUSE/RESUME ---

def test_4_pause_stops_progress_resume_continues(tmp_path):
    handles = _handles(tmp_path, "s4.sqlite3")
    try:
        task = handles.harness.start_background_task("obj", total_steps=6)
        assert _wait_for(lambda: (handles.tasks.get(task.id).checkpoint or {}).get("step_index", 0) >= 2, timeout_s=2.0)
        handles.harness.pause_task(task.id)
        time.sleep(0.1)
        frozen = handles.tasks.get(task.id).progress
        time.sleep(0.15)
        assert handles.tasks.get(task.id).progress == frozen  # aucune progression pendant la pause

        handles.harness.resume_task(task.id)
        assert _wait_for(lambda: handles.tasks.get(task.id).state == TaskState.COMPLETED)
    finally:
        handles.shutdown()


# --- 5. CANCEL ---

def test_5_cancel_running_task_cooperative_shutdown(tmp_path):
    handles = _handles(tmp_path, "s5.sqlite3")
    try:
        task = handles.harness.start_background_task("obj", total_steps=50)
        time.sleep(0.03)
        handles.harness.cancel_task(task.id)
        assert _wait_for(lambda: handles.tasks.get(task.id).state == TaskState.CANCELLED)
        assert handles.tasks.get(task.id).cancellation_requested is True
    finally:
        handles.shutdown()


# --- 6. ATTENTION ---

def test_6_attention_classifies_events_low_value_ignored_critical_interrupts(tmp_path):
    handles = _handles(tmp_path, "s6.sqlite3")
    try:
        focus_task = handles.harness.start_background_task(
            "focus task", session_id="s1", priority=prio.NORMAL, total_steps=30
        )
        assert _wait_for(lambda: handles.tasks.get(focus_task.id).state == TaskState.RUNNING)

        # Événement critique : un échec CRITICAL doit produire un INTERRUPT
        # et mettre en pause la tâche en focus (§40), sans que l'Attention
        # n'exécute elle-même quoi que ce soit.
        critical_task = handles.harness.create_task("tâche critique", channel="cli", session_id="s1", priority=prio.CRITICAL)
        handles.tasks.start(critical_task.id)  # une tâche doit être RUNNING avant de pouvoir FAILED (contrat §5)
        handles.harness.simulate_task_failure(critical_task.id)

        assert _wait_for(lambda: handles.tasks.get(focus_task.id).state == TaskState.PAUSED, timeout_s=2.0)

        decisions = handles.harness.recent_attention_decisions(50)
        assert any(d.get("decision") == "INTERRUPT" for d in decisions)
        # Le bruit (task.created/task.ready/task.started routiniers) reste IGNORE,
        # jamais transformé en spam PROCESS_NOW/INTERRUPT.
        routine = [d for d in decisions if d.get("source_event_type") in ("task.created", "task.ready", "task.started")]
        assert routine and all(d.get("decision") == "IGNORE" for d in routine)
    finally:
        handles.shutdown()


# --- 7. STOP ---

def test_7_stop_all_tasks_stop_persistence_consistent(tmp_path):
    handles = _handles(tmp_path, "s7.sqlite3", max_concurrent=3)
    try:
        tasks = [handles.harness.start_background_task(f"t{i}", set_as_focus=False) for i in range(3)]
        time.sleep(0.03)
        handles.bus.publish(Event(type="interface.stop_requested", source="interfaces.cli", payload={}))
        handles.bus.wait_idle(timeout_s=1.0)

        assert _wait_for(lambda: all(handles.tasks.get(t.id).state == TaskState.CANCELLED for t in tasks))

        # Persistance cohérente : rechargement indépendant depuis le même fichier.
        from raya.persistence import SqliteBackend as _SB
        fresh_backend = _SB(handles.config.db_path)
        from raya.tasks import TaskRegistry as _TR

        fresh_registry = _TR(fresh_backend)
        for t in tasks:
            assert fresh_registry.get(t.id).state == TaskState.CANCELLED
        fresh_backend.close()
    finally:
        handles.shutdown()


# --- 8. CRASH RECOVERY ---

def test_8_crash_recovery_no_false_completed_checkpoint_valid(tmp_path):
    db_name = "s8.sqlite3"
    handles1 = _handles(tmp_path, db_name)
    task = handles1.harness.start_background_task("obj", total_steps=10)
    assert _wait_for(lambda: (handles1.tasks.get(task.id).checkpoint or {}).get("step_index", 0) >= 2, timeout_s=2.0)
    # "crash" : pas de shutdown propre.

    handles2 = _handles(tmp_path, db_name)
    try:
        recovered = handles2.tasks.get(task.id)
        assert recovered.state == TaskState.PAUSED
        assert recovered.state != TaskState.COMPLETED
        assert recovered.checkpoint is not None
        assert recovered.checkpoint["step_index"] >= 2
    finally:
        handles2.shutdown()
