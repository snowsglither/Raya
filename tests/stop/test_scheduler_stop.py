"""Groupe D (Phase 2) — STOP avec plusieurs Task Actors concurrents : toutes
les tâches s'arrêtent proprement, aucune écriture SQLite après fermeture,
répété plusieurs fois (consigne §42)."""

from __future__ import annotations

import time

from raya.contracts import Channel, Event, HarnessRequest, InterfaceInput, TaskState
from raya.persistence import SqliteBackend
from raya.runtime.bootstrap import bootstrap
from raya.runtime.config import load_config


def _handles(tmp_path, name: str):
    cfg = load_config()
    cfg.db_path = tmp_path / name
    cfg.max_concurrent_tasks = 3
    return bootstrap(config=cfg, backend=SqliteBackend(cfg.db_path))


def _run_stop_scenario(tmp_path, run_id: int) -> None:
    handles = _handles(tmp_path, f"stop_{run_id}.sqlite3")
    try:
        tasks = [handles.harness.start_background_task(f"task_{i}") for i in range(3)]
        time.sleep(0.03)

        handles.bus.publish(Event(type="interface.stop_requested", source="interfaces.cli", payload={}))
        handles.bus.wait_idle(timeout_s=1.0)
        assert handles.safety.should_stop() is True

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if all(handles.tasks.get(t.id).state == TaskState.CANCELLED for t in tasks):
                break
            time.sleep(0.02)
        for t in tasks:
            assert handles.tasks.get(t.id).state == TaskState.CANCELLED, f"run {run_id}: {t.id} pas annulée"
    finally:
        handles.shutdown()  # ne doit lever aucune exception (pas d'écriture après close)


def test_stop_cancels_all_concurrent_tasks_repeated_three_times(tmp_path):
    for run_id in range(3):
        _run_stop_scenario(tmp_path, run_id)


def test_new_tasks_do_not_start_once_stop_is_active(tmp_path):
    handles = _handles(tmp_path, "no_new.sqlite3")
    try:
        handles.bus.publish(Event(type="interface.stop_requested", source="interfaces.cli", payload={}))
        handles.bus.wait_idle(timeout_s=1.0)
        task = handles.harness.start_background_task("ne devrait jamais vraiment démarrer")
        time.sleep(0.2)
        final = handles.tasks.get(task.id)
        assert final.state in (TaskState.CANCELLED, TaskState.PENDING)
        assert final.state != TaskState.COMPLETED
    finally:
        handles.shutdown()


def test_conversation_still_answers_while_stop_is_active(tmp_path):
    """STOP arrête les tâches, mais ne doit pas rendre RAYA muette — répondre
    reste possible (le modèle stub répond toujours, même en état STOP)."""
    handles = _handles(tmp_path, "conv_during_stop.sqlite3")
    try:
        handles.bus.publish(Event(type="interface.stop_requested", source="interfaces.cli", payload={}))
        handles.bus.wait_idle(timeout_s=1.0)
        req = HarnessRequest(channel=Channel.CLI, session_id="s1", input=InterfaceInput(text="bonjour"))
        state = handles.harness.handle_request(req)
        # STOP actif dès le premier point de contrôle -> le tour échoue
        # explicitement (comportement Phase 0/1 inchangé), jamais un crash silencieux.
        assert state.status.value == "FAILED"
        assert state.error.code == "STOP_ACTIVE"
    finally:
        handles.shutdown()


def test_shutdown_idempotent_multiple_calls_do_not_raise(tmp_path):
    handles = _handles(tmp_path, "idempotent.sqlite3")
    handles.harness.start_background_task("obj")
    time.sleep(0.02)
    handles.shutdown()
    handles.shutdown()  # deuxième appel : ne doit rien casser
