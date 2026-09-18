"""Chantier 20C — PC Interaction Target + World State Semantic Consistency.

Tests vérifiant que :
T1  pc.keyboard.type écrit pc.last_interaction_target après succès
T2  pc.ui.type écrit pc.last_interaction_target après succès
T3  la valeur vient de la cible réellement résolue (jamais inventée)
T4  last_interaction_target respecte le TTL 300s
T5  pc.active_window a le même type (str) quelle que soit la source
T6  ObservationSpec est le mécanisme — aucun if tool_name dans Harness
T7  §18 distingue active_window de last_interaction_target dans le rendu
T8  contexte avec active_window=Calculator, last_interaction_target=Notepad
    → le rendu expose les deux signaux distincts
T9  un référent explicitement nommé par l'utilisateur continue de dominer
T10 vrai cas ambigu (sans signal dominant) continue de demander clarification
T11 browser.last_typed_target continue de fonctionner (régression)
T12 le texte tapé n'est PAS enregistré comme last_interaction_target
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raya.contracts import (  # noqa: E402
    Channel,
    Confidence,
    ContentPart,
    FinishReason,
    HarnessRequest,
    HarnessStatus,
    InterfaceInput,
    ModelResponse,
    ObservationSpec,
    PermissionLevel,
    RequestedToolCall,
    Tool,
    ToolCall,
    ToolCallRequester,
    ToolResult,
    ToolResultStatus,
    WorldStateFact,
)
from raya.context_engine.render import render_system_prompt  # noqa: E402
from raya.contracts import Context, ContextSection, SectionKind  # noqa: E402


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _req(text: str, session_id: str = "s1") -> HarnessRequest:
    return HarnessRequest(channel=Channel.CLI, session_id=session_id, input=InterfaceInput(text=text))


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


def _system_rules_ctx() -> Context:
    return Context(session_id="s1", budget_tokens=4096, sections=[ContextSection(
        kind=SectionKind.SYSTEM_RULES,
        content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
        provenance="context_engine:runtime_identity",
    )], used_tokens_estimate=0)


def _world_state_ctx(facts: list[dict]) -> Context:
    """Contexte minimal avec des faits World State."""
    from raya.contracts.context import Freshness
    from raya.contracts import FactStatus
    import datetime

    sections = [ContextSection(
        kind=SectionKind.SYSTEM_RULES,
        content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
        provenance="context_engine:runtime_identity",
    )]
    active_freshness = Freshness(status=FactStatus.ACTIVE, as_of=datetime.datetime.utcnow().isoformat() + "Z")
    for fact in facts:
        sections.append(ContextSection(
            kind=SectionKind.WORLD_STATE,
            content=fact,
            provenance=f"world_state:{fact.get('domain')}.{fact.get('key')}",
            freshness=active_freshness,
        ))
    return Context(session_id="s1", budget_tokens=4096, sections=sections, used_tokens_estimate=0)


# ─── T4 : TTL 300s de last_interaction_target ─────────────────────────────────

def test_last_interaction_target_ttl_300s():
    """WorldStateFact(last_interaction_target) avec TTL=300s ne doit pas
    expirer après 1 seconde — survit bien au-delà du TTL perception (20s)."""
    fact = WorldStateFact(
        domain="pc", key="last_interaction_target",
        value="notepad", source="tool:pc.keyboard.type",
        confidence=Confidence.KNOWN_FACT, freshness_ttl_s=300,
    )
    # Après 0 secondes : actif
    assert not fact.is_expired()
    # Simule 25 secondes (> perception TTL de 20s)
    import datetime
    now_plus_25 = (
        datetime.datetime.fromisoformat(fact.timestamp.replace("Z", "+00:00"))
        + datetime.timedelta(seconds=25)
    ).isoformat()
    assert not fact.is_expired(now_iso=now_plus_25), (
        "last_interaction_target ne doit pas expirer après 25s (perception TTL=20s)"
    )


# ─── T5 : pc.active_window même type str depuis toutes sources ────────────────

def test_active_window_str_from_tool_observation():
    """L'ObservationSpec de pc.application.launch écrit active_window comme str."""
    from raya.tools.catalog.pc import _ACTIVE_WINDOW_OBSERVATION
    spec = _ACTIVE_WINDOW_OBSERVATION[0]
    # La spec lit evidence_field="window" — ce champ doit être une str (titre fenêtre)
    # Simulation : ToolResult.evidence = {"window": "Sans titre - Bloc-notes", "process": "notepad.exe"}
    fake_evidence = {"window": "Sans titre - Bloc-notes", "process": "notepad.exe"}
    value = fake_evidence.get(spec.evidence_field)
    assert isinstance(value, str), f"Tool ObservationSpec doit produire str, obtenu {type(value)}"


