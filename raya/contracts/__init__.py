"""Contrats de données RAYA V2 (RAYA_V2_CONTRACTS.md).

Aucune logique métier. contracts/ ne dépend d'aucun autre subsystem RAYA —
tout le monde peut en dépendre (RAYA_V2_REPOSITORY_STRUCTURE.md §2).
"""

from ._base import from_dict, from_json, new_id, parse_iso, to_dict, to_json, utc_now_iso
from .attention import AttentionDecision, AttentionFactors, AttentionOutcome
from .clock import DEFAULT_TIMEZONE, LocalTime, local_time_to_dict, now_local, resolve_not_before
from .context import Context, ContextSection, Freshness, SectionKind
from .device import Capability, Command, CommandStatus, Device, DeviceStatus, DeviceType, Health, Result
from .errors import ErrorInfo
from .event import Event
from .execution_record import ExecutionRecord, ExecutionState, VerificationState
from .harness import (
    Channel,
    HarnessProfile,
    HarnessRequest,
    HarnessState,
    HarnessStatus,
    InterfaceInput,
)
from .interface import InterfacePresentation, InterfaceRequest, InterfaceResponse
from .memory import ChannelScope, MemoryEntry, MemoryLayer, MemoryLifecycle, MemoryType
from .notification import NotificationChannel
from .model import (
    ContentPart,
    FinishReason,
    Message,
    ModelCapability,
    ModelConstraints,
    ModelDescriptor,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    RequestedToolCall,
)
from .perception import PerceptionObservation
from .permission import GrantedBy, Permission, PermissionDecision
from .spatial import (
    Geometry,
    Material,
    Scene,
    SpatialError,
    SpatialObject,
    Transform,
    Vec3,
    slugify_object_id,
    validate_object_id,
)
from .task import (
    Plan,
    PlanStep,
    StepState,
    Task,
    TaskEvent,
    TaskEventPayload,
    TaskOwner,
    TaskProgress,
    TaskState,
    can_transition,
    next_runnable_step,
    plan_is_complete,
    plan_is_stuck,
)
from .tool import ObservationSpec, PermissionLevel, Tool, ToolCall, ToolCallRequester, ToolResult, ToolResultStatus
from .voice import InterruptionReason, VoiceEvent, VoiceEventPayload
from .world_state import Confidence, FactStatus, WorldStateFact

__all__ = [
    "AttentionDecision",
    "AttentionFactors",
    "AttentionOutcome",
    "Capability",
    "Channel",
    "ChannelScope",
    "Command",
    "CommandStatus",
    "Confidence",
    "Context",
    "ContextSection",
    "ContentPart",
    "DEFAULT_TIMEZONE",
    "Device",
    "DeviceStatus",
    "DeviceType",
    "ErrorInfo",
    "Event",
    "ExecutionRecord",
    "ExecutionState",
    "FactStatus",
    "FinishReason",
    "Freshness",
    "Geometry",
    "GrantedBy",
    "HarnessProfile",
    "HarnessRequest",
    "HarnessState",
    "HarnessStatus",
    "Health",
    "InterfaceInput",
    "InterfacePresentation",
    "InterfaceRequest",
    "InterfaceResponse",
    "LocalTime",
    "Material",
    "Message",
    "MemoryEntry",
    "MemoryLayer",
    "MemoryLifecycle",
    "MemoryType",
    "NotificationChannel",
    "ObservationSpec",
    "PerceptionObservation",
    "ModelCapability",
    "ModelConstraints",
    "ModelDescriptor",
    "ModelRequest",
    "ModelResponse",
    "ModelUsage",
    "Permission",
    "PermissionDecision",
    "PermissionLevel",
    "Plan",
    "PlanStep",
    "RequestedToolCall",
    "Result",
    "Scene",
    "SectionKind",
    "SpatialError",
    "SpatialObject",
    "StepState",
    "Task",
    "TaskEvent",
    "TaskEventPayload",
    "TaskOwner",
    "TaskProgress",
    "TaskState",
    "Tool",
    "ToolCall",
    "ToolCallRequester",
    "ToolResult",
    "ToolResultStatus",
    "Transform",
    "Vec3",
    "VerificationState",
    "WorldStateFact",
    "can_transition",
    "from_dict",
    "from_json",
    "local_time_to_dict",
    "new_id",
    "next_runnable_step",
    "now_local",
    "parse_iso",
    "plan_is_complete",
    "plan_is_stuck",
    "resolve_not_before",
    "slugify_object_id",
    "to_dict",
    "to_json",
    "utc_now_iso",
    "validate_object_id",
]
