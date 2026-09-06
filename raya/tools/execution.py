"""Pipeline d'exécution d'un outil (RAYA_V2_TECHNICAL_ARCHITECTURE.md §8.1) :

    validation -> Safety permission -> exécution -> ToolResult.

Un Tool ne peut jamais s'auto-autoriser ni bypasser Safety — check_permission()
est appelé ICI, avant tout handler, jamais à l'intérieur d'un handler.
safety.should_stop() est vérifié avant l'exécution (RAYA_V2_ARCHITECTURAL_INVARIANTS.md #6).

Phase 3 : publie `tool.call_requested`/`tool.call_completed`/`tool.call_failed`
sur l'EventBus (déjà catalogués RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.10 depuis
Phase 0, jamais câblés jusqu'ici) — observabilité réelle du pipeline agentique
(consigne §32) sans stocker de chain-of-thought.
"""

from __future__ import annotations

import time

from raya.contracts import Event, ErrorInfo, PermissionDecision, ToolCall, ToolResult, ToolResultStatus
from raya.event_bus import EventBus
from raya.safety import SafetyService

from .registry import ToolRegistry
from .validation import ValidationError, validate_call


def _publish(bus: EventBus | None, event_type: str, call: ToolCall, extra: dict) -> None:
    if bus is None:
        return
    payload = {"tool_name": call.tool_name, "tool_call_id": call.id, "operation_id": call.operation_id}
    payload.update(extra)
    bus.publish(Event(type=event_type, source="tools", correlation_id=call.correlation_id, payload=payload))


def execute(registry: ToolRegistry, safety: SafetyService, call: ToolCall, bus: EventBus | None = None,
            user_confirmed: bool = False) -> ToolResult:
    start = time.monotonic()
    _publish(bus, "tool.call_requested", call, {})

    def _finish(result: ToolResult) -> ToolResult:
        event_type = "tool.call_completed" if result.status == ToolResultStatus.SUCCESS else "tool.call_failed"
        _publish(bus, event_type, call, {"status": result.status.value})
        return result

    if safety.should_stop():
        return _finish(ToolResult(
            tool_call_id=call.id,
            status=ToolResultStatus.CANCELLED,
            error=ErrorInfo(code="STOP_ACTIVE", message="safety.should_stop()==True", retryable=False),
        ))

    tool = registry.get(call.tool_name)
    if tool is None:
        return _finish(ToolResult(
            tool_call_id=call.id,
            status=ToolResultStatus.FAILURE,
            error=ErrorInfo(code="UNKNOWN_TOOL", message=f"Tool inconnu : {call.tool_name!r}"),
        ))

    try:
        validate_call(tool, call)
    except ValidationError as exc:
        return _finish(ToolResult(
            tool_call_id=call.id,
            status=ToolResultStatus.FAILURE,
            error=ErrorInfo(code="VALIDATION_ERROR", message=str(exc)),
        ))

    permission = safety.check_permission(action_ref=tool.name, capability_tags=tool.capability_tags,
                                          arguments=call.arguments, user_confirmed=user_confirmed)
    if permission.decision != PermissionDecision.ALLOWED:
        return _finish(ToolResult(
            tool_call_id=call.id,
            status=ToolResultStatus.PERMISSION_DENIED,
            error=ErrorInfo(
                code="PERMISSION_" + permission.decision.value.upper(),
                message=permission.reason,
                retryable=permission.decision == PermissionDecision.REQUIRES_CONFIRMATION,
            ),
        ))

    handler = registry.handler_for(call.tool_name)
    if handler is None:
        return _finish(ToolResult(
            tool_call_id=call.id,
            status=ToolResultStatus.FAILURE,
            error=ErrorInfo(
                code="NOT_IMPLEMENTED",
                message=f"Aucun handler enregistré pour {call.tool_name!r}",
                retryable=False,
            ),
        ))

    try:
        result = handler(call)
    except Exception as exc:  # un handler ne doit jamais faire planter le Harness
        return _finish(ToolResult(
            tool_call_id=call.id,
            status=ToolResultStatus.FAILURE,
            error=ErrorInfo(code="HANDLER_EXCEPTION", message=str(exc), retryable=False),
            duration_ms=int((time.monotonic() - start) * 1000),
        ))

    result.duration_ms = int((time.monotonic() - start) * 1000)
    return _finish(result)
