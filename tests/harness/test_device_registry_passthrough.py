"""Harness — passthroughs Device Registry / identité modèle / statut STOP
(RAYA V2 Phase 9, consigne §16/§21) : les interfaces (Telegram, futures)
n'importent JAMAIS raya.devices directement (RAYA_V2_REPOSITORY_STRUCTURE.md
§20) — uniquement via ces méthodes publiques du Harness."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import DeviceType  # noqa: E402


def test_active_model_identity_reflects_null_provider_honestly(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        identity = handles.harness.active_model_identity()
        assert identity["model"] == "null_provider"
    finally:
        handles.shutdown()


def test_is_stop_active_reflects_real_safety_state(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        assert handles.harness.is_stop_active() is False
        from raya.contracts import Event
        handles.bus.publish(Event(type="interface.stop_requested", source="test", payload={}))
        handles.bus.wait_idle(timeout_s=1.0)
        assert handles.harness.is_stop_active() is True
    finally:
        handles.shutdown()


def test_register_and_describe_device_round_trip(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        handles.harness.register_device_info("telegram-mobile", DeviceType.MOBILE, platform="telegram")
        device = handles.harness.describe_device("telegram-mobile")
        assert device["id"] == "telegram-mobile"
        assert device["type"] == "mobile"
        assert device["platform"] == "telegram"
    finally:
        handles.shutdown()


def test_describe_unknown_device_returns_none(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        assert handles.harness.describe_device("ghost") is None
    finally:
        handles.shutdown()


def test_list_devices_includes_registered_info_devices(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        handles.harness.register_device_info("telegram-mobile", DeviceType.MOBILE)
        ids = [d["id"] for d in handles.harness.list_devices()]
        assert "telegram-mobile" in ids
    finally:
        handles.shutdown()


def test_mark_device_offline_reflected_in_describe(tmp_path):
    handles, fake = build_test_harness(tmp_path, [])
    try:
        handles.harness.register_device_info("telegram-mobile", DeviceType.MOBILE)
        handles.harness.mark_device_offline("telegram-mobile")
        assert handles.harness.describe_device("telegram-mobile")["status"] == "offline"
    finally:
        handles.shutdown()
