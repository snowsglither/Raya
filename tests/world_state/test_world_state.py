"""Priorité B — World State : CRUD, freshness, expiration, stale filtering,
confidence, status, domain filtering, persistance réelle (SQLite)."""

from __future__ import annotations

import time

from raya.contracts import Confidence, FactStatus, WorldStateFact
from raya.event_bus import EventBus
from raya.persistence import SqliteBackend
from raya.world_state import WorldStateStore


def _store(tmp_path, bus: EventBus | None = None) -> WorldStateStore:
    return WorldStateStore(SqliteBackend(tmp_path / "ws.sqlite3"), bus)


def test_create_and_retrieve_fact(tmp_path):
    store = _store(tmp_path)
    store.create_fact(
        WorldStateFact(domain="pc", key="k1", value="v1", source="s", confidence=Confidence.KNOWN_FACT)
    )
    fact = store.retrieve_fact("pc", "k1")
    assert fact is not None
    assert fact.value == "v1"


def test_update_fact_changes_value_and_source(tmp_path):
    store = _store(tmp_path)
    store.create_fact(
        WorldStateFact(domain="pc", key="k1", value="v1", source="s1", confidence=Confidence.KNOWN_FACT)
    )
    updated = store.update_fact("pc", "k1", "v2", source="s2")
    assert updated.value == "v2"
    assert updated.source == "s2"
    assert store.retrieve_fact("pc", "k1").value == "v2"


def test_update_fact_missing_returns_none(tmp_path):
    store = _store(tmp_path)
    assert store.update_fact("pc", "nope", "v", "s") is None


def test_invalidate_fact_marks_superseded_without_replacement(tmp_path):
    store = _store(tmp_path)
    store.create_fact(
        WorldStateFact(domain="app", key="chrome_open", value=True, source="s", confidence=Confidence.KNOWN_FACT)
    )
    invalidated = store.invalidate_fact("app", "chrome_open")
    assert invalidated.status == FactStatus.SUPERSEDED
    # invalidé -> exclu de retrieve_relevant (jamais présenté comme actif)
    assert all(f.key != "chrome_open" for f in store.retrieve_relevant(("app",)))


def test_expire_fact_forces_stale(tmp_path):
    store = _store(tmp_path)
    store.create_fact(
        WorldStateFact(domain="pc", key="k1", value="v1", source="s", confidence=Confidence.KNOWN_FACT)
    )
    expired = store.expire_fact("pc", "k1")
    assert expired.status == FactStatus.STALE


def test_expire_fact_missing_returns_none(tmp_path):
    store = _store(tmp_path)
    assert store.expire_fact("pc", "nope") is None


def test_ttl_expiration_lazy_on_read(tmp_path):
    store = _store(tmp_path)
    store.apply_update(
        WorldStateFact(
            domain="pc", key="k1", value="v1", source="s", confidence=Confidence.KNOWN_FACT, freshness_ttl_s=0
        )
    )
    time.sleep(0.01)
    assert store.retrieve_fact("pc", "k1").status == FactStatus.STALE


def test_no_ttl_never_expires(tmp_path):
    store = _store(tmp_path)
    store.apply_update(
        WorldStateFact(domain="pc", key="k1", value="v1", source="s", confidence=Confidence.KNOWN_FACT)
    )
    time.sleep(0.01)
    assert store.retrieve_fact("pc", "k1").status == FactStatus.ACTIVE


def test_superseded_excluded_from_retrieve_relevant(tmp_path):
    store = _store(tmp_path)
    store.apply_update(
        WorldStateFact(domain="pc", key="k1", value="v1", source="s", confidence=Confidence.KNOWN_FACT)
    )
    store.apply_update(
        WorldStateFact(domain="pc", key="k1", value="v2", source="s", confidence=Confidence.KNOWN_FACT)
    )
    relevant = store.retrieve_relevant(("pc",))
    assert len(relevant) == 1
    assert relevant[0].value == "v2"


def test_domain_filtering(tmp_path):
    store = _store(tmp_path)
    store.apply_update(
        WorldStateFact(domain="pc", key="k1", value="v1", source="s", confidence=Confidence.KNOWN_FACT)
    )
    store.apply_update(
        WorldStateFact(domain="browser", key="k1", value="v2", source="s", confidence=Confidence.KNOWN_FACT)
    )
    assert len(store.retrieve_by_domain("pc")) == 1
    assert len(store.retrieve_by_domain("browser")) == 1


def test_retrieve_relevant_without_domain_returns_all_known(tmp_path):
    store = _store(tmp_path)
    store.apply_update(
        WorldStateFact(domain="pc", key="k1", value="v1", source="s", confidence=Confidence.KNOWN_FACT)
    )
    store.apply_update(
        WorldStateFact(domain="browser", key="k1", value="v2", source="s", confidence=Confidence.KNOWN_FACT)
    )
    assert len(store.retrieve_relevant()) == 2


def test_confidence_preserved_across_persistence(tmp_path):
    store = _store(tmp_path)
    store.apply_update(
        WorldStateFact(domain="pc", key="k1", value="v1", source="s", confidence=Confidence.HYPOTHESIS)
    )
    assert store.retrieve_fact("pc", "k1").confidence == Confidence.HYPOTHESIS


def test_world_state_survives_restart(tmp_path):
    path = tmp_path / "ws.sqlite3"
    store1 = WorldStateStore(SqliteBackend(path))
    store1.apply_update(
        WorldStateFact(domain="pc", key="k1", value="v1", source="s", confidence=Confidence.KNOWN_FACT)
    )

    store2 = WorldStateStore(SqliteBackend(path))
    fact = store2.retrieve_fact("pc", "k1")
    assert fact is not None
    assert fact.value == "v1"


def test_apply_update_publishes_event(tmp_path):
    bus = EventBus()
    received = []
    bus.subscribe("world_state.*", lambda e: received.append(e), subscriber="test")
    store = _store(tmp_path, bus)
    store.apply_update(
        WorldStateFact(domain="pc", key="k1", value="v1", source="s", confidence=Confidence.KNOWN_FACT)
    )
    bus.wait_idle(timeout_s=1.0)
    assert len(received) == 1
    assert received[0].type == "world_state.updated"