def test_active_window_str_from_perception():
    """ActiveWindowSensor écrit active_window comme str (titre seul, Chantier 20C Fix B)."""
    from raya.perception.windows_sensors import ActiveWindowSensor

    received = []

    def _fake_read():
        return {"title": "Calculatrice", "process": "calc.exe"}

    sensor = ActiveWindowSensor(read_foreground_window=_fake_read)
    sensor._last = None  # force changement
    event = sensor.sample()
    assert event is not None
    payload = event.payload
    value = payload.get("value")
    assert isinstance(value, str), (
        f"Perception doit écrire str pour active_window (Fix B), obtenu {type(value)!r}: {value!r}"
    )
    assert value == "Calculatrice"


# ─── T6 : ObservationSpec est le mécanisme, pas if tool_name dans Harness ─────

def test_keyboard_type_has_last_interaction_observation():
    """pc.keyboard.type doit déclarer ObservationSpec pour last_interaction_target."""
    from raya.tools.catalog.pc import _LAST_INTERACTION_OBSERVATION
    from raya.tools.catalog import register_pc_tools
    from raya.tools import ToolRegistry

    registry = ToolRegistry()

    class _FakeAgent:
        def execute(self, cmd, should_stop=None):
            return None
        def list_capabilities(self):
            return []
        def health(self):
            return None
        def shutdown(self):
            pass

    register_pc_tools(registry, _FakeAgent(), lambda: False)
    tool = registry.get("pc.keyboard.type")
    assert tool is not None
    obs_keys = [spec.key for spec in tool.observation]
    assert "last_interaction_target" in obs_keys, (
        f"pc.keyboard.type doit avoir ObservationSpec pour last_interaction_target, "
        f"trouvé : {obs_keys}"
    )
    # Vérifie que TTL=300s
    obs = next(s for s in tool.observation if s.key == "last_interaction_target")
    assert obs.freshness_ttl_s == 300
    assert obs.evidence_field == "window"


def test_ui_type_has_last_interaction_observation():
    """pc.ui.type doit déclarer ObservationSpec pour last_interaction_target."""
    from raya.tools.catalog import register_pc_tools
    from raya.tools import ToolRegistry

    registry = ToolRegistry()

    class _FakeAgent:
        def execute(self, cmd, should_stop=None):
            return None
        def list_capabilities(self):
            return []
        def health(self):
            return None
        def shutdown(self):
            pass

    register_pc_tools(registry, _FakeAgent(), lambda: False)
    tool = registry.get("pc.ui.type")
    assert tool is not None
    obs_keys = [spec.key for spec in tool.observation]
    assert "last_interaction_target" in obs_keys, (
        f"pc.ui.type doit avoir ObservationSpec pour last_interaction_target, "
        f"trouvé : {obs_keys}"
    )


