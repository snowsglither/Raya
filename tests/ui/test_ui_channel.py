"""UIChannel (RAYA V2 Phase 6) — client mince du Harness pour le Cockpit.

Même discipline que tests/harness/test_agentic_loop.py : seul le modèle est
scripté (FakeScriptedProvider), tout le reste (Tools/Safety/Tasks/Persistence)
reste 100% réel. Vérifie que l'UI ne fait QUE lire l'API publique du Harness
et ne fabrique jamais un état qu'elle n'a pas reçu."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import (  # noqa: E402
    ContentPart,
    FinishReason,
    ModelResponse,
    PermissionLevel,
    RequestedToolCall,
    Tool,
    ToolResult,
    ToolResultStatus,
)
from raya.interfaces.ui import UIChannel  # noqa: E402
from raya.interfaces.voice.presence import PresenceLabel  # noqa: E402


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


def _wait_for_state(harness, task_id: str, state: str, timeout: float = 2.0) -> None:
    """Le TaskScheduler démarre une tâche dans un thread worker séparé —
    PENDING -> RUNNING n'est jamais instantané (même pattern que
    tests/harness/test_scheduler.py)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = harness.get_task(task_id)
        if task is not None and task.state.value == state:
            return
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} n'a jamais atteint l'état {state!r}")


def test_send_message_returns_real_conversation_with_user_and_raya_turns(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("Bonjour !")])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        view = ui.send_message("salut")
        assert [m.role for m in view.messages] == ["user", "raya"]
        assert view.messages[0].text == "salut"
        assert view.messages[1].text == "Bonjour !"
    finally:
        handles.shutdown()


def test_conversation_history_persists_across_calls(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("un"), _text_response("deux")])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        ui.send_message("premier")
        view = ui.send_message("second")
        assert len(view.messages) == 4
    finally:
        handles.shutdown()


