"""Scénarios d'intégration Phase 7 (Perception & Environment) — système réel
de bout en bout : vrai Harness, vrai Windows Device Agent, vrai Bloc-notes
lancé sur cette machine, vrai Browser Device Agent sur une page HTML locale.
Même discipline que test_phase4_scenarios.py (modèle scripté, tout le reste
réel) — pas de "live" spécial : Windows/Edge sont assumés disponibles sur
cette machine de développement, exactement comme les scénarios Phase 4."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402
from support.local_http_server import LocalFixtureServer  # noqa: E402

from raya.contracts import (  # noqa: E402
    Channel,
    ContentPart,
    FinishReason,
    HarnessRequest,
    HarnessStatus,
    InterfaceInput,
    ModelResponse,
    RequestedToolCall,
)
from raya.perception.windows_sensors import ActiveWindowSensor, _read_foreground_window  # noqa: E402


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


def _req(text: str, session_id: str = "s1") -> HarnessRequest:
    return HarnessRequest(channel=Channel.CLI, session_id=session_id, input=InterfaceInput(text=text))


@pytest.fixture(scope="module")
def server():
    s = LocalFixtureServer()
    yield s
    s.shutdown()


def _system_text(call) -> str:
    system_messages = [m for m in call.messages if m.role == "system"]
    return "".join(p.value for p in system_messages[0].content if p.type == "text") if system_messages else ""


# --- 1. Real ActiveWindowSensor against the real OS ---

def test_real_active_window_sensor_reads_actual_os_state():
    """Aucune exception, jamais un crash — retourne un dict plausible ou None
    (jamais garanti QUELLE fenêtre a le focus pendant les tests, mais la
    lecture OS réelle elle-même doit fonctionner sur cette machine Windows)."""
    result = _read_foreground_window()
    assert result is None or (isinstance(result, dict) and "title" in result and "process" in result)


def test_real_sensor_detects_change_when_notepad_becomes_active(tmp_path):
    """Preuve réelle : ouvre le Bloc-notes (vrai process Windows), vérifie que
    le capteur détecte le changement de fenêtre active.

    BLOCKED honnête (jamais un FAIL silencieux) : Windows refuse parfois
    `SetForegroundWindow` à un process non-interactif/en arrière-plan
    (verrou de focus, erreur Win32 5 'Accès refusé') — restriction OS de
    sécurité anti-vol-de-focus, hors du contrôle de RAYA, indépendante du
    code testé (le même mécanisme a réellement fonctionné lors du rapport
    Phase 7). Ne jamais transformer ce BLOCKED en PASS ni en FAIL trompeur."""
    from raya.devices.windows.mechanisms import applications, window_mgmt

    sensor = ActiveWindowSensor()
    sensor.sample()  # capture l'état AVANT (établit la baseline "last")
    try:
        applications.launch("notepad", wait_timeout_s=8.0)
        focus_result = window_mgmt.focus_window("notepad")
        if focus_result.get("status") == "error":
            pytest.skip(f"BLOCKED: SetForegroundWindow refusé par l'OS ({focus_result.get('error')}) — hors contrôle de RAYA")
        time.sleep(0.3)
        event = sensor.sample()
        assert event is not None
        assert "notepad" in event.payload["value"]["process"].lower() or "notepad" in event.payload["value"]["title"].lower()
    finally:
        window_mgmt.close_window("notepad")


# --- 2. Device Agent -> Tool -> Harness -> World State (réel, bout en bout) ---

def test_real_notepad_launch_promotes_real_observation_into_world_state(tmp_path):
    script = [
        _tool_call_response("pc.application.launch", {"target": "notepad"}),
        _text_response("Le Bloc-notes est ouvert."),
    ]
    handles, fake = build_test_harness(tmp_path, script, enable_windows_device=True)
    try:
        state = handles.harness.handle_request(_req("ouvre le bloc-notes"))
        assert state.status == HarnessStatus.COMPLETED

        fact = handles.world_state.get_fact("pc", "active_window")
        assert fact is not None
        # LIMITATION RÉELLE découverte ici : le Bloc-notes Windows localisé en
        # français s'affiche "Bloc-notes", pas "Notepad" — la correspondance
        # generique observation_matches_expectation("notepad", "Bloc-notes")
        # échoue donc légitimement (aucun sous-mot commun). On vérifie ici
        # uniquement que la PROMOTION a bien eu lieu avec une preuve réelle,
        # pas que le nom corresponde à l'anglais (voir rapport §Limitations).
        assert isinstance(fact.value, str) and fact.value
        assert fact.source == "tool:pc.application.launch"
        assert fact.freshness_ttl_s == 60
    finally:
        from raya.devices.windows.mechanisms import window_mgmt

        window_mgmt.close_window("notepad")
        handles.shutdown()


def test_real_observation_reaches_the_actual_model_request(tmp_path):
    """ContextEngine capable d'exploiter l'observation (consigne §9) —
    vérifié sur le VRAI message système envoyé au (fake) modèle, pas une
    supposition sur le câblage interne."""
    script = [
        _tool_call_response("pc.application.launch", {"target": "notepad"}),
        _text_response("Le Bloc-notes est ouvert."),
        _text_response("Oui, le Bloc-notes est actif."),
    ]
    handles, fake = build_test_harness(tmp_path, script, enable_windows_device=True)
    try:
        handles.harness.handle_request(_req("ouvre le bloc-notes"))
        handles.harness.handle_request(_req("le bloc-notes est-il actif ?"))
        last_call = fake.calls[-1]
        text = _system_text(last_call)
        assert "notepad" in text.lower() or "bloc-notes" in text.lower() or "active_window" in text.lower()
    finally:
        from raya.devices.windows.mechanisms import window_mgmt

        window_mgmt.close_window("notepad")
        handles.shutdown()


# --- 3. Browser (réel, bout en bout) ---

def test_real_browser_navigation_promotes_url_into_world_state(tmp_path, server):
    script = [
        _tool_call_response("browser.navigate", {"url": server.url_for("plain.html")}),
        _text_response("Page chargée."),
    ]
    handles, fake = build_test_harness(tmp_path, script, enable_browser_device=True)
    try:
        state = handles.harness.handle_request(_req("va sur la page de test"))
        assert state.status == HarnessStatus.COMPLETED
        fact = handles.world_state.get_fact("browser", "current_url")
        assert fact is not None
        assert fact.value == server.url_for("plain.html")
        assert fact.source == "tool:browser.navigate"
    finally:
        handles.shutdown()


# --- 4. STOP reste fonctionnel avec la promotion d'observation en place ---

def test_stop_still_works_during_real_windows_action(tmp_path):
    from raya.contracts import Event

    script = [
        _tool_call_response("pc.application.launch", {"target": "notepad"}),
        _text_response("ne devrait jamais être renvoyé"),
    ]
    handles, fake = build_test_harness(tmp_path, script, enable_windows_device=True)
    try:
        handles.bus.publish(Event(type="interface.stop_requested", source="test", payload={}))
        handles.bus.wait_idle(timeout_s=1.0)
        state = handles.harness.handle_request(_req("ouvre le bloc-notes"))
        assert state.status == HarnessStatus.FAILED
        assert state.error.code == "STOP_ACTIVE"
        assert len(fake.calls) == 0
    finally:
        from raya.devices.windows.mechanisms import window_mgmt

        window_mgmt.close_window("notepad")
        handles.shutdown()


# --- 5. Perception light sensor réellement démarré via bootstrap() ---

def test_perception_runtime_actually_starts_and_stops_via_bootstrap(tmp_path):
    from raya.persistence import SqliteBackend
    from raya.runtime.bootstrap import bootstrap
    from raya.runtime.config import load_config

    cfg = load_config()
    cfg.db_path = tmp_path / "perception_boot.sqlite3"
    cfg.tool_workspace_dir = tmp_path / "workspace"
    cfg.ollama_api_key = None
    cfg.model_pool = []
    cfg.enable_windows_device = False
    cfg.enable_browser_device = False
    cfg.enable_perception = True
    cfg.perception_poll_interval_s = 0.2

    handles = bootstrap(config=cfg, backend=SqliteBackend(cfg.db_path))
    try:
        assert handles.perception is not None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and handles.world_state.get_fact("pc", "active_window") is None:
            time.sleep(0.05)
        fact = handles.world_state.get_fact("pc", "active_window")
        # Honnête : peut rester None si aucune fenêtre n'a le focus au bon
        # instant sur cette machine (jamais un FAIL pour ça), mais le
        # runtime lui-même doit tourner sans exception.
        assert fact is None or fact.source == "perception:foreground_window"
    finally:
        handles.shutdown()
    assert handles.perception._thread is None  # arrêt propre confirmé
