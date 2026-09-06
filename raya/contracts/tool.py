"""Tool, ToolCall, ToolResult (RAYA_V2_CONTRACTS.md §9-11).

Champs operation_id/idempotency_key sur ToolCall et idempotent sur Tool
ajoutés lors de la revue de cohérence finale (RAYA_V2_CONTRACTS.md §18,
RAYA_V2_ARCHITECTURE_FINAL_REVIEW.md §3).
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ._base import new_id, utc_now_iso
from .errors import ErrorInfo
from .world_state import Confidence


class PermissionLevel(str, enum.Enum):
    SAFE = "safe"
    SENSITIVE = "sensitive"
    DESTRUCTIVE = "destructive"


@dataclass
class ObservationSpec:
    """Déclare, UNE FOIS par Tool (jamais par appel/utilisateur — consigne
    Phase 7 §13 "ne pas hard-coder des cas utilisateur"), comment un
    `ToolResult` réussi doit être promu en `WorldStateFact`. Générique :
    `raya/harness/loop.py` lit ce spec pour N'IMPORTE QUEL tool, sans jamais
    connaître son nom (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.2/§1.3 — le
    Harness reste le seul point d'écriture World State déclenchée par une
    action, jamais un `if tool_name == ...`).

    `evidence_field` : clé lue dans `ToolResult.evidence` puis, à défaut,
    `ToolResult.output` — la valeur à stocker dans World State.
    `expected_argument` : si renseigné, nom de l'argument du `ToolCall`
    représentant l'état ATTENDU (ex: "target", "url") — permet la
    vérification post-action générique (consigne Phase 7 §8-9) en comparant
    cet argument à la valeur réellement observée, sans connaître le domaine
    métier de l'outil.

    `key_from_argument` (Chantier 12 §C, additif) : si renseigné, la clé
    RÉELLE du fait promu est lue dans cet argument du `ToolCall` (normalisée
    en minuscules) plutôt que d'utiliser `key` tel quel — nécessaire pour un
    domaine à faits MULTIPLES (ex: "filesystem", un fait par dossier connu),
    contrairement à `pc.active_window` qui n'a qu'un seul fait possible.
    Généralise le mécanisme existant SANS jamais connaître le nom du tool
    (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.2/§1.3, inchangé)."""

    domain: str
    key: str
    evidence_field: str
    confidence: Confidence = Confidence.KNOWN_FACT
    freshness_ttl_s: int | None = None
    expected_argument: str | None = None
    key_from_argument: str | None = None


@dataclass
class Tool:
    name: str
    description: str
    capability_tags: list[str]
    input_schema: dict
    output_schema: dict
    permission_level: PermissionLevel
    idempotent: bool = False  # prudent par défaut — jamais un défaut implicite à True
    default_timeout_ms: int = 30_000
    retryable: bool = True
    requires_device: str | None = None
    observation: tuple[ObservationSpec, ...] = ()


@dataclass
class ToolCallRequester:
    subsystem: str
    session_id: str
    # AJOUTÉ Phase 10 : le canal d'origine (Channel.value) — permet à
    # tools/catalog/tasks.py::tasks.create de fixer correctement
    # TaskOwner.channel sans jamais importer raya.harness/raya.interfaces
    # (tools/ reste en dessous dans le graphe de dépendance). Additif, défaut
    # "" — aucun appelant Phase 0-9 ne le renseignait encore.
    channel: str = ""


@dataclass
class ToolCall:
    tool_name: str
    arguments: dict
    correlation_id: str
    requested_by: ToolCallRequester
    id: str = field(default_factory=lambda: new_id("tc"))
    operation_id: str = field(default_factory=lambda: new_id("op"))
    idempotency_key: str = ""
    timestamp: str = field(default_factory=utc_now_iso)
    timeout_ms: int = 30_000

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            self.idempotency_key = f"{self.operation_id}:{self.tool_name}"


class ToolResultStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    PERMISSION_DENIED = "permission_denied"


@dataclass
class ToolResult:
    tool_call_id: str
    status: ToolResultStatus
    output: dict | None = None
    evidence: dict | None = None
    error: ErrorInfo | None = None
    duration_ms: int = 0
    retried_count: int = 0

    def __post_init__(self) -> None:
        if self.status == ToolResultStatus.SUCCESS and self.error is not None:
            raise ValueError("ToolResult: status=success ne peut pas porter un error non-null")
        if self.status != ToolResultStatus.SUCCESS and self.error is None:
            raise ValueError(f"ToolResult: status={self.status.value} exige un error non-null")
