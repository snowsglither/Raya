"""Directives système ajoutées pour le Chantier 16 (Contextualisation +
Capability Discovery / CLI vs GUI / USE ≠ SHOW) — même style de test que
test_chantier12_directives.py/test_chantier14_directives.py : présence
structurelle du texte injecté par `render_system_prompt()`."""

from __future__ import annotations

from raya.context_engine.render import render_system_prompt
from raya.contracts import Context, ContextSection, SectionKind


def _system_rules_ctx() -> Context:
    return Context(session_id="s1", budget_tokens=4096, sections=[ContextSection(
        kind=SectionKind.SYSTEM_RULES,
        content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
        provenance="context_engine:runtime_identity",
    )], used_tokens_estimate=0)


def test_cli_preference_directive_mentions_shell_execute_and_discovery():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "pc.shell.execute" in rendered
    assert "pc.capability.discover" in rendered
    assert "prefer that over opening and clicking through a graphical application" in rendered


def test_use_not_show_directive_present():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "never opens a visible window" in rendered
    assert "Only make an application visible" in rendered


def test_no_tool_hallucination_directive_generalizes_beyond_filesystem():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "Never invent or assume the existence of an executable" in rendered
    assert "pc.capability.discover" in rendered
    # La directive Chantier 12 (chemin de dossier) doit rester présente,
    # jamais remplacée par la nouvelle -- les deux coexistent.
    assert "never invent or guess a path" in rendered
