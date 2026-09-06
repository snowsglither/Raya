"""Device, Capability, Command, Result, Health (RAYA_V2_CONTRACTS.md §16)."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ._base import new_id, utc_now_iso
from .errors import ErrorInfo


class DeviceType(str, enum.Enum):
    WINDOWS = "windows"
    LINUX = "linux"
    BROWSER = "browser"
    CAMERA = "camera"
    IOS = "ios"
    MOBILE = "mobile"  # AJOUTÉ Phase 9 — un téléphone connu via une Interface (ex: Telegram), PAS un DeviceAgent exécutable (§17 : jamais transformé en agent autonome)
    FUTURE = "future"


class DeviceStatus(str, enum.Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


@dataclass
class Capability:
    name: str
    input_schema: dict
    mechanism_hint: str | None = None  # debug/observability uniquement, jamais un critère de décision


@dataclass
class Device:
    """Enregistrement du Device Registry (RAYA_V2_ARCHITECTURAL_BLUEPRINT.md
    §19 : identity/status/capabilities/availability/health). `platform` et
    `metadata` AJOUTÉS Phase 9 (ex: Telegram Mobile Device) — champs additifs
    par défaut vides, aucun appelant Phase 0-8 n'instanciait `Device` (jamais
    câblé avant cette phase), donc aucune rétrocompatibilité à préserver."""
    id: str
    type: DeviceType
    capabilities: list[Capability] = field(default_factory=list)
    status: DeviceStatus = DeviceStatus.OFFLINE
    platform: str | None = None
    metadata: dict = field(default_factory=dict)
    last_seen: str = field(default_factory=utc_now_iso)


@dataclass
class Command:
    device_id: str
    capability_name: str
    arguments: dict
    correlation_id: str
    id: str = field(default_factory=lambda: new_id("cmd"))
    timeout_ms: int = 30_000


class CommandStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class Result:
    command_id: str
    status: CommandStatus
    output: dict | None = None
    evidence: dict | None = None
    error: ErrorInfo | None = None
    mechanism_used: str = ""


@dataclass
class Health:
    device_id: str
    status: DeviceStatus
    checked_at: str = field(default_factory=utc_now_iso)
    detail: str | None = None
