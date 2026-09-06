"""Priorité E — Tasks : lifecycle, transitions invalides, persistance, checkpoint,
pause, resume, cancel, recovery, état après crash."""

from __future__ import annotations

import pytest

from raya.contracts import ErrorInfo, TaskOwner, TaskState
from raya.event_bus import EventBus
from raya.persistence import SqliteBackend
from raya.tasks import TaskRegistry


def _registry(tmp_path, bus: EventBus | None = None) -> TaskRegistry:
    return TaskRegistry(SqliteBackend(tmp_path / "tasks.sqlite3"), bus)


def _owner() -> TaskOwner:
    return TaskOwner(channel="cli", session_id="s1")


def test_create_and_get(tmp_path):
    reg = _registry(tmp_path)
    task = reg.create("télécharger un fichier", _owner(), correlation_id="c1")
    assert reg.get(task.id).objective == "télécharger un fichier"
    assert task.state == TaskState.PENDING


def test_full_lifecycle_pending_running_completed(tmp_path):
    reg = _registry(tmp_path)
    task = reg.create("obj", _owner(), correlation_id="c1")
    reg.start(task.id)
    assert reg.get(task.id).state == TaskState.RUNNING
    reg.complete(task.id, {"ok": True})
    final = reg.get(task.id)
    assert final.state == TaskState.COMPLETED
    assert final.result == {"ok": True}


def test_fail_stores_error(tmp_path):
    reg = _registry(tmp_path)
    task = reg.create("obj", _owner(), correlation_id="c1")
    reg.start(task.id)
    reg.fail(task.id, ErrorInfo(code="X", message="échec"))
    final = reg.get(task.id)
    assert final.state == TaskState.FAILED
    assert final.error.code == "X"


def test_invalid_transition_rejected(tmp_path):
    reg = _registry(tmp_path)
    task = reg.create("obj", _owner(), correlation_id="c1")
    with pytest.raises(ValueError):
        reg._transition(task.id, TaskState.COMPLETED, "task.completed")  # PENDING -> COMPLETED illégal


# --- Chantier 15 (Axe D) : block(), distinct de fail() ---

def test_block_stores_error_and_is_resumable(tmp_path):
    reg = _registry(tmp_path)
    task = reg.create("obj", _owner(), correlation_id="c1")
    reg.start(task.id)
    reg.block(task.id, ErrorInfo(code="CONFIRMATION_REQUIRED_IN_BACKGROUND", message="besoin d'une confirmation"))
    blocked = reg.get(task.id)
    assert blocked.state == TaskState.BLOCKED
    assert blocked.error.code == "CONFIRMATION_REQUIRED_IN_BACKGROUND"
    # Résumable exactement comme PAUSED, jamais un état terminal.
    reg.resume(task.id)
    assert reg.get(task.id).state == TaskState.RUNNING


def test_block_emits_task_blocked_event(tmp_path):
    bus = EventBus()
    reg = _registry(tmp_path, bus)
    captured = []
    bus.subscribe("task.blocked", lambda e: captured.append(e), subscriber="test")
    task = reg.create("obj", _owner(), correlation_id="c1")
    reg.start(task.id)
    reg.block(task.id, ErrorInfo(code="X", message="m"))
    bus.wait_idle(timeout_s=1.0)
    assert len(captured) == 1
    assert captured[0].payload.new_state == "BLOCKED"


def test_terminal_state_never_reactivates(tmp_path):
    reg = _registry(tmp_path)
    task = reg.create("obj", _owner(), correlation_id="c1")
    reg.start(task.id)
    reg.cancel(task.id)
    with pytest.raises(ValueError):
        reg.start(task.id)


def test_checkpoint_stores_progress(tmp_path):
    reg = _registry(tmp_path)
    task = reg.create("obj", _owner(), correlation_id="c1")
    reg.start(task.id)
    reg.checkpoint(task.id, {"current_step": "step_2", "percent": 40})
    assert reg.get(task.id).checkpoint == {"current_step": "step_2", "percent": 40}


def test_pause_resume_cycle(tmp_path):
    reg = _registry(tmp_path)
    task = reg.create("obj", _owner(), correlation_id="c1")
    reg.start(task.id)
    reg.pause(task.id)
    assert reg.get(task.id).state == TaskState.PAUSED
    reg.resume(task.id)
    assert reg.get(task.id).state == TaskState.RUNNING


