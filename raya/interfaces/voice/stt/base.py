"""SpeechToText — abstraction (consigne Phase 5 §4). Le Harness ne voit
jamais Whisper directement — uniquement cette abstraction, via un adapter
(ex: `WhisperSTTAdapter`)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class TranscriptionResult:
    text: str
    is_final: bool
    confidence: float | None = None
    language: str | None = None
    no_speech: bool = False


class STTError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SpeechToText(ABC):
    @abstractmethod
    def transcribe_final(self, pcm_bytes: bytes, sample_rate: int) -> TranscriptionResult:
        """Transcription complète et définitive d'un segment de parole déjà
        borné par le VAD (SPEECH_START..SPEECH_END). Lève `STTError` en cas
        d'échec réel — ne renvoie jamais un texte inventé."""
        ...

    def transcribe_partial(self, pcm_bytes: bytes, sample_rate: int) -> TranscriptionResult | None:
        """Optionnel — certains moteurs ne le permettent pas (consigne §4 :
        "streaming/partial... si le moteur le permet"). Défaut honnête :
        aucun support (None), pas un résultat inventé."""
        return None

    @abstractmethod
    def cancel(self) -> None: ...

    @property
    def supports_partial(self) -> bool:
        return False


class FakeSTT(SpeechToText):
    """Résultats scriptés — aucune dépendance à un vrai moteur, pour les
    tests unitaires du pipeline (VAD->STT->Events->Policy) indépendamment
    de Whisper."""

    def __init__(self, script: list[TranscriptionResult | Exception]) -> None:
        self._script = list(script)
        self.cancelled = False
        self.calls: list[bytes] = []

    def transcribe_final(self, pcm_bytes: bytes, sample_rate: int) -> TranscriptionResult:
        self.calls.append(pcm_bytes)
        if not self._script:
            raise STTError("SCRIPT_EXHAUSTED", "FakeSTT: script épuisé")
        entry = self._script.pop(0)
        if isinstance(entry, Exception):
            raise entry
        return entry

    def cancel(self) -> None:
        self.cancelled = True
