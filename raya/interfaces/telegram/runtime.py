"""TelegramRuntime — boucle de polling (RAYA V2 Phase 9, consigne §5/§32/§33/§34).

Polling long, PAS de webhook (consigne §5 : rien ne justifie encore un
endpoint public pour cette phase — le Core reste local). Un thread daemon
dédié, jamais le thread appelant (`start()`/`stop()` symétriques, comme
`raya/perception/runtime.py::PerceptionRuntime`, Phase 7). Backoff simple et
borné sur erreur réseau (consigne §34 : "pas une usine à gaz de retry")."""

from __future__ import annotations

import threading
import time

from raya.observability import log

from .auth import TelegramAuthDecision, TelegramAuthorizer
from .channel import TelegramChannel
from .chunker import chunk_message
from .client import TelegramClient
from .commands import handle_help, handle_start, handle_status, handle_unauthorized

_CONFIRM_KEYBOARD = {
    "inline_keyboard": [[
        {"text": "Confirmer", "callback_data": "confirm:yes"},
        {"text": "Annuler", "callback_data": "confirm:no"},
    ]]
}

_MIN_BACKOFF_S = 1.0
_MAX_BACKOFF_S = 30.0


class TelegramRuntime:
    def __init__(
        self, client: TelegramClient, channel: TelegramChannel, authorizer: TelegramAuthorizer,
        harness, *, poll_timeout_s: int = 25,
    ) -> None:
        self._client = client
        self._channel = channel
        self._authorizer = authorizer
        self._harness = harness
        self._poll_timeout_s = poll_timeout_s
        self._offset: int | None = None
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._poll_loop, name="telegram-poll", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 5.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout_s)
            self._thread = None

    # ------------------------------------------------------------------
    # Boucle de polling
    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        backoff = _MIN_BACKOFF_S
        while not self._stop_event.is_set():
            try:
                updates = self._client.get_updates(self._offset, timeout_s=self._poll_timeout_s)
                backoff = _MIN_BACKOFF_S
            except Exception as exc:  # jamais tuer RAYA pour une panne réseau Telegram (§34)
                log("error", "telegram.error", detail=str(exc)[:300])
                self._stop_event.wait(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF_S)
                continue

            for update in updates:
                self._offset = update["update_id"] + 1
                try:
                    self._dispatch(update)
                except Exception as exc:  # un update mal formé ne doit jamais arrêter le polling
                    log("error", "telegram.error", detail=str(exc)[:300])

    # ------------------------------------------------------------------
    # Dispatch d'un update Telegram
    # ------------------------------------------------------------------

    def _dispatch(self, update: dict) -> None:
        if "callback_query" in update:
            self._handle_callback_query(update["callback_query"])
            return
        message = update.get("message")
        if not message or "text" not in message:
            return  # médias non pris en charge cette phase (consigne §13 : texte d'abord)
        self._handle_message(message)

    def _handle_message(self, message: dict) -> None:
        chat_id = message["chat"]["id"]
        user_id = (message.get("from") or {}).get("id")
        text = message["text"]

        decision = self._authorizer.check(user_id)
        if decision != TelegramAuthDecision.AUTHORIZED:
            log("info", "telegram.authorization_denied", chat_id=chat_id, decision=decision.value)
            self._send(chat_id, handle_unauthorized())
            return

        if text.startswith("/"):
            self._handle_command(chat_id, text)
            return

        response = self._channel.handle_message(chat_id, text)
        if self._channel.needs_confirmation(chat_id):
            log("info", "telegram.confirmation_requested", chat_id=chat_id)
            self._send(chat_id, response, reply_markup=_CONFIRM_KEYBOARD)
        else:
            self._send(chat_id, response)

    def _handle_command(self, chat_id: int, text: str) -> None:
        command = text.split()[0].lower().lstrip("/").split("@")[0]
        log("info", "telegram.command", chat_id=chat_id, command=command)
        if command == "start":
            self._send(chat_id, handle_start(self._harness))
        elif command == "help":
            self._send(chat_id, handle_help(self._harness))
        elif command == "status":
            self._send(chat_id, handle_status(self._harness, chat_id))
        elif command == "stop":
            self._channel.request_stop(chat_id)
            self._send(chat_id, "Arrêt demandé.")
        else:
            self._send(chat_id, "Commande inconnue. Essaie /help.")

    def _handle_callback_query(self, callback_query: dict) -> None:
        callback_id = callback_query["id"]
        user_id = (callback_query.get("from") or {}).get("id")
        message = callback_query.get("message") or {}
        chat_id = (message.get("chat") or {}).get("id")
        data = callback_query.get("data", "")

        if chat_id is None or self._authorizer.check(user_id) != TelegramAuthDecision.AUTHORIZED:
            self._client.answer_callback_query(callback_id, "Accès non autorisé.")
            return

        if not data.startswith("confirm:"):
            self._client.answer_callback_query(callback_id)
            return

        approved = data == "confirm:yes"
        try:
            response = self._channel.resolve_confirmation(chat_id, approved)
        except ValueError:
            # Déjà résolue (double tap, ou expirée) — jamais un crash du polling.
            self._client.answer_callback_query(callback_id, "Déjà traité.")
            return
        self._client.answer_callback_query(callback_id)
        self._send(chat_id, response)

    # ------------------------------------------------------------------
    # Envoi — découpage systématique (consigne §14)
    # ------------------------------------------------------------------

    def _send(self, chat_id: int, text: str, *, reply_markup: dict | None = None) -> None:
        chunks = chunk_message(text)
        for i, chunk in enumerate(chunks):
            is_last = i == len(chunks) - 1
            self._client.send_message(chat_id, chunk, reply_markup=reply_markup if is_last else None)
