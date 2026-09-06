"""SileroVADAdapter — EXTRACT du mécanisme d'inférence Silero de
modules/voice/stt.py (`_vad_prob()`), sans la capture/transcription/echo-gate
mêlées dans le même fichier V1. Hystérésis identique à `EnergyVAD` (seuil
plus haut pour démarrer, plus bas pour continuer)."""

from __future__ import annotations

from .base import AudioChunk, VADState, VoiceActivityDetector


class SileroVADAdapter(VoiceActivityDetector):
    def __init__(self, start_threshold: float = 0.5, continue_threshold: float = 0.35,
                 silence_chunks_to_end: int = 8, sample_rate: int = 16_000) -> None:
        self._start_threshold = start_threshold
        self._continue_threshold = continue_threshold
        self._silence_chunks_to_end = silence_chunks_to_end
        self._sample_rate = sample_rate
        self._speaking = False
        self._silence_run = 0
        self._model = None

    def _ensure_model(self):
        if self._model is None:
            import silero_vad

            self._model = silero_vad.load_silero_vad()
        return self._model

    def _probability(self, chunk: AudioChunk) -> float:
        import numpy as np
        import torch

        model = self._ensure_model()
        samples = np.frombuffer(chunk.data, dtype=np.int16).astype(np.float32) / 32768.0
        tensor = torch.from_numpy(samples)
        with torch.no_grad():
            return float(model(tensor, self._sample_rate).item())

    def process(self, chunk: AudioChunk) -> VADState:
        prob = self._probability(chunk)
        if not self._speaking:
            if prob >= self._start_threshold:
                self._speaking = True
                self._silence_run = 0
                return VADState.SPEECH_START
            return VADState.SILENCE
        if prob >= self._continue_threshold:
            self._silence_run = 0
            return VADState.SPEECH
        self._silence_run += 1
        if self._silence_run >= self._silence_chunks_to_end:
            self._speaking = False
            self._silence_run = 0
            return VADState.SPEECH_END
        return VADState.SPEECH

    def reset(self) -> None:
        self._speaking = False
        self._silence_run = 0


def silero_available() -> bool:
    try:
        import silero_vad  # noqa: F401
        import torch  # noqa: F401

        return True
    except Exception:
        return False
