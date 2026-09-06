"""Permission (RAYA_V2_CONTRACTS.md §15)."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ._base import new_id, utc_now_iso
from .tool import PermissionLevel


class PermissionDecision(str, enum.Enum):
    ALLOWED = "allowed"
    REQUIRES_CONFIRMATION = "requires_confirmation"
    DENIED = "denied"


class GrantedBy(str, enum.Enum):
    POLICY = "policy"
    USER_CONFIRMATION = "user_confirmation"
    PHYSICAL_PRESENCE = "physical_presence"


@dataclass
class Permission:
    action_ref: str
    risk_level: PermissionLevel
    decision: PermissionDecision
    reason: str
    audit_id: str = field(default_factory=lambda: new_id("audit"))
    granted_by: GrantedBy | None = None
    timestamp: str = field(default_factory=utc_now_iso)

    def __post_init__(self) -> None:
        if (
            self.risk_level == PermissionLevel.DESTRUCTIVE
            and self.decision == PermissionDecision.ALLOWED
            and self.granted_by is None
        ):
            raise ValueError(
                "Permission: risk_level=destructive + decision=allowed exige granted_by non-null "
                "(RAYA_V2_CONTRACTS.md §15 — pas d'auto-approbation silencieuse)"
            )
