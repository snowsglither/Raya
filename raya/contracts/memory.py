"""MemoryEntry (RAYA_V2_CONTRACTS.md §3) — connaissances persistantes, distinctes du World State."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ._base import new_id, utc_now_iso
from .world_state import Confidence


class MemoryType(str, enum.Enum):
    FACT = "fact"
    PREFERENCE = "preference"
    RULE = "rule"
    EXPERIENCE = "experience"


class MemoryLayer(str, enum.Enum):
    WORKING = "working"
    CONVERSATION = "conversation"
    PERSONAL = "personal"
    PROJECT = "project"
    TASK = "task"
    EXPERIENCE = "experience"


class ChannelScope(str, enum.Enum):
    SHARED = "shared"
    VOICE = "voice"
    CHAT = "chat"
    IOS = "ios"


class MemoryLifecycle(str, enum.Enum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    CONFIRMED = "confirmed"
    AGING = "aging"
    OBSOLETE = "obsolete"


@dataclass
class MemoryEntry:
    type: MemoryType
    layer: MemoryLayer
    channel_scope: ChannelScope
    content: object
    provenance: str
    id: str = field(default_factory=lambda: new_id("mem"))
    confidence: Confidence = Confidence.KNOWN_FACT
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
    lifecycle: MemoryLifecycle = MemoryLifecycle.CANDIDATE
    related_task_id: str | None = None
    supersedes: str | None = None
