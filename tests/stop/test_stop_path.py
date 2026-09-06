"""Priorité F — STOP : Interface publie event, Safety reçoit, should_stop() devient
true, propagation correcte (RAYA_V2_ARCHITECTURE_FINAL_REVIEW.md §2, invariant #21)."""

from __future__ import annotations

from raya.contracts import Channel, Event, HarnessRequest, HarnessStatus, InterfaceInput
from raya.event_bus import EventBus
from raya.safety import AuditTrail, SafetyService, StopController


def _wired_safety():
    bus = EventBus()
    safety = SafetyService(StopController(bus), AuditTrail())
    return bus, safety


def test_interface_stop_event_reaches_safety():
    bus, safety = _wired_safety()
    assert safety.should_stop() is False
    bus.publish(Event(type="interface.stop_requested", source="interfaces.cli", payload={}))
    bus.wait_idle(timeout_s=1.0)
    assert safety.should_stop() is True


def test_should_stop_is_synchronous_read_not_event_based():
    bus, safety = _wired_safety()
    bus.publish(Event(type="interface.stop_requested", source="interfaces.voice", payload={}))
    bus.wait_idle(timeout_s=1.0)
    # deux lectures immédiates consécutives, aucune latence supplémentaire attendue
    r1 = safety.should_stop()
    r2 = safety.should_stop()
    assert r1 is True and r2 is True


def test_harness_aborts_when_stop_active_before_first_step():
    from raya.runtime.bootstrap import bootstrap

    handles = bootstrap()
    try:
        handles.bus.publish(Event(type="interface.stop_requested", source="interfaces.cli", payload={}))
        handles.bus.wait_idle(timeout_s=1.0)
        req = HarnessRequest(channel=Channel.CLI, session_id="s1", input=InterfaceInput(text="fais un truc"))
        state = handles.harness.handle_request(req)
        assert state.status == HarnessStatus.FAILED
        assert state.error.code == "STOP_ACTIVE"
    finally:
        handles.shutdown()


def test_tool_execution_cancelled_when_stop_active():
    from raya.contracts import PermissionLevel, Tool, ToolCall, ToolCallRequester
    from raya.tools import ToolRegistry, execute

    bus, safety = _wired_safety()
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="utils.noop", description="x", capability_tags=["utils"],
            input_schema={}, output_schema={}, permission_level=PermissionLevel.SAFE,
        )
    )
    bus.publish(Event(type="task.stop_requested", source="tasks", payload={}))
    bus.wait_idle(timeout_s=1.0)

    call = ToolCall(
        tool_name="utils.noop", arguments={}, correlation_id="c1",
        requested_by=ToolCallRequester(subsystem="harness", session_id="s1"),
    )
    result = execute(registry, safety, call)
    from raya.contracts import ToolResultStatus

    assert result.status == ToolResultStatus.CANCELLED


def test_reset_clears_stop():
    bus, safety = _wired_safety()
    bus.publish(Event(type="interface.stop_requested", source="interfaces.cli", payload={}))
    bus.wait_idle(timeout_s=1.0)
    assert safety.should_stop() is True
    safety._stop.reset()  # équivalent d'un redémarrage clean (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.12)
    assert safety.should_stop() is False
