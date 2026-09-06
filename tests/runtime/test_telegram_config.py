"""Configuration Telegram (RAYA V2 Phase 9, consigne §4/§31/§32) — désactivée
par défaut, token jamais en dur, liste d'IDs FAIL CLOSED par défaut."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raya.runtime.config import _load_dotenv, _parse_int_list, load_config  # noqa: E402


def test_telegram_disabled_by_default(tmp_path, monkeypatch):
    for var in ("RAYA_TELEGRAM_ENABLED", "RAYA_TELEGRAM_BOT_TOKEN", "RAYA_TELEGRAM_ALLOWED_USER_IDS"):
        monkeypatch.delenv(var, raising=False)
    cfg = load_config(root=tmp_path)
    assert cfg.enable_telegram is False
    assert cfg.telegram_bot_token is None
    assert cfg.telegram_allowed_user_ids == ()


def test_telegram_can_be_enabled_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAYA_TELEGRAM_ENABLED", "true")
    monkeypatch.setenv("RAYA_TELEGRAM_BOT_TOKEN", "dummy-token-for-config-test")
    monkeypatch.setenv("RAYA_TELEGRAM_ALLOWED_USER_IDS", "111,222")
    cfg = load_config(root=tmp_path)
    assert cfg.enable_telegram is True
    assert cfg.telegram_bot_token == "dummy-token-for-config-test"
    assert cfg.telegram_allowed_user_ids == (111, 222)
    monkeypatch.delenv("RAYA_TELEGRAM_ENABLED", raising=False)
    monkeypatch.delenv("RAYA_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("RAYA_TELEGRAM_ALLOWED_USER_IDS", raising=False)


def test_parse_int_list_ignores_non_numeric_entries():
    assert _parse_int_list("111, abc, 222") == (111, 222)


def test_parse_int_list_empty_string_is_empty_tuple():
    assert _parse_int_list("") == ()


def test_dotenv_strips_inline_comment_after_value(tmp_path):
    """Bug réel découvert Phase 9 : `RAYA_TELEGRAM_ALLOWED_USER_IDS=123 #
    commentaire` faisait échouer le parsing entier de la valeur (le
    commentaire faisait partie de la chaîne), donnant un allowlist VIDE
    sans avertissement — fail-closed silencieux, jamais acceptable."""
    env_path = tmp_path / ".env"
    env_path.write_text(
        "RAYA_TELEGRAM_ALLOWED_USER_IDS=123,456                # IDs séparés par des virgules\n",
        encoding="utf-8",
    )
    values = _load_dotenv(env_path)
    assert values["RAYA_TELEGRAM_ALLOWED_USER_IDS"] == "123,456"


def test_dotenv_quoted_value_keeps_a_literal_hash(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text('SOME_SECRET="a#b"\n', encoding="utf-8")
    assert _load_dotenv(env_path)["SOME_SECRET"] == "a#b"
