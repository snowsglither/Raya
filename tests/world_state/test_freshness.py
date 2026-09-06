"""Priorité G (Phase 0) — World State / Context : stale, freshness obligatoire,
impossible de la masquer (RAYA_V2_ARCHITECTURE_FINAL_REVIEW.md §6, invariant #23).

Mis à jour Phase 1 : WorldStateStore/MemoryStore exigent maintenant un
PersistenceBackend explicite (InMemoryBackend ici — tests rapides, pas d'I/O ;
la couverture SQLite réelle est dans tests/persistence/ et tests/world_state/test_world_state.py).
"""

from __future__ import annotations

import time

import pytest

from raya.context_engine import assemble
from raya.contracts import ChannelScope, Confidence, FactStatus, SectionKind, WorldStateFact
from raya.memory import MemoryStore
from raya.persistence import InMemoryBackend
from raya.world_state import WorldStateStore


def test_fact_becomes_stale_after_ttl():
    store = WorldStateStore(InMemoryBackend())
    store.apply_update(
        WorldStateFact(
            domain="pc", key="foreground_window", value="Chrome", source="perception:window",
            confidence=Confidence.KNOWN_FACT, freshness_ttl_s=0,
        )
    )
    time.sleep(0.01)
    fact = store.get_fact("pc", "foreground_window")
    assert fact.status == FactStatus.STALE


def test_superseded_on_new_value_for_same_key():
    store = WorldStateStore(InMemoryBackend())
    store.apply_update(
        WorldStateFact(domain="pc", key="k", value="v1", source="s", confidence=Confidence.KNOWN_FACT)
    )
    store.apply_update(
        WorldStateFact(domain="pc", key="k", value="v2", source="s", confidence=Confidence.KNOWN_FACT)
    )
    current = store.get_fact("pc", "k")
    assert current.value == "v2"


def test_assemble_propagates_stale_status_into_context():
    ws = WorldStateStore(InMemoryBackend())
    ws.apply_update(
        WorldStateFact(
            domain="pc", key="foreground_window", value="Chrome", source="perception:window",
            confidence=Confidence.KNOWN_FACT, freshness_ttl_s=0,
        )
    )
    time.sleep(0.01)
    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws,
        memory=MemoryStore(InMemoryBackend()), world_state_domains=("pc",),
    )
    ws_sections = [s for s in ctx.sections if s.kind == SectionKind.WORLD_STATE]
    assert len(ws_sections) == 1
    assert ws_sections[0].freshness is not None
    assert ws_sections[0].freshness.status == FactStatus.STALE


def test_impossible_to_construct_world_state_section_without_freshness():
    from raya.contracts import ContextSection

    with pytest.raises(ValueError):
        ContextSection(kind=SectionKind.WORLD_STATE, content={"v": 1}, provenance="perception:window")
