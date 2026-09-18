"""Chantier 18A — World State → Context cabling.

Prouve que le chemin réel (world_state_domains=() par défaut, celui utilisé
par handle_request) inclut effectivement les faits World State dans le
contexte assemblé. Les tests existants (test_context.py) utilisent des
domaines explicites — ces tests couvrent le chemin sans domaine.
"""

from __future__ import annotations

from raya.context_engine import assemble
from raya.context_engine.render import render_system_prompt
from raya.contracts import (
    ChannelScope,
    Confidence,
    FactStatus,
    SectionKind,
    WorldStateFact,
)
from raya.memory import MemoryStore
from raya.persistence import InMemoryBackend
from raya.world_state import WorldStateStore


def _stores():
    return WorldStateStore(InMemoryBackend()), MemoryStore(InMemoryBackend())


# --- Default empty domains = tous les faits ---

def test_world_state_included_with_default_empty_domains():
    """world_state_domains=() (défaut de handle_request) inclut tous les faits."""
    ws, mem = _stores()
    ws.apply_update(WorldStateFact(
        domain="browser", key="current_url", value="https://example.com",
        source="tool:browser.navigate", confidence=Confidence.KNOWN_FACT,
    ))
    # Appel SANS world_state_domains → défaut ()
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    ws_sections = [s for s in ctx.sections if s.kind == SectionKind.WORLD_STATE]
    assert len(ws_sections) == 1
    assert ws_sections[0].content["domain"] == "browser"
    assert ws_sections[0].content["key"] == "current_url"
    assert ws_sections[0].content["value"] == "https://example.com"


def test_browser_url_visible_in_context_after_navigate_observation():
    """Après browser.navigate, browser.current_url doit apparaître dans le contexte."""
    ws, mem = _stores()
    ws.apply_update(WorldStateFact(
        domain="browser", key="current_url", value="https://youtube.com",
        source="tool:browser.navigate", confidence=Confidence.KNOWN_FACT, freshness_ttl_s=60,
    ))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem,
                   query_text="relance la lecture")
    values = {s.content["value"] for s in ctx.sections if s.kind == SectionKind.WORLD_STATE}
    assert "https://youtube.com" in values


def test_pc_active_window_visible_in_context_after_launch_observation():
    """Après pc.application.launch, pc.active_window doit apparaître dans le contexte."""
    ws, mem = _stores()
    ws.apply_update(WorldStateFact(
        domain="pc", key="active_window", value="Notepad",
        source="tool:pc.application.launch", confidence=Confidence.KNOWN_FACT, freshness_ttl_s=60,
    ))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem,
                   query_text="ferme-le")
    keys = {(s.content["domain"], s.content["key"]) for s in ctx.sections if s.kind == SectionKind.WORLD_STATE}
    assert ("pc", "active_window") in keys


def test_last_typed_text_in_context_after_type_observation():
    """Après browser.type, browser.last_typed_text doit être dans le contexte."""
    ws, mem = _stores()
    ws.apply_update(WorldStateFact(
        domain="browser", key="last_typed_text", value="bonjour monde",
        source="tool:browser.type", confidence=Confidence.KNOWN_FACT, freshness_ttl_s=300,
    ))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    values = {s.content.get("value") for s in ctx.sections if s.kind == SectionKind.WORLD_STATE}
    assert "bonjour monde" in values


def test_last_clicked_target_in_context_after_click_observation():
    ws, mem = _stores()
    ws.apply_update(WorldStateFact(
        domain="browser", key="last_clicked_target", value="bouton Play",
        source="tool:browser.click", confidence=Confidence.KNOWN_FACT, freshness_ttl_s=300,
    ))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    values = {s.content.get("value") for s in ctx.sections if s.kind == SectionKind.WORLD_STATE}
    assert "bouton Play" in values


def test_stale_world_state_still_appears_in_context():
    """Un fait stale (TTL dépassé) doit rester dans le contexte — marché stale,
    jamais ignoré silencieusement (cohérence avec test_stale_fact_marked_not_excluded)."""
    ws, mem = _stores()
    ws.apply_update(WorldStateFact(
        domain="browser", key="current_url", value="https://stale.example.com",
        source="tool:browser.navigate", confidence=Confidence.KNOWN_FACT,
        freshness_ttl_s=0,  # expire immédiatement
    ))
    import time; time.sleep(0.01)
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    ws_sections = [s for s in ctx.sections if s.kind == SectionKind.WORLD_STATE]
    assert len(ws_sections) == 1
    assert ws_sections[0].freshness.status == FactStatus.STALE


def test_superseded_world_state_never_appears_in_context():
    """Un fait superseded (remplacé par une nouvelle valeur) ne doit jamais
    apparaître dans le contexte — seule la valeur courante est pertinente."""
    ws, mem = _stores()
    ws.apply_update(WorldStateFact(domain="browser", key="current_url", value="https://old.com",
                                    source="s", confidence=Confidence.KNOWN_FACT))
    ws.apply_update(WorldStateFact(domain="browser", key="current_url", value="https://new.com",
                                    source="s", confidence=Confidence.KNOWN_FACT))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    ws_sections = [s for s in ctx.sections if s.kind == SectionKind.WORLD_STATE]
    assert len(ws_sections) == 1
    assert ws_sections[0].content["value"] == "https://new.com"


def test_world_state_rendered_with_freshness_and_domain():
    """Le rendu inclut le domaine, la clé, la valeur et la fraîcheur."""
    ws, mem = _stores()
    ws.apply_update(WorldStateFact(
        domain="browser", key="current_url", value="https://example.com",
        source="tool:browser.navigate", confidence=Confidence.KNOWN_FACT,
    ))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    rendered = render_system_prompt(ctx)
    assert "browser.current_url" in rendered
    assert "https://example.com" in rendered
    assert "active" in rendered  # freshness status


def test_multiple_domains_all_included_by_default():
    """Plusieurs domaines (browser + pc) → tous dans le contexte."""
    ws, mem = _stores()
    ws.apply_update(WorldStateFact(domain="browser", key="current_url", value="https://x.com",
                                    source="s", confidence=Confidence.KNOWN_FACT))
    ws.apply_update(WorldStateFact(domain="pc", key="active_window", value="Chrome",
                                    source="s", confidence=Confidence.KNOWN_FACT))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    domains_in_context = {s.content["domain"] for s in ctx.sections if s.kind == SectionKind.WORLD_STATE}
    assert "browser" in domains_in_context
    assert "pc" in domains_in_context
