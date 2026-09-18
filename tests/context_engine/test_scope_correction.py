"""Scope correction + overlay autonomy directive tests.

Verifies that render_system_prompt() includes the two new directives added in
this chantier:
  - Scope correction: bare prepositional phrase after factual answer → re-answer
    for new scope (not a new task/command)
  - Cookie/overlay autonomy: cookie_banner=true → dismiss before retrying,
    ELEMENT_NOT_FOUND ≠ objective failure

These are structural tests: they verify that the model receives the right
instructions in the system prompt, without scripting the model's reasoning.
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
# A. Scope correction directive (5 tests)
# ---------------------------------------------------------------------------

def test_scope_correction_directive_present():
    rendered = render_system_prompt(_ctx())
    assert "Scope correction" in rendered


def test_scope_correction_covers_prepositional_examples():
    rendered = render_system_prompt(_ctx())
    assert "Sur le laptop" in rendered


def test_scope_correction_no_verb_condition():
    rendered = render_system_prompt(_ctx())
    assert "no explicit verb" in rendered or "no verb" in rendered


def test_scope_correction_requires_prior_factual_answer():
    rendered = render_system_prompt(_ctx())
    assert "previous turn" in rendered or "preceding exchange" in rendered


def test_scope_correction_verb_guard_prevents_collision():
    rendered = render_system_prompt(_ctx())
    # Directive must give a counter-example (with verb = normal instruction)
    assert "Mets" in rendered or "contains a verb" in rendered


# ---------------------------------------------------------------------------
# B. Cookie / overlay autonomy directive (5 tests)
# ---------------------------------------------------------------------------

def test_overlay_directive_present():
    rendered = render_system_prompt(_ctx())
    assert "Cookie banner" in rendered or "cookie_banner" in rendered


def test_overlay_directive_mentions_cookie_banner_flag():
    rendered = render_system_prompt(_ctx())
    assert "cookie_banner" in rendered


def test_overlay_directive_recommends_dismiss_overlay():
    rendered = render_system_prompt(_ctx())
    assert "browser.dismiss_overlay" in rendered or "dismiss_overlay" in rendered


def test_overlay_directive_tool_failure_vs_objective_failure():
    rendered = render_system_prompt(_ctx())
    assert "tool failure" in rendered or "objective failure" in rendered


def test_overlay_directive_safe_for_standard_banners():
    rendered = render_system_prompt(_ctx())
    assert "SAFE" in rendered or "does not require" in rendered
