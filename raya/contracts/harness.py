"""HarnessRequest, HarnessState (RAYA_V2_CONTRACTS.md §7-8)."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ._base import new_id, utc_now_iso
from .errors import ErrorInfo


class Channel(str, enum.Enum):
    CLI = "cli"
    WEB = "web"
    DESKTOP = "desktop"
    MOBILE = "mobile"
    VOICE = "voice"
    API = "api"


class HarnessProfile(str, enum.Enum):
    STANDARD = "standard"
    FAST_MINIMAL_CONTEXT = "fast_minimal_context"


@dataclass
class InterfaceInput:
    text: str | None = None
    attachments: list[str] | None = None
    audio_ref: str | None = None


@dataclass
class HarnessRequest:
    channel: Channel
    session_id: str
    input: InterfaceInput
    id: str = field(default_factory=lambda: new_id("hreq"))
    correlation_id: str = field(default_factory=lambda: new_id("corr"))
    steering: bool = False
    profile: HarnessProfile = HarnessProfile.STANDARD
    timestamp: str = field(default_factory=utc_now_iso)


class HarnessStatus(str, enum.Enum):
    IDLE = "IDLE"
    ASSEMBLING_CONTEXT = "ASSEMBLING_CONTEXT"
    AWAITING_MODEL = "AWAITING_MODEL"
    EXECUTING_TOOL = "EXECUTING_TOOL"
    VERIFYING = "VERIFYING"
    AWAITING_USER_INPUT = "AWAITING_USER_INPUT"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass
class HarnessState:
    session_id: str
    correlation_id: str
    channel: Channel
    history_ref: str
    status: HarnessStatus = HarnessStatus.IDLE
    current_turn: int = 0
    active_task_ids: list[str] = field(default_factory=list)
    pending_confirmation: dict | None = None
    last_checkpoint_at: str = field(default_factory=utc_now_iso)
    error: ErrorInfo | None = None

    def checkpoint(self) -> None:
        self.last_checkpoint_at = utc_now_iso()
