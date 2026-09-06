"""RAYA V2 Phase 6 — scénarios d'intégration réels (REAL END-TO-END BENCHMARK).

Même discipline que test_phase5_scenarios.py : bootstrap() réel (SQLite,
EventBus, Safety, TaskScheduler, Tools réels), seul le modèle est scripté.
Prouve le flux complet UI <-> Harness pour les capacités Phase 6 : conversation,
présence réelle, tâches à la demande, confirmation Safety, vues contextuelles
pilotées par la cognition (jamais par du pattern-matching frontend), STOP."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import ContentPart, Event, FinishReason, ModelResponse, RequestedToolCall  # noqa: E402
from raya.interfaces.ui import UIChannel, UIEventBridge  # noqa: E402
from raya.interfaces.voice.presence import PresenceLabel  # noqa: E402


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


def _wait_for_state(harness, task_id: str, state: str, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = harness.get_task(task_id)
        if task is not None and task.state.value == state:
            return
        time.sleep(0.02)
    raise AssertionError(f"task {task_id} n'a jamais atteint l'état {state!r}")


def test_scenario_1_idle_cockpit_shows_minimal_honest_presence(tmp_path):
    """Étapes 1-3 du benchmark : runtime + UI démarrent, état minimal, rien
    de fabriqué (pas de tâche, pas de conversation, présence IDLE réelle)."""
    handles, fake = build_test_harness(tmp_path, [])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="cockpit")
        assert ui.presence_view().state == PresenceLabel.IDLE.value
        assert ui.conversation_view().messages == []
        assert ui.task_summary_view().tasks == []
    finally:
        handles.shutdown()


def test_scenario_2_text_request_answered_and_reflected_in_conversation(tmp_path):
    """Étape 4 du benchmark (texte, alternative à la voix)."""
    handles, fake = build_test_harness(tmp_path, [_text_response("Bien sûr, je m'en occupe.")])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="cockpit")
        view = ui.send_message("ouvre le bloc-notes")
        assert view.messages[-1].text == "Bien sûr, je m'en occupe."
    finally:
        handles.shutdown()


def test_scenario_3_model_opens_task_view_via_real_tool_not_frontend_guessing(tmp_path):
    """'User: Show me my tasks.' -> show Task view — la décision vient de la
    cognition (ui.show_view exécuté via Safety), jamais d'un pattern-matching
    JS sur le texte de la réponse."""
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("ui.show_view", {"view": "tasks"}), _text_response("Voici tes tâches."),
    ])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="cockpit")
        bridge = UIEventBridge(handles.bus, session_id="cockpit")
        received = []
        bridge.add_listener(received.append)
        try:
            ui.send_message("montre-moi mes tâches")
            handles.bus.wait_idle(timeout_s=1.0)
            assert any(n["type"] == "ui.view_requested" and n["payload"]["view"] == "tasks" and n["payload"]["action"] == "show"
                       for n in received)
        finally:
            bridge.close()
    finally:
        handles.shutdown()


def test_scenario_4_hide_view_returns_to_minimal_state(tmp_path):
    """'User: Hide that.' -> retour à l'état minimal, piloté par le même
    mécanisme honnête (ui.hide_view)."""
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("ui.hide_view", {"view": "all"}), _text_response("D'accord."),
    ])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="cockpit")
        bridge = UIEventBridge(handles.bus, session_id="cockpit")
        received = []
        bridge.add_listener(received.append)
        try:
            ui.send_message("cache tout")
            handles.bus.wait_idle(timeout_s=1.0)
            assert any(n["type"] == "ui.view_requested" and n["payload"]["action"] == "hide" and n["payload"]["view"] == "all"
                       for n in received)
        finally:
            bridge.close()
    finally:
        handles.shutdown()


def test_scenario_5_background_task_does_not_turn_cockpit_into_permanent_dashboard(tmp_path):
    """Étapes 11-17 du benchmark : une tâche de fond démarre, la présence
    reflète WORKING, mais task_summary_view() n'est interrogée QUE quand
    demandé — rien n'est poussé automatiquement en continu vers le frontend
    (aucun event 'task.progress' dans le catalogue whitelisté du bridge)."""
    handles, fake = build_test_harness(tmp_path, [])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="cockpit")
        bridge = UIEventBridge(handles.bus, session_id="cockpit")
        received = []
        bridge.add_listener(received.append)
        try:
            task = handles.harness.start_background_task("tâche de fond réelle", channel="ui", session_id="cockpit")
            handles.bus.wait_idle(timeout_s=1.0)
            assert ui.presence_view().state == PresenceLabel.WORKING.value

            view = ui.task_summary_view()  # "à la demande" seulement
            assert any(t.id == task.id for t in view.tasks)

            # task.progress n'est jamais dans le catalogue poussé au frontend :
            assert not any(n["type"] == "task.progress" for n in received)
        finally:
            bridge.close()
    finally:
        handles.shutdown()


def test_scenario_6_sensitive_action_requires_real_confirmation_ui_never_decides(tmp_path):
    """Bloc CONFIRMATION UI complet : demande -> event -> présence
    NEEDS_ATTENTION -> résolution utilisateur -> exécution réelle."""
    # Phase 11 (§4) : confirm_pending() fait désormais un appel modèle
    # supplémentaire pour reformuler le ToolResult en langage naturel — le
    # 2e élément du script couvre cet appel.
    script = [_tool_call_response("demo.idempotent_counter", {}), _text_response("C'est fait, compteur incrémenté.")]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="cockpit")
        bridge = UIEventBridge(handles.bus, session_id="cockpit")
        received = []
        bridge.add_listener(received.append)
        try:
            ui.send_message("incrémente le compteur")
            handles.bus.wait_idle(timeout_s=1.0)
            assert any(n["type"] == "harness.confirmation_required" for n in received)
            assert ui.presence_view().state == PresenceLabel.NEEDS_ATTENTION.value

            conv = ui.confirm(approved=True)
            handles.bus.wait_idle(timeout_s=1.0)
            assert any(n["type"] == "harness.confirmation_resolved" and n["payload"]["approved"] is True for n in received)
            assert ui.presence_view().state != PresenceLabel.NEEDS_ATTENTION.value
            assert conv.messages[-1].text == "C'est fait, compteur incrémenté."
            assert '"status"' not in conv.messages[-1].text  # plus de ToolResult JSON brut exposé (§4)
        finally:
            bridge.close()
    finally:
        handles.shutdown()


def test_scenario_7_stop_reaches_ui_through_the_same_event_path_as_every_other_channel(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="cockpit")
        bridge = UIEventBridge(handles.bus, session_id="cockpit")
        received = []
        bridge.add_listener(received.append)
        try:
            assert handles.safety.should_stop() is False
            ui.request_stop()
            handles.bus.wait_idle(timeout_s=1.0)
            assert handles.safety.should_stop() is True
            assert any(n["type"] == "interface.stop_requested" for n in received)
        finally:
            bridge.close()
    finally:
        handles.shutdown()


def test_scenario_8_world_view_never_shown_unless_requested_and_reflects_real_facts(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        ui = UIChannel(handles.harness, handles.bus, session_id="cockpit")
        assert ui.world_view().facts == []  # rien tant que rien n'est demandé/connu
        handles.harness.set_world_fact("pc", "active_window", "Bloc-notes")
        view = ui.world_view(domains=("pc",))
        assert view.facts[0].value == "Bloc-notes"
    finally:
        handles.shutdown()


def test_scenario_9_two_cockpit_sessions_are_isolated(tmp_path):
    """CHANNEL/CLIENT ISOLATION : deux sessions Cockpit distinctes ne
    partagent ni conversation ni présence individuelle, même Harness/bus
    partagés en dessous."""
    handles, fake = build_test_harness(tmp_path, [_text_response("réponse A"), _text_response("réponse B")])
    try:
        ui_a = UIChannel(handles.harness, handles.bus, session_id="session-a")
        ui_b = UIChannel(handles.harness, handles.bus, session_id="session-b")
        ui_a.send_message("question A")
        assert ui_b.conversation_view().messages == []  # aucune fuite vers B

        ui_b.send_message("question B")
        assert len(ui_a.conversation_view().messages) == 2
        assert len(ui_b.conversation_view().messages) == 2
        assert ui_a.conversation_view().messages[-1].text == "réponse A"
        assert ui_b.conversation_view().messages[-1].text == "réponse B"
    finally:
        handles.shutdown()


def test_scenario_10_confirmation_targeted_at_one_session_never_leaks_to_another(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_tool_call_response("demo.idempotent_counter", {})])
    try:
        ui_a = UIChannel(handles.harness, handles.bus, session_id="session-a")
        ui_b = UIChannel(handles.harness, handles.bus, session_id="session-b")
        ui_a.send_message("incrémente")
        handles.bus.wait_idle(timeout_s=1.0)
        assert ui_a.confirmation_view() is not None
        assert ui_b.confirmation_view() is None
        assert ui_a.presence_view().needs_attention is True
        assert ui_b.presence_view().needs_attention is False
    finally:
        handles.shutdown()
