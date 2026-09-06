"""tools/catalog/preferences.py (Chantier 12 §E) — preferences.set_channel,
injection étroite PreferenceOps (même pattern que TaskControlOps/NotifyOps)."""

from __future__ import annotations

from raya.contracts import PermissionLevel, ToolCall, ToolCallRequester, ToolResultStatus
from raya.tools import ToolRegistry, execute
from raya.tools.catalog import PreferenceOps, register_preference_tools


class _FakeEntry:
    def __init__(self, entry_id: str) -> None:
        self.id = entry_id


def _setup(captured: dict | None = None):
    captured = captured if captured is not None else {}

    def _set_channel(channel_for, channel, session_id):
        captured["channel_for"] = channel_for
        captured["channel"] = channel
        captured["session_id"] = session_id
        return _FakeEntry("mem_123")

    registry = ToolRegistry()
    register_preference_tools(registry, PreferenceOps(set_channel=_set_channel))
    from raya.safety import AuditTrail, SafetyService, StopController
    from raya.event_bus import EventBus

    safety = SafetyService(StopController(EventBus()), AuditTrail())
    return registry, safety, captured


def _call(arguments: dict) -> ToolCall:
    return ToolCall(tool_name="preferences.set_channel", arguments=arguments, correlation_id="c1",
                     requested_by=ToolCallRequester(subsystem="harness", session_id="s1"))


def test_preferences_set_channel_is_registered_and_safe():
    registry, safety, captured = _setup()
    tool = registry.get("preferences.set_channel")
    assert tool is not None
    assert tool.permission_level == PermissionLevel.SAFE
    from raya.safety.risk import classify_risk

    assert classify_risk(tool.capability_tags) == PermissionLevel.SAFE


def test_preferences_set_channel_executes_and_persists_via_ops():
    registry, safety, captured = _setup()
    result = execute(registry, safety, _call({"channel_for": "message", "channel": "email"}))
    assert result.status == ToolResultStatus.SUCCESS
    assert captured == {"channel_for": "message", "channel": "email", "session_id": "s1"}
    assert result.output["preference_id"] == "mem_123"


def test_preferences_set_channel_rejects_unknown_channel():
    registry, safety, captured = _setup()
    result = execute(registry, safety, _call({"channel_for": "message", "channel": "carrier_pigeon"}))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "UNKNOWN_CHANNEL"


def test_preferences_set_channel_requires_both_arguments():
    registry, safety, captured = _setup()
    result = execute(registry, safety, _call({"channel_for": "message"}))
    assert result.status == ToolResultStatus.FAILURE
