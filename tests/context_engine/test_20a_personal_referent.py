"""Chantier 20A — Tests ciblés : Personal Context + Referent Dominance.

F1 — Personal Memory Retrieval (T1-T6) :
    Protège _personal_context_sections() : les entrées PERSONAL CONFIRMED/ACTIVE
    sont visibles sans filtre keyword, avec la limite explicite de 8, sans doublon
    avec identity et sans doublon avec _memory_sections().

F2 — Referent Dominance directive (T7-T10) :
    Protège que la directive §18 expose les critères corrects de dominance :
    WS.active_window / last tool result comme grounding requis, et NOT MRU seul.
"""

from __future__ import annotations

import pytest

from raya.context_engine import assemble, render_system_prompt
from raya.contracts import (
    ChannelScope,
    Confidence,
    MemoryEntry,
    MemoryLayer,
    MemoryLifecycle,
    MemoryType,
    SectionKind,
)
from raya.memory import MemoryStore
from raya.persistence import InMemoryBackend
from raya.world_state import WorldStateStore


# ─── helpers ───────────────────────────────────────────────────────────────


def _stores():
    return WorldStateStore(InMemoryBackend()), MemoryStore(InMemoryBackend())


def _personal_entry(
    content: str,
    provenance: str = "profile_migration:centres_d_interet",
    lifecycle: MemoryLifecycle = MemoryLifecycle.CONFIRMED,
    mem_type: MemoryType = MemoryType.FACT,
    channel_scope: ChannelScope = ChannelScope.SHARED,
) -> MemoryEntry:
    return MemoryEntry(
        type=mem_type,
        layer=MemoryLayer.PERSONAL,
        channel_scope=channel_scope,
        content=content,
        provenance=provenance,
        confidence=Confidence.KNOWN_FACT,
        lifecycle=lifecycle,
    )


def _identity_entry(content: str) -> MemoryEntry:
    return MemoryEntry(
        type=MemoryType.FACT,
        layer=MemoryLayer.PERSONAL,
        channel_scope=ChannelScope.SHARED,
        content=content,
        provenance="profile_migration:identity",
        confidence=Confidence.KNOWN_FACT,
        lifecycle=MemoryLifecycle.CONFIRMED,
    )


def _personal_sections(ctx):
    return [
        s for s in ctx.sections
        if s.kind == SectionKind.MEMORY
        and s.provenance != "profile_migration:identity"
    ]


# ─── F1-T1 : CONFIRMED sans keyword match ──────────────────────────────────


def test_f1_personal_confirmed_visible_without_keyword_match():
    """Root cause confirmée (20A) : 'ÉCHECS : joue le dimanche' était invisible
    pour 'mes activités du week-end' (0 mots communs ≥4 chars). Après correction,
    une entrée PERSONAL CONFIRMED doit apparaître sans correspondance lexicale."""
    ws, mem = _stores()
    mem.write(_personal_entry("ÉCHECS : joue le dimanche"))

    ctx = assemble(
        session_id="s", channel_scope=ChannelScope.SHARED,
        world_state=ws, memory=mem,
        query_text="qu'est-ce que tu connais de mes activités du week-end ?",
    )
    personal = _personal_sections(ctx)
    assert any("ÉCHECS" in s.content["content"] for s in personal), \
        "PERSONAL CONFIRMED doit être dans le contexte sans keyword match"


# ─── F1-T2 : ACTIVE (pas seulement CONFIRMED) ──────────────────────────────


def test_f1_personal_active_visible_without_keyword():
    """MemoryLifecycle.ACTIVE doit également passer — pas seulement CONFIRMED."""
    ws, mem = _stores()
    mem.write(_personal_entry("Joue au tennis le mardi", lifecycle=MemoryLifecycle.ACTIVE))

    ctx = assemble(
        session_id="s", channel_scope=ChannelScope.SHARED,
        world_state=ws, memory=mem,
        query_text="bonjour, comment tu vas ?",
    )
    personal = _personal_sections(ctx)
    assert any("tennis" in s.content["content"] for s in personal), \
        "PERSONAL ACTIVE doit être visible sans keyword match"


# ─── F1-T3 : CANDIDATE et AGING exclus ────────────────────────────────────


def test_f1_candidate_and_aging_excluded():
    """CANDIDATE (non confirmé) et AGING (vieillissant) ne doivent pas être
    injectés dans le contexte permanent — seul CONFIRMED/ACTIVE est admis."""
    ws, mem = _stores()
    mem.write(_personal_entry("Probable hobby golf", lifecycle=MemoryLifecycle.CANDIDATE))
    mem.write(_personal_entry("Ancienne passion piano", lifecycle=MemoryLifecycle.AGING))
    mem.write(_personal_entry("Lecture confirmée", lifecycle=MemoryLifecycle.CONFIRMED))

    ctx = assemble(
        session_id="s", channel_scope=ChannelScope.SHARED,
        world_state=ws, memory=mem,
        query_text="dis-moi quelque chose",
    )
    personal = _personal_sections(ctx)
    contents = [s.content["content"] for s in personal]
    assert not any("golf" in c for c in contents), "CANDIDATE ne doit pas être présent"
    assert not any("piano" in c for c in contents), "AGING ne doit pas être présent"
    assert any("Lecture confirmée" in c for c in contents), "CONFIRMED doit être présent"


# ─── F1-T4 : Identity non dupliquée ───────────────────────────────────────


