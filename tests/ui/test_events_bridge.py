"""UIEventBridge (RAYA V2 Phase 6 EVENT-DRIVEN UI) — filtre/traduit
l'EventBus vers un flux JSON-safe restreint, sans jamais fabriquer d'event."""

from __future__ import annotations

from raya.contracts import Event, TaskEvent, TaskEventPayload
from raya.event_bus import EventBus
from raya.interfaces.ui.events import UIEventBridge


def test_relevant_event_reaches_listener():
    bus = EventBus()
    bridge = UIEventBridge(bus, session_id="s1")
    received = []
    bridge.add_listener(received.append)
    try:
        bus.publish(Event(type="attention.decision_made", source="attention", payload={"decision": "IGNORE"}))
        bus.wait_idle(timeout_s=1.0)
        assert len(received) == 1
        assert received[0]["type"] == "attention.decision_made"
        assert received[0]["payload"]["decision"] == "IGNORE"
    finally:
        bridge.close()


def test_irrelevant_event_type_never_reaches_listener():
    """world_state.updated/tool.call_* ne sont jamais poussés en continu
    (consigne 'Do not create a permanent dashboard') — uniquement lus à la
    demande via les endpoints REST."""
    bus = EventBus()
    bridge = UIEventBridge(bus, session_id="s1")
    received = []
    bridge.add_listener(received.append)
    try:
        bus.publish(Event(type="world_state.updated", source="world_state", payload={"domain": "pc"}))
        bus.publish(Event(type="tool.call_requested", source="tools", payload={"tool_name": "pc.demo"}))
        bus.wait_idle(timeout_s=1.0)
        assert received == []
    finally:
        bridge.close()


def test_real_task_event_dataclass_payload_translated_to_dict():
    bus = EventBus()
    bridge = UIEventBridge(bus, session_id="s1")
    received = []
    bridge.add_listener(received.append)
    try:
        bus.publish(TaskEvent(type="task.started", source="tasks", payload=TaskEventPayload(task_id="t1", new_state="RUNNING")))
        bus.wait_idle(timeout_s=1.0)
        assert len(received) == 1
        assert received[0]["payload"]["task_id"] == "t1"
        assert received[0]["payload"]["new_state"] == "RUNNING"
    finally:
        bridge.close()


def test_remove_listener_stops_delivery():
    bus = EventBus()
    bridge = UIEventBridge(bus, session_id="s1")
    received = []
    bridge.add_listener(received.append)
    bridge.remove_listener(received.append)
    try:
        bus.publish(Event(type="attention.decision_made", source="attention", payload={"decision": "IGNORE"}))
        bus.wait_idle(timeout_s=1.0)
        assert received == []
    finally:
        bridge.close()


def test_multiple_listeners_all_receive_and_one_failure_does_not_break_others():
    bus = EventBus()
    bridge = UIEventBridge(bus, session_id="s1")
    received = []

    def bad_listener(_notification):
        raise RuntimeError("listener défaillant (ex: websocket fermée)")

    bridge.add_listener(bad_listener)
    bridge.add_listener(received.append)
    try:
        bus.publish(Event(type="harness.turn_completed", source="harness", payload={"session_id": "s1"}))
        bus.wait_idle(timeout_s=1.0)
        assert len(received) == 1
    finally:
        bridge.close()


def test_close_unsubscribes_and_stops_all_delivery():
    bus = EventBus()
    bridge = UIEventBridge(bus, session_id="s1")
    received = []
    bridge.add_listener(received.append)
    bridge.close()
    bus.publish(Event(type="attention.decision_made", source="attention", payload={"decision": "IGNORE"}))
    bus.wait_idle(timeout_s=0.3)
    assert received == []


def test_view_requested_event_scoped_to_matching_session_only():
    bus = EventBus()
    bridge_a = UIEventBridge(bus, session_id="session-a")
    bridge_b = UIEventBridge(bus, session_id="session-b")
    received_a, received_b = [], []
    bridge_a.add_listener(received_a.append)
    bridge_b.add_listener(received_b.append)
    try:
        bus.publish(Event(type="ui.view_requested", source="tools",
                           payload={"session_id": "session-a", "view": "tasks", "action": "show"}))
        bus.wait_idle(timeout_s=1.0)
        assert len(received_a) == 1
        assert received_a[0]["payload"]["view"] == "tasks"
        assert received_b == []  # jamais de fuite vers une autre session Cockpit
    finally:
        bridge_a.close()
        bridge_b.close()


def test_confirmation_events_are_relayed():
    bus = EventBus()
    bridge = UIEventBridge(bus, session_id="s1")
    received = []
    bridge.add_listener(received.append)
    try:
        bus.publish(Event(type="harness.confirmation_required", source="harness",
                           payload={"session_id": "s1", "tool_name": "demo.idempotent_counter"}))
        bus.wait_idle(timeout_s=1.0)
        assert received[0]["type"] == "harness.confirmation_required"
        assert received[0]["payload"]["tool_name"] == "demo.idempotent_counter"
    finally:
        bridge.close()
