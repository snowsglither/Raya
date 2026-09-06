"""WorldStateStore ↔ perception (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.2 —
"Events consommés : tout event perception.*"). Découplage entièrement par
EventBus : aucun import direct world_state ↔ perception dans un sens ou l'autre."""

from __future__ import annotations

from raya.contracts import Confidence, Event, PerceptionObservation, to_dict
from raya.event_bus import EventBus
from raya.persistence import InMemoryBackend
from raya.world_state import WorldStateStore


def _obs_event(**overrides) -> Event:
    defaults = dict(domain="pc", key="active_window", value={"title": "Notepad"}, source="perception:foreground_window")
    defaults.update(overrides)
    obs = PerceptionObservation(**defaults)
    return Event(type="perception.window_changed", source="perception", payload=to_dict(obs))


def test_perception_event_becomes_real_world_state_fact():
    bus = EventBus()
    store = WorldStateStore(InMemoryBackend(), bus)
    bus.publish(_obs_event())
    bus.wait_idle(timeout_s=1.0)
    fact = store.get_fact("pc", "active_window")
    assert fact is not None
    assert fact.value == {"title": "Notepad"}
    assert fact.source == "perception:foreground_window"
    assert fact.confidence == Confidence.KNOWN_FACT


def test_perception_event_carries_freshness_ttl_into_fact():
    bus = EventBus()
    store = WorldStateStore(InMemoryBackend(), bus)
    bus.publish(_obs_event(freshness_ttl_s=20))
    bus.wait_idle(timeout_s=1.0)
    fact = store.get_fact("pc", "active_window")
    assert fact.freshness_ttl_s == 20


def test_new_perception_observation_replaces_previous_value_same_key():
    bus = EventBus()
    store = WorldStateStore(InMemoryBackend(), bus)
    bus.publish(_obs_event(value={"title": "Notepad"}))
    bus.wait_idle(timeout_s=1.0)
    bus.publish(_obs_event(value={"title": "Calculatrice"}))
    bus.wait_idle(timeout_s=1.0)
    fact = store.get_fact("pc", "active_window")
    assert fact.value == {"title": "Calculatrice"}


def test_malformed_perception_payload_never_crashes_the_store():
    bus = EventBus()
    store = WorldStateStore(InMemoryBackend(), bus)
    bus.publish(Event(type="perception.window_changed", source="perception", payload={"nonsense": True}))
    bus.wait_idle(timeout_s=1.0)  # ne doit pas lever, ni planter le subscriber
    assert store.get_fact("pc", "active_window") is None


def test_non_perception_events_never_reach_the_subscriber():
    bus = EventBus()
    store = WorldStateStore(InMemoryBackend(), bus)
    bus.publish(Event(type="task.completed", source="tasks", payload={"task_id": "t1"}))
    bus.wait_idle(timeout_s=1.0)
    assert store.all() == []


def test_world_state_still_works_without_a_bus():
    """bus=None reste un cas valide (ex: certains tests unitaires) — jamais
    un crash à la construction."""
    store = WorldStateStore(InMemoryBackend(), bus=None)
    assert store.get_fact("pc", "active_window") is None
