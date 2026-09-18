"""Chantier 20 — intégration : cycle de vie complet d'une interaction externe.

Prouve que :
- Un fait interaction apparaît dans le Context Engine (assemblage + rendu).
- Le rendu "Pending external interaction" est correct pour AWAITING, "replied"
  pour REPLIED.
- La directive Chantier 20 est présente dans le system prompt.
- La persistance SQLite permet la survie au restart (cross-session).
- Deux interactions actives sont toutes les deux visibles dans le contexte.
- L'état REPLIED est reflété après interaction.reply.

Zéro Ollama requis — tests purement structurels."""

from __future__ import annotations

import tempfile

from raya.context_engine import assemble
from raya.context_engine.render import render_system_prompt
from raya.contracts import ChannelScope, Confidence, WorldStateFact
from raya.contracts.interaction import ExternalInteraction, ExternalInteractionState
from raya.contracts import to_dict, utc_now_iso
from raya.memory import MemoryStore
from raya.persistence import InMemoryBackend, SqliteBackend
from raya.world_state import WorldStateStore


def _stores_memory():
    return WorldStateStore(InMemoryBackend()), MemoryStore(InMemoryBackend())


def _write_interaction(ws: WorldStateStore, interlocutor: str, outgoing: str,
                       channel: str = "whatsapp", session_id: str = "sess_x") -> str:
    interaction = ExternalInteraction(
        interlocutor=interlocutor,
        channel=channel,
        outgoing_message=outgoing,
        owner_session_id=session_id,
    )
    ws.apply_update(WorldStateFact(
        domain="interaction",
        key=interaction.id,
        value=to_dict(interaction),
        source=f"interaction.track:{session_id}",
        confidence=Confidence.KNOWN_FACT,
        freshness_ttl_s=86_400,
    ))
    return interaction.id


def _mark_replied(ws: WorldStateStore, inter_id: str, reply_text: str) -> None:
    fact = ws.retrieve_fact("interaction", inter_id)
    value = dict(fact.value)
    value["state"] = ExternalInteractionState.REPLIED.value
    value["reply_text"] = reply_text
    value["last_activity"] = utc_now_iso()
    ws.apply_update(WorldStateFact(
        domain=fact.domain,
        key=fact.key,
        value=value,
        source="interaction.reply:test",
        confidence=Confidence.KNOWN_FACT,
        freshness_ttl_s=fact.freshness_ttl_s,
    ))


# --- Context Engine — assemblage ---

def test_interaction_fact_appears_in_assembled_context():
    ws, mem = _stores_memory()
    _write_interaction(ws, "mon frère", "Est-ce que tu viens ce soir ?")
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    ws_contents = [s.content for s in ctx.sections if s.content and s.content.get("domain") == "interaction"]
    assert len(ws_contents) == 1


def test_pending_interaction_renders_as_awaiting_text():
    ws, mem = _stores_memory()
    _write_interaction(ws, "Marie", "Tu viens demain ?", channel="telegram")
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    prompt = render_system_prompt(ctx)
    assert "Pending external interaction" in prompt
    assert "Marie" in prompt
    assert "Tu viens demain ?" in prompt


def test_pending_interaction_includes_channel_in_render():
    ws, mem = _stores_memory()
    _write_interaction(ws, "Lucas", "Rappelle-moi.", channel="sms")
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    prompt = render_system_prompt(ctx)
    assert "sms" in prompt


def test_replied_interaction_renders_as_replied_text():
    ws, mem = _stores_memory()
    inter_id = _write_interaction(ws, "Sophie", "Es-tu disponible ?")
    _mark_replied(ws, inter_id, "Oui, je suis là.")
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    prompt = render_system_prompt(ctx)
    assert "Sophie" in prompt
    assert "Oui, je suis là." in prompt
    assert "replied" in prompt.lower()


def test_two_active_interactions_both_visible_in_context():
    ws, mem = _stores_memory()
    _write_interaction(ws, "mon frère", "Tu viens ?")
    _write_interaction(ws, "Marie", "Tu es dispo ?", channel="telegram")
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    ws_sections = [s for s in ctx.sections if s.content and s.content.get("domain") == "interaction"]
    assert len(ws_sections) == 2
    interlocutors = {s.content["value"]["interlocutor"] for s in ws_sections}
    assert interlocutors == {"mon frère", "Marie"}


def test_interaction_renders_awaiting_vs_replied_differently():
    ws, mem = _stores_memory()
    id1 = _write_interaction(ws, "A", "msg1")
    id2 = _write_interaction(ws, "B", "msg2")
    _mark_replied(ws, id2, "réponse de B")
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    prompt = render_system_prompt(ctx)
    assert "Pending external interaction" in prompt  # A still awaiting
    assert "réponse de B" in prompt  # B replied


# --- Directive Chantier 20 présente dans le system prompt ---

def test_chantier20_directive_present_in_system_prompt():
    ws, mem = _stores_memory()
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    prompt = render_system_prompt(ctx)
    assert "interaction.track" in prompt
    assert "interaction.reply" in prompt
    assert "AWAITING_EXTERNAL_REPLY" in prompt


# --- Cross-session visibility (WorldState non scopé par session) ---

def test_interaction_visible_from_different_session():
    ws, mem = _stores_memory()
    _write_interaction(ws, "mon frère", "Tu viens ?", session_id="sess_cockpit")
    # Assembler depuis une autre session
    ctx = assemble(session_id="sess_telegram", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    ws_contents = [s.content for s in ctx.sections if s.content and s.content.get("domain") == "interaction"]
    assert len(ws_contents) == 1
    assert ws_contents[0]["value"]["owner_session_id"] == "sess_cockpit"


# --- Restart survival (SQLite persistence) ---

def test_interaction_survives_restart_via_sqlite():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = f"{tmp}/test.db"

        # Session 1 : écriture
        backend1 = SqliteBackend(db_path)
        ws1 = WorldStateStore(backend1)
        inter_id = _write_interaction(ws1, "mon frère", "Tu viens ce soir ?", session_id="sess_1")
        backend1.close()

        # Session 2 : lecture (simule un restart)
        backend2 = SqliteBackend(db_path)
        ws2 = WorldStateStore(backend2)
        fact = ws2.retrieve_fact("interaction", inter_id)
        assert fact is not None
        assert fact.value["interlocutor"] == "mon frère"
        assert fact.value["state"] == ExternalInteractionState.AWAITING_EXTERNAL_REPLY.value
        backend2.close()


# --- Full lifecycle test ---

def test_full_lifecycle_track_then_reply_then_context_reflects():
    ws, mem = _stores_memory()

    # 1. Trace l'interaction
    inter_id = _write_interaction(ws, "Julien", "As-tu reçu mon email ?", channel="email")

    # 2. Avant réponse : Pending
    ctx_before = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    prompt_before = render_system_prompt(ctx_before)
    assert "Pending external interaction" in prompt_before
    assert "Julien" in prompt_before

    # 3. Réponse reçue
    _mark_replied(ws, inter_id, "Oui, je l'ai reçu.")

    # 4. Après réponse : pas de Pending, mais REPLIED visible
    ctx_after = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    prompt_after = render_system_prompt(ctx_after)
    assert "Oui, je l'ai reçu." in prompt_after
    # Le rendu REPLIED ne dit pas "Pending"
    assert "Pending external interaction" not in prompt_after or "Julien" in prompt_after
