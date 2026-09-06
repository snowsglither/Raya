"""Priorité C — Memory : CRUD, lifecycle, confidence, isolation stricte de
canal, ranking, persistance, supersedes."""

from __future__ import annotations

from raya.contracts import (
    ChannelScope,
    Confidence,
    MemoryEntry,
    MemoryLayer,
    MemoryLifecycle,
    MemoryType,
)
from raya.event_bus import EventBus
from raya.persistence import SqliteBackend
from raya.memory import MemoryStore


def _store(tmp_path, bus: EventBus | None = None) -> MemoryStore:
    return MemoryStore(SqliteBackend(tmp_path / "mem.sqlite3"), bus)


def _entry(**overrides) -> MemoryEntry:
    defaults = dict(
        type=MemoryType.FACT,
        layer=MemoryLayer.PERSONAL,
        channel_scope=ChannelScope.CHAT,
        content="Ruben préfère Chrome",
        provenance="test",
    )
    defaults.update(overrides)
    return MemoryEntry(**defaults)


def test_write_and_get(tmp_path):
    store = _store(tmp_path)
    entry = store.write(_entry())
    assert store.get(entry.id).content == "Ruben préfère Chrome"


def test_update_lifecycle(tmp_path):
    store = _store(tmp_path)
    entry = store.write(_entry(lifecycle=MemoryLifecycle.CANDIDATE))
    updated = store.update_lifecycle(entry.id, MemoryLifecycle.CONFIRMED)
    assert updated.lifecycle == MemoryLifecycle.CONFIRMED
    assert store.get(entry.id).lifecycle == MemoryLifecycle.CONFIRMED


def test_update_lifecycle_missing_returns_none(tmp_path):
    store = _store(tmp_path)
    assert store.update_lifecycle("mem_nope", MemoryLifecycle.CONFIRMED) is None


def test_correct_marks_old_obsolete_and_writes_new_with_supersedes(tmp_path):
    store = _store(tmp_path)
    old = store.write(_entry(content="Ruben aime Firefox"))
    new = store.correct(old.id, "Ruben préfère Chrome (correction)", provenance="user:explicit_correction")
    assert new.supersedes == old.id
    assert new.lifecycle == MemoryLifecycle.CONFIRMED
    assert store.get(old.id).lifecycle == MemoryLifecycle.OBSOLETE


def test_search_excludes_obsolete_by_default(tmp_path):
    store = _store(tmp_path)
    entry = store.write(_entry(content="fait obsolète"))
    store.update_lifecycle(entry.id, MemoryLifecycle.OBSOLETE)
    hits = store.search(query="obsolète", channel_scope=ChannelScope.CHAT)
    assert hits == []


def test_type_lifecycle_confidence_filters(tmp_path):
    store = _store(tmp_path)
    store.write(_entry(type=MemoryType.PREFERENCE, content="préférence importante"))
    store.write(_entry(type=MemoryType.FACT, content="fait important"))
    hits = store.search(query="important", channel_scope=ChannelScope.CHAT, type_filter=MemoryType.PREFERENCE)
    assert len(hits) == 1
    assert hits[0].type == MemoryType.PREFERENCE


# --- Isolation stricte de canal (RAYA_V2_MIGRATION_PLAN.md §7.1, critique) ---

def test_chat_private_memory_never_appears_in_voice(tmp_path):
    store = _store(tmp_path)
    store.write(_entry(channel_scope=ChannelScope.CHAT, content="secret du chat"))
    hits = store.search(query="secret", channel_scope=ChannelScope.VOICE)
    assert hits == []


def test_voice_private_memory_never_appears_in_chat(tmp_path):
    store = _store(tmp_path)
    store.write(_entry(channel_scope=ChannelScope.VOICE, content="secret vocal"))
    hits = store.search(query="secret", channel_scope=ChannelScope.CHAT)
    assert hits == []


def test_shared_memory_visible_from_any_channel(tmp_path):
    store = _store(tmp_path)
    store.write(_entry(channel_scope=ChannelScope.SHARED, content="info partagée"))
    assert len(store.search(query="partagée", channel_scope=ChannelScope.CHAT)) == 1
    assert len(store.search(query="partagée", channel_scope=ChannelScope.VOICE)) == 1
    assert len(store.search(query="partagée", channel_scope=ChannelScope.IOS)) == 1


