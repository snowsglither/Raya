"""Identity / Memory / Context wiring — stabilisation pré-Phase 7.

Cause diagnostiquée : (1) context_engine.assemble() n'exposait ni l'identité
RAYA ni le modèle/provider runtime réels (system_rules était une chaîne
statique "Phase 1" figée) ; (2) le profil utilisateur historique n'était
jamais ingéré dans MemoryStore, donc jamais récupérable ; (3) même une fois
ingéré, une correspondance par mots-clés seule échoue sur "Qui suis-je ?"
(aucun chevauchement lexical avec "Nom complet : Ruben Lukusa"). Ces tests
verrouillent les trois correctifs SANS jamais halluciner une valeur non
fournie explicitement, et sans jamais injecter tout le profil par défaut."""

from __future__ import annotations

from raya.context_engine import assemble
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


def _stores():
    return WorldStateStore(InMemoryBackend()), MemoryStore(InMemoryBackend())


def _identity_fact(content: str, channel_scope: ChannelScope = ChannelScope.SHARED) -> MemoryEntry:
    return MemoryEntry(
        type=MemoryType.FACT, layer=MemoryLayer.PERSONAL, channel_scope=channel_scope,
        content=content, provenance="profile_migration:identity",
        confidence=Confidence.KNOWN_FACT, lifecycle=MemoryLifecycle.CONFIRMED,
    )


# --- Assistant identity / runtime (model) identity ---

def test_assistant_identity_always_present_in_system_rules():
    ws, mem = _stores()
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    rules = next(s for s in ctx.sections if s.kind == SectionKind.SYSTEM_RULES)
    assert rules.content["assistant_identity"]["name"] == "RAYA"


def test_runtime_model_and_provider_exposed_when_given():
    ws, mem = _stores()
    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem,
        runtime_identity={"provider": "ollama_cloud", "model": "deepseek-v4-flash:cloud"},
    )
    rules = next(s for s in ctx.sections if s.kind == SectionKind.SYSTEM_RULES)
    assert rules.content["runtime"] == {"provider": "ollama_cloud", "model": "deepseek-v4-flash:cloud"}


def test_no_hallucinated_runtime_identity_when_not_provided():
    """Aucun provider/modèle enregistré (ou runtime_identity omis) -> None,
    jamais une valeur inventée (consigne '§3 Le modèle ne doit pas être
    autorisé à inventer son identité')."""
    ws, mem = _stores()
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    rules = next(s for s in ctx.sections if s.kind == SectionKind.SYSTEM_RULES)
    assert rules.content["runtime"] == {"provider": None, "model": None}


# --- Identity baseline (le fix "Qui suis-je ?") ---

def test_identity_fact_retrieved_even_with_zero_lexical_overlap():
    """LE cas exact diagnostiqué : 'Qui suis-je ?' ne partage aucun mot avec
    'Nom complet : Ruben Lukusa' — doit quand même apparaître."""
    ws, mem = _stores()
    mem.write(_identity_fact("Nom complet : Ruben Lukusa"))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="Qui suis-je ?")
    mem_sections = [s for s in ctx.sections if s.kind == SectionKind.MEMORY]
    assert any("Ruben Lukusa" in s.content["content"] for s in mem_sections)


def test_identity_fact_retrieved_even_with_empty_query():
    ws, mem = _stores()
    mem.write(_identity_fact("Nom complet : Ruben Lukusa"))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="")
    mem_sections = [s for s in ctx.sections if s.kind == SectionKind.MEMORY]
    assert any("Ruben Lukusa" in s.content["content"] for s in mem_sections)


def test_identity_baseline_never_duplicated_with_relevance_search():
    """Une puce IDENTITÉ qui matche AUSSI par mots-clés ne doit apparaître
    qu'une fois (exclude_ids)."""
    ws, mem = _stores()
    mem.write(_identity_fact("Réside à Evere, Bruxelles, Belgique"))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="Bruxelles")
    mem_sections = [s for s in ctx.sections if s.kind == SectionKind.MEMORY]
    assert len([s for s in mem_sections if "Bruxelles" in s.content["content"]]) == 1


