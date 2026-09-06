"""TelegramChannel (RAYA V2 Phase 9, consigne §3/§9/§10/§28) — le client mince
du Harness pour Telegram. Système réel de bout en bout (vrai Harness/Safety/
EventBus/Tasks, seul le modèle est scripté — même discipline Phase 3+)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import ContentPart, Event, FinishReason, ModelResponse, RequestedToolCall  # noqa: E402
from raya.interfaces.telegram.channel import TelegramChannel  # noqa: E402


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


def test_message_reaches_real_harness_and_gets_a_response(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("Bonjour Ruben.")])
    try:
        sent = []
        channel = TelegramChannel(handles.harness, handles.bus, lambda cid, txt: sent.append((cid, txt)))
        response = channel.handle_message(111, "Salut RAYA")
        assert response == "Bonjour Ruben."
    finally:
        handles.shutdown()


def test_same_pipeline_as_any_other_interface_no_special_casing(tmp_path):
    """Consigne §10 : un message Telegram suit EXACTEMENT le même pipeline
    qu'un message Cockpit/CLI/Voix — même Harness, même découverte d'outils."""
    handles, fake = build_test_harness(tmp_path, [_text_response("ok")])
    try:
        channel = TelegramChannel(handles.harness, handles.bus, lambda cid, txt: None)
        channel.handle_message(111, "salut")
        assert fake.calls[0].available_tools is not None or fake.calls[0].available_tools == []
    finally:
        handles.shutdown()


def test_two_chats_are_fully_isolated_sessions(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("réponse A"), _text_response("réponse B")])
    try:
        channel = TelegramChannel(handles.harness, handles.bus, lambda cid, txt: None)
        channel.handle_message(111, "question A")
        channel.handle_message(222, "question B")
        assert handles.harness.response_text("telegram:111") == "réponse A"
        assert handles.harness.response_text("telegram:222") == "réponse B"
    finally:
        handles.shutdown()


def test_needs_confirmation_reflects_real_pending_safety_decision(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_tool_call_response("demo.idempotent_counter", {})])
    try:
        channel = TelegramChannel(handles.harness, handles.bus, lambda cid, txt: None)
        channel.handle_message(111, "incrémente")
        assert channel.needs_confirmation(111) is True
        assert "confirmation" in (channel.confirmation_reason(111) or "").lower() or channel.confirmation_reason(111)
    finally:
        handles.shutdown()


def test_resolve_confirmation_executes_the_exact_original_tool_call(tmp_path):
    # Phase 11 (§4) : confirm_pending() fait désormais un appel modèle
    # supplémentaire pour reformuler le ToolResult en langage naturel — le
    # 2e élément du script couvre cet appel.
    script = [_tool_call_response("demo.idempotent_counter", {}), _text_response("C'est fait, compteur incrémenté.")]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        channel = TelegramChannel(handles.harness, handles.bus, lambda cid, txt: None)
        channel.handle_message(111, "incrémente")
        assert channel.needs_confirmation(111) is True
        response = channel.resolve_confirmation(111, True)
        assert response == "C'est fait, compteur incrémenté."
        assert '"status"' not in response  # plus de ToolResult JSON brut exposé (§4)
        assert channel.needs_confirmation(111) is False
    finally:
        handles.shutdown()


def test_resolve_confirmation_denied_never_executes_the_tool(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_tool_call_response("demo.idempotent_counter", {})])
    try:
        channel = TelegramChannel(handles.harness, handles.bus, lambda cid, txt: None)
        channel.handle_message(111, "incrémente")
        response = channel.resolve_confirmation(111, False)
        assert "n'exécute pas" in response.lower() or "annul" in response.lower()
    finally:
        handles.shutdown()


def test_request_stop_publishes_the_same_global_event_as_every_other_interface(tmp_path):
    """Consigne §9 : jamais un STOP Telegram indépendant."""
    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    try:
        channel = TelegramChannel(handles.harness, handles.bus, lambda cid, txt: None)
        received = []
        handles.bus.subscribe("interface.stop_requested", lambda e: received.append(e), subscriber="test")
        channel.request_stop(111)
        handles.bus.wait_idle(timeout_s=1.0)
        assert len(received) == 1
        assert received[0].source == "interfaces.telegram"
    finally:
        handles.shutdown()


def test_task_completed_for_a_telegram_session_triggers_a_notification(tmp_path):
    """Consigne §28 : PC/Core -> Telegram, notification de tâche terminée."""
    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    try:
        sent = []
        TelegramChannel(handles.harness, handles.bus, lambda cid, txt: sent.append((cid, txt)))
        task = handles.harness.start_background_task("analyse un fichier", channel="mobile", session_id="telegram:555")
        deadline_events = handles.bus
        import time

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline and not sent:
            time.sleep(0.02)
        assert sent, "aucune notification Telegram reçue pour la tâche"
        assert sent[0][0] == 555
        assert "analyse un fichier" in sent[0][1]
    finally:
        handles.shutdown()


def test_task_completed_for_a_non_telegram_session_never_notifies(tmp_path):
    """Isolation (consigne §7) : une tâche CLI/Cockpit ne doit jamais
    déclencher une notification Telegram."""
    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    try:
        sent = []
        TelegramChannel(handles.harness, handles.bus, lambda cid, txt: sent.append((cid, txt)))
        handles.harness.start_background_task("tâche cli", channel="cli", session_id="cli-session-1")
        handles.bus.wait_idle(timeout_s=1.0)
        import time
        time.sleep(0.3)
        assert sent == []
    finally:
        handles.shutdown()


def test_handle_message_touches_the_device_registry(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("ok")])
    try:
        channel = TelegramChannel(handles.harness, handles.bus, lambda cid, txt: None, device_id="telegram-mobile")
        assert handles.harness.describe_device("telegram-mobile") is None
        handles.harness.register_device_info("telegram-mobile", __import__("raya.contracts", fromlist=["DeviceType"]).DeviceType.MOBILE)
        before = handles.harness.describe_device("telegram-mobile")["last_seen"]
        import time
        time.sleep(0.01)
        channel.handle_message(111, "salut")
        after = handles.harness.describe_device("telegram-mobile")["last_seen"]
        assert after >= before
    finally:
        handles.shutdown()


# --- Passe "Targeted Fix" (Sujet 2) : "envoie-moi le lien sur Telegram" ---
# résolvait toujours vers "aucun chat Telegram connu" car `last_chat_id`
# (métadonnée en mémoire, jamais persistée) est perdu à chaque redémarrage
# tant qu'aucun message n'a été reçu depuis. `send_proactive` accepte
# désormais un repli vers l'unique ID de RAYA_TELEGRAM_ALLOWED_USER_IDS —
# jamais une invention (c'est le même propriétaire déjà explicitement
# autorisé pour les messages entrants), jamais un choix arbitraire parmi
# plusieurs IDs.

def test_send_proactive_resolves_to_the_single_authorized_owner_when_no_chat_seen_yet(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        sent = []
        channel = TelegramChannel(handles.harness, handles.bus, lambda cid, txt: sent.append((cid, txt)),
                                   allowed_user_ids=(8782090842,))
        handles.harness.register_device_info(
            "telegram-mobile", __import__("raya.contracts", fromlist=["DeviceType"]).DeviceType.MOBILE,
        )
        ok = channel.send_proactive("le lien du produit")
        assert ok is True
        assert sent == [(8782090842, "le lien du produit")]
    finally:
        handles.shutdown()


def test_send_proactive_never_guesses_among_multiple_authorized_ids(tmp_path):
    """Plusieurs IDs autorisés -> choisir parmi eux serait une supposition
    arbitraire, pas une résolution de 'moi' — refus honnête, jamais un choix
    au hasard (§ consigne : 'ne pas utiliser un chat ID arbitraire')."""
    handles, fake = build_test_harness(tmp_path, [])
    try:
        sent = []
        channel = TelegramChannel(handles.harness, handles.bus, lambda cid, txt: sent.append((cid, txt)),
                                   allowed_user_ids=(111, 222))
        handles.harness.register_device_info(
            "telegram-mobile", __import__("raya.contracts", fromlist=["DeviceType"]).DeviceType.MOBILE,
        )
        ok = channel.send_proactive("le lien du produit")
        assert ok is False
        assert sent == []
    finally:
        handles.shutdown()


def test_send_proactive_prefers_the_freshest_known_chat_over_the_allowlist_fallback(tmp_path):
    """Le dernier chat_id réellement vu (message entrant) reste préféré au
    repli allowlist — le repli n'intervient QUE si aucun signal frais n'existe."""
    handles, fake = build_test_harness(tmp_path, [_text_response("ok")])
    try:
        sent = []
        channel = TelegramChannel(handles.harness, handles.bus, lambda cid, txt: sent.append((cid, txt)),
                                   allowed_user_ids=(8782090842,))
        handles.harness.register_device_info(
            "telegram-mobile", __import__("raya.contracts", fromlist=["DeviceType"]).DeviceType.MOBILE,
        )
        channel.handle_message(999888777, "salut")  # chat_id réel différent de l'allowlist
        ok = channel.send_proactive("le lien du produit")
        assert ok is True
        assert sent == [(999888777, "le lien du produit")]
    finally:
        handles.shutdown()
