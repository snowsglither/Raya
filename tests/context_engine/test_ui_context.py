"""Chantier 19 — UI Semantic Context (source/target resolution).

Tests ciblés couvrant :
1. Directive système : résolution source/cible depuis l'accessibilité DOM
2. browser.read_page expose position (left/top) et aria_label pour les inputs
3. Formulaire réel : deux champs distinguables par position et label

Sites réels utilisés :
  the-internet.herokuapp.com/login  — Username + Password (stacked, site de test dédié)
  duckduckgo.com                    — champ de recherche avec aria-label explicite
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raya.context_engine.render import render_system_prompt  # noqa: E402
from raya.contracts import Context, ContextSection, SectionKind  # noqa: E402
from raya.contracts import PermissionLevel, ToolCall, ToolCallRequester, ToolResultStatus  # noqa: E402
from raya.devices.browser import BrowserDeviceAgent  # noqa: E402
from raya.event_bus import EventBus  # noqa: E402
from raya.safety import AuditTrail, SafetyService, StopController  # noqa: E402
from raya.tools import ToolRegistry, execute  # noqa: E402
from raya.tools.catalog import register_browser_tools  # noqa: E402

_LOGIN_PAGE = "https://the-internet.herokuapp.com/login"
_DDG = "https://duckduckgo.com"


@pytest.fixture(scope="module")
def browser_env(tmp_path_factory):
    """Module-scoped — absorbs Playwright Node.js cold-start ONCE per module (120s budget)."""
    bus = EventBus()
    registry = ToolRegistry()
    tmp = tmp_path_factory.mktemp("browser")
    agent = BrowserDeviceAgent(tmp / "screens")
    safety = SafetyService(StopController(bus), AuditTrail())
    register_browser_tools(registry, agent, should_stop=safety.should_stop)
    try:
        agent._worker.run_sync(agent._session.get_or_create, timeout=120.0)
    except Exception:
        pass
    yield registry, safety, agent
    try:
        agent._worker.run_sync(agent._session.close, timeout=10.0)
    except Exception:
        pass
    try:
        agent._worker.shutdown()
    except Exception:
        pass


def _call(name: str, arguments: dict) -> ToolCall:
    return ToolCall(tool_name=name, arguments=arguments, correlation_id="c1",
                    requested_by=ToolCallRequester(subsystem="harness", session_id="s1"))


def _system_rules_ctx() -> Context:
    return Context(session_id="s1", budget_tokens=4096, sections=[ContextSection(
        kind=SectionKind.SYSTEM_RULES,
        content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
        provenance="context_engine:runtime_identity",
    )], used_tokens_estimate=0)


# ── Directives ────────────────────────────────────────────────────────────────


def test_render_ui_context_directive_present():
    """La directive Chantier 19 sur la résolution source/cible est présente dans le rendu."""
    rendered = render_system_prompt(_system_rules_ctx())
    assert "aria_label" in rendered
    assert "left" in rendered.lower()
    assert "source" in rendered.lower()
    assert "target" in rendered.lower() or "traduction" in rendered.lower()


def test_render_ui_context_mentions_accessibility_over_position():
    """La directive indique que aria_label a la priorité sur la position."""
    rendered = render_system_prompt(_system_rules_ctx())
    assert "aria_label" in rendered
    assert "precedence" in rendered.lower() or "takes precedence" in rendered.lower() or \
           "prend" in rendered.lower() or "priority" in rendered.lower() or \
           "first check" in rendered.lower()


def test_render_no_hardcoded_site_position_rule():
    """La directive interdit explicitement le hardcodage position→rôle."""
    rendered = render_system_prompt(_system_rules_ctx())
    assert "NEVER hardcode" in rendered or "never hardcode" in rendered.lower()


# ── Fonctionnel browser.read_page ─────────────────────────────────────────────


def test_read_page_inputs_expose_left_position(browser_env):
    """browser.read_page expose les positions (left, top) pour chaque input.
    Site : the-internet.herokuapp.com/login — formulaire Username + Password."""
    registry, safety, _ = browser_env
    execute(registry, safety, _call("browser.navigate", {"url": _LOGIN_PAGE}))
    result = execute(registry, safety, _call("browser.read_page", {}))
    assert result.status == ToolResultStatus.SUCCESS, f"read_page failed: {result.error}"
    inputs = result.output.get("inputs", [])
    assert len(inputs) >= 2, f"Au moins 2 inputs attendus, obtenu {len(inputs)}"
    for inp in inputs:
        assert "left" in inp, f"Input sans champ 'left' : {inp}"
        assert isinstance(inp["left"], int)
        assert "top" in inp, f"Input sans champ 'top' : {inp}"
        assert isinstance(inp["top"], int)


def test_read_page_inputs_expose_aria_label_when_present(browser_env):
    """browser.read_page expose aria_label pour les inputs qui ont l'attribut aria-label.
    Site : duckduckgo.com — la barre de recherche a un aria-label explicite."""
    registry, safety, _ = browser_env
    execute(registry, safety, _call("browser.navigate", {"url": _DDG}))
    result = execute(registry, safety, _call("browser.read_page", {}))
    assert result.status == ToolResultStatus.SUCCESS, f"read_page failed: {result.error}"
    inputs = result.output.get("inputs", [])
    labeled = [i for i in inputs if "aria_label" in i]
    assert len(labeled) >= 1, (
        f"Au moins 1 input avec aria_label attendu sur DDG, obtenu {inputs}"
    )
    for i in labeled:
        assert isinstance(i["aria_label"], str) and i["aria_label"], (
            f"aria_label doit être une chaîne non-vide : {i}"
        )


def test_read_page_form_fields_differentiable_by_position(browser_env):
    """Le modèle peut distinguer deux champs par leur position (top).
    Sur the-internet.herokuapp.com/login : Username est au-dessus de Password —
    chaque champ a un top distinct, permettant une identification positionnelle."""
    registry, safety, _ = browser_env
    execute(registry, safety, _call("browser.navigate", {"url": _LOGIN_PAGE}))
    result = execute(registry, safety, _call("browser.read_page", {}))
    assert result.status == ToolResultStatus.SUCCESS, f"read_page failed: {result.error}"
    inputs = result.output.get("inputs", [])
    assert len(inputs) >= 2, f"Au moins 2 inputs attendus : {inputs}"
    tops = [i.get("top", 0) for i in inputs]
    assert len(set(tops)) >= 2, (
        f"Les inputs doivent avoir des positions top distinctes (formulaire empilé) : {tops}"
    )
    # Username est au-dessus de Password (ordre naturel du formulaire)
    assert tops[0] < tops[1], (
        f"Premier input devrait être au-dessus (top={tops[0]}) du second (top={tops[1]})"
    )
