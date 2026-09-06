"""VoiceEvent, VoiceEventPayload, InterruptionReason (Phase 5).

Suit exactement le précédent `TaskEvent`/`TaskEventPayload` (contracts/task.py,
Phase 2) : payload TYPÉ, jamais un dict libre — la leçon du bug Phase 2
("TaskEvent.payload est un TaskEventPayload, pas un dict — AttentionEvaluator
faisait .get() dessus, AttributeError silencieusement avalée par l'EventBus")
s'applique identiquement ici. Catalogue de types fermé (mêmes garanties).

Consigne Phase 5 §6 : "chaque événement doit avoir : id, timestamp, source,
session_id, correlation_id, payload structuré. Ne pas envoyer des
dictionnaires arbitraires partout si un contrat structuré existe déjà."
`session_id` vit dans le payload (comme pour TaskEventPayload/task_id) —
`Event`/`TaskEvent` n'ont jamais eu de champ top-level dédié à un
identifiant métier, toujours dans le payload typé.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ._base import new_id, utc_now_iso


class InterruptionReason(str, enum.Enum):
    """Consigne Phase 5 §17 : différencier explicitement la source d'une
    interruption plutôt qu'un booléen `cancel=true` unique."""

    USER = "user_interruption"
    TASK = "task_interruption"
    SYSTEM = "system_interruption"
    SAFETY = "safety_interruption"
    TTS = "tts_interruption"


@dataclass
class VoiceEventPayload:
    session_id: str
    text: str | None = None
    confidence: float | None = None
    language: str | None = None
    is_final: bool | None = None
    reason: InterruptionReason | None = None
    detail: dict | None = None


_VOICE_EVENT_TYPES = frozenset(
    {
        "voice.session_started",
        "voice.session_ended",
        "voice.speech_started",
        "voice.speech_partial",
        "voice.speech_final",
        "voice.speech_cancelled",
        "voice.no_speech",
        "voice.stt_error",
        "voice.tts_started",
        "voice.tts_cancel_requested",
        "voice.tts_cancelled",
        "voice.tts_completed",
        "voice.tts_error",
        "voice.barge_in",
        "voice.response_started",
        "voice.response_completed",
    }
)


@dataclass
class VoiceEvent:
    type: str
    payload: VoiceEventPayload
    source: str = "interfaces.voice"
    id: str = field(default_factory=lambda: new_id("evt"))
    timestamp: str = field(default_factory=utc_now_iso)
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if self.type not in _VOICE_EVENT_TYPES:
            raise ValueError(f"VoiceEvent.type inconnu : {self.type!r} (catalogue fermé)")
