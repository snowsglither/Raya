"""VoiceRuntime (consigne Phase 5 §9/§27/§33) — transport temps réel VAD->
STT->Channel. `process_one_chunk()` permet un contrôle déterministe (pas de
thread réel à synchroniser) ; `start()`/`stop()` couvrent le vrai chemin
threadé utilisé en production."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import ContentPart, FinishReason, ModelResponse  # noqa: E402
from raya.interfaces.voice.audio.base import FakeAudioInput  # noqa: E402
from raya.interfaces.voice.channel import VoiceChannel  # noqa: E402
from raya.interfaces.voice.stt.base import FakeSTT, STTError, TranscriptionResult  # noqa: E402
from raya.interfaces.voice.tts.base import FakeTTS  # noqa: E402
from raya.interfaces.voice.vad.base import EnergyVAD, VADState  # noqa: E402
from raya.interfaces.voice.runtime import VoiceRuntime  # noqa: E402


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _loud() -> bytes:
    return np.random.randint(-20_000, 20_000, 512).astype(np.int16).tobytes()


def _silence() -> bytes:
    return np.zeros(512, dtype=np.int16).tobytes()


def _build(tmp_path, script, stt_script):
    handles, fake = build_test_harness(tmp_path, script)
    tts = FakeTTS()
    channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
    stt = FakeSTT(stt_script)
    audio = FakeAudioInput()
    audio.start()
    runtime = VoiceRuntime(audio, EnergyVAD(silence_chunks_to_end=3), stt, channel)
    return handles, channel, tts, stt, audio, runtime


def test_full_segment_reaches_harness_and_speaks_response(tmp_path):
    handles, channel, tts, stt, audio, runtime = _build(
        tmp_path, [_text_response("Bonjour !")],
        [TranscriptionResult(text="salut RAYA", is_final=True, confidence=0.9, language="fr")],
    )
    try:
        for _ in range(2):
            audio.push_chunk(_loud())
        for _ in range(5):
            audio.push_chunk(_silence())
        for _ in range(7):
            runtime.process_one_chunk(timeout=0.1)
        assert tts.spoken == ["Bonjour !"]
        assert len(stt.calls) == 1
    finally:
        handles.shutdown()


def test_no_speech_result_never_reaches_harness(tmp_path):
    """Consigne §5/§6 : un no_speech ne doit jamais déclencher un tour de
    conversation (publie voice.no_speech, s'arrête là)."""
    handles, channel, tts, stt, audio, runtime = _build(
        tmp_path, [_text_response("ne devrait jamais être appelé")],
        [TranscriptionResult(text="", is_final=True, no_speech=True)],
    )
    try:
        for _ in range(2):
            audio.push_chunk(_loud())
        for _ in range(5):
            audio.push_chunk(_silence())
        for _ in range(7):
            runtime.process_one_chunk(timeout=0.1)
        assert tts.spoken == []
        assert channel.session.current_turn == 0
    finally:
        handles.shutdown()


def test_stt_error_does_not_crash_the_loop(tmp_path):
    handles, channel, tts, stt, audio, runtime = _build(
        tmp_path, [_text_response("n/a")],
        [STTError("ENGINE_DOWN", "panne simulée")],
    )
    try:
        for _ in range(2):
            audio.push_chunk(_loud())
        for _ in range(5):
            audio.push_chunk(_silence())
        for _ in range(7):
            state = runtime.process_one_chunk(timeout=0.1)  # ne doit jamais lever
        assert tts.spoken == []
    finally:
        handles.shutdown()


def test_speech_during_active_tts_triggers_barge_in(tmp_path):
    """Consigne §9 : détecter SPEECH_START pendant que TTS parle déclenche
    immédiatement barge_in() — pas d'attente de la fin du segment."""
    handles, channel, tts, stt, audio, runtime = _build(tmp_path, [_text_response("n/a")], [])
    try:
        tts.arm_hang()
        channel.speak("réponse en cours...")
        assert tts.is_speaking() is True

        audio.push_chunk(_loud())
        state = runtime.process_one_chunk(timeout=0.1)
        assert state == VADState.SPEECH_START
        assert tts.cancel_calls == 1
        assert tts.is_speaking() is False
    finally:
        handles.shutdown()


def test_partial_never_calls_harness_only_final_does(tmp_path):
    """Consigne §5 : équivalent structurel — FakeSTT n'expose que
    transcribe_final ; ce test prouve que runtime n'appelle JAMAIS
    transcribe_partial (pas de faux support inventé côté runtime)."""
    handles, channel, tts, stt, audio, runtime = _build(
        tmp_path, [_text_response("ok")],
        [TranscriptionResult(text="ouvre le navigateur", is_final=True)],
    )
    try:
        assert not hasattr(runtime, "_use_partial")  # aucun mécanisme de partial câblé dans le runtime
        for _ in range(2):
            audio.push_chunk(_loud())
        for _ in range(5):
            audio.push_chunk(_silence())
        for _ in range(7):
            runtime.process_one_chunk(timeout=0.1)
        assert len(stt.calls) == 1  # un seul appel, jamais un par chunk
    finally:
        handles.shutdown()


def test_start_stop_lifecycle_real_thread(tmp_path):
    handles, channel, tts, stt, audio, runtime = _build(tmp_path, [_text_response("n/a")], [])
    try:
        runtime.start()
        time.sleep(0.1)
        assert audio.is_active() is True
        runtime.stop()
        assert audio.is_active() is False
    finally:
        handles.shutdown()


def test_no_audio_feedback_loop_tts_output_never_feeds_back_into_vad_input(tmp_path):
    """Anti-boucle acoustique (consigne §35) : par CONSTRUCTION, la sortie
    TTS et l'entrée VAD/STT sont deux chemins de données complètement
    séparés dans ce runtime (aucune donnée audio de synthèse n'est jamais
    injectée dans `audio_input`/`vad`) — donc RAYA ne peut structurellement
    pas se répondre à elle-même. Vérifié ici en s'assurant qu'un cycle
    parler->silence->reparler ne fait JAMAIS grandir le buffer entre deux
    segments (pas d'accumulation croisée)."""
    handles, channel, tts, stt, audio, runtime = _build(
        tmp_path, [_text_response("réponse 1"), _text_response("réponse 2")],
        [TranscriptionResult(text="premier segment", is_final=True),
         TranscriptionResult(text="deuxième segment", is_final=True)],
    )
    try:
        for _round in range(2):
            for _ in range(2):
                audio.push_chunk(_loud())
            for _ in range(5):
                audio.push_chunk(_silence())
        for _ in range(14):
            runtime.process_one_chunk(timeout=0.1)
        assert tts.spoken == ["réponse 1", "réponse 2"]
        assert runtime._buffer == bytearray()  # jamais de résidu entre segments
    finally:
        handles.shutdown()
