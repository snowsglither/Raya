"""ui.show_view / ui.hide_view (RAYA V2 Phase 6 ADAPTIVE UI) — le seul chemin
honnête pour qu'une vue s'ouvre depuis une intention conversationnelle réelle,
passée par Safety comme n'importe quelle autre capacité."""

from __future__ import annotations

from raya.contracts import Event, PermissionLevel, ToolCall, ToolCallRequester, ToolResultStatus
from raya.event_bus import EventBus
from raya.tools.catalog.ui_views import register_ui_view_tools
from raya.tools.registry import ToolRegistry
from raya.safety.risk import classify_risk


def _call(tool_name: str, view: str, session_id: str = "s1") -> ToolCall:
    return ToolCall(
        tool_name=tool_name, arguments={"view": view}, correlation_id="corr1",
        requested_by=ToolCallRequester(subsystem="harness", session_id=session_id),
    )


def test_ui_presentation_tag_is_safe_never_requires_confirmation():
    assert classify_risk(["ui.presentation"]) == PermissionLevel.SAFE


def test_show_view_publishes_scoped_event():
    bus = EventBus()
    registry = ToolRegistry()
    register_ui_view_tools(registry, bus)
    received = []
    bus.subscribe("ui.view_requested", lambda e: received.append(e), subscriber="test")

    handler = registry.handler_for("ui.show_view")
    result = handler(_call("ui.show_view", "tasks", session_id="s1"))

    bus.wait_idle(timeout_s=1.0)
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output == {"view": "tasks", "action": "show"}
    assert len(received) == 1
    assert received[0].payload == {"session_id": "s1", "view": "tasks", "action": "show"}


def test_hide_view_accepts_all():
    bus = EventBus()
    registry = ToolRegistry()
    register_ui_view_tools(registry, bus)
    handler = registry.handler_for("ui.hide_view")
    result = handler(_call("ui.hide_view", "all", session_id="s1"))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output == {"view": "all", "action": "hide"}


def test_invalid_view_name_fails_honestly():
    bus = EventBus()
    registry = ToolRegistry()
    register_ui_view_tools(registry, bus)
    handler = registry.handler_for("ui.show_view")
    result = handler(_call("ui.show_view", "nonexistent_view", session_id="s1"))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "INVALID_VIEW"


def test_registered_tools_are_safe_permission_level():
    bus = EventBus()
    registry = ToolRegistry()
    register_ui_view_tools(registry, bus)
    assert registry.get("ui.show_view").permission_level == PermissionLevel.SAFE
    assert registry.get("ui.hide_view").permission_level == PermissionLevel.SAFE
