"""Cockpit web entrypoint (RAYA V2 Phase 6) — FastAPI/WebSocket réels, testés
via starlette TestClient (httpx). Le modèle reste scripté (FakeScriptedProvider,
même discipline que le reste de la suite Phase 3+) ; le serveur HTTP/WS,
UIChannel, UIEventBridge, Tools, Safety, Tasks, Persistence restent 100% réels."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from raya.contracts import ContentPart, FinishReason, ModelResponse, RequestedToolCall  # noqa: E402
from raya.runtime.entrypoints.web import create_app  # noqa: E402


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


def _client(tmp_path, script):
    handles, fake = build_test_harness(tmp_path, script)
    app = create_app(handles)
    return handles, fake, TestClient(app)


def _receive_until(ws, expected_type: str, max_messages: int = 10) -> dict:
    """D'autres events légitimes (ex: attention.decision_made, publié dès
    interface.request_received) peuvent arriver avant celui attendu — on lit
    jusqu'à le trouver plutôt que de supposer un ordre strict à un seul event."""
    for _ in range(max_messages):
        message = ws.receive_json()
        if message["type"] == expected_type:
            return message
    raise AssertionError(f"'{expected_type}' jamais reçu après {max_messages} messages")


def test_index_serves_the_cockpit_page(tmp_path):
    handles, fake, client = _client(tmp_path, [])
    try:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
    finally:
        handles.shutdown()


def test_default_cockpit_state_is_minimal(tmp_path):
    """DEFAULT UI mandatory test : idle, pas de tâche, pas de conversation."""
    handles, fake, client = _client(tmp_path, [])
    try:
        presence = client.get("/api/session/s1/presence").json()
        assert presence["state"] == "idle"
        assert client.get("/api/session/s1/tasks").json()["tasks"] == []
        assert client.get("/api/session/s1/conversation").json()["messages"] == []
        assert client.get("/api/session/s1/confirmation").json() is None
    finally:
        handles.shutdown()


def test_post_message_returns_real_conversation(tmp_path):
    handles, fake, client = _client(tmp_path, [_text_response("Bonjour !")])
    try:
        resp = client.post("/api/session/s1/message", json={"text": "salut"})
        assert resp.status_code == 200
        messages = resp.json()["messages"]
        assert messages[0]["role"] == "user" and messages[0]["text"] == "salut"
        assert messages[1]["role"] == "raya" and messages[1]["text"] == "Bonjour !"
    finally:
        handles.shutdown()


def test_post_message_without_text_is_rejected(tmp_path):
    handles, fake, client = _client(tmp_path, [])
    try:
        resp = client.post("/api/session/s1/message", json={})
        assert resp.status_code == 400
    finally:
        handles.shutdown()


def test_stop_endpoint_reaches_safety(tmp_path):
    handles, fake, client = _client(tmp_path, [])
    try:
        resp = client.post("/api/session/s1/stop")
        assert resp.status_code == 200
        handles.bus.wait_idle(timeout_s=1.0)
        assert handles.safety.should_stop() is True
    finally:
        handles.shutdown()


def test_task_lifecycle_via_api(tmp_path):
    handles, fake, client = _client(tmp_path, [])
    try:
        task = handles.harness.start_background_task("objectif api", channel="ui", session_id="s1")
        import time
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if handles.harness.get_task(task.id).state.value == "RUNNING":
                break
            time.sleep(0.02)

        detail = client.get(f"/api/session/s1/tasks/{task.id}").json()
        assert detail["id"] == task.id
        assert detail["controls"]["can_pause"] is True

        resp = client.post(f"/api/session/s1/tasks/{task.id}/pause")
        assert resp.status_code == 200
        assert resp.json()["state"] == "PAUSED"

        resp = client.post(f"/api/session/s1/tasks/{task.id}/resume")
        assert resp.json()["state"] == "RUNNING"
    finally:
        handles.shutdown()


def test_task_action_unknown_returns_400(tmp_path):
    handles, fake, client = _client(tmp_path, [])
    try:
        task = handles.harness.start_background_task("x", channel="ui", session_id="s1")
        resp = client.post(f"/api/session/s1/tasks/{task.id}/nonsense")
        assert resp.status_code == 400
    finally:
        handles.shutdown()


