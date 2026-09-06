"""AttentionDecision (RAYA_V2_CONTRACTS.md §4).

Attention décide QUOI mérite du traitement, jamais COMMENT l'exécuter
(RAYA_V2_TECHNICAL_ARCHITECTURE.md §7.3). Cette dataclass ne peut structurellement
pas porter un choix d'outil ou une instruction d'exécution.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ._base import utc_now_iso


class AttentionOutcome(str, enum.Enum):
    PROCESS_NOW = "PROCESS_NOW"
    BACKGROUND = "BACKGROUND"
    INTERRUPT = "INTERRUPT"
    IGNORE = "IGNORE"


@dataclass
class AttentionFactors:
    urgency: float = 0.0
    importance: float = 0.0
    novelty: float = 0.0
    confidence: float = 0.0
    cost: float = 0.0
    user_relevance: float = 0.0


@dataclass
class AttentionDecision:
    event_ref: str
    decision: AttentionOutcome
    reasoning: str = ""
    factors: AttentionFactors = field(default_factory=AttentionFactors)
    timestamp: str = field(default_factory=utc_now_iso)
    target_session_id: str | None = None
