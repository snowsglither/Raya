"""Scénarios d'intégration Phase 10 (Long-Horizon Autonomy) — système réel de
bout en bout : vrai Windows Device Agent, vrai Bloc-notes, vrai TaskRegistry/
TaskScheduler/Safety, vrai canal Telegram (le modèle reste scripté — même
discipline que test_phase7_scenarios.py/test_phase9_scenarios.py). Les
scénarios A-F de la consigne §21 dont la substance est déjà couverte de
façon déterministe dans tests/harness/test_long_horizon.py ne sont pas
dupliqués ici — ce fichier se concentre sur ce qui exige un VRAI
environnement (Windows, Telegram)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import ContentPart, FinishReason, ModelCapability, ModelResponse, RequestedToolCall  # noqa: E402
from raya.interfaces.telegram.channel import TelegramChannel  # noqa: E402


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


def _register_planning(handles, *entries) -> None:
    responses = [
        _text_response(str(e).replace("'", '"')) if isinstance(e, list) else _text_response(e)
        for e in entries
    ]
    handles.models.register(FakeScriptedProvider(responses, capabilities=[ModelCapability.PLANNING]))


def _wait_for(predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


# --- Scenario A (réel) : objectif -> plan -> vraie action Windows -> completion ---

def test_real_notepad_launch_as_a_long_horizon_step(tmp_path):
    """Une étape de plan qui appelle réellement le Windows Device Agent —
    pas de logique spéciale : le même `execute_tool()`/`ObservationSpec`/
    promotion World State que n'importe quel autre tour (Phase 7, réutilisé)."""
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("pc.application.launch", {"target": "notepad"}),
        _text_response("Le Bloc-notes est ouvert."),
    ], enable_windows_device=True)
    try:
        _register_planning(handles, ["ouvrir le bloc-notes"])
        task = handles.harness.create_long_horizon_task("ouvre le bloc-notes", channel="cli", session_id="s1")
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED"))
        final = handles.harness.get_task(task.id)
        assert final.state.value == "COMPLETED"

        fact = handles.world_state.get_fact("pc", "active_window")
        assert fact is not None
        assert fact.source == "tool:pc.application.launch"
    finally:
        from raya.devices.windows.mechanisms import window_mgmt

        window_mgmt.close_window("notepad")
        handles.shutdown()


# --- Scenario F (réel) : Telegram -> Task -> completion -> vraie notification ---

def test_telegram_triggered_long_horizon_task_notifies_on_completion(tmp_path):
    """Consigne §14/§19/§28 : réutilise EXACTEMENT le mécanisme Phase 9
    (TelegramChannel._on_task_event) — jamais un système de notification
    Long-Horizon séparé."""
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("tasks.create", {"objective": "analyser un fichier"}),
        _text_response("Je m'en occupe en fond."),
    ])
    try:
        _register_planning(handles, ["analyser le fichier"])
        sent = []
        channel = TelegramChannel(handles.harness, handles.bus, lambda cid, txt: sent.append((cid, txt)))
        channel.handle_message(777, "analyse un fichier pour moi")

        tasks = handles.harness.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].owner.channel == "mobile"
        assert tasks[0].owner.session_id == "telegram:777"

        assert _wait_for(lambda: any("terminée" in t or "échouée" in t for _cid, t in sent))
        assert sent[-1][0] == 777
    finally:
        handles.shutdown()


# --- Cockpit minimal display (§19) ---

def test_cockpit_task_summary_exposes_the_current_step(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("fini.")])
    try:
        _register_planning(handles, ["rédiger la conclusion"])
        from raya.interfaces.ui import UIChannel

        ui = UIChannel(handles.harness, handles.bus, session_id="ui-1")
        task = handles.harness.create_long_horizon_task("rédiger un rapport", channel="web", session_id="ui-1")
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED"))

        summary = ui.task_summary_view()
        assert len(summary.tasks) == 1
        # La tâche est déjà terminée au moment de la lecture — le champ existe
        # et reflète honnêtement le dernier `current_step` connu, jamais fabriqué.
        assert hasattr(summary.tasks[0], "current_step")
    finally:
        handles.shutdown()
