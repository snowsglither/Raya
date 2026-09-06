"""KokoroTTSAdapter — EXTRACT du mécanisme de streaming de
modules/voice/tts_kokoro.py : thread producteur (synthèse phrase par
phrase) → `queue.Queue` bornée → thread consommateur qui joue via UN SEUL
`sounddevice.OutputStream` continu, écritures en petits blocs pour que
`cancel()` (abort()) réponde en dizaines de millisecondes, pas en secondes.
Le nom "Kokoro" n'apparaît que dans ce fichier (consigne §10)."""

from __future__ import annotations

import queue
import re
import threading
from pathlib import Path
from typing import Callable

from .base import SpeechSynthesizer, TTSState

_WRITE_CHUNK = 1024
_DEFAULT_MODEL_DIR = Path.home() / ".cache" / "kokoro-onnx"

# Mapping DONNÉE (pas de l'intelligence, cf. consigne Phase 3 §6 "pas de
# règle métier gravée") langue -> (voix Kokoro, code langue espeak). Kokoro
# ne couvre pas toutes les langues (ex: néerlandais absent de son catalogue
# de voix réel, vérifié à l'implémentation) — `_FALLBACK_LANGUAGE` documente
# le repli explicite, jamais silencieux (consigne "TTS LANGUAGE ROUTING").
_VOICE_BY_LANGUAGE: dict[str, tuple[str, str]] = {
    "fr": ("ff_siwis", "fr-fr"),
    "en": ("af_sarah", "en-us"),
    "es": ("ef_dora", "es"),
}
_FALLBACK_LANGUAGE = "en"


def _split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?…])\s+", text.strip())
    return [p for p in parts if p.strip()] or [text]


def _resolve_voice(language: str | None) -> tuple[str, str, str, bool]:
    """-> (voice, espeak_lang, language_used, fallback_applied)."""
    if language and language in _VOICE_BY_LANGUAGE:
        voice, espeak_lang = _VOICE_BY_LANGUAGE[language]
        return voice, espeak_lang, language, False
    voice, espeak_lang = _VOICE_BY_LANGUAGE[_FALLBACK_LANGUAGE]
    return voice, espeak_lang, _FALLBACK_LANGUAGE, bool(language)


class KokoroTTSAdapter(SpeechSynthesizer):
    def __init__(self, speed: float = 1.0, model_path: Path | None = None, voices_path: Path | None = None) -> None:
        self._speed = speed
        self._model_path = model_path or (_DEFAULT_MODEL_DIR / "kokoro-v1.0.onnx")
        self._voices_path = voices_path or (_DEFAULT_MODEL_DIR / "voices-v1.0.bin")
        self._kokoro = None
        self._state = TTSState.IDLE
        self._cancel_event = threading.Event()
        self._stream = None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self.last_language_requested: str | None = None
        self.last_language_used: str | None = None
        self.last_language_fallback: bool = False

    def _ensure_engine(self):
        if self._kokoro is None:
            from kokoro_onnx import Kokoro

            self._kokoro = Kokoro(str(self._model_path), str(self._voices_path))
        return self._kokoro

    def speak(self, text: str, language: str | None = None,
              on_complete: Callable[[TTSState], None] | None = None) -> None:
        voice, espeak_lang, language_used, fallback = _resolve_voice(language)
        self.last_language_requested = language
        self.last_language_used = language_used
        self.last_language_fallback = fallback
        with self._lock:
            if self._state == TTSState.SPEAKING:
                self.cancel()  # une nouvelle synthèse remplace toujours l'ancienne, jamais superposée
            self._cancel_event = threading.Event()
            self._state = TTSState.SPEAKING
        self._thread = threading.Thread(
            target=self._run, args=(text, voice, espeak_lang, self._cancel_event, on_complete), daemon=True,
        )
        self._thread.start()

    def _run(self, text: str, voice: str, espeak_lang: str, cancel_event: threading.Event,
              on_complete: Callable[[TTSState], None] | None) -> None:
        import sounddevice as sd

        final_state = TTSState.COMPLETED
        try:
            engine = self._ensure_engine()
            sample_rate = 24_000
            stream = sd.OutputStream(samplerate=sample_rate, channels=1, dtype="float32")
            with self._lock:
                self._stream = stream
            stream.start()
            try:
                for sentence in _split_sentences(text):
                    if cancel_event.is_set():
                        final_state = TTSState.CANCELLED
                        break
                    audio, sr = engine.create(sentence, voice=voice, speed=self._speed, lang=espeak_lang)
                    for i in range(0, len(audio), _WRITE_CHUNK):
                        if cancel_event.is_set():
                            final_state = TTSState.CANCELLED
                            break
                        stream.write(audio[i:i + _WRITE_CHUNK].reshape(-1, 1))
                    if cancel_event.is_set():
                        final_state = TTSState.CANCELLED
                        break
            finally:
                try:
                    if cancel_event.is_set():
                        stream.abort()
                    else:
                        stream.stop()
                    stream.close()
                except Exception:
                    pass
        except Exception:
            final_state = TTSState.ERROR
        with self._lock:
            self._state = final_state
            self._stream = None
        if on_complete:
            on_complete(final_state)

    def cancel(self) -> None:
        with self._lock:
            if self._state != TTSState.SPEAKING:
                return  # idempotent (consigne §20 double cancellation)
            self._state = TTSState.INTERRUPTING
            self._cancel_event.set()
            stream = self._stream
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass

    def is_speaking(self) -> bool:
        with self._lock:
            return self._state == TTSState.SPEAKING

    def state(self) -> TTSState:
        with self._lock:
            return self._state


def kokoro_available() -> bool:
    try:
        import kokoro_onnx  # noqa: F401
        import sounddevice  # noqa: F401

        model = _DEFAULT_MODEL_DIR / "kokoro-v1.0.onnx"
        voices = _DEFAULT_MODEL_DIR / "voices-v1.0.bin"
        return model.exists() and voices.exists()
    except Exception:
        return False
