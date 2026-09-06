"""DeviceRegistry (RAYA V2 Phase 9, consigne §16) — donne enfin vie au
contrat `Device` (identity/status/capabilities/platform/metadata/last_seen),
sans dupliquer un second système : une seule classe, deux formes de
présence (agent exécutable vs device informationnel)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raya.contracts import Capability, Command, DeviceStatus, DeviceType, Health  # noqa: E402
from raya.devices import DeviceAgent, DeviceRegistry  # noqa: E402


class _FakeAgent(DeviceAgent):
    def __init__(self):
        self._caps = [Capability(name="demo.action", input_schema={})]

    def list_capabilities(self):
        return self._caps

    def execute(self, command: Command, should_stop=None):
        raise NotImplementedError

    def health(self):
        return Health(device_id="fake", status=DeviceStatus.ONLINE, detail="ok")


def test_register_without_device_type_is_backward_compatible():
    """Les appels Phase 4 existants (`devices.register(id, agent)`, sans
    device_type) doivent rester valides à l'identique."""
    registry = DeviceRegistry()
    registry.register("win", _FakeAgent())
    assert registry.get("win") is not None
    device = registry.describe("win")
    assert device.type == DeviceType.FUTURE  # inconnu, jamais deviné


def test_register_with_device_type_is_reflected_in_describe():
    registry = DeviceRegistry()
    registry.register("win", _FakeAgent(), device_type=DeviceType.WINDOWS)
    device = registry.describe("win")
    assert device.type == DeviceType.WINDOWS
    assert device.status == DeviceStatus.ONLINE
    assert device.capabilities == [Capability(name="demo.action", input_schema={})]


def test_register_info_creates_a_non_executable_device():
    registry = DeviceRegistry()
    device = registry.register_info("phone", DeviceType.MOBILE, platform="telegram", metadata={"chat_id": 1})
    assert device.id == "phone"
    assert device.type == DeviceType.MOBILE
    assert device.platform == "telegram"
    assert device.metadata == {"chat_id": 1}
    assert device.status == DeviceStatus.ONLINE


def test_info_device_is_never_a_real_agent():
    registry = DeviceRegistry()
    registry.register_info("phone", DeviceType.MOBILE)
    assert registry.get("phone") is None  # jamais promu en DeviceAgent exécutable


def test_touch_updates_last_seen_without_changing_identity():
    registry = DeviceRegistry()
    registry.register_info("phone", DeviceType.MOBILE)
    before = registry.describe("phone").last_seen
    import time
    time.sleep(0.01)
    registry.touch("phone")
    after = registry.describe("phone")
    assert after.last_seen >= before
    assert after.id == "phone"


def test_touch_unknown_device_is_a_safe_noop():
    registry = DeviceRegistry()
    registry.touch("ghost")  # ne lève jamais


def test_mark_offline_reflects_in_describe():
    registry = DeviceRegistry()
    registry.register_info("phone", DeviceType.MOBILE)
    registry.mark_offline("phone")
    assert registry.describe("phone").status == DeviceStatus.OFFLINE


def test_describe_unknown_device_returns_none():
    registry = DeviceRegistry()
    assert registry.describe("does-not-exist") is None


def test_list_devices_merges_agents_and_info_sorted_by_id():
    registry = DeviceRegistry()
    registry.register("win", _FakeAgent(), device_type=DeviceType.WINDOWS)
    registry.register_info("phone", DeviceType.MOBILE)
    devices = registry.list_devices()
    assert [d.id for d in devices] == ["phone", "win"]