def test_unknown_task_detail_returns_404(tmp_path):
    handles, fake, client = _client(tmp_path, [])
    try:
        resp = client.get("/api/session/s1/tasks/does-not-exist")
        assert resp.status_code == 404
    finally:
        handles.shutdown()


def test_world_endpoint_reflects_real_facts(tmp_path):
    handles, fake, client = _client(tmp_path, [])
    try:
        assert client.get("/api/session/s1/world").json()["facts"] == []
        handles.harness.set_world_fact("pc", "active_window", "Notepad")
        facts = client.get("/api/session/s1/world?domains=pc").json()["facts"]
        assert facts[0]["value"] == "Notepad"
    finally:
        handles.shutdown()


def test_confirmation_flow_via_api(tmp_path):
    # Phase 11 (§4) : confirm_pending() fait désormais un appel modèle
    # supplémentaire pour reformuler le ToolResult en langage naturel — le
    # 2e élément du script couvre cet appel.
    script = [_tool_call_response("demo.idempotent_counter", {}), _text_response("C'est fait, compteur incrémenté.")]
    handles, fake, client = _client(tmp_path, script)
    try:
        client.post("/api/session/s1/message", json={"text": "incrémente"})
        confirmation = client.get("/api/session/s1/confirmation").json()
        assert confirmation is not None
        assert confirmation["tool_name"] == "demo.idempotent_counter"

        resp = client.post("/api/session/s1/confirmation/resolve", json={"approved": True})
        assert resp.status_code == 200
        last_text = resp.json()["messages"][-1]["text"]
        assert last_text == "C'est fait, compteur incrémenté."
        assert '"status"' not in last_text  # plus de ToolResult JSON brut exposé (§4)
        assert client.get("/api/session/s1/confirmation").json() is None
    finally:
        handles.shutdown()


def test_confirmation_resolve_without_pending_returns_409(tmp_path):
    handles, fake, client = _client(tmp_path, [])
    try:
        resp = client.post("/api/session/s1/confirmation/resolve", json={"approved": True})
        assert resp.status_code == 409
    finally:
        handles.shutdown()


def test_websocket_sync_message_on_connect_reflects_real_state(tmp_path):
    handles, fake, client = _client(tmp_path, [_text_response("bonjour")])
    try:
        client.post("/api/session/s1/message", json={"text": "salut"})
        with client.websocket_connect("/ws/s1") as ws:
            first = ws.receive_json()
            assert first["type"] == "sync"
            assert first["payload"]["presence"]["state"] == "idle"
            assert len(first["payload"]["conversation"]["messages"]) == 2
            assert first["payload"]["confirmation"] is None
    finally:
        handles.shutdown()


def test_websocket_receives_confirmation_required_notification(tmp_path):
    handles, fake, client = _client(tmp_path, [_tool_call_response("demo.idempotent_counter", {})])
    try:
        with client.websocket_connect("/ws/s1") as ws:
            ws.receive_json()  # sync initial
            client.post("/api/session/s1/message", json={"text": "incrémente"})
            notification = _receive_until(ws, "harness.confirmation_required")
            assert notification["payload"]["tool_name"] == "demo.idempotent_counter"
    finally:
        handles.shutdown()


def test_websocket_reconnect_resyncs_from_authoritative_state_not_stale_client_state(tmp_path):
    """RECONNECTION : une déconnexion puis reconnexion doit repartir de
    l'état RÉEL courant (jamais rejouer un historique périmé)."""
    handles, fake, client = _client(tmp_path, [_text_response("un"), _text_response("deux")])
    try:
        with client.websocket_connect("/ws/s1") as ws1:
            ws1.receive_json()
            client.post("/api/session/s1/message", json={"text": "premier"})
        # ws1 fermée ici — nouvelle connexion "reconnect"
        client.post("/api/session/s1/message", json={"text": "second"})
        with client.websocket_connect("/ws/s1") as ws2:
            sync = ws2.receive_json()
            assert len(sync["payload"]["conversation"]["messages"]) == 4
    finally:
        handles.shutdown()


