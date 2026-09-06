"""WorldStateFact (RAYA_V2_CONTRACTS.md §2) — état COURANT de l'environnement, pas Memory."""

from __future__ import annotations

import enum
from dataclasses import dataclass

from ._base import parse_iso, utc_now_iso


class Confidence(str, enum.Enum):
    KNOWN_FACT = "known_fact"
    INFERRED = "inferred"
    HYPOTHESIS = "hypothesis"


class FactStatus(str, enum.Enum):
    ACTIVE = "active"
    STALE = "stale"
    SUPERSEDED = "superseded"


@dataclass
class WorldStateFact:
    domain: str
    key: str
    value: object
    source: str
    confidence: Confidence
    timestamp: str = ""
    status: FactStatus = FactStatus.ACTIVE
    freshness_ttl_s: int | None = None

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = utc_now_iso()
        # RAYA_V2_ARCHITECTURAL_INVARIANTS.md (provenance) : "confidence:
        # hypothesis ne peut jamais provenir de source commençant par
        # perception: sans passage explicite par une étape de vérification —
        # la perception observe des faits ou des inférences immédiates, pas
        # des hypothèses non fondées." Vérifié ici, au niveau du contrat,
        # jamais laissé à la discrétion de chaque appelant (Phase 7).
        if self.confidence == Confidence.HYPOTHESIS and self.source.startswith("perception:"):
            raise ValueError(
                "WorldStateFact: confidence=hypothesis interdit pour source='perception:*' "
                "(RAYA_V2_ARCHITECTURAL_INVARIANTS.md) — la perception observe, elle n'hypothèse jamais."
            )

    def is_expired(self, now_iso: str | None = None) -> bool:
        """True si freshness_ttl_s est dépassé (RAYA_V2_CONTRACTS.md §2)."""
        if self.freshness_ttl_s is None:
            return False
        now = parse_iso(now_iso) if now_iso else parse_iso(utc_now_iso())
        observed = parse_iso(self.timestamp)
        return (now - observed).total_seconds() > self.freshness_ttl_s
