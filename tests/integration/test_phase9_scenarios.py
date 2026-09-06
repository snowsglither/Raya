"""Scénarios d'intégration Phase 9 (Mobile / Telegram / Device Registry) —
système réel de bout en bout : vrai Harness, vrai Windows Device Agent, vrai
Bloc-notes lancé sur cette machine, à travers le pipeline Telegram (pas une
logique spéciale). Le VRAI bot Telegram (réseau réel) est conditionné à la
présence de `RAYA_TELEGRAM_BOT_TOKEN`/`RAYA_TELEGRAM_ALLOWED_USER_IDS` dans
l'environnement — absent sur cette machine au moment de ce rapport, donc
honnêtement `BLOCKED`, jamais simulé silencieusement (même discipline que
`test_phase3_scenarios.py::...OLLAMA_API_KEY indisponible`)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import ContentPart, Event, FinishReason, ModelResponse, RequestedToolCall  # noqa: E402
from raya.interfaces.telegram.channel import TelegramChannel  # noqa: E402
from raya.interfaces.telegram.client import TelegramClient  # noqa: E402
from raya.interfaces.ui import UIChannel  # noqa: E402


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


# --- 1. Vraie action Windows déclenchée depuis le pipeline Telegram ---

def test_real_notepad_launch_via_telegram_pipeline_not_a_special_telegram_path(tmp_path):
    """Consigne §27 : Telegram -> Harness -> Windows Device Agent -> Windows,
    et PAS une logique spéciale Telegram."""
    script = [
        _tool_call_response("pc.application.launch", {"target": "notepad"}),
        _text_response("Le Bloc-notes est ouvert."),
    ]
    handles, fake = build_test_harness(tmp_path, script, enable_windows_device=True)
    try:
        sent = []
        channel = TelegramChannel(handles.harness, handles.bus, lambda cid, txt: sent.append((cid, txt)))
        response = channel.handle_message(555, "ouvre le bloc-notes")
        assert response == "Le Bloc-notes est ouvert."

        fact = handles.world_state.get_fact("pc", "active_window")
        assert fact is not None
        assert fact.source == "tool:pc.application.launch"
    finally:
        from raya.devices.windows.mechanisms import window_mgmt

        window_mgmt.close_window("notepad")
        handles.shutdown()


def test_stop_via_telegram_stops_a_real_pending_windows_action(tmp_path):
    """Consigne §9/§12 : /stop Telegram utilise le MÊME mécanisme STOP
    global — vérifié ici contre une vraie action Windows en attente."""
    script = [_tool_call_response("pc.application.launch", {"target": "notepad"}), _text_response("jamais renvoyé")]
    handles, fake = build_test_harness(tmp_path, script, enable_windows_device=True)
    try:
        channel = TelegramChannel(handles.harness, handles.bus, lambda cid, txt: None)
        channel.request_stop(555)
        handles.bus.wait_idle(timeout_s=1.0)
        channel.handle_message(555, "ouvre le bloc-notes")
        state = handles.harness.session_state("telegram:555")
        assert state.status.value == "FAILED"
        assert state.error.code == "STOP_ACTIVE"
        assert len(fake.calls) == 0
    finally:
        from raya.devices.windows.mechanisms import window_mgmt

        window_mgmt.close_window("notepad")
        handles.shutdown()


# --- 2. Preuve : même pipeline, quelle que soit l'interface (§10) ---

def test_telegram_and_cockpit_produce_the_identical_tool_trace_for_the_same_intent(tmp_path):
    script = [
        _tool_call_response("demo.idempotent_counter", {}),
        _tool_call_response("demo.idempotent_counter", {}),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        telegram = TelegramChannel(handles.harness, handles.bus, lambda cid, txt: None)
        ui = UIChannel(handles.harness, handles.bus, session_id="ui-1")

        telegram.handle_message(555, "incrémente")  # déclenche une confirmation (SENSITIVE)
        ui.send_message("incrémente")  # même intention, autre canal

        trace_telegram = handles.harness.last_tool_trace("telegram:555")
        trace_ui = handles.harness.last_tool_trace("ui-1")
        assert trace_telegram[0]["tool_name"] == trace_ui[0]["tool_name"] == "demo.idempotent_counter"
        assert trace_telegram[0]["outcome"] == trace_ui[0]["outcome"] == "awaiting_confirmation"
    finally:
        handles.shutdown()


# --- 3. Test réel Telegram (réseau) — conditionné à un vrai token ---

def test_real_telegram_bot_connectivity():
    """Consigne §26 : vrai test contre RayaV2AssistantBot si le token est
    configuré. BLOCKED honnête sinon — jamais un test simulé qui se fait
    passer pour un test réel."""
    token = os.environ.get("RAYA_TELEGRAM_BOT_TOKEN")
    if not token:
        pytest.skip("BLOCKED: RAYA_TELEGRAM_BOT_TOKEN absent de l'environnement — test réseau réel non exécuté")
    client = TelegramClient(token)
    updates = client.get_updates(None, timeout_s=1)
    assert isinstance(updates, list)  # une vraie réponse API (même vide) prouve la connectivité


def test_real_telegram_end_to_end_conversation_from_a_real_phone():
    """Consigne §26/§27/§28 : /start, "Salut RAYA", "Qui es-tu ?", "Quel
    modèle utilises-tu ?", /status, /stop, puis une vraie action PC et une
    vraie notification — nécessite un échange RÉEL avec un téléphone, qu'un
    test automatisé ne peut pas simuler à la place de Ruben. BLOCKED
    honnête : voir RAYA_V2_PHASE9_IMPLEMENTATION_REPORT.md §Real Telegram
    Test pour le compte-rendu manuel de cette vérification."""
    pytest.skip("BLOCKED: nécessite une action humaine réelle sur un téléphone Telegram — voir rapport §Real Telegram Test")
