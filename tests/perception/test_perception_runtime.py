"""PerceptionRuntime (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.3) — poll_once()
déterministe (sans thread réel), start()/stop() pour la vraie boucle."""

from __future__ import annotations

import time

from raya.contracts import Event
from raya.event_bus import EventBus
from raya.perception.runtime import PerceptionRuntime
from raya.perception.sensors import LightSensor


class _ScriptedSensor(LightSensor):
    def __init__(self, events: list) -> None:
        self._events = list(events)

    def sample(self):
        return self._events.pop(0) if self._events else None


class _FailingSensor(LightSensor):
    def sample(self):
        raise RuntimeError("capteur cassé")


def test_poll_once_publishes_non_none_events():
    bus = EventBus()
    received = []
    bus.subscribe("perception.*", lambda e: received.append(e), subscriber="test")
    sensor = _ScriptedSensor([Event(type="perception.window_changed", source="perception", payload={"domain": "pc"})])
    runtime = PerceptionRuntime([sensor], bus)
    published = runtime.poll_once()
    bus.wait_idle(timeout_s=1.0)
    assert published == 1
    assert len(received) == 1


def test_poll_once_skips_none_silently():
    bus = EventBus()
    sensor = _ScriptedSensor([None, None])
    runtime = PerceptionRuntime([sensor], bus)
    assert runtime.poll_once() == 0


def test_one_failing_sensor_does_not_block_others():
    bus = EventBus()
    received = []
    bus.subscribe("perception.*", lambda e: received.append(e), subscriber="test")
    ok_sensor = _ScriptedSensor([Event(type="perception.window_changed", source="perception", payload={})])
    runtime = PerceptionRuntime([_FailingSensor(), ok_sensor], bus)
    published = runtime.poll_once()
    bus.wait_idle(timeout_s=1.0)
    assert published == 1
    assert len(received) == 1


def test_start_stop_real_background_loop():
    bus = EventBus()
    received = []
    bus.subscribe("perception.*", lambda e: received.append(e), subscriber="test")
    sensor = _ScriptedSensor([Event(type="perception.window_changed", source="perception", payload={})])
    runtime = PerceptionRuntime([sensor], bus, interval_s=0.05)
    runtime.start()
    try:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not received:
            time.sleep(0.02)
        assert len(received) == 1
    finally:
        runtime.stop()


def test_stop_is_idempotent_and_stops_the_thread():
    bus = EventBus()
    runtime = PerceptionRuntime([_ScriptedSensor([])], bus, interval_s=0.05)
    runtime.start()
    time.sleep(0.1)
    runtime.stop()
    runtime.stop()  # ne doit jamais lever
    assert runtime._thread is None
