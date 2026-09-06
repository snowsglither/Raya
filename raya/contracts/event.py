"""Event (RAYA_V2_CONTRACTS.md §1) — unité de communication asynchrone entre subsystems."""

from __future__ import annotations

from dataclasses import dataclass, field

from ._base import new_id, utc_now_iso


@dataclass
class Event:
    type: str
    source: str
    payload: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: new_id("evt"))
    timestamp: str = field(default_factory=utc_now_iso)
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if "." not in self.type:
            raise ValueError(
                f"Event.type doit suivre le format <subsystem>.<event_name> (reçu: {self.type!r})"
            )
