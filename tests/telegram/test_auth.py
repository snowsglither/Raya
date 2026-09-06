"""TelegramAuthorizer (RAYA V2 Phase 9, consigne §8) — FAIL CLOSED par
construction : personne n'est autorisé tant que la liste n'est pas remplie."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raya.interfaces.telegram.auth import TelegramAuthDecision, TelegramAuthorizer  # noqa: E402


def test_empty_allowlist_denies_everyone():
    auth = TelegramAuthorizer(())
    assert auth.check(123456789) == TelegramAuthDecision.UNAUTHORIZED


def test_known_id_is_authorized():
    auth = TelegramAuthorizer((111, 222))
    assert auth.check(111) == TelegramAuthDecision.AUTHORIZED
    assert auth.check(222) == TelegramAuthDecision.AUTHORIZED


def test_unknown_id_is_unauthorized():
    auth = TelegramAuthorizer((111,))
    assert auth.check(999) == TelegramAuthDecision.UNAUTHORIZED


def test_missing_user_id_is_unknown_never_authorized():
    auth = TelegramAuthorizer((111,))
    assert auth.check(None) == TelegramAuthDecision.UNKNOWN


def test_is_configured_reflects_non_empty_allowlist():
    assert TelegramAuthorizer(()).is_configured() is False
    assert TelegramAuthorizer((1,)).is_configured() is True