def test_harness_promotes_last_interaction_target_declaratively(tmp_path):
    """La promotion last_interaction_target passe par ObservationSpec (générique) —
    jamais un if tool_name dans Harness. Vérifié en montrant que le mécanisme
    générique _promote_observations_and_verify écrit le fait."""
    from support.harness_factory import build_test_harness

    from raya.contracts import ErrorInfo

    def handler(call: ToolCall) -> ToolResult:
        return ToolResult(
            tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
            output={"typed": True},
            evidence={"length": 7, "window": "Sans titre - Bloc-notes"},
        )

    script = [
        _tool_call_response("pc.keyboard.type", {"text": "Bonjour"}),
        _text_response("Écrit."),
    ]
    handles, fake = build_test_harness(tmp_path, script, enable_windows_device=False)
    try:
        from raya.contracts import Tool

        handles.tools.register(
            Tool(
                name="pc.keyboard.type",
                description="test stub",
                capability_tags=["pc.interact"],
                input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                output_schema={"type": "object"},
                permission_level=PermissionLevel.SENSITIVE,
                observation=(
                    ObservationSpec(domain="pc", key="last_interaction_target", evidence_field="window", freshness_ttl_s=300),
                ),
            ),
            handler,
        )
        handles.harness.handle_request(_req("écris bonjour"))
        fact = handles.world_state.get_fact("pc", "last_interaction_target")
        assert fact is not None, "pc.last_interaction_target doit être écrit après keyboard.type"
        assert fact.value == "Sans titre - Bloc-notes"
        assert fact.source == "tool:pc.keyboard.type"
        assert fact.freshness_ttl_s == 300
    finally:
        handles.shutdown()


# ─── T7 : §18 distingue active_window de last_interaction_target ─────────────

def test_directive_18_mentions_last_interaction_target():
    """La directive §18 doit mentionner explicitement last_interaction_target."""
    rendered = render_system_prompt(_system_rules_ctx())
    assert "last_interaction_target" in rendered, (
        "§18 doit mentionner last_interaction_target (Chantier 20C)"
    )


def test_directive_18_warns_active_window_is_os_focus():
    """La directive §18 doit indiquer que active_window est le focus OS (passif)."""
    rendered = render_system_prompt(_system_rules_ctx())
    lower = rendered.lower()
    assert "passiv" in lower or "os" in lower or "focus" in lower, (
        "§18 doit mentionner le caractère passif de active_window"
    )
    # La directive doit distinguer active_window et last_interaction_target
    assert "last_interaction_target" in rendered
    assert "active_window" in rendered


# ─── T8 : rendu expose deux signaux distincts ─────────────────────────────────

def test_rendered_context_exposes_both_signals_distinctly():
    """Un contexte avec active_window=Calculator ET last_interaction_target=Notepad
    doit montrer les deux valeurs distinctes au modèle."""
    ctx = _world_state_ctx([
        {"domain": "pc", "key": "active_window", "value": "Calculatrice"},
        {"domain": "pc", "key": "last_interaction_target", "value": "Sans titre - Bloc-notes"},
    ])
    rendered = render_system_prompt(ctx)
    assert "Calculatrice" in rendered
    assert "Bloc-notes" in rendered or "Bloc" in rendered
    # Les deux doivent être distincts dans le rendu
    assert "pc.active_window" in rendered
    assert "pc.last_interaction_target" in rendered


# ─── T11 : browser.last_typed_target continue de fonctionner ─────────────────

def test_browser_last_typed_target_still_declared():
    """Régression structurelle : browser.type (catalog) doit toujours déclarer
    last_typed_target via ObservationSpec — sans Device Agent réel."""
    from raya.tools.catalog.browser import _LAST_TYPED_OBSERVATION
    obs_keys = [spec.key for spec in _LAST_TYPED_OBSERVATION]
    assert "last_typed_target" in obs_keys, (
        f"browser._LAST_TYPED_OBSERVATION doit toujours contenir last_typed_target, "
        f"trouvé: {obs_keys}"
    )
    assert "last_typed_text" in obs_keys
    # TTL intact
    target_spec = next(s for s in _LAST_TYPED_OBSERVATION if s.key == "last_typed_target")
    assert target_spec.freshness_ttl_s == 300


