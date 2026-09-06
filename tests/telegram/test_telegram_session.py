"""session_id_for_chat / chat_id_from_session (RAYA V2 Phase 9, consigne §7)
— jamais une session globale unique "telegram"."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raya.interfaces.telegram.session import chat_id_from_session, session_id_for_chat  # noqa: E402


def test_session_id_is_prefixed_and_chat_specific():
    assert session_id_for_chat(123456789) == "telegram:123456789"


def test_two_different_chats_get_two_different_sessions():
    assert session_id_for_chat(1) != session_id_for_chat(2)


def test_round_trip():
    assert chat_id_from_session(session_id_for_chat(42)) == 42


def test_non_telegram_session_returns_none():
    assert chat_id_from_session("voice-default") is None


def test_malformed_telegram_session_returns_none():
    assert chat_id_from_session("telegram:not-a-number") is None
