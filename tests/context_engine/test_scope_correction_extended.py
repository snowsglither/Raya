"""Extended scope correction — tests for Trigger (B): capability/device refusal.

Trigger (A) was already tested in test_scope_correction.py.
This file covers Trigger (B): previous turn was an unavailability refusal.
"""
from __future__ import annotations

from raya.context_engine.render import render_system_prompt
from raya.contracts import Context, ContextSection, SectionKind


def _ctx() -> Context:
    return Context(
        session_id="s1", budget_tokens=4096,
        sections=[ContextSection(
            kind=SectionKind.SYSTEM_RULES,
            content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
            provenance="context_engine:runtime_identity",
        )],
        used_tokens_estimate=0,
    )


# ---------------------------------------------------------------------------
# 11. Factual answer + scope switch (Trigger A — existing, regression guard)
# ---------------------------------------------------------------------------

def test_scope_correction_trigger_a_present():
    rendered = render_system_prompt(_ctx())
    assert "Trigger (A)" in rendered or "factual answer" in rendered.lower()


# ---------------------------------------------------------------------------
# 12. Capability refusal + scope switch (Trigger B — new)
# ---------------------------------------------------------------------------

def test_scope_correction_trigger_b_present():
    rendered = render_system_prompt(_ctx())
    assert "Trigger (B)" in rendered or "unavailability" in rendered.lower() or "not connected" in rendered.lower()


def test_scope_correction_covers_not_connected_refusal():
    rendered = render_system_prompt(_ctx())
    assert "not connected" in rendered or "not available" in rendered or "Aucun" in rendered


def test_scope_correction_covers_phone_to_laptop_example():
    rendered = render_system_prompt(_ctx())
    assert "laptop" in rendered.lower()


# ---------------------------------------------------------------------------
# 13. Action verb prevents scope correction (shared guard)
# ---------------------------------------------------------------------------

def test_scope_correction_blocked_by_verb():
    rendered = render_system_prompt(_ctx())
    assert "verb" in rendered.lower() or "Mets" in rendered or "imperative" in rendered.lower()


# ---------------------------------------------------------------------------
# 14. Unrelated short phrase does not trigger scope correction
# ---------------------------------------------------------------------------

def test_scope_correction_does_not_trigger_for_navigation():
    rendered = render_system_prompt(_ctx())
    # "Va sur le bureau" must be listed as NOT a scope correction
    assert "Va sur" in rendered or "navigation command" in rendered.lower() or "normal instruction" in rendered.lower()