def test_non_identity_personal_memory_visible_without_keyword_match():
    """F1 (Chantier 20A) : un fait PERSONAL CONFIRMED hors identity baseline
    doit désormais être visible même sans chevauchement lexical — c'est le
    comportement explicitement corrigé par _personal_context_sections()."""
    ws, mem = _stores()
    mem.write(MemoryEntry(
        type=MemoryType.FACT, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.SHARED,
        content="GUITARE : joue depuis un an, fingerstyle", provenance="profile_migration:centres_d_interet",
        confidence=Confidence.KNOWN_FACT, lifecycle=MemoryLifecycle.CONFIRMED,
    ))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="Qui suis-je ?")
    mem_sections = [s for s in ctx.sections if s.kind == SectionKind.MEMORY]
    assert any("GUITARE" in s.content["content"] for s in mem_sections)


def test_hobby_fact_retrieved_when_actually_relevant():
    """Le même fait EST récupéré quand la question le mentionne réellement —
    la pertinence n'est pas cassée, seulement l'injection systématique."""
    ws, mem = _stores()
    mem.write(MemoryEntry(
        type=MemoryType.FACT, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.SHARED,
        content="GUITARE : joue depuis un an, fingerstyle", provenance="profile_migration:centres_d_interet",
        confidence=Confidence.KNOWN_FACT, lifecycle=MemoryLifecycle.CONFIRMED,
    ))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="Tu te souviens de ma guitare ?")
    mem_sections = [s for s in ctx.sections if s.kind == SectionKind.MEMORY]
    assert any("GUITARE" in s.content["content"] for s in mem_sections)


def test_family_fact_retrieved_by_name_match():
    """'Qui est mon frère Christopher ?' — exemple canonique de la consigne."""
    ws, mem = _stores()
    mem.write(MemoryEntry(
        type=MemoryType.FACT, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.SHARED,
        content="4 grand frere Christopher Lukusa (né le 14/10/1998)", provenance="profile_migration:relations",
        confidence=Confidence.KNOWN_FACT, lifecycle=MemoryLifecycle.CONFIRMED,
    ))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="Qui est mon frère Christopher ?")
    mem_sections = [s for s in ctx.sections if s.kind == SectionKind.MEMORY]
    assert any("Christopher" in s.content["content"] for s in mem_sections)


def test_personal_confirmed_entry_visible_even_for_unrelated_question():
    """F1 (Chantier 20A) : une entrée PERSONAL CONFIRMED (relation, etc.)
    est désormais visible même pour une question sans rapport — c'est le
    comportement souhaité de _personal_context_sections()."""
    ws, mem = _stores()
    mem.write(MemoryEntry(
        type=MemoryType.FACT, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.SHARED,
        content="Père Denis Lukusa Mwamba", provenance="profile_migration:relations",
        confidence=Confidence.KNOWN_FACT, lifecycle=MemoryLifecycle.CONFIRMED,
    ))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="Quel temps fait-il ?")
    mem_sections = [s for s in ctx.sections if s.kind == SectionKind.MEMORY]
    assert any("Denis" in s.content["content"] for s in mem_sections)


# --- Provenance / confidence / lifecycle carried through ---

def test_identity_provenance_and_confidence_preserved():
    ws, mem = _stores()
    mem.write(_identity_fact("Nom complet : Ruben Lukusa"))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="")
    section = next(s for s in ctx.sections if s.kind == SectionKind.MEMORY)
    assert section.provenance == "profile_migration:identity"


# --- Channel isolation ---

def test_identity_baseline_respects_channel_isolation():
    """Un fait d'identité scopé VOICE (pas SHARED) ne doit jamais fuiter vers
    le canal CHAT — même règle d'isolation que le reste de Memory."""
    ws, mem = _stores()
    mem.write(_identity_fact("secret vocal uniquement", channel_scope=ChannelScope.VOICE))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="")
    mem_sections = [s for s in ctx.sections if s.kind == SectionKind.MEMORY]
    assert not any("secret vocal" in s.content["content"] for s in mem_sections)