def test_three_channels_full_isolation_scenario(tmp_path):
    """Scénario exact demandé par la consigne §7.1 : chat-private / voice-private
    / shared, isolation vérifiée dans les deux sens."""
    store = _store(tmp_path)
    store.write(_entry(channel_scope=ChannelScope.CHAT, content="chat only info"))
    store.write(_entry(channel_scope=ChannelScope.VOICE, content="voice only info"))
    store.write(_entry(channel_scope=ChannelScope.SHARED, content="shared info"))

    chat_view = {e.content for e in store.search(query="", channel_scope=ChannelScope.CHAT)}
    voice_view = {e.content for e in store.search(query="", channel_scope=ChannelScope.VOICE)}

    assert "chat only info" in chat_view
    assert "voice only info" not in chat_view
    assert "shared info" in chat_view

    assert "voice only info" in voice_view
    assert "chat only info" not in voice_view
    assert "shared info" in voice_view


# --- Ranking déterministe ---

def test_ranking_prefers_confirmed_over_candidate(tmp_path):
    store = _store(tmp_path)
    store.write(_entry(content="dossier x candidate", lifecycle=MemoryLifecycle.CANDIDATE))
    store.write(_entry(content="dossier x confirmed", lifecycle=MemoryLifecycle.CONFIRMED))
    hits = store.search(query="dossier", channel_scope=ChannelScope.CHAT)
    assert hits[0].lifecycle == MemoryLifecycle.CONFIRMED


def test_ranking_prefers_more_keyword_occurrences(tmp_path):
    store = _store(tmp_path)
    store.write(_entry(content="chrome"))
    store.write(_entry(content="chrome chrome chrome navigateur préféré"))
    hits = store.search(query="chrome", channel_scope=ChannelScope.CHAT)
    assert "chrome chrome chrome" in str(hits[0].content)


def test_search_ignores_short_stopword_like_tokens(tmp_path):
    store = _store(tmp_path)
    store.write(_entry(content="quelque chose sans rapport"))
    # requête composée uniquement de mots courts -> aucun filtre agressif, pas de crash
    hits = store.search(query="le la de", channel_scope=ChannelScope.CHAT)
    assert isinstance(hits, list)


# --- Persistance réelle ---

def test_memory_survives_restart(tmp_path):
    path = tmp_path / "mem.sqlite3"
    store1 = MemoryStore(SqliteBackend(path))
    store1.write(_entry(content="donnée durable"))

    store2 = MemoryStore(SqliteBackend(path))
    hits = store2.search(query="durable", channel_scope=ChannelScope.CHAT)
    assert len(hits) == 1


def test_write_publishes_event(tmp_path):
    bus = EventBus()
    received = []
    bus.subscribe("memory.*", lambda e: received.append(e), subscriber="test")
    store = _store(tmp_path, bus)
    store.write(_entry())
    bus.wait_idle(timeout_s=1.0)
    assert any(e.type == "memory.entry_written" for e in received)


def test_confidence_preserved(tmp_path):
    store = _store(tmp_path)
    entry = store.write(_entry(confidence=Confidence.HYPOTHESIS))
    assert store.get(entry.id).confidence == Confidence.HYPOTHESIS


def test_ranking_breaks_ties_by_write_order_not_backend_row_order(tmp_path):
    """RAYA_V2_PHASE11 (context continuité) : `utc_now_iso()` a une précision
    milliseconde — deux écritures rapprochées (ex: tour utilisateur puis
    réponse de RAYA, quasi immédiate dans le même `handle_request()`) peuvent
    partager EXACTEMENT le même horodatage, donnant un score de récence
    identique. Sans départage explicite, `list.sort()` retombe sur l'ordre de
    retour du backend — jamais garanti par SQL (pas de `ORDER BY`). Ce test
    force artificiellement la collision (horodatages identiques passés
    explicitement) et vérifie que l'ordre d'ÉCRITURE (jamais l'ordre de
    retour du backend) départage."""
    frozen = "2026-01-01T00:00:00.000Z"
    store = _store(tmp_path)
    older = store.write(_entry(content="premier message unique_xyz", created_at=frozen, updated_at=frozen))
    newer = store.write(_entry(content="second message unique_xyz", created_at=frozen, updated_at=frozen))
    assert older.created_at == newer.created_at  # collision d'horodatage bien forcée

    hits = store.search(query="unique_xyz", channel_scope=ChannelScope.CHAT)
    assert [h.id for h in hits] == [newer.id, older.id]
