"""Directives système ajoutées lors de la passe 'Targeted Execution Repair'
(navigation adaptative, discipline d'URL, blocage humain) — GÉNÉRIQUES,
jamais un cas par site/langue codé en dur (§15). Même style de test que
`test_render.py` : présence structurelle du texte injecté par
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


def test_search_fallback_directive_present():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "search" in rendered.lower()
    assert "one strategy, not the only one" in rendered


def test_no_url_guessing_directive_present():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "never something you invent" in rendered


def test_human_block_directive_present():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "captcha" in rendered.lower()
    assert "2fa" in rendered.lower()


def test_human_block_directive_forbids_bypassing_security():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "never attempt to bypass" in rendered.lower()
    assert "never extract, guess, or display credentials" in rendered.lower()


def test_no_hardcoded_site_name_in_any_directive():
    """§15/§16 : aucune directive ajoutée cette passe ne doit mentionner un
    site/application précis — uniquement des exemples génériques."""
    rendered = render_system_prompt(_system_rules_ctx()).lower()
    for forbidden in ("youtube", "netflix", "disney", "coolblue", "amazon"):
        assert forbidden not in rendered


def test_directives_are_additive_never_replace_claim_evidence_rule():
    """Non-régression Phase 11 : la règle CLAIM -> EVIDENCE reste présente
    telle quelle, jamais remplacée par les nouvelles directives."""
    rendered = render_system_prompt(_system_rules_ctx())
    assert "never invent it. if you are not sure, say you will check" in rendered.lower()


def test_vision_tool_never_referenced_since_it_does_not_exist_in_v2():
    """Audit 'Targeted Execution Repair' : aucun mécanisme de vision n'est
    câblé en V2 (Phase 4 l'a explicitement reporté, jamais construit depuis
    — confirmé par grep complet du code, ModelCapability.VISION reste une
    métadonnée de routing jamais utilisée par un Tool). Le prompt système ne
    doit donc jamais prétendre qu'un tel outil existe."""
    rendered = render_system_prompt(_system_rules_ctx()).lower()
    assert "vision tool" not in rendered
    assert "vision fallback" not in rendered