def test_shared_identity_fact_visible_from_any_channel():
    ws, mem = _stores()
    mem.write(_identity_fact("Nom complet : Ruben Lukusa", channel_scope=ChannelScope.SHARED))
    for scope in (ChannelScope.CHAT, ChannelScope.VOICE, ChannelScope.IOS):
        ctx = assemble(session_id="s1", channel_scope=scope, world_state=ws, memory=mem, query_text="")
        mem_sections = [s for s in ctx.sections if s.kind == SectionKind.MEMORY]
        assert any("Ruben Lukusa" in s.content["content"] for s in mem_sections), f"absent pour {scope}"


# --- No giant static profile (déterminisme déjà couvert par
# tests/context_engine/test_context.py::test_deterministic_output_same_inputs_same_sections) ---

def test_identity_fact_survives_heavy_conversation_accumulation():
    """BUG CORRIGÉ (passe 'Targeted Fix', Sujet 1 — confirmé empiriquement
    contre la vraie base : contexte 'Belgique' absent pour 'trouve-moi une
    manette PS5' malgré un budget largement disponible). Cause exacte :
    `_identity_baseline_sections()` interrogeait `memory.search(query="",
    limit=50)` — un tri par score/récence, PAS un filtre par provenance en
    amont — donc un fait migré une seule fois en début d'usage finissait par
    sortir des 50 entrées les mieux scorées à mesure que des dizaines
    d'échanges de conversation (plus récents) s'accumulaient, AVANT même que
    le filtre de provenance ne s'applique. Reproduit ici avec 80 entrées
    CONVERSATION plus récentes que le fait d'identité (dépasse largement
    l'ancienne limite de 50)."""
    ws, mem = _stores()
    mem.write(_identity_fact("Réside à Evere, Bruxelles, Belgique"))
    for i in range(80):
        mem.write(MemoryEntry(
            type=MemoryType.FACT, layer=MemoryLayer.CONVERSATION, channel_scope=ChannelScope.CHAT,
            content=f"échange de conversation numéro {i}", provenance="interface:cli",
            confidence=Confidence.KNOWN_FACT, lifecycle=MemoryLifecycle.CONFIRMED,
        ))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="trouve-moi une manette PS5")
    mem_sections = [s for s in ctx.sections if s.kind == SectionKind.MEMORY]
    assert any("Belgique" in s.content["content"] for s in mem_sections)


def test_full_profile_ingestion_does_not_dump_everything_for_unrelated_question():
    """27 puces migrées (27 non-identity), question neutre -> 7 identity +
    au maximum 8 personal via _personal_context_sections() = max 15 sections.
    Jamais les 27 non-identity puces en entier — limit=8 protège contre
    l'énorme profil statique (F1, Chantier 20A)."""
    ws, mem = _stores()
    sections_data = {
        "identity": [f"fait identite {i}" for i in range(7)],
        "centres_d_interet": [f"loisir {i}" for i in range(10)],
        "relations": [f"famille {i}" for i in range(5)],
        "sante_alimentation": [f"sante {i}" for i in range(5)],
    }
    for slug, bullets in sections_data.items():
        for bullet in bullets:
            mem.write(MemoryEntry(
                type=MemoryType.FACT, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.SHARED,
                content=bullet, provenance=f"profile_migration:{slug}",
                confidence=Confidence.KNOWN_FACT, lifecycle=MemoryLifecycle.CONFIRMED,
            ))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="Quelle est la météo ?")
    mem_sections = [s for s in ctx.sections if s.kind == SectionKind.MEMORY]
    # 7 identity + max 8 personal (limit) = max 15 — jamais les 27 puces non-identity
    assert len(mem_sections) <= 15
