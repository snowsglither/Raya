"""VoiceRuntime — transport temps réel (consigne Phase 5 §27) : boucle
légère qui déplace des événements d'un composant à l'autre. Elle ne décide
JAMAIS quoi faire, quel outil utiliser, quelle tâche créer, quel modèle
utiliser — elle appelle `VAD.process()`, accumule un segment de parole,
appelle `STT.transcribe_final()` UNE FOIS le segment terminé, et transmet le
texte à `VoiceChannel` (qui, lui, parle au Harness). Aucun appel LLM par
chunk audio/VAD (consigne §26).

`process_one_chunk()` est exposé séparément de `run_forever()` pour un
contrôle déterministe en test (pas de thread, pas de timing réel à
synchroniser) — `run_forever()` est la boucle réelle utilisée en production."""

from __future__ import annotations

import threading
import time

from .audio.base import AudioInput
from .channel import VoiceChannel
from .events import publish_voice_event
from .stt.base import STTError, SpeechToText
from .vad.base import VADState, VoiceActivityDetector


class VoiceRuntime:
    def __init__(self, audio_input: AudioInput, vad: VoiceActivityDetector, stt: SpeechToText,
                 channel: VoiceChannel) -> None:
        self._audio = audio_input
        self._vad = vad
        self._stt = stt
        self._channel = channel
        self._buffer = bytearray()
        self._sample_rate = 16_000
        self._running = False
        self._thread: threading.Thread | None = None

    def process_one_chunk(self, timeout: float = 1.0) -> VADState | None:
        chunk = self._audio.read_chunk(timeout=timeout)
        if chunk is None:
            return None
        self._sample_rate = chunk.sample_rate
        state = self._vad.process(chunk)

        if state == VADState.SPEECH_START:
            self._buffer = bytearray(chunk.data)
            if self._channel.tts_is_speaking():
                self._channel.barge_in()
            self._channel.presence.set_user_speaking(True)
            self._channel.presence.set_listening(True)
            publish_voice_event(self._channel.bus, "voice.speech_started", self._channel.session.session_id,
                                 correlation_id=self._channel.session.correlation_id)
        elif state == VADState.SPEECH:
            self._buffer.extend(chunk.data)
        elif state == VADState.SPEECH_END:
            self._buffer.extend(chunk.data)
            self._channel.presence.set_user_speaking(False)
            self._channel.presence.set_listening(False)
            segment = bytes(self._buffer)
            self._buffer = bytearray()
            self._finalize_segment(segment)

        return state

    def _finalize_segment(self, segment: bytes) -> None:
        try:
            result = self._stt.transcribe_final(segment, self._sample_rate)
        except STTError as exc:
            publish_voice_event(self._channel.bus, "voice.stt_error", self._channel.session.session_id,
                                 correlation_id=self._channel.session.correlation_id, detail={"code": exc.code, "message": exc.message})
            return
        if result.no_speech or not result.text.strip():
            publish_voice_event(self._channel.bus, "voice.no_speech", self._channel.session.session_id,
                                 correlation_id=self._channel.session.correlation_id)
            return
        self._channel.handle_final_transcript(result.text, confidence=result.confidence, language=result.language)

    def start(self) -> None:
        if self._running:
            return
        self._audio.start()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="raya-voice-runtime")
        self._thread.start()

    def _loop(self) -> None:
        while self._running:
            self.process_one_chunk(timeout=0.5)

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        self._audio.stop()
