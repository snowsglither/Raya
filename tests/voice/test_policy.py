"""VoiceResponsePolicy (consigne Phase 5 §13/§33) — SPEAK/TEXT_ONLY/SILENT.
Aucune liste de phrases : la matrice de décision teste exclusivement des
COMBINAISONS d'état structuré (Attention/focus/urgence/interruption)."""

from __future__ import annotations

from raya.interfaces.voice.policy import VoiceResponseContext, VoiceResponseDecision, decide_voice_response


def test_no_response_text_is_silent():
    ctx = VoiceResponseContext(has_response_text=False)
    assert decide_voice_response(ctx) == VoiceResponseDecision.SILENT


def test_attention_ignore_is_silent_even_with_text():
    ctx = VoiceResponseContext(has_response_text=True, attention_decision="IGNORE")
    assert decide_voice_response(ctx) == VoiceResponseDecision.SILENT


def test_normal_process_now_speaks():
    ctx = VoiceResponseContext(has_response_text=True, attention_decision="PROCESS_NOW")
    assert decide_voice_response(ctx) == VoiceResponseDecision.SPEAK


def test_tts_incapable_falls_back_to_text_only():
    ctx = VoiceResponseContext(has_response_text=True, tts_capable=False)
    assert decide_voice_response(ctx) == VoiceResponseDecision.TEXT_ONLY


def test_interrupted_by_user_never_speaks_immediately():
    ctx = VoiceResponseContext(has_response_text=True, interrupted_by_user=True)
    assert decide_voice_response(ctx) == VoiceResponseDecision.TEXT_ONLY


def test_low_importance_background_result_while_user_elsewhere_is_text_only():
    ctx = VoiceResponseContext(has_response_text=True, attention_decision="BACKGROUND",
                                user_is_focus=False, importance=0.1)
    assert decide_voice_response(ctx) == VoiceResponseDecision.TEXT_ONLY


def test_high_importance_background_result_still_speaks():
    """Un résultat de fond suffisamment important reste parlé même hors
    focus — la policy ne bloque pas aveuglément tout ce qui est BACKGROUND."""
    ctx = VoiceResponseContext(has_response_text=True, attention_decision="BACKGROUND",
                                user_is_focus=False, importance=0.9)
    assert decide_voice_response(ctx) == VoiceResponseDecision.SPEAK
