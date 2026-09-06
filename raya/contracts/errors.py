"""ErrorInfo (RAYA_V2_CONTRACTS.md §0) — traverse toute frontière qui peut échouer."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ErrorInfo:
    code: str
    message: str
    retryable: bool = False
    details: dict | None = field(default=None)
