"""Directives système ajoutées pour le Chantier 14 (Natural Language Tasks +
Real Persistent Scheduling) — même style de test que
test_chantier12_directives.py : présence structurelle du texte injecté par
`render_system_prompt()`, jamais une invention hors de `context.sections`."""

from __future__ import annotations

from raya.context_engine.render import render_system_prompt
from raya.contracts import Context, ContextSection, SectionKind


def _system_rules_ctx() -> Context:
    return Context(session_id="s1", budget_tokens=4096, sections=[ContextSection(
        kind=SectionKind.SYSTEM_RULES,
        content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
        provenance="context_engine:runtime_identity",
    )], used_tokens_estimate=0)


def test_future_task_directive_names_a_third_category():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "FUTURE TASK" in rendered
    assert "third category" in rendered


def test_future_task_directive_forbids_immediate_execution():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "only the creation of that task runs now" in rendered
    assert "never the requested action itself" in rendered


def test_task_identity_directive_requires_tasks_list_before_cancel():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "tasks.list" in rendered
    assert "never guess or assume one" in rendered


def test_task_identity_directive_requires_clarification_on_ambiguity():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "ask which one before acting" in rendered


def test_task_identity_directive_describes_cancel_and_recreate_for_modification():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "no in-place reschedule" in rendered
    assert "tasks.cancel" in rendered