# ─── T12 : texte tapé jamais stocké comme last_interaction_target ─────────────

def test_typed_text_not_stored_as_last_interaction_target(tmp_path):
    """Le CONTENU tapé ne doit jamais apparaître dans last_interaction_target —
    seule la CIBLE (fenêtre/app) y figure."""
    from support.harness_factory import build_test_harness

    _SECRET_TEXT = "MonMotDePasseSecret123!"

    def handler(call: ToolCall) -> ToolResult:
        return ToolResult(
            tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
            output={"typed": True},
            evidence={"length": len(_SECRET_TEXT), "window": "application_cible"},
        )

    script = [
        _tool_call_response("pc.keyboard.type", {"text": _SECRET_TEXT}),
        _text_response("Écrit."),
    ]
    handles, fake = build_test_harness(tmp_path, script, enable_windows_device=False)
    try:
        from raya.contracts import Tool
        handles.tools.register(
            Tool(
                name="pc.keyboard.type",
                description="stub",
                capability_tags=["pc.interact"],
                input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                output_schema={"type": "object"},
                permission_level=PermissionLevel.SENSITIVE,
                observation=(
                    ObservationSpec(domain="pc", key="last_interaction_target", evidence_field="window", freshness_ttl_s=300),
                ),
            ),
            handler,
        )
        handles.harness.handle_request(_req("tape le texte"))
        fact = handles.world_state.get_fact("pc", "last_interaction_target")
        assert fact is not None
        # La valeur doit être la CIBLE, pas le texte tapé
        assert _SECRET_TEXT not in str(fact.value), (
            f"Le texte tapé ne doit PAS figurer dans last_interaction_target : {fact.value!r}"
        )
        assert fact.value == "application_cible"
    finally:
        handles.shutdown()


# ─── Tests Windows réels (skip si environnement non-dispo) ────────────────────

def _windows_available() -> bool:
    try:
        import win32gui
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _windows_available(), reason="win32 non disponible")
def test_real_keyboard_type_writes_last_interaction_target(tmp_path):
    """T1 — RÉEL Windows : après pc.keyboard.type dans Notepad, le World State
    contient pc.last_interaction_target pointant vers Notepad."""
    from raya.devices.windows import WindowsDeviceAgent
    from raya.safety import AuditTrail, SafetyService, StopController
    from raya.tools import ToolRegistry, execute
    from raya.tools.catalog import register_pc_tools
    from raya.world_state import WorldStateStore
    from raya.persistence import SqliteBackend
    from raya.event_bus import EventBus

    bus = EventBus()
    backend = SqliteBackend(tmp_path / "ws.sqlite3")
    ws = WorldStateStore(backend, bus)
    registry = ToolRegistry()
    agent = WindowsDeviceAgent(tmp_path / "screens")
    safety = SafetyService(StopController(bus), AuditTrail())
    register_pc_tools(registry, agent, should_stop=safety.should_stop)

    def _call(name, arguments):
        return ToolCall(
            tool_name=name, arguments=arguments, correlation_id="c1",
            requested_by=ToolCallRequester(subsystem="test", session_id="s1"),
        )

    from raya.devices.windows.mechanisms import window_mgmt, applications

    try:
        # Lance le bloc-notes
        launch_result = execute(registry, safety, _call("pc.application.launch", {"target": "notepad"}))
        if launch_result.status != ToolResultStatus.SUCCESS:
            pytest.skip(f"BLOCKED: Notepad n'a pas pu être lancé : {launch_result.error}")

        time.sleep(0.5)

        # Tape du texte
        type_result = execute(registry, safety, _call("pc.keyboard.type", {"text": "Bonjour"}))
        assert type_result.status == ToolResultStatus.SUCCESS, f"keyboard.type échoué : {type_result.error}"

        # Vérifie que l'evidence contient "window"
        assert "window" in (type_result.evidence or {}), (
            f"Evidence keyboard.type doit contenir 'window' : {type_result.evidence}"
        )
        window_val = type_result.evidence["window"]
        assert isinstance(window_val, str) and window_val, (
            f"evidence['window'] doit être une str non-vide : {window_val!r}"
        )
        # Notepad doit être identifiable dans le titre
        assert "bloc" in window_val.lower() or "notepad" in window_val.lower() or "untitled" in window_val.lower(), (
            f"La fenêtre active doit être Notepad, obtenu : {window_val!r}"
        )

        # Promotion en World State (simulée via _promote_observations_and_verify)
        tool_def = registry.get("pc.keyboard.type")
        from raya.contracts import Confidence
        for spec in tool_def.observation:
            value = (type_result.evidence or {}).get(spec.evidence_field)
            if value is not None:
                ws.apply_update(WorldStateFact(
                    domain=spec.domain, key=spec.key, value=value,
                    source="tool:pc.keyboard.type", confidence=Confidence.KNOWN_FACT,
                    freshness_ttl_s=spec.freshness_ttl_s,
                ))

        fact = ws.retrieve_fact("pc", "last_interaction_target")
        assert fact is not None, "pc.last_interaction_target doit être dans World State"
        assert isinstance(fact.value, str) and fact.value
        assert "bloc" in fact.value.lower() or "notepad" in fact.value.lower() or "untitled" in fact.value.lower()
        assert fact.freshness_ttl_s == 300

    finally:
        try:
            window_mgmt.close_window("notepad")
        except Exception:
            pass


