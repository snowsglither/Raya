"""Harness.set_channel_preference / get_channel_preference (Chantier 12 §E)
— Memory structurée existante (MemoryType.PREFERENCE), jamais un second
système de préférences. `NotificationChannel` (canal d'ENVOI) reste distinct
de `ChannelScope` (INTERFACE d'origine)."""

from __future__ import annotations

import pytest

from raya.contracts import ChannelScope
from raya.runtime.bootstrap import bootstrap
from raya.runtime.config import load_config
from raya.persistence import SqliteBackend


def _handles(tmp_path):
    cfg = load_config()
    cfg.db_path = tmp_path / "prefs.sqlite3"
    return bootstrap(config=cfg, backend=SqliteBackend(cfg.db_path))


def test_no_preference_returns_none(tmp_path):
    handles = _handles(tmp_path)
    try:
        assert handles.harness.get_channel_preference("message") is None
    finally:
        handles.shutdown()


def test_set_then_get_channel_preference_round_trips(tmp_path):
    handles = _handles(tmp_path)
    try:
        handles.harness.set_channel_preference("message", "email", session_id="s1")
        assert handles.harness.get_channel_preference("message") == "email"
    finally:
        handles.shutdown()


def test_setting_a_new_preference_for_same_channel_for_overrides_previous(tmp_path):
    handles = _handles(tmp_path)
    try:
        handles.harness.set_channel_preference("message", "email", session_id="s1")
        handles.harness.set_channel_preference("message", "telegram", session_id="s1")
        assert handles.harness.get_channel_preference("message") == "telegram"
    finally:
        handles.shutdown()


def test_preference_for_a_different_channel_for_does_not_leak(tmp_path):
    handles = _handles(tmp_path)
    try:
        handles.harness.set_channel_preference("message", "email", session_id="s1")
        assert handles.harness.get_channel_preference("call_notification") is None
    finally:
        handles.shutdown()


def test_unknown_channel_value_rejected(tmp_path):
    handles = _handles(tmp_path)
    try:
        with pytest.raises(ValueError):
            handles.harness.set_channel_preference("message", "carrier_pigeon", session_id="s1")
    finally:
        handles.shutdown()
