"""TelegramClient (RAYA V2 Phase 9, consigne §4/§15/§24) — API Telegram
réelle mais mockée au niveau `requests` (aucun appel réseau ici ; le vrai
test réseau vit dans tests/integration/test_phase9_scenarios.py, conditionné
à un vrai token). Priorité absolue vérifiée ici : le token ne doit JAMAIS
apparaître dans un log, même quand `requests` lui-même l'embarque dans le
message d'une exception (comportement réel de la librairie)."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raya.interfaces.telegram.client import TelegramClient, redact_token  # noqa: E402

_FAKE_TOKEN = "123456789:AAFakeTokenForTestsOnlyNotReal12345"


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (str(payload) if payload else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def test_redact_token_strips_token_from_url():
    text = f"https://api.telegram.org/bot{_FAKE_TOKEN}/getMe failed"
    redacted = redact_token(text)
    assert _FAKE_TOKEN not in redacted
    assert "REDACTED" in redacted


def test_get_updates_returns_result_list(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(200, {"ok": True, "result": [{"update_id": 1}]}))
    client = TelegramClient(_FAKE_TOKEN)
    updates = client.get_updates(None)
    assert updates == [{"update_id": 1}]


def test_get_updates_returns_empty_list_on_http_error(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(500, text="server error"))
    client = TelegramClient(_FAKE_TOKEN)
    assert client.get_updates(None) == []


def test_send_message_plain_text_single_call(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(json)
        return _FakeResponse(200, {"ok": True, "result": {}})

    monkeypatch.setattr(requests, "post", fake_post)
    client = TelegramClient(_FAKE_TOKEN)
    assert client.send_message(123, "bonjour") is True
    assert len(calls) == 1
    assert "parse_mode" not in calls[0]


def test_send_message_tries_markdown_then_falls_back_to_plain(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append(json)
        if json.get("parse_mode") == "Markdown":
            return _FakeResponse(400, text="can't parse entities")
        return _FakeResponse(200, {"ok": True, "result": {}})

    monkeypatch.setattr(requests, "post", fake_post)
    client = TelegramClient(_FAKE_TOKEN)
    assert client.send_message(123, "*bold* text") is True
    assert len(calls) == 2
    assert calls[0]["parse_mode"] == "Markdown"
    assert "parse_mode" not in calls[1]
    assert calls[1]["text"] == "*bold* text"  # jamais perdu


def test_send_message_returns_false_when_every_attempt_fails(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(500, text="down"))
    client = TelegramClient(_FAKE_TOKEN)
    assert client.send_message(123, "salut") is False


def test_answer_callback_query_posts_expected_payload(monkeypatch):
    calls = []

    def fake_post(url, json=None, timeout=None):
        calls.append((url, json))
        return _FakeResponse(200, {"ok": True, "result": True})

    monkeypatch.setattr(requests, "post", fake_post)
    client = TelegramClient(_FAKE_TOKEN)
    client.answer_callback_query("cb1", "Déjà traité.")
    assert calls[0][0].endswith("/answerCallbackQuery")
    assert calls[0][1] == {"callback_query_id": "cb1", "text": "Déjà traité."}


def test_token_never_appears_in_log_on_connection_failure(monkeypatch, caplog):
    def fake_post(url, json=None, timeout=None):
        raise requests.exceptions.ConnectionError(f"Failed to establish connection to {url}")

    monkeypatch.setattr(requests, "post", fake_post)
    client = TelegramClient(_FAKE_TOKEN)
    with caplog.at_level(logging.ERROR, logger="raya"):
        result = client.get_updates(None, timeout_s=1)
    assert result == []
    assert _FAKE_TOKEN not in caplog.text
    assert "REDACTED" in caplog.text
