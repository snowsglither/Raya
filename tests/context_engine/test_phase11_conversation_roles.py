"""Continuité de contexte (RAYA V2 Phase 11, §2/§Root Causes) : avant cette
phase, `Harness.handle_request()` n'écrivait que le message UTILISATEUR en
mémoire CONVERSATION — la réponse de RAYA elle-même n'était jamais persistée,
rendant structurellement impossible toute résolution de référent portant sur
sa propre réponse (ex: "Oui lance-la." après que RAYA ait dit "je peux lancer
la calculatrice"). Le rôle est distingué par un simple suffixe de provenance
(`:assistant`), jamais un nouveau champ de contrat `MemoryEntry` ni un second
système de mémoire dédié aux résolutions de référence (interdit par la
consigne §16)."""

from __future__ import annotations

from raya.context_engine import assemble
from raya.contracts import ChannelScope, MemoryEntry, MemoryLayer, MemoryType, SectionKind
from raya.memory import MemoryStore
from raya.persistence import InMemoryBackend
from raya.world_state import WorldStateStore


def _stores():
    return WorldStateStore(InMemoryBackend()), MemoryStore(InMemoryBackend())


def _write_turn(mem: MemoryStore, content: str, *, assistant: bool, session_id: str = "s1") -> None:
    provenance = f"harness:{session_id}:assistant" if assistant else f"interface:cli:{session_id}"
    mem.write(MemoryEntry(
        type=MemoryType.FACT, layer=MemoryLayer.CONVERSATION, channel_scope=ChannelScope.CHAT,
        content=content, provenance=provenance,
    ))


def test_assistant_turn_is_tagged_role_assistant_in_conversation_history():
    ws, mem = _stores()
    _write_turn(mem, "je peux lancer la calculatrice", assistant=True)
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="")
    section = next(s for s in ctx.sections if s.kind == SectionKind.CONVERSATION_HISTORY)
    assert section.content["recent"][0]["role"] == "assistant"


def test_user_turn_is_tagged_role_user_in_conversation_history():
    ws, mem = _stores()
    _write_turn(mem, "lance la calculatrice", assistant=False)
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="")
    section = next(s for s in ctx.sections if s.kind == SectionKind.CONVERSATION_HISTORY)
    assert section.content["recent"][0]["role"] == "user"


def test_conversation_history_reads_chronologically_oldest_first():
    """Un transcript doit se lire comme une vraie conversation (ancien ->
    récent) — `search()` classe par pertinence/récence décroissante (le plus
    récent en tête), l'assemblage doit donc inverser l'ordre d'AFFICHAGE
    sans jamais changer la SÉLECTION des N derniers tours."""
    ws, mem = _stores()
    _write_turn(mem, "tour 1 : lance la calculatrice", assistant=False)
    _write_turn(mem, "tour 2 : je peux lancer la calculatrice", assistant=True)
    _write_turn(mem, "tour 3 : oui lance-la", assistant=False)
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="")
    section = next(s for s in ctx.sections if s.kind == SectionKind.CONVERSATION_HISTORY)
    contents = [m["content"] for m in section.content["recent"]]
    assert contents == [
        "tour 1 : lance la calculatrice",
        "tour 2 : je peux lancer la calculatrice",
        "tour 3 : oui lance-la",
    ]


def test_both_user_and_assistant_turns_appear_in_the_same_history():
    """La conversation renvoyée au modèle doit contenir SES PROPRES réponses
    passées, pas seulement les messages de l'utilisateur — condition
    nécessaire pour résoudre un référent portant sur sa propre affirmation
    précédente (Problèmes 1/2/6, cause commune)."""
    ws, mem = _stores()
    _write_turn(mem, "lance la calculatrice", assistant=False)
    _write_turn(mem, "je peux lancer la calculatrice, tu veux que je le fasse ?", assistant=True)
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="")
    section = next(s for s in ctx.sections if s.kind == SectionKind.CONVERSATION_HISTORY)
    roles = {m["role"] for m in section.content["recent"]}
    assert roles == {"user", "assistant"}
