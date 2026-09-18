"""Chantier 19 — Write = Replace semantics.

Tests ciblés couvrant :
1. Schémas des outils : browser.type et pc.ui.type déclarent un paramètre `mode`
2. Directives système : write/écris = replace par défaut, append seulement si explicite
3. Comportement browser.type réel : replace, append, clear sur un vrai formulaire web
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raya.contracts import PermissionLevel, ToolCall, ToolCallRequester, ToolResultStatus  # noqa: E402
from raya.context_engine.render import render_system_prompt  # noqa: E402
from raya.contracts import Context, ContextSection, SectionKind  # noqa: E402
from raya.devices.browser import BrowserDeviceAgent  # noqa: E402
from raya.event_bus import EventBus  # noqa: E402
from raya.safety import AuditTrail, SafetyService, StopController  # noqa: E402
from raya.tools import ToolRegistry, execute  # noqa: E402
from raya.tools.catalog import register_browser_tools  # noqa: E402
from raya.tools.catalog.pc import register_pc_tools  # noqa: E402

# Formulaire de test réel : champ Username labellisé, input type=text, stable
_FORM_URL = "https://the-internet.herokuapp.com/login"
_FORM_TARGET = "Username"


@pytest.fixture(scope="module")
def browser_env(tmp_path_factory):
    """Module-scoped — absorbs Playwright Node.js cold-start ONCE per module."""
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


def _setup(tmp_path):
    bus = EventBus()
    registry = ToolRegistry()
    agent = BrowserDeviceAgent(tmp_path / "screens")
    safety = SafetyService(StopController(bus), AuditTrail())
    register_browser_tools(registry, agent, should_stop=safety.should_stop)
    return registry, safety, bus, agent


def _call(name: str, arguments: dict) -> ToolCall:
    return ToolCall(tool_name=name, arguments=arguments, correlation_id="c1",
                    requested_by=ToolCallRequester(subsystem="harness", session_id="s1"))


def _system_rules_ctx() -> Context:
    return Context(session_id="s1", budget_tokens=4096, sections=[ContextSection(
        kind=SectionKind.SYSTEM_RULES,
        content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
        provenance="context_engine:runtime_identity",
    )], used_tokens_estimate=0)


# ── Schéma ────────────────────────────────────────────────────────────────────


def test_browser_type_schema_has_mode_parameter(tmp_path):
    """browser.type déclare un champ `mode` dans son input_schema (Chantier 19)."""
    registry, _, _, agent = _setup(tmp_path)
    tool = registry.get("browser.type")
    assert "mode" in tool.input_schema["properties"]
    agent.shutdown()


def test_browser_type_mode_enum_and_is_optional(tmp_path):
    """mode a les bonnes valeurs enum et n'est PAS dans la liste required."""
    registry, _, _, agent = _setup(tmp_path)
    tool = registry.get("browser.type")
    mode_schema = tool.input_schema["properties"]["mode"]
    assert set(mode_schema["enum"]) == {"replace", "append", "clear"}
    assert "mode" not in tool.input_schema.get("required", [])
    agent.shutdown()


def test_pc_ui_type_schema_has_mode_parameter():
    """pc.ui.type déclare également le paramètre mode (même sémantique que browser.type)."""
    from raya.devices.windows import DEVICE_ID as WIN_DEVICE_ID
    from raya.devices.windows.agent import WindowsDeviceAgent
    import tempfile
    bus = EventBus()
    registry = ToolRegistry()
    safety = SafetyService(StopController(bus), AuditTrail())
    with tempfile.TemporaryDirectory() as d:
        agent = WindowsDeviceAgent(Path(d))
        register_pc_tools(registry, agent, should_stop=safety.should_stop)
    tool = registry.get("pc.ui.type")
    assert "mode" in tool.input_schema["properties"]
    assert set(tool.input_schema["properties"]["mode"]["enum"]) == {"replace", "append", "clear"}


# ── Directives ────────────────────────────────────────────────────────────────


def test_render_write_replace_is_default_directive():
    """La directive Chantier 19 indique que write/écris = REPLACE par défaut."""
    rendered = render_system_prompt(_system_rules_ctx())
    assert "REPLACE" in rendered
    assert "replace" in rendered.lower()
    assert "never append" in rendered.lower() or "never combine" in rendered.lower()


