"""Language awareness — pas de langue hardcodée dans Voice (consigne
"LANGUAGE AWARENESS"). `decide_response_language()` est une fonction pure ;
le routage TTS (`_resolve_voice`) est une donnée (mapping), jamais une
règle métier ; un changement de langue entre tours reste un attribut de
contexte — jamais une nouvelle session/tâche."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import ChannelScope, ContentPart, FinishReason, ModelResponse  # noqa: E402
from raya.interfaces.voice.channel import VoiceChannel  # noqa: E402
from raya.interfaces.voice.language import decide_response_language, detect_explicit_language_request  # noqa: E402
from raya.interfaces.voice.tts.base import FakeTTS  # noqa: E402
from raya.interfaces.voice.tts.kokoro_adapter import _resolve_voice  # noqa: E402


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


# --- Décision pure de langue (pas de TTS, pas de Harness) ---

def test_dutch_input_high_confidence_decides_dutch_response():
    lang = decide_response_language(detected_language="nl", language_confidence=0.9, session_language=None)
    assert lang == "nl"


def test_french_input_high_confidence_decides_french_response():
    lang = decide_response_language(detected_language="fr", language_confidence=0.9, session_language=None)
    assert lang == "fr"


def test_english_input_high_confidence_decides_english_response():
    lang = decide_response_language(detected_language="en", language_confidence=0.9, session_language=None)
    assert lang == "en"


def test_low_confidence_detection_falls_back_to_session_language():
    lang = decide_response_language(detected_language="es", language_confidence=0.1, session_language="fr")
    assert lang == "fr"  # trop incertain -> garde la préférence de session déjà connue


def test_low_confidence_without_session_uses_documented_fallback():
    lang = decide_response_language(detected_language="es", language_confidence=0.1, session_language=None)
    assert lang == "en"  # repli documenté, jamais deviné


def test_explicit_request_wins_over_detected_language():
    lang = decide_response_language(detected_language="fr", language_confidence=0.95, session_language=None, explicit_request="en")
    assert lang == "en"


def test_detect_explicit_answer_in_french():
    assert detect_explicit_language_request("réponds en français s'il te plaît") == "fr"


def test_detect_explicit_answer_in_dutch():
    assert detect_explicit_language_request("antwoord in het nederlands") == "nl"


def test_detect_explicit_no_marker_returns_none():
    assert detect_explicit_language_request("ouvre le navigateur") is None


# --- TTS reçoit la langue explicitement (consigne "TTS LANGUAGE ROUTING") ---

def test_kokoro_voice_mapping_is_a_data_table_not_business_logic():
    voice_fr, lang_fr, used_fr, fallback_fr = _resolve_voice("fr")
    assert used_fr == "fr" and fallback_fr is False
    voice_en, lang_en, used_en, fallback_en = _resolve_voice("en")
    assert used_en == "en" and fallback_en is False
    assert voice_fr != voice_en  # une voix différente selon la langue, pas une seule voix globale


def test_kokoro_unsupported_language_falls_back_honestly_never_claims_success():
    """Le néerlandais n'a pas de voix Kokoro réelle (vérifié à
    l'implémentation) — le repli doit être EXPLICITE, jamais silencieux."""
    voice, espeak_lang, used, fallback = _resolve_voice("nl")
    assert fallback is True
    assert used == "en"  # repli documenté
    assert used != "nl"  # ne prétend jamais avoir parlé néerlandais


def test_fake_tts_records_requested_vs_used_language_honestly():
    tts = FakeTTS()
    tts.speak("hallo", language="nl")
    assert tts.last_language_requested == "nl"
    assert tts.last_language_used == "en"
    assert tts.last_language_fallback is True


def test_fake_tts_supported_language_no_fallback():
    tts = FakeTTS()
    tts.speak("bonjour", language="fr")
    assert tts.last_language_used == "fr"
    assert tts.last_language_fallback is False