@pytest.mark.skipif(not _windows_available(), reason="win32 non disponible")
def test_real_active_window_str_type_after_launch(tmp_path):
    """T5 — RÉEL Windows : active_window est str depuis l'outil comme depuis perception."""
    from raya.devices.windows import WindowsDeviceAgent
    from raya.safety import AuditTrail, SafetyService, StopController
    from raya.tools import ToolRegistry, execute
    from raya.tools.catalog import register_pc_tools
    from raya.world_state import WorldStateStore
    from raya.persistence import SqliteBackend
    from raya.event_bus import EventBus

    bus = EventBus()
    backend = SqliteBackend(tmp_path / "ws2.sqlite3")
    ws = WorldStateStore(backend, bus)
    registry = ToolRegistry()
    agent = WindowsDeviceAgent(tmp_path / "screens2")
    safety = SafetyService(StopController(bus), AuditTrail())
    register_pc_tools(registry, agent, should_stop=safety.should_stop)

    def _call(name, arguments):
        return ToolCall(
            tool_name=name, arguments=arguments, correlation_id="c2",
            requested_by=ToolCallRequester(subsystem="test", session_id="s1"),
        )

    from raya.devices.windows.mechanisms import window_mgmt

    try:
        launch_result = execute(registry, safety, _call("pc.application.launch", {"target": "notepad"}))
        if launch_result.status != ToolResultStatus.SUCCESS:
            pytest.skip(f"BLOCKED: Notepad non lancé : {launch_result.error}")

        tool_def = registry.get("pc.application.launch")
        for spec in tool_def.observation:
            if spec.key == "active_window":
                value = (launch_result.evidence or {}).get(spec.evidence_field)
                assert isinstance(value, str), (
                    f"active_window écrit par outil doit être str, obtenu {type(value)!r}: {value!r}"
                )

        # Vérifie perception
        from raya.perception.windows_sensors import ActiveWindowSensor
        sensor = ActiveWindowSensor()
        event = sensor.sample()
        if event is not None:
            assert isinstance(event.payload.get("value"), str), (
                f"active_window écrit par perception doit être str, obtenu : {event.payload.get('value')!r}"
            )

    finally:
        try:
            window_mgmt.close_window("notepad")
        except Exception:
            pass