def test_presence_idle_by_default(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        assert ui.presence_view().state == PresenceLabel.IDLE.value
    finally:
        handles.shutdown()


def test_presence_reflects_real_background_task_working_state(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        handles.harness.start_background_task("tâche de fond", channel="ui", session_id="s1")
        handles.bus.wait_idle(timeout_s=1.0)
        assert ui.presence_view().state == PresenceLabel.WORKING.value
        assert ui.presence_view().active_task_count == 1
    finally:
        handles.shutdown()


def test_task_summary_view_lists_real_tasks(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        task = handles.harness.start_background_task("objectif réel", channel="ui", session_id="s1")
        view = ui.task_summary_view()
        assert any(t.id == task.id and t.objective == "objectif réel" for t in view.tasks)
    finally:
        handles.shutdown()


def test_task_summary_view_empty_by_default_no_fake_tasks(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        assert ui.task_summary_view().tasks == []
    finally:
        handles.shutdown()


def test_task_detail_view_unknown_task_returns_none(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        assert ui.task_detail_view("does-not-exist") is None
    finally:
        handles.shutdown()


def test_task_detail_view_controls_reflect_real_transitions(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        task = handles.harness.start_background_task("objectif", channel="ui", session_id="s1")
        _wait_for_state(handles.harness, task.id, "RUNNING")
        detail = ui.task_detail_view(task.id)
        assert detail is not None
        # RUNNING -> peut être mis en pause/annulé, pas "repris" (déjà en cours)
        assert detail.controls.can_pause is True
        assert detail.controls.can_resume is False
        assert detail.controls.can_cancel is True
        assert detail.controls.steering_available is False  # honnête : pas de bouton steer fantôme
    finally:
        handles.shutdown()


def test_pause_resume_cancel_delegate_to_real_harness(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        task = handles.harness.start_background_task("objectif", channel="ui", session_id="s1")
        _wait_for_state(handles.harness, task.id, "RUNNING")
        paused = ui.pause_task(task.id)
        assert paused.state.value == "PAUSED"
        resumed = ui.resume_task(task.id)
        assert resumed.state.value == "RUNNING"
        cancelled = ui.cancel_task(task.id)
        assert cancelled.state.value in ("CANCELLED", "RUNNING")  # RUNNING = annulation coopérative en cours
    finally:
        handles.shutdown()


def test_world_view_reflects_real_facts_only(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        assert ui.world_view().facts == []
        handles.harness.set_world_fact("pc", "active_window", "Notepad")
        view = ui.world_view()
        assert len(view.facts) == 1
        assert view.facts[0].domain == "pc"
        assert view.facts[0].key == "active_window"
        assert view.facts[0].value == "Notepad"
    finally:
        handles.shutdown()


def test_world_view_filters_by_domain(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        handles.harness.set_world_fact("pc", "a", "1")
        handles.harness.set_world_fact("browser", "b", "2")
        view = ui.world_view(domains=("pc",))
        assert [f.domain for f in view.facts] == ["pc"]
    finally:
        handles.shutdown()


def test_computer_view_empty_when_no_pc_activity(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("bonjour")])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        ui.send_message("salut")
        view = ui.computer_view()
        assert view.has_activity is False
        assert view.activity == []
    finally:
        handles.shutdown()


def test_computer_view_reflects_real_pc_tool_activity(tmp_path):
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("pc.demo_action", {}), _text_response("fait."),
    ])
    try:
        handles.tools.register(
            Tool(name="pc.demo_action", description="démo", capability_tags=["pc.read"],
                 input_schema={"type": "object"}, output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE),
            lambda call: ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output={}, evidence={"window": "Notepad"}),
        )
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        ui.send_message("regarde mon écran")
        view = ui.computer_view()
        assert view.has_activity is True
        assert view.activity[0].tool_name == "pc.demo_action"
        assert view.activity[0].evidence == {"window": "Notepad"}
        # Le navigateur ne doit JAMAIS voir cette activité PC
        assert ui.browser_view().has_activity is False
    finally:
        handles.shutdown()


def test_browser_view_reflects_real_browser_tool_activity(tmp_path):
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("browser.demo_navigate", {}), _text_response("fait."),
    ])
    try:
        handles.tools.register(
            Tool(name="browser.demo_navigate", description="démo", capability_tags=["browser.read"],
                 input_schema={"type": "object"}, output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE),
            lambda call: ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output={}, evidence={"url": "https://example.com"}),
        )
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        ui.send_message("ouvre ce site")
        view = ui.browser_view()
        assert view.has_activity is True
        assert view.activity[0].evidence == {"url": "https://example.com"}
    finally:
        handles.shutdown()


def test_result_view_reflects_last_response_and_trace(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("voici le résultat")])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        ui.send_message("fais quelque chose")
        view = ui.result_view()
        assert view.response_text == "voici le résultat"
        assert view.tool_trace == []
    finally:
        handles.shutdown()


def test_confirmation_view_none_by_default(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        assert ui.confirmation_view() is None
    finally:
        handles.shutdown()


def test_confirmation_view_and_confirm_flow(tmp_path):
    # Phase 11 (§4) : confirm_pending() fait désormais un appel modèle
    # supplémentaire pour reformuler le ToolResult en langage naturel — le
    # 2e élément du script couvre cet appel.
    script = [_tool_call_response("demo.idempotent_counter", {}), _text_response("C'est fait, compteur incrémenté.")]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        ui.send_message("incrémente")
        view = ui.confirmation_view()
        assert view is not None
        assert view.tool_name == "demo.idempotent_counter"
        handles.bus.wait_idle(timeout_s=1.0)
        assert ui.presence_view().needs_attention is True

        conv = ui.confirm(approved=True)
        handles.bus.wait_idle(timeout_s=1.0)
        assert ui.confirmation_view() is None
        assert ui.presence_view().needs_attention is False
        assert conv.messages[-1].text == "C'est fait, compteur incrémenté."
        assert '"status"' not in conv.messages[-1].text  # plus de ToolResult JSON brut exposé (§4)
    finally:
        handles.shutdown()


def test_confirm_denied_reflected_honestly(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_tool_call_response("demo.idempotent_counter", {})])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        ui.send_message("incrémente")
        conv = ui.confirm(approved=False)
        assert "je n'exécute pas" in conv.messages[-1].text
        assert ui.confirmation_view() is None
    finally:
        handles.shutdown()


def test_request_stop_publishes_event_never_calls_safety_directly(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        received = []
        handles.bus.subscribe("interface.stop_requested", lambda e: received.append(e), subscriber="test")
        ui.request_stop()
        handles.bus.wait_idle(timeout_s=1.0)
        assert len(received) == 1
        assert handles.safety.should_stop() is True
    finally:
        handles.shutdown()


def test_attention_view_reflects_real_decisions(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="s1")
        assert ui.attention_view().decisions == []
    finally:
        handles.shutdown()