def test_render_write_append_only_when_explicit_directive():
    """La directive précise que append n'est utilisé que sur instruction explicite."""
    rendered = render_system_prompt(_system_rules_ctx())
    assert "append" in rendered.lower()
    assert "mode='append'" in rendered or "mode=\"append\"" in rendered
    assert "ONLY" in rendered or "only" in rendered.lower()


# ── Fonctionnel browser.type (vrai site) ─────────────────────────────────────


def test_browser_type_default_replaces_existing_content(browser_env):
    """mode par défaut : un texte saisi est REMPLACÉ si on resaisit — pas concaténé."""
    registry, safety, _ = browser_env
    execute(registry, safety, _call("browser.navigate", {"url": _FORM_URL}))
    # Première saisie
    execute(registry, safety, _call("browser.type", {"target": _FORM_TARGET, "text": "Contenu original"}))
    # Deuxième saisie en mode replace
    result = execute(registry, safety, _call("browser.type", {"target": _FORM_TARGET, "text": "Nouveau texte"}))
    assert result.status == ToolResultStatus.SUCCESS, f"type failed: {result.error}"
    page_result = execute(registry, safety, _call("browser.read_page", {}))
    inputs = page_result.output.get("inputs", [])
    input_texts = [i.get("text", "") for i in inputs]
    assert any("Nouveau texte" in t for t in input_texts), f"Replace non effectué : {input_texts}"
    assert not any("Contenu original" in t for t in input_texts), f"Contenu original pas remplacé : {input_texts}"


def test_browser_type_explicit_replace_mode(browser_env):
    """mode='replace' explicite : même comportement que le mode par défaut."""
    registry, safety, _ = browser_env
    execute(registry, safety, _call("browser.navigate", {"url": _FORM_URL}))
    execute(registry, safety, _call("browser.type", {"target": _FORM_TARGET, "text": "Contenu initial"}))
    result = execute(registry, safety, _call("browser.type",
                                              {"target": _FORM_TARGET, "text": "Texte remplacé", "mode": "replace"}))
    assert result.status == ToolResultStatus.SUCCESS, f"type failed: {result.error}"
    assert result.output.get("mode") == "replace"
    page_result = execute(registry, safety, _call("browser.read_page", {}))
    inputs = page_result.output.get("inputs", [])
    assert any("Texte remplacé" in i.get("text", "") for i in inputs)
    assert not any("Contenu initial" in i.get("text", "") for i in inputs)


def test_browser_type_append_mode_adds_to_existing(browser_env):
    """mode='append' : le texte est ajouté EN FIN du contenu existant."""
    registry, safety, _ = browser_env
    execute(registry, safety, _call("browser.navigate", {"url": _FORM_URL}))
    execute(registry, safety, _call("browser.type", {"target": _FORM_TARGET, "text": "Contenu original"}))
    result = execute(registry, safety, _call("browser.type",
                                              {"target": _FORM_TARGET, "text": " AJOUT", "mode": "append"}))
    assert result.status == ToolResultStatus.SUCCESS, f"type append failed: {result.error}"
    page_result = execute(registry, safety, _call("browser.read_page", {}))
    inputs = page_result.output.get("inputs", [])
    input_texts = [i.get("text", "") for i in inputs]
    assert any(t.endswith(" AJOUT") for t in input_texts), f"Append non effectué : {input_texts}"
    assert any("Contenu original" in t for t in input_texts), f"Contenu original effacé : {input_texts}"


def test_browser_type_clear_mode_empties_field(browser_env):
    """mode='clear' : le champ est vidé."""
    registry, safety, _ = browser_env
    execute(registry, safety, _call("browser.navigate", {"url": _FORM_URL}))
    execute(registry, safety, _call("browser.type", {"target": _FORM_TARGET, "text": "Contenu à effacer"}))
    result = execute(registry, safety, _call("browser.type",
                                              {"target": _FORM_TARGET, "text": "", "mode": "clear"}))
    assert result.status == ToolResultStatus.SUCCESS, f"type clear failed: {result.error}"
    page_result = execute(registry, safety, _call("browser.read_page", {}))
    inputs = page_result.output.get("inputs", [])
    assert not any("Contenu à effacer" in i.get("text", "") for i in inputs), \
        f"Champ non vidé : {[i.get('text') for i in inputs]}"
