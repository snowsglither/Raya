"""TelegramRuntime (RAYA V2 Phase 9, consigne §8/§9/§22/§32/§33/§34) — dispatch
d'updates Telegram, aucun réseau réel (client factice). Vérifie : autorisation
appliquée à CHAQUE update (message ET callback_query), commandes /start /help
/status /stop, flux de confirmation via boutons inline, robustesse (update
malformé, erreur réseau -> ne tue jamais le polling)."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import ContentPart, FinishReason, ModelResponse, RequestedToolCall  # noqa: E402
from raya.interfaces.telegram.auth import TelegramAuthorizer  # noqa: E402
from raya.interfaces.telegram.channel import TelegramChannel  # noqa: E402
from raya.interfaces.telegram.runtime import TelegramRuntime  # noqa: E402


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


class _FakeClient:
    def __init__(self, updates_batches: list[list[dict]] | None = None):
        self._batches = list(updates_batches or [])
        self.sent: list[tuple[int, str, dict | None]] = []
        self.answered: list[tuple[str, str | None]] = []
        self.get_updates_calls = 0

    def get_updates(self, offset, *, timeout_s=25):
        self.get_updates_calls += 1
        if self._batches:
            return self._batches.pop(0)
        return []

    def send_message(self, chat_id, text, *, reply_markup=None):
        self.sent.append((chat_id, text, reply_markup))
        return True

    def answer_callback_query(self, callback_query_id, text=None):
        self.answered.append((callback_query_id, text))


def _message_update(update_id, chat_id, user_id, text):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "from": {"id": user_id}, "text": text}}


def _callback_update(update_id, chat_id, user_id, data, callback_id="cb1"):
    return {"update_id": update_id, "callback_query": {
        "id": callback_id, "from": {"id": user_id}, "data": data,
        "message": {"chat": {"id": chat_id}},
    }}


def _build(tmp_path, script, allowed_user_ids=(111,)):
    handles, fake = build_test_harness(tmp_path, script)
    client = _FakeClient()
    channel = TelegramChannel(handles.harness, handles.bus, client.send_message)
    authorizer = TelegramAuthorizer(allowed_user_ids)
    runtime = TelegramRuntime(client, channel, authorizer, handles.harness, poll_timeout_s=1)
    return handles, client, runtime


def test_unauthorized_user_message_gets_denied_and_never_reaches_the_harness(tmp_path):
    handles, client, runtime = _build(tmp_path, [_text_response("ne devrait jamais être appelé")], allowed_user_ids=(111,))
    try:
        runtime._dispatch(_message_update(1, chat_id=999, user_id=42, text="ouvre le bloc-notes"))
        assert client.sent == [(999, "Accès non autorisé.", None)]
    finally:
        handles.shutdown()


def test_authorized_user_message_reaches_the_real_harness(tmp_path):
    handles, client, runtime = _build(tmp_path, [_text_response("Bonjour !")])
    try:
        runtime._dispatch(_message_update(1, chat_id=999, user_id=111, text="salut"))
        assert client.sent == [(999, "Bonjour !", None)]
    finally:
        handles.shutdown()


def test_missing_user_id_is_denied():
    import tempfile
    tmp_path = Path(tempfile.mkdtemp())
    handles, client, runtime = _build(tmp_path, [_text_response("n/a")])
    try:
        update = {"update_id": 1, "message": {"chat": {"id": 999}, "text": "salut"}}  # pas de "from"
        runtime._dispatch(update)
        assert client.sent == [(999, "Accès non autorisé.", None)]
    finally:
        handles.shutdown()


def test_start_command(tmp_path):
    handles, client, runtime = _build(tmp_path, [])
    try:
        runtime._dispatch(_message_update(1, 999, 111, "/start"))
        assert len(client.sent) == 1
        assert "RAYA" in client.sent[0][1]
    finally:
        handles.shutdown()


def test_help_command_never_leaks_tool_names(tmp_path):
    handles, client, runtime = _build(tmp_path, [])
    try:
        runtime._dispatch(_message_update(1, 999, 111, "/help"))
        text = client.sent[0][1]
        assert "scene.create" not in text and "pc.application" not in text
    finally:
        handles.shutdown()


def test_status_command_reports_real_state(tmp_path):
    handles, client, runtime = _build(tmp_path, [])
    try:
        runtime._dispatch(_message_update(1, 999, 111, "/status"))
        text = client.sent[0][1]
        assert "RAYA" in text
        assert "Tâches actives" in text
    finally:
        handles.shutdown()


def test_stop_command_uses_the_global_stop_mechanism(tmp_path):
    handles, client, runtime = _build(tmp_path, [])
    try:
        received = []
        handles.bus.subscribe("interface.stop_requested", lambda e: received.append(e), subscriber="test")
        runtime._dispatch(_message_update(1, 999, 111, "/stop"))
        handles.bus.wait_idle(timeout_s=1.0)
        assert len(received) == 1
        assert "Arrêt demandé" in client.sent[0][1]
    finally:
        handles.shutdown()


def test_confirmation_flow_via_inline_buttons(tmp_path):
    # Phase 11 (§4) : confirm_pending() fait désormais un appel modèle
    # supplémentaire pour reformuler le ToolResult en langage naturel — le
    # 2e élément du script couvre cet appel.
    script = [_tool_call_response("demo.idempotent_counter", {}), _text_response("C'est fait, compteur incrémenté.")]
    handles, client, runtime = _build(tmp_path, script)
    try:
        runtime._dispatch(_message_update(1, 999, 111, "incrémente"))
        assert client.sent[-1][2] is not None  # reply_markup présent = boutons de confirmation
        runtime._dispatch(_callback_update(2, 999, 111, "confirm:yes"))
        assert client.answered == [("cb1", None)]
        assert client.sent[-1][1] == "C'est fait, compteur incrémenté."
        assert '"status"' not in client.sent[-1][1]  # plus de ToolResult JSON brut exposé (§4)
    finally:
        handles.shutdown()


def test_confirmation_denied_via_inline_button(tmp_path):
    handles, client, runtime = _build(tmp_path, [_tool_call_response("demo.idempotent_counter", {})])
    try:
        runtime._dispatch(_message_update(1, 999, 111, "incrémente"))
        runtime._dispatch(_callback_update(2, 999, 111, "confirm:no"))
        assert "n'exécute pas" in client.sent[-1][1].lower()
    finally:
        handles.shutdown()


def test_double_tap_on_confirmation_button_never_crashes_the_loop(tmp_path):
    # Phase 11 (§4) : la 1re confirmation fait un appel modèle supplémentaire
    # (reformulation naturelle) — le double tap suivant est court-circuité
    # AVANT confirm_pending (déjà traité) et n'en consomme pas d'autre.
    script = [_tool_call_response("demo.idempotent_counter", {}), _text_response("C'est fait.")]
    handles, client, runtime = _build(tmp_path, script)
    try:
        runtime._dispatch(_message_update(1, 999, 111, "incrémente"))
        runtime._dispatch(_callback_update(2, 999, 111, "confirm:yes", callback_id="cb1"))
        runtime._dispatch(_callback_update(3, 999, 111, "confirm:yes", callback_id="cb2"))  # double tap
        assert client.answered[-1] == ("cb2", "Déjà traité.")
    finally:
        handles.shutdown()


def test_unauthorized_callback_query_is_denied(tmp_path):
    handles, client, runtime = _build(tmp_path, [_tool_call_response("demo.idempotent_counter", {})])
    try:
        runtime._dispatch(_message_update(1, 999, 111, "incrémente"))
        runtime._dispatch(_callback_update(2, 999, 42, "confirm:yes"))  # user_id 42 non autorisé
        assert client.answered[-1] == ("cb1", "Accès non autorisé.")
    finally:
        handles.shutdown()


def test_malformed_update_never_crashes_dispatch(tmp_path):
    handles, client, runtime = _build(tmp_path, [])
    try:
        runtime._dispatch({"update_id": 1})  # ni message ni callback_query
        runtime._dispatch({"update_id": 2, "message": {}})  # message sans "text" ni "chat"
    finally:
        handles.shutdown()


def test_long_response_is_chunked_before_sending(tmp_path):
    long_text = "x" * 9000
    handles, client, runtime = _build(tmp_path, [_text_response(long_text)])
    try:
        runtime._dispatch(_message_update(1, 999, 111, "raconte"))
        assert len(client.sent) > 1
        assert all(len(chunk_text) <= 4096 for _cid, chunk_text, _markup in client.sent)
    finally:
        handles.shutdown()


def test_start_stop_thread_lifecycle_leaves_no_thread_alive(tmp_path):
    handles, client, runtime = _build(tmp_path, [])
    try:
        runtime.start()
        time.sleep(0.1)
        assert runtime._thread is not None and runtime._thread.is_alive()
        runtime.stop(timeout_s=2.0)
        assert runtime._thread is None
    finally:
        handles.shutdown()


def test_get_updates_exception_does_not_kill_the_poll_loop(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        class _FlakyClient(_FakeClient):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def get_updates(self, offset, *, timeout_s=25):
                self.calls += 1
                if self.calls == 1:
                    raise ConnectionError("réseau indisponible")
                return []

        client = _FlakyClient()
        channel = TelegramChannel(handles.harness, handles.bus, client.send_message)
        runtime = TelegramRuntime(client, channel, TelegramAuthorizer((111,)), handles.harness, poll_timeout_s=1)
        runtime.start()
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and client.calls < 2:
            time.sleep(0.05)
        runtime.stop(timeout_s=2.0)
        assert client.calls >= 2  # le polling a survécu à l'erreur et a retenté
    finally:
        handles.shutdown()
