"""tool.call_requested/completed/failed publiés sur l'EventBus (déjà
catalogués RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.10 depuis Phase 0, câblés en
Phase 3 — observabilité réelle du pipeline agentique, consigne §32)."""

from __future__ import annotations

from raya.contracts import PermissionLevel, Tool, ToolCall, ToolCallRequester, ToolResult, ToolResultStatus
from raya.event_bus import EventBus
from raya.safety import AuditTrail, SafetyService, StopController
from raya.tools import ToolRegistry, execute


def _setup():
    bus = EventBus()
    registry = ToolRegistry()
    registry.register(
        Tool(name="utils.noop", description="x", capability_tags=["utils"], input_schema={}, output_schema={},
             permission_level=PermissionLevel.SAFE),
        lambda call: ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output={}),
    )
    safety = SafetyService(StopController(bus), AuditTrail())
    return registry, safety, bus


def _call(name="utils.noop"):
    return ToolCall(tool_name=name, arguments={}, correlation_id="corr_x",
                     requested_by=ToolCallRequester(subsystem="harness", session_id="s1"))


def test_success_publishes_requested_then_completed():
    registry, safety, bus = _setup()
    received = []
    bus.subscribe("tool.*", lambda e: received.append(e), subscriber="test")
    execute(registry, safety, _call(), bus=bus)
    bus.wait_idle(timeout_s=1.0)
    types = [e.type for e in received]
    assert types == ["tool.call_requested", "tool.call_completed"]


def test_failure_publishes_requested_then_failed():
    registry, safety, bus = _setup()
    received = []
    bus.subscribe("tool.*", lambda e: received.append(e), subscriber="test")
    execute(registry, safety, _call("unknown.tool"), bus=bus)
    bus.wait_idle(timeout_s=1.0)
    types = [e.type for e in received]
    assert types == ["tool.call_requested", "tool.call_failed"]


def test_events_carry_correlation_id():
    registry, safety, bus = _setup()
    received = []
    bus.subscribe("tool.*", lambda e: received.append(e), subscriber="test")
    execute(registry, safety, _call(), bus=bus)
    bus.wait_idle(timeout_s=1.0)
    assert all(e.correlation_id == "corr_x" for e in received)


def test_no_bus_means_no_crash_events_optional():
    registry, safety, bus = _setup()
    result = execute(registry, safety, _call())  # bus=None, comportement Phase 0/1/2 préservé
    assert result.status == ToolResultStatus.SUCCESS
