"""VoiceEvent/VoiceEventPayload/InterruptionReason (consigne Phase 5 §6) —
même garantie que TaskEvent (Phase 2) : payload typé, catalogue fermé."""

from __future__ import annotations

import pytest

from raya.contracts import InterruptionReason, VoiceEvent, VoiceEventPayload


def test_voice_event_requires_known_type():
    with pytest.raises(ValueError):
        VoiceEvent(type="voice.made_up_event", payload=VoiceEventPayload(session_id="s1"))


def test_voice_event_accepts_catalogued_type():
    event = VoiceEvent(type="voice.speech_started", payload=VoiceEventPayload(session_id="s1"))
    assert event.type == "voice.speech_started"
    assert event.source == "interfaces.voice"


def test_voice_event_has_id_timestamp_correlation():
    event = VoiceEvent(type="voice.speech_final", payload=VoiceEventPayload(session_id="s1"), correlation_id="c1")
    assert event.id.startswith("evt_")
    assert event.timestamp
    assert event.correlation_id == "c1"


def test_voice_event_payload_carries_session_id_not_top_level():
    """session_id vit dans le payload — même convention que
    TaskEventPayload.task_id (consigne §6, note du fichier contracts/voice.py)."""
    event = VoiceEvent(type="voice.barge_in", payload=VoiceEventPayload(session_id="s42"))
    assert event.payload.session_id == "s42"
    assert not hasattr(event, "session_id")


def test_interruption_reason_has_five_distinct_values():
    values = {r.value for r in InterruptionReason}
    assert values == {"user_interruption", "task_interruption", "system_interruption", "safety_interruption", "tts_interruption"}
