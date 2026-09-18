"""Tests Experience Memory dans le Context Engine.

Vérifie que les expériences sont correctement récupérées et rendues.
"""

from __future__ import annotations

import pytest

from raya.contracts import (
    ChannelScope,
    MemoryEntry,
    MemoryLayer,
    MemoryLifecycle,
    MemoryType,
    Plan,
    PlanStep,
    StepState,
    Task,
    TaskOwner,
    TaskState,
    Confidence,
)
from raya.context_engine.assembler import assemble
from raya.context_engine.render import render_system_prompt
from raya.memory import MemoryStore, maybe_distill_experience
from raya.persistence import InMemoryBackend
from raya.world_state import WorldStateStore


def _backend():
    return InMemoryBackend()


def _stores(backend=None):
    b = backend or _backend()
    return MemoryStore(b), WorldStateStore(b)


def _write_experience(mem: MemoryStore, objective: str, strategy: str) -> MemoryEntry:
    return mem.write(MemoryEntry(
        type=MemoryType.EXPERIENCE,
        layer=MemoryLayer.EXPERIENCE,
        channel_scope=ChannelScope.CHAT,
        content={"objective_type": objective, "strategy": strategy, "proof": "succès observé", "interface": "browser"},
        provenance="harness:task_completion:test",
        lifecycle=MemoryLifecycle.CONFIRMED,
        confidence=Confidence.INFERRED,
    ))


# ---------------------------------------------------------------------------
# Experience in context when relevant query
# ---------------------------------------------------------------------------

def test_experience_appears_in_context_when_query_matches():
    mem, ws = _stores()
    _write_experience(mem, "ajouter produit au panier", "Browser-based: identifier bouton, cliquer, vérifier")

    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT,
        world_state=ws, memory=mem,
        query_text="ajouter cet article au panier",
    )
    has_experience = any(
        s.content.get("type") == "experience"
        for s in ctx.sections
        if s.content
    )
    assert has_experience


def test_experience_not_in_context_for_trivial_query():
    mem, ws = _stores()
    _write_experience(mem, "achat e-commerce", "identifier bouton principal, cliquer, vérifier état")

    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT,
        world_state=ws, memory=mem,
        query_text="",  # requête vide → pas d'expérience
    )
    has_experience = any(
        s.content.get("type") == "experience"
        for s in ctx.sections
        if s.content
    )
    assert not has_experience


# ---------------------------------------------------------------------------
# Experience rendered correctly
# ---------------------------------------------------------------------------

def test_experience_rendered_as_strategy():
    mem, ws = _stores()
    _write_experience(
        mem,
        "ajouter produit au panier",
        "Browser-based: trouver bouton Add to Cart, cliquer, vérifier état panier",
    )

    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT,
        world_state=ws, memory=mem,
        query_text="acheter un produit en ligne",
    )
    prompt = render_system_prompt(ctx)
    assert "Past successful strategy" in prompt
    assert "ajouter produit au panier" in prompt


def test_experience_rendering_includes_proof():
    mem, ws = _stores()
    mem.write(MemoryEntry(
        type=MemoryType.EXPERIENCE,
        layer=MemoryLayer.EXPERIENCE,
        channel_scope=ChannelScope.CHAT,
        content={
            "objective_type": "naviguer vers bibliothèque",
            "strategy": "Browser-based: cliquer Bibliothèque dans Steam",
            "proof": "Page bibliothèque chargée confirmée",
            "interface": "browser",
            "attempts_before_success": 2,
        },
        provenance="harness:task:test",
        lifecycle=MemoryLifecycle.CONFIRMED,
        confidence=Confidence.INFERRED,
    ))

    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT,
        world_state=ws, memory=mem,
        query_text="aller dans bibliothèque steam",
    )
    prompt = render_system_prompt(ctx)
    assert "Past successful strategy" in prompt
    assert "took 2 attempts" in prompt


# ---------------------------------------------------------------------------
# Rule memory rendered correctly (if ever written)
# ---------------------------------------------------------------------------

def test_rule_memory_rendered_as_rule():
    mem, ws = _stores()
    mem.write(MemoryEntry(
        type=MemoryType.RULE,
        layer=MemoryLayer.PERSONAL,
        channel_scope=ChannelScope.SHARED,
        content="Toujours demander confirmation avant un achat",
        provenance="user:explicit",
        lifecycle=MemoryLifecycle.CONFIRMED,
        confidence=Confidence.KNOWN_FACT,
    ))

    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT,
        world_state=ws, memory=mem,
        query_text="achat confirmation",
    )
    prompt = render_system_prompt(ctx)
    assert "Persistent rule" in prompt


# ---------------------------------------------------------------------------
# Channel isolation — experience from VOICE not visible in CHAT
# ---------------------------------------------------------------------------

def test_experience_channel_isolation():
    mem, ws = _stores()
    mem.write(MemoryEntry(
        type=MemoryType.EXPERIENCE,
        layer=MemoryLayer.EXPERIENCE,
        channel_scope=ChannelScope.VOICE,  # voice only
        content={"objective_type": "test voice", "strategy": "voice strategy"},
        provenance="harness:task:voice",
        lifecycle=MemoryLifecycle.CONFIRMED,
        confidence=Confidence.INFERRED,
    ))

    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT,  # chat channel
        world_state=ws, memory=mem,
        query_text="test voice",
    )
    has_experience = any(
        s.content.get("type") == "experience"
        for s in ctx.sections
        if s.content
    )
    assert not has_experience
