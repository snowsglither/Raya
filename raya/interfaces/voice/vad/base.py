"""VoiceActivityDetector — abstraction événementielle (consigne Phase 5 §3).

RÈGLE ABSOLUE : le VAD ne doit JAMAIS appeler le LLM, créer une tâche,
exécuter un outil, ni décider seul d'interrompre une tâche métier — il
publie des événements (transitions d'état), rien de plus. Cette classe et
ses implémentations n'importent donc que `..audio` (types locaux) — aucune
dépendance vers `raya.cognition`/`raya.tasks`/`raya.tools`."""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod

from ..audio.base import AudioChunk


class VADState(str, enum.Enum):
    SILENCE = "silence"
    SPEECH_START = "speech_start"
    SPEECH = "speech"
    SPEECH_END = "speech_end"


class VoiceActivityDetector(ABC):
    @abstractmethod
    def process(self, chunk: AudioChunk) -> VADState:
        """Traite UN chunk et renvoie la transition d'état réelle — jamais
        un état deviné, toujours dérivé du signal reçu."""
        ...

    @abstractmethod
    def reset(self) -> None: ...


class EnergyVAD(VoiceActivityDetector):
    """VAD par énergie RMS — mécanisme simple, réel, déterministe (repli
    "RMS-only" déjà présent en V1 quand torch/Silero est indisponible).
    Hystérésis : seuil plus haut pour DÉMARRER, plus bas pour CONTINUER
    (repris de modules/voice/stt.py — évite les faux départs/faux arrêts
    sur un flux légèrement bruité)."""

    def __init__(self, start_threshold: float = 500.0, continue_threshold: float = 250.0,
                 silence_chunks_to_end: int = 8) -> None:
        self._start_threshold = start_threshold
        self._continue_threshold = continue_threshold
        self._silence_chunks_to_end = silence_chunks_to_end
        self._speaking = False
        self._silence_run = 0

    @staticmethod
    def _rms(data: bytes) -> float:
        import array

        if not data:
            return 0.0
        samples = array.array("h", data)  # int16 mono
        if not samples:
            return 0.0
        return (sum(s * s for s in samples) / len(samples)) ** 0.5

    def process(self, chunk: AudioChunk) -> VADState:
        energy = self._rms(chunk.data)
        if not self._speaking:
            if energy >= self._start_threshold:
                self._speaking = True
                self._silence_run = 0
                return VADState.SPEECH_START
            return VADState.SILENCE
        if energy >= self._continue_threshold:
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
