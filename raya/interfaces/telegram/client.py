"""Client HTTP mince pour l'API Telegram Bot officielle (RAYA V2 Phase 9,
consigne §4/§5/§34/§35). `requests` est déjà une dépendance du projet
(raya/models/providers/ollama_cloud.py) — aucune librairie Telegram
supplémentaire n'est nécessaire pour du polling + sendMessage/getUpdates/
answerCallbackQuery (consigne §35 : pas 5 frameworks).

RÈGLE ABSOLUE (consigne §4) : le token ne doit JAMAIS apparaître dans un log
ou une exception. L'API Telegram l'exige dans l'URL elle-même
(`/bot<token>/<method>`) — `requests` embarque l'URL complète dans ses
propres messages d'exception (ex: ConnectionError). `_redact()` est donc
appliqué systématiquement à tout texte qui pourrait contenir cette URL avant
tout `log()`."""

from __future__ import annotations

import re

import requests

from raya.observability import log

_TOKEN_IN_URL_RE = re.compile(r"/bot\d+:[A-Za-z0-9_-]+")
_REDACTED = "/bot***REDACTED***"

_API_BASE = "https://api.telegram.org"
_LOOKS_MARKDOWN_RE = re.compile(r"```|(?<!\w)[*_](?!\s)\S")


def redact_token(text: str) -> str:
    return _TOKEN_IN_URL_RE.sub(_REDACTED, text)


class TelegramClient:
    def __init__(self, token: str, *, base_url: str = _API_BASE, timeout_s: float = 30.0) -> None:
        self._token = token
        self._base = f"{base_url.rstrip('/')}/bot{token}"
        self._timeout_s = timeout_s

    def _post(self, method: str, payload: dict, *, timeout: float | None = None) -> dict | None:
        try:
            response = requests.post(f"{self._base}/{method}", json=payload, timeout=timeout or self._timeout_s)
        except requests.exceptions.RequestException as exc:
            log("error", "telegram.error", detail=redact_token(str(exc)), method=method)
            return None
        if response.status_code != 200:
            log("error", "telegram.error", detail=redact_token(response.text[:300]),
                method=method, http_status=response.status_code)
            return None
        try:
            data = response.json()
        except ValueError:
            log("error", "telegram.error", detail="réponse non-JSON", method=method)
            return None
        if not data.get("ok"):
            log("error", "telegram.error", detail=redact_token(str(data.get("description"))), method=method)
            return None
        return data.get("result")

    # ------------------------------------------------------------------
    # Polling (consigne §5 : pas de webhook tant que rien ne le justifie)
    # ------------------------------------------------------------------

    def get_updates(self, offset: int | None, *, timeout_s: int = 25) -> list[dict]:
        payload: dict = {"timeout": timeout_s, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._post("getUpdates", payload, timeout=timeout_s + 10)
        return result if isinstance(result, list) else []

    # ------------------------------------------------------------------
    # Envoi — Markdown avec repli honnête vers texte brut (consigne §15)
    # ------------------------------------------------------------------

    def send_message(self, chat_id: int, text: str, *, reply_markup: dict | None = None) -> bool:
        if not text:
            return True
        if _LOOKS_MARKDOWN_RE.search(text):
            payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
            if reply_markup is not None:
                payload["reply_markup"] = reply_markup
            if self._post("sendMessage", payload) is not None:
                log("info", "telegram.message_sent", chat_id=chat_id, parse_mode="Markdown")
                return True
            # Repli : le contenu (potentiellement mal formaté) ne doit JAMAIS
            # être perdu simplement parce que Telegram a rejeté le Markdown.
        payload = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        ok = self._post("sendMessage", payload) is not None
        if ok:
            log("info", "telegram.message_sent", chat_id=chat_id, parse_mode="plain")
        return ok

    def answer_callback_query(self, callback_query_id: str, text: str | None = None) -> None:
        payload: dict = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        self._post("answerCallbackQuery", payload)
