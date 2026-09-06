"""Directives système ajoutées pour le Chantier 12 (Temporal/Scheduling/
Environment/Intent/Channels) — même style de test que
test_targeted_execution_repair_directives.py : présence structurelle du
texte injecté par `render_system_prompt()`, jamais une invention hors de
`context.sections`."""

from __future__ import annotations

from raya.context_engine.render import render_system_prompt
from raya.contracts import Context, ContextSection, SectionKind


def _system_rules_ctx() -> Context:
    return Context(session_id="s1", budget_tokens=4096, sections=[ContextSection(
        kind=SectionKind.SYSTEM_RULES,
        content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
        provenance="context_engine:runtime_identity",
    )], used_tokens_estimate=0)


def test_temporal_directive_present():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "system.time.now" in rendered
    assert "Europe/Brussels" in rendered


def test_scheduling_directive_mentions_delay_seconds_and_run_at():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "delay_seconds" in rendered
    assert "run_at" in rendered
    assert "tasks.create" in rendered


def test_environment_directive_forbids_inventing_a_path():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "pc.filesystem.find_folder" in rendered
    assert "never invent or guess a path" in rendered


def test_intent_directive_distinguishes_information_and_action():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "INFORMATION" in rendered
    assert "ACTION" in rendered


def test_channel_directive_present_and_respects_explicit_wording():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "defaults to Telegram" in rendered
    assert "Email specifically" in rendered
    assert "means Phone" in rendered
    assert "Never substitute a different channel" in rendered


def test_memory_preference_rendered_distinctly_from_fact():
    ctx = Context(session_id="s1", budget_tokens=4096, sections=[ContextSection(
        kind=SectionKind.MEMORY,
        content={"id": "m1", "type": "preference", "content": {"channel_for": "message", "channel": "email"}},
        provenance="memory:m1",
    )], used_tokens_estimate=0)
    rendered = render_system_prompt(ctx)
    assert "confirmed user preference" in rendered


def test_memory_fact_still_rendered_as_fact():
    ctx = Context(session_id="s1", budget_tokens=4096, sections=[ContextSection(
        kind=SectionKind.MEMORY,
        content={"id": "m1", "type": "fact", "content": "aime le café"},
        provenance="memory:m1",
    )], used_tokens_estimate=0)
    rendered = render_system_prompt(ctx)
    assert "confirmed fact about the user" in rendered
