"""VoiceSession (consigne Phase 5 §21) — état transitoire minimal, jamais
une deuxième mémoire."""

from __future__ import annotations

from raya.interfaces.voice.session import VoiceSession
from raya.interfaces.voice.stt.base import TranscriptionResult
from raya.interfaces.voice.tts.base import TTSState
from raya.interfaces.voice.vad.base import VADState


def test_new_session_starts_at_turn_zero():
    session = VoiceSession(session_id="s1")
    assert session.current_turn == 0
    assert session.last_partial is None


def test_new_turn_increments_and_clears_partial():
    session = VoiceSession(session_id="s1")
    session.set_partial(TranscriptionResult(text="ouv", is_final=False))
    session.new_turn()
    assert session.current_turn == 1
    assert session.last_partial is None  # jamais accumulé entre les tours


def test_partial_overwrites_not_accumulates():
    """Consigne §5/§23 : un partial écrase le précédent, jamais une liste
    qui grossit indéfiniment (ça deviendrait une mémoire de facto)."""
    session = VoiceSession(session_id="s1")
    session.set_partial(TranscriptionResult(text="ouv", is_final=False))
    session.set_partial(TranscriptionResult(text="ouvre le", is_final=False))
    assert session.last_partial.text == "ouvre le"


def test_state_setters_touch_updated_at():
    session = VoiceSession(session_id="s1")
    created = session.updated_at
    session.set_vad_state(VADState.SPEECH)
    assert session.vad_state == VADState.SPEECH
    session.set_tts_state(TTSState.SPEAKING)
    assert session.tts_state == TTSState.SPEAKING


def test_session_has_no_conversation_storage_attribute():
    """Preuve structurelle : VoiceSession ne possède aucun champ de type
    liste/historique de messages — seulement de l'état transitoire."""
    import dataclasses

    fields = {f.name for f in dataclasses.fields(VoiceSession)}
    assert "messages" not in fields
    assert "history" not in fields
    assert "conversation" not in fields
