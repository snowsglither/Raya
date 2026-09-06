"""Helpers de publication d'événements voix — un seul point d'écriture pour
construire des `VoiceEvent` valides (catalogue fermé), évite que chaque
appelant reconstruise le contrat à la main (consigne §6/§37)."""

from __future__ import annotations

from raya.contracts import InterruptionReason, VoiceEvent, VoiceEventPayload
from raya.event_bus import EventBus


def publish_voice_event(bus: EventBus, event_type: str, session_id: str, *, correlation_id: str | None = None,
                         text: str | None = None, confidence: float | None = None, language: str | None = None,
                         is_final: bool | None = None, reason: InterruptionReason | None = None,
                         detail: dict | None = None) -> None:
    event = VoiceEvent(
        type=event_type,
        payload=VoiceEventPayload(
            session_id=session_id, text=text, confidence=confidence, language=language,
            is_final=is_final, reason=reason, detail=detail,
        ),
        correlation_id=correlation_id,
    )
    bus.publish(event)