def test_two_sessions_isolated_via_api():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        handles, fake, client = _client(tmp_path, [_text_response("réponse A")])
        try:
            client.post("/api/session/session-a/message", json={"text": "question A"})
            assert client.get("/api/session/session-b/conversation").json()["messages"] == []
            assert len(client.get("/api/session/session-a/conversation").json()["messages"]) == 2
        finally:
            handles.shutdown()


def test_ui_view_requested_pushed_to_correct_websocket_only(tmp_path):
    handles, fake, client = _client(tmp_path, [
        _tool_call_response("ui.show_view", {"view": "tasks"}), _text_response("voilà"),
    ])
    try:
        with client.websocket_connect("/ws/s1") as ws:
            ws.receive_json()  # sync
            client.post("/api/session/s1/message", json={"text": "montre mes tâches"})
            notification = _receive_until(ws, "ui.view_requested")
            assert notification["payload"] == {"session_id": "s1", "view": "tasks", "action": "show"}
    finally:
        handles.shutdown()


def test_model_can_still_explicitly_request_the_conversation_view(tmp_path):
    """Fix Cockpit (conversation panel contextuel, pas auto-ouvert) : le
    mécanisme d'ouverture EXPLICITE par le modèle (ui.show_view) reste
    intact côté backend — seul l'auto-ouverture frontend sur une réponse
    normale a été retirée (raya/interfaces/ui/static/app.js)."""
    handles, fake, client = _client(tmp_path, [
        _tool_call_response("ui.show_view", {"view": "conversation"}), _text_response("voilà"),
    ])
    try:
        with client.websocket_connect("/ws/s1") as ws:
            ws.receive_json()  # sync
            client.post("/api/session/s1/message", json={"text": "montre notre conversation"})
            notification = _receive_until(ws, "ui.view_requested")
            assert notification["payload"] == {"session_id": "s1", "view": "conversation", "action": "show"}
    finally:
        handles.shutdown()


# --- Spatial (Phase 8) — la vue est à la demande, jamais une supposition ---

def test_spatial_endpoint_reports_nothing_mounted_by_default(tmp_path):
    """DEFAULT UI mandatory test, étendu à Phase 8 : aucune scène montée tant
    que le modèle n'a jamais appelé scene.render pour cette session."""
    handles, fake, client = _client(tmp_path, [])
    try:
        view = client.get("/api/session/s1/spatial").json()
        assert view == {"mounted": False, "scene_id": None, "payload": None}
    finally:
        handles.shutdown()


def test_spatial_endpoint_reflects_real_scene_after_render(tmp_path):
    script = [
        _tool_call_response("scene.create", {"label": "Solar System"}),
        None,  # remplacé ci-dessous par un callable lisant le vrai scene_id
        _text_response("Voilà la scène."),
    ]

    def _step_render(req):
        import json
        tool_messages = [m for m in req.messages if m.role == "tool"]
        scene_id = json.loads(tool_messages[-1].content[0].value)["output"]["scene_id"]
        return _tool_call_response("scene.render", {"scene_id": scene_id})

    script[1] = _step_render
    handles, fake, client = _client(tmp_path, script)
    try:
        client.post("/api/session/s1/message", json={"text": "montre-moi le système solaire"})
        view = client.get("/api/session/s1/spatial").json()
        assert view["mounted"] is True
        assert view["scene_id"] == handles.scene_store.list_scenes()[0].id
        assert view["payload"]["label"] == "Solar System"
    finally:
        handles.shutdown()


def test_spatial_view_requested_pushed_over_websocket(tmp_path):
    def _step_render(req):
        import json
        tool_messages = [m for m in req.messages if m.role == "tool"]
        scene_id = json.loads(tool_messages[-1].content[0].value)["output"]["scene_id"]
        return _tool_call_response("scene.render", {"scene_id": scene_id})

    script = [_tool_call_response("scene.create", {"label": "X"}), _step_render, _text_response("ok")]
    handles, fake, client = _client(tmp_path, script)
    try:
        with client.websocket_connect("/ws/s1") as ws:
            ws.receive_json()  # sync
            client.post("/api/session/s1/message", json={"text": "crée et montre une scène"})
            notification = _receive_until(ws, "ui.view_requested")
            assert notification["payload"] == {"session_id": "s1", "view": "spatial", "action": "show"}
    finally:
        handles.shutdown()