def test_f1_identity_not_duplicated_via_personal_context():
    """Une entrée identity doit apparaître UNE SEULE FOIS — via
    _identity_baseline_sections(), jamais une deuxième fois via
    _personal_context_sections()."""
    ws, mem = _stores()
    mem.write(_identity_entry("Nom complet : Ruben Lukusa"))
    mem.write(_personal_entry("ÉCHECS : joue le dimanche"))

    ctx = assemble(
        session_id="s", channel_scope=ChannelScope.SHARED,
        world_state=ws, memory=mem,
        query_text="dis-moi tout",
    )
    mem_sections = [s for s in ctx.sections if s.kind == SectionKind.MEMORY]
    ruben_count = sum(1 for s in mem_sections if "Ruben Lukusa" in s.content["content"])
    assert ruben_count == 1, f"identity doit apparaître exactement 1 fois, trouvé {ruben_count}"


# ─── F1-T5 : Limit=8 respectée ────────────────────────────────────────────


def test_f1_personal_context_limit_is_eight():
    """Même avec 20 entrées PERSONAL CONFIRMED, au maximum 8 apparaissent via
    _personal_context_sections() — protection contre un profil très grand."""
    ws, mem = _stores()
    for i in range(20):
        mem.write(_personal_entry(f"fait personnel {i}", provenance=f"profile_migration:section_{i}"))

    ctx = assemble(
        session_id="s", channel_scope=ChannelScope.SHARED,
        world_state=ws, memory=mem,
        query_text="dis-moi quelque chose",
    )
    personal = _personal_sections(ctx)
    assert len(personal) <= 8, f"limit=8 dépassée : {len(personal)} sections PERSONAL"


# ─── F1-T6 : Pas de doublon avec _memory_sections ─────────────────────────


def test_f1_personal_entry_not_duplicated_in_memory_sections():
    """Une PERSONAL entry qui pourrait aussi être récupérée par keyword match
    ne doit apparaître qu'une fois — exclude_ids couvre les deux chemins."""
    ws, mem = _stores()
    # Ce contenu a "echecs" — matcherait également via keyword si query="echecs"
    mem.write(_personal_entry("ÉCHECS : joue le dimanche"))

    # Query qui déclenche keyword match sur "echecs" ET personal_context_sections
    ctx = assemble(
        session_id="s", channel_scope=ChannelScope.SHARED,
        world_state=ws, memory=mem,
        query_text="parle-moi de mes échecs du dimanche",
    )
    all_mem = [s for s in ctx.sections if s.kind == SectionKind.MEMORY]
    echecs_sections = [s for s in all_mem if "ÉCHECS" in s.content["content"]]
    assert len(echecs_sections) == 1, \
        f"PERSONAL entry ne doit apparaître qu'une fois, trouvé {len(echecs_sections)}"


# ─── F2-T7 : Directive contient les critères de dominance explicites ───────


def test_f2_directive_contains_dominance_criteria():
    """La directive §18 doit mentionner explicitement les conditions de dominance :
    active window OU last tool result — pas MRU conversationnel seul."""
    ws, mem = _stores()
    ctx = assemble(
        session_id="s", channel_scope=ChannelScope.SHARED,
        world_state=ws, memory=mem,
        query_text="ferme-le",
    )
    prompt = render_system_prompt(ctx)

    # Les critères de dominance doivent être présents
    assert "active window" in prompt or "active_window" in prompt, \
        "Critère WS.active_window doit être dans la directive"
    assert "most recent tool result" in prompt or "last tool result" in prompt, \
        "Critère last tool result doit être dans la directive"


# ─── F2-T8 : Directive interdit MRU seul comme dominance ──────────────────


def test_f2_directive_disallows_mru_alone_as_dominance():
    """La directive doit explicitement indiquer que 'le plus récemment mentionné'
    dans l'historique de conversation seul ne suffit pas quand plusieurs référents
    sont plausibles."""
    ws, mem = _stores()
    ctx = assemble(
        session_id="s", channel_scope=ChannelScope.SHARED,
        world_state=ws, memory=mem,
        query_text="ferme-le",
    )
    prompt = render_system_prompt(ctx)

    # La directive doit déconsidérer MRU conversationnel seul comme preuve suffisante
    assert "most recently opened or mentioned" in prompt or \
           "conversation history alone" in prompt, \
        "La directive doit rejeter MRU conversationnel comme dominance suffisante"


# ─── F2-T9 : Directive prescrit clarification si pas de grounding direct ──


def test_f2_directive_prescribes_clarification_without_grounding():
    """La directive doit indiquer de demander une clarification quand aucun
    référent n'a de grounding direct depuis l'environnement ou le dernier tool."""
    ws, mem = _stores()
    ctx = assemble(
        session_id="s", channel_scope=ChannelScope.SHARED,
        world_state=ws, memory=mem,
        query_text="ferme-le",
    )
    prompt = render_system_prompt(ctx)
    assert "clarif" in prompt.lower(), \
        "La directive doit mentionner la clarification pour les cas ambigus"


# ─── F2-T10 : Directive préserve la hiérarchie (WS > tool > conv) ─────────


def test_f2_directive_preserves_priority_hierarchy():
    """La hiérarchie de résolution de référent doit rester intacte :
    WS (1) > tool results (2) > conv history (3) > tasks (4) > memory (5)."""
    ws, mem = _stores()
    ctx = assemble(
        session_id="s", channel_scope=ChannelScope.SHARED,
        world_state=ws, memory=mem,
        query_text="ferme-le",
    )
    prompt = render_system_prompt(ctx)

    # La hiérarchie numérotée doit être présente
    assert "(1)" in prompt and "(2)" in prompt and "(3)" in prompt, \
        "La hiérarchie de priorité (1)(2)(3)... doit rester dans la directive"
    # L'ordre : WS avant tool results avant conv history
    pos_ws = prompt.find("Observed environment state")
    pos_tool = prompt.find("most recent tool results")
    pos_conv = prompt.find("most recent conversational exchanges")
    assert pos_ws < pos_tool < pos_conv, \
        "Ordre de priorité WS > tool_results > conv_history doit être respecté"
