"""tools/catalog/system_time.py (Chantier 12 §A) — system.time.now, seule
source de vérité temporelle exposée à Cognition."""

from __future__ import annotations

from raya.contracts import PermissionLevel, ToolCall, ToolCallRequester, ToolResultStatus
from raya.safety import AuditTrail, SafetyService, StopController
from raya.event_bus import EventBus
from raya.tools import ToolRegistry, execute
from raya.tools.catalog import register_system_time_tool


def _setup(default_timezone: str = "Europe/Brussels"):
    registry = ToolRegistry()
    register_system_time_tool(registry, default_timezone=default_timezone)
    safety = SafetyService(StopController(EventBus()), AuditTrail())
    return registry, safety


def _call(arguments: dict) -> ToolCall:
    return ToolCall(tool_name="system.time.now", arguments=arguments, correlation_id="c1",
                     requested_by=ToolCallRequester(subsystem="harness", session_id="s1"))


def test_system_time_now_is_registered_safe_and_needs_no_confirmation():
    registry, safety = _setup()
    tool = registry.get("system.time.now")
    assert tool is not None
    assert tool.permission_level == PermissionLevel.SAFE
    from raya.safety.risk import classify_risk

    assert classify_risk(tool.capability_tags) == PermissionLevel.SAFE


def test_system_time_now_executes_for_real_and_returns_structured_time():
    registry, safety = _setup()
    result = execute(registry, safety, _call({}))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["timezone"] == "Europe/Brussels"
    assert set(result.output) >= {"iso_utc", "iso_local", "date", "time", "weekday", "day", "month", "year", "hour", "minute", "second", "utc_offset"}


def test_system_time_now_accepts_an_explicit_timezone_override():
    registry, safety = _setup(default_timezone="Europe/Brussels")
    result = execute(registry, safety, _call({"timezone": "UTC"}))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["timezone"] == "UTC"
    assert result.output["utc_offset"] == "+00:00"
