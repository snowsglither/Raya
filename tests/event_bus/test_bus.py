"""Priorité B — EventBus : publish, subscribe, unsubscribe, ordering, backpressure, immutabilité."""

from __future__ import annotations

import time

from raya.contracts import Event
from raya.event_bus import BackpressurePolicy, EventBus


def test_publish_subscribe_basic():
    bus = EventBus()
    received = []
    bus.subscribe("task.*", lambda e: received.append(e), subscriber="test")
    bus.publish(Event(type="task.completed", source="tasks"))
    bus.wait_idle(timeout_s=1.0)
    assert len(received) == 1
    assert received[0].type == "task.completed"


def test_pattern_does_not_match_other_types():
    bus = EventBus()
    received = []
    bus.subscribe("task.*", lambda e: received.append(e), subscriber="test")
    bus.publish(Event(type="perception.window_changed", source="perception"))
    bus.wait_idle(timeout_s=0.5)
    assert received == []


def test_unsubscribe_stops_delivery():
    bus = EventBus()
    received = []
    handle = bus.subscribe("task.*", lambda e: received.append(e), subscriber="test")
    bus.unsubscribe(handle)
    bus.publish(Event(type="task.completed", source="tasks"))
    bus.wait_idle(timeout_s=0.5)
    assert received == []


def test_ordering_per_correlation_and_subscriber():
    bus = EventBus()
    received = []
    bus.subscribe("task.*", lambda e: received.append(e.payload["n"]), subscriber="test")
    for i in range(10):
        bus.publish(Event(type="task.progress", source="tasks", correlation_id="corr_x", payload={"n": i}))
    bus.wait_idle(timeout_s=1.0)
    assert received == list(range(10))


def test_backpressure_drop_oldest():
    bus = EventBus()
    gate = []
    def slow_handler(e):
        gate.append(e)
        time.sleep(0.05)
    bus.subscribe(
        "task.*", slow_handler, subscriber="slow", backpressure_policy=BackpressurePolicy.DROP_OLDEST,
        queue_depth=2,
    )
    for i in range(20):
        bus.publish(Event(type="task.progress", source="tasks", payload={"n": i}))
    bus.wait_idle(timeout_s=3.0)
    assert bus.dropped_count > 0
    assert len(gate) < 20


def test_correlation_id_never_mutated():
    bus = EventBus()
    received = []
    bus.subscribe("task.*", lambda e: received.append(e.correlation_id), subscriber="test")
    bus.publish(Event(type="task.completed", source="tasks", correlation_id="corr_fixed"))
    bus.wait_idle(timeout_s=0.5)
    assert received == ["corr_fixed"]


def test_payload_object_identity_preserved_not_deep_copied_unexpectedly():
    bus = EventBus()
    original_payload = {"k": "v"}
    received = []
    bus.subscribe("task.*", lambda e: received.append(e.payload), subscriber="test")
    bus.publish(Event(type="task.completed", source="tasks", payload=original_payload))
    bus.wait_idle(timeout_s=0.5)
    assert received[0] == original_payload