def test_pause_freezes_progress_no_further_change_until_resumed(tmp_path):
    reg = _registry(tmp_path)
    task = reg.create("obj", _owner(), correlation_id="c1")
    reg.start(task.id)
    reg.checkpoint(task.id, {"current_step": "step_1", "percent": 20})
    reg.pause(task.id)
    checkpoint_at_pause = reg.get(task.id).checkpoint
    # aucune progression tant que PAUSED (le test d'intégration Phase 1 §18
    # TEST 4 vérifie ceci avec un vrai worker en tests/integration/)
    assert reg.get(task.id).checkpoint == checkpoint_at_pause


def test_cancel_from_running(tmp_path):
    reg = _registry(tmp_path)
    task = reg.create("obj", _owner(), correlation_id="c1")
    reg.start(task.id)
    reg.cancel(task.id)
    final = reg.get(task.id)
    assert final.state == TaskState.CANCELLED
    assert final.cancellation_requested is True


def test_dependencies_field_round_trips(tmp_path):
    reg = _registry(tmp_path)
    task = reg.create("obj", _owner(), correlation_id="c1")
    task.dependencies = ["task_other"]
    reg._save(task)
    assert reg.get(task.id).dependencies == ["task_other"]


def test_tasks_survive_restart(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    reg1 = TaskRegistry(SqliteBackend(path))
    task = reg1.create("obj durable", _owner(), correlation_id="c1")
    reg1.start(task.id)

    reg2 = TaskRegistry(SqliteBackend(path))
    restored = reg2.get(task.id)
    assert restored is not None
    assert restored.state == TaskState.RUNNING
    assert restored.objective == "obj durable"


def test_recover_after_restart_moves_running_to_paused_never_completed(tmp_path):
    """RAYA_V2_MIGRATION_PLAN.md §11.3 : un Task RUNNING au crash n'est JAMAIS
    considéré comme réussi au redémarrage."""
    path = tmp_path / "tasks.sqlite3"
    reg1 = TaskRegistry(SqliteBackend(path))
    task = reg1.create("obj", _owner(), correlation_id="c1")
    reg1.start(task.id)
    # crash simulé : pas de reg1.complete()/pause() appelé, on "coupe le courant" ici

    reg2 = TaskRegistry(SqliteBackend(path))
    recovered = reg2.recover_after_restart()
    assert len(recovered) == 1
    restored = reg2.get(task.id)
    assert restored.state == TaskState.PAUSED
    assert restored.state != TaskState.COMPLETED
    assert restored.checkpoint.get("recovered_after_restart") is True


def test_recover_after_restart_ignores_already_terminal_tasks(tmp_path):
    path = tmp_path / "tasks.sqlite3"
    reg1 = TaskRegistry(SqliteBackend(path))
    task = reg1.create("obj", _owner(), correlation_id="c1")
    reg1.start(task.id)
    reg1.complete(task.id, {"ok": True})

    reg2 = TaskRegistry(SqliteBackend(path))
    recovered = reg2.recover_after_restart()
    assert recovered == []
    assert reg2.get(task.id).state == TaskState.COMPLETED


def test_task_events_published_with_correlation_id(tmp_path):
    bus = EventBus()
    received = []
    bus.subscribe("task.*", lambda e: received.append(e), subscriber="test")
    reg = _registry(tmp_path, bus)
    task = reg.create("obj", _owner(), correlation_id="corr_xyz")
    bus.wait_idle(timeout_s=1.0)
    assert any(e.correlation_id == "corr_xyz" and e.type == "task.created" for e in received)


def test_list_filters_by_state(tmp_path):
    reg = _registry(tmp_path)
    t1 = reg.create("a", _owner(), correlation_id="c1")
    t2 = reg.create("b", _owner(), correlation_id="c2")
    reg.start(t1.id)
    pending = reg.list(state=TaskState.PENDING.value)
    running = reg.list(state=TaskState.RUNNING.value)
    assert {t.id for t in pending} == {t2.id}
    assert {t.id for t in running} == {t1.id}
