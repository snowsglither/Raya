"""PresenceState/PresenceTracker (consigne Phase 5 §8/§33) — listening,
speaking, working, interrupted, idle. La présence DÉCRIT, ne décide jamais
(pas de second orchestrateur) — vérifié ici par construction : aucune
méthode ne fait autre chose que muter un champ et renvoyer un snapshot."""

from __future__ import annotations

from raya.contracts import Event, TaskEvent, TaskEventPayload
from raya.interfaces.voice.presence import PresenceLabel, PresenceTracker


def test_default_state_is_idle():
    tracker = PresenceTracker(session_id="s1")
    snap = tracker.snapshot()
    assert snap.state == PresenceLabel.IDLE
    assert snap.active_session == "s1"


def test_speaking_state():
    tracker = PresenceTracker()
    tracker.set_speaking(True)
    assert tracker.snapshot().state == PresenceLabel.SPEAKING


def test_listening_state_via_user_speaking():
    tracker = PresenceTracker()
    tracker.set_user_speaking(True)
    assert tracker.snapshot().state == PresenceLabel.LISTENING


def test_working_state_when_task_active_and_nothing_else():
    tracker = PresenceTracker()
    tracker.task_started("task_1")
    assert tracker.snapshot().state == PresenceLabel.WORKING
    tracker.task_ended("task_1")
    assert tracker.snapshot().state == PresenceLabel.IDLE


def test_interrupted_state_takes_priority_over_working():
    tracker = PresenceTracker()
    tracker.task_started("task_1")
    tracker.set_interrupted(True)
    assert tracker.snapshot().state == PresenceLabel.INTERRUPTED


def test_speaking_clears_interrupted_flag():
    """Une nouvelle synthèse qui démarre efface l'état 'interrompu' du tour
    précédent — sinon RAYA resterait INTERRUPTED indéfiniment après un seul
    barge-in, même en parlant à nouveau normalement ensuite."""
    tracker = PresenceTracker()
    tracker.set_interrupted(True)
    assert tracker.snapshot().state == PresenceLabel.INTERRUPTED
    tracker.set_speaking(True)
    assert tracker.snapshot().state == PresenceLabel.SPEAKING


def test_unavailable_overrides_everything():
    tracker = PresenceTracker()
    tracker.set_speaking(True)
    tracker.set_unavailable(True)
    assert tracker.snapshot().state == PresenceLabel.UNAVAILABLE


def test_on_event_task_started_updates_active_tasks():
    tracker = PresenceTracker()
    tracker.on_event(Event(type="task.started", source="tasks", payload={"task_id": "t1"}))
    assert "t1" in tracker.snapshot().active_tasks


def test_on_event_task_completed_removes_from_active_tasks():
    tracker = PresenceTracker()
    tracker.on_event(Event(type="task.started", source="tasks", payload={"task_id": "t1"}))
    tracker.on_event(Event(type="task.completed", source="tasks", payload={"task_id": "t1"}))
    assert tracker.snapshot().active_tasks == []


def test_on_event_attention_decision_updates_attention_state():
    tracker = PresenceTracker()
    tracker.on_event(Event(type="attention.decision_made", source="attention", payload={"decision": "INTERRUPT"}))
    assert tracker.snapshot().attention_state == "INTERRUPT"


def test_real_task_event_with_dataclass_payload_updates_working_state():
    """Régression Phase 6 : un VRAI TaskEvent tel que publié par
    raya/tasks/registry.py porte un TaskEventPayload (dataclass), jamais un
    dict — l'ancien isinstance(payload, dict) ne matchait donc jamais, et
    WORKING ne s'activait jamais pour une tâche de fond réelle."""
    tracker = PresenceTracker()
    real_event = TaskEvent(type="task.started", source="tasks",
                            payload=TaskEventPayload(task_id="task_1", new_state="RUNNING"))
    tracker.on_event(real_event)
    assert tracker.snapshot().state == PresenceLabel.WORKING
    assert "task_1" in tracker.snapshot().active_tasks

    completed_event = TaskEvent(type="task.completed", source="tasks",
                                 payload=TaskEventPayload(task_id="task_1", new_state="COMPLETED"))
    tracker.on_event(completed_event)
    assert tracker.snapshot().state == PresenceLabel.IDLE


def test_processing_state_when_nothing_else_active():
    """RAYA V2 Phase 6 — reflète un appel Harness synchrone réellement en
    vol (jamais une supposition)."""
    tracker = PresenceTracker()
    tracker.set_processing(True)
    assert tracker.snapshot().state == PresenceLabel.PROCESSING
    tracker.set_processing(False)
    assert tracker.snapshot().state == PresenceLabel.IDLE


def test_needs_attention_state_via_confirmation_required_event():
    tracker = PresenceTracker(session_id="s1")
    tracker.on_event(Event(type="harness.confirmation_required", source="harness",
                            payload={"session_id": "s1", "tool_name": "demo.idempotent_counter"}))
    assert tracker.snapshot().state == PresenceLabel.NEEDS_ATTENTION


def test_needs_attention_cleared_via_confirmation_resolved_event():
    tracker = PresenceTracker(session_id="s1")
    tracker.on_event(Event(type="harness.confirmation_required", source="harness", payload={"session_id": "s1"}))
    tracker.on_event(Event(type="harness.confirmation_resolved", source="harness", payload={"session_id": "s1", "approved": True}))
    assert tracker.snapshot().state == PresenceLabel.IDLE


def test_needs_attention_ignores_other_sessions():
    tracker = PresenceTracker(session_id="s1")
    tracker.on_event(Event(type="harness.confirmation_required", source="harness", payload={"session_id": "other-session"}))
    assert tracker.snapshot().state != PresenceLabel.NEEDS_ATTENTION


def test_needs_attention_takes_priority_over_working_but_not_over_interrupted():
    tracker = PresenceTracker(session_id="s1")
    tracker.task_started("t1")
    tracker.on_event(Event(type="harness.confirmation_required", source="harness", payload={"session_id": "s1"}))
    assert tracker.snapshot().state == PresenceLabel.NEEDS_ATTENTION
    tracker.set_interrupted(True)
    assert tracker.snapshot().state == PresenceLabel.INTERRUPTED


def test_on_event_malformed_payload_does_not_crash():
    """Un event mal formé (payload non-dict) ne doit jamais faire planter le
    tracker de présence — leçon du bug Phase 2 TaskEvent.payload."""
    tracker = PresenceTracker()
    tracker.on_event(Event(type="task.started", source="tasks", payload={}))  # pas de task_id
    assert tracker.snapshot().active_tasks == []
