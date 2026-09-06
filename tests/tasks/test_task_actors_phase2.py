"""Groupe B (Phase 2) — extensions Task Actors : report_progress (bon marché,
pas d'écriture SQLite), request_cancellation (coopératif, distinct de cancel),
mark_ready, nouveaux types d'events (task.ready/cancel_requested/recovered)."""

from __future__ import annotations

import pytest

from raya.contracts import TaskOwner, TaskState
from raya.event_bus import EventBus
from raya.persistence import SqliteBackend
from raya.tasks import TaskRegistry, priority as prio


def _registry(tmp_path, bus: EventBus | None = None) -> TaskRegistry:
    return TaskRegistry(SqliteBackend(tmp_path / "t.sqlite3"), bus)


def _owner() -> TaskOwner:
    return TaskOwner(channel="cli", session_id="s1")


def test_report_progress_updates_in_memory_view(tmp_path):
    reg = _registry(tmp_path)
    task = reg.create("obj", _owner(), correlation_id="c1")
    reg.report_progress(task.id, "step_1", 20.0)
    assert reg.get(task.id).progress.percent == 20.0


def test_report_progress_does_not_write_to_backend(tmp_path):
    """Vérification directe (pas seulement fonctionnelle) : report_progress
    ne doit PAS écrire dans le backend — seul checkpoint() le fait (§19/§46)."""
    from raya.persistence import InMemoryBackend

    backend = InMemoryBackend()
    reg = TaskRegistry(backend)
    task = reg.create("obj", _owner(), correlation_id="c1")
    raw_before = backend.load("tasks", task.id)
    reg.report_progress(task.id, "step_1", 20.0)
    raw_after = backend.load("tasks", task.id)
    assert raw_before == raw_after  # payload persisté inchangé, seul le cache mémoire a bougé


def test_report_progress_publishes_task_progress_event(tmp_path):
    bus = EventBus()
    received = []
    bus.subscribe("task.progress", lambda e: received.append(e), subscriber="test")
    reg = _registry(tmp_path, bus)
    task = reg.create("obj", _owner(), correlation_id="corr_x")
    reg.report_progress(task.id, "step_1", 20.0)
    bus.wait_idle(timeout_s=1.0)
    assert len(received) == 1
    assert received[0].correlation_id == "corr_x"


def test_checkpoint_clears_progress_cache_and_persists(tmp_path):
    reg = _registry(tmp_path)
    task = reg.create("obj", _owner(), correlation_id="c1")
    reg.report_progress(task.id, "step_1", 20.0)
    reg.checkpoint(task.id, {"step_index": 1, "percent": 20.0})
    # après checkpoint, la valeur vient du backend (persistée), pas du cache
    assert reg.get(task.id).checkpoint == {"step_index": 1, "percent": 20.0}


def test_report_progress_on_unknown_task_does_not_raise(tmp_path):
    reg = _registry(tmp_path)
    reg.report_progress("task_nope", "step_1", 10.0)  # ne doit pas lever


def test_mark_ready_publishes_task_ready_event(tmp_path):
    bus = EventBus()
    received = []
    bus.subscribe("task.ready", lambda e: received.append(e), subscriber="test")
    reg = _registry(tmp_path, bus)
    task = reg.create("obj", _owner(), correlation_id="c1")
    reg.mark_ready(task.id)
    bus.wait_idle(timeout_s=1.0)
    assert len(received) == 1


def test_request_cancellation_sets_flag_without_immediate_transition(tmp_path):
    reg = _registry(tmp_path)
    task = reg.create("obj", _owner(), correlation_id="c1")
    reg.start(task.id)
    updated = reg.request_cancellation(task.id)
    assert updated.cancellation_requested is True
    assert updated.state == TaskState.RUNNING  # PAS encore CANCELLED — coopératif


def test_request_cancellation_publishes_distinct_event_type(tmp_path):
    bus = EventBus()
    received = []
    bus.subscribe("task.cancel_requested", lambda e: received.append(e), subscriber="test")
    bus.subscribe("task.cancelled", lambda e: received.append(e), subscriber="test2")
    reg = _registry(tmp_path, bus)
    task = reg.create("obj", _owner(), correlation_id="c1")
    reg.start(task.id)
    reg.request_cancellation(task.id)
    bus.wait_idle(timeout_s=1.0)
    types = [e.type for e in received]
    assert "task.cancel_requested" in types
    assert "task.cancelled" not in types  # pas encore finalisé


def test_cancel_still_immediate_backward_compatible(tmp_path):
    """cancel() garde EXACTEMENT son comportement Phase 1 — pas de régression."""
    reg = _registry(tmp_path)
    task = reg.create("obj", _owner(), correlation_id="c1")
    reg.start(task.id)
    result = reg.cancel(task.id)
    assert result.state == TaskState.CANCELLED


def test_recover_after_restart_emits_task_recovered_not_task_paused(tmp_path):
    path = tmp_path / "t.sqlite3"
    bus = EventBus()
    received = []
    bus.subscribe("task.recovered", lambda e: received.append(e), subscriber="test")
    reg1 = TaskRegistry(SqliteBackend(path), bus)
    task = reg1.create("obj", _owner(), correlation_id="c1")
    reg1.start(task.id)

    reg2 = TaskRegistry(SqliteBackend(path), bus)
    reg2.recover_after_restart()
    bus.wait_idle(timeout_s=1.0)
    assert any(e.payload.task_id == task.id for e in received)


def test_priority_name_helpers_roundtrip():
    for name in ("low", "normal", "high", "critical"):
        value = prio.from_name(name)
        assert prio.to_name(value) == name


def test_priority_unknown_name_defaults_to_normal():
    assert prio.from_name("bogus") == prio.NORMAL


def test_priority_ordering():
    assert prio.LOW < prio.NORMAL < prio.HIGH < prio.CRITICAL
