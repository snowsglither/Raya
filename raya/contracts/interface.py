"""InterfaceRequest, InterfaceResponse (RAYA_V2_CONTRACTS.md §17)."""

from __future__ import annotations

from dataclasses import dataclass, field

from ._base import new_id, utc_now_iso
from .harness import Channel


@dataclass
class InterfaceRequest:
    channel: Channel
    raw_input: dict
    id: str = field(default_factory=lambda: new_id("ireq"))
    session_id: str | None = None
    timestamp: str = field(default_factory=utc_now_iso)


@dataclass
class InterfacePresentation:
    text: str | None = None
    audio_ref: str | None = None
    structured_payload: dict | None = None


@dataclass
class InterfaceResponse:
    request_id: str
    session_id: str
    presentation: InterfacePresentation
    requires_user_action: bool = False
    timestamp: str = field(default_factory=utc_now_iso)