# --- Bout en bout via VoiceChannel (vrai Harness, FakeTTS) ---

def test_channel_passes_detected_language_through_to_tts(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("Bonjour !")])
    try:
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        channel.handle_final_transcript("salut", confidence=0.9, language="fr")
        assert tts.last_language_requested == "fr"
        assert channel.last_turn.response_language == "fr"
        assert channel.last_turn.detected_language == "fr"
    finally:
        handles.shutdown()


def test_language_switch_between_turns_same_session_no_new_task(tmp_path):
    """"Open mijn browser" (nl) -> "ouvre Discord" (fr) -> "search this" (en) —
    même session à chaque tour, jamais une nouvelle tâche créée pour un
    simple changement de langue."""
    handles, fake = build_test_harness(tmp_path, [_text_response("Ik open het."), _text_response("Je l'ouvre."), _text_response("Searching now.")])
    try:
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        session_id_before = id(channel.session)
        tasks_before = len(handles.tasks.list())

        channel.handle_final_transcript("open mijn browser", confidence=0.9, language="nl")
        assert channel.last_turn.response_language == "nl"

        channel.handle_final_transcript("ouvre Discord", confidence=0.9, language="fr")
        assert channel.last_turn.response_language == "fr"

        channel.handle_final_transcript("now search for this", confidence=0.9, language="en")
        assert channel.last_turn.response_language == "en"

        assert id(channel.session) == session_id_before  # même objet session
        assert channel.session.session_id == "v1"
        assert len(handles.tasks.list()) == tasks_before  # aucune tâche créée par le changement de langue
    finally:
        handles.shutdown()


def test_session_language_persists_across_low_confidence_turn(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("D'accord."), _text_response("Toujours en français.")])
    try:
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        channel.handle_final_transcript("bonjour", confidence=0.9, language="fr")
        assert channel.session.session_language == "fr"

        channel.handle_final_transcript("...", confidence=0.05, language="es")  # détection très incertaine
        assert channel.last_turn.response_language == "fr"  # garde la préférence de session
    finally:
        handles.shutdown()


def test_mixed_language_input_uses_detected_language_of_this_turn(tmp_path):
    """Une transcription mêlant deux langues reste traitée via LA détection
    STT de ce tour (le système ne tente pas de scinder par mot — hors
    scope) ; la langue déclarée par STT pour le segment fait foi."""
    handles, fake = build_test_harness(tmp_path, [_text_response("Sure thing.")])
    try:
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        channel.handle_final_transcript("ouvre my browser please", confidence=0.6, language="en")
        assert channel.last_turn.response_language == "en"
    finally:
        handles.shutdown()


def test_explicit_answer_in_french_request_overrides_detected_english(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("D'accord, je réponds en français.")])
    try:
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        channel.handle_final_transcript("please answer in french from now", confidence=0.9, language="en")
        assert channel.last_turn.response_language == "fr"
        assert tts.last_language_requested == "fr"
    finally:
        handles.shutdown()


def test_channel_isolation_preserved_with_different_session_languages(tmp_path):
    """Deux canaux voix distincts gardent chacun leur propre
    session_language — jamais partagée (consigne §22)."""
    handles, fake = build_test_harness(tmp_path, [_text_response("Hallo."), _text_response("Bonjour.")])
    try:
        tts_a = FakeTTS()
        tts_b = FakeTTS()
        channel_a = VoiceChannel(handles.harness, handles.bus, tts_a, session_id="voice-A")
        channel_b = VoiceChannel(handles.harness, handles.bus, tts_b, session_id="voice-B")

        channel_a.handle_final_transcript("hoi", confidence=0.9, language="nl")
        channel_b.handle_final_transcript("salut", confidence=0.9, language="fr")

        assert channel_a.session.session_language == "nl"
        assert channel_b.session.session_language == "fr"
    finally:
        handles.shutdown()
