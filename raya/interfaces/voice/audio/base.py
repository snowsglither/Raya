"""AudioInput — abstraction de capture audio (consigne Phase 5 §2).

Type interne à `interfaces/voice/` (pas un contrat `raya/contracts/` — ces
types ne traversent jamais l'EventBus, contrairement à `VoiceEvent`). Doit
fonctionner sans microphone réel en test unitaire (`FakeAudioInput`) ; les
tests d'intégration utilisent un vrai périphérique quand disponible
(`SounddeviceAudioInput`).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AudioChunk:
    data: bytes
    timestamp: float
    sample_rate: int
    channels: int = 1


@dataclass
class AudioDeviceInfo:
    name: str
    sample_rate: int
    channels: int
    is_real_hardware: bool


class AudioInput(ABC):
    @abstractmethod
    def start(self) -> None: ...

    @abstractmethod
    def stop(self) -> None: ...

    @abstractmethod
    def pause(self) -> None: ...

    @abstractmethod
    def resume(self) -> None: ...

    @abstractmethod
    def read_chunk(self, timeout: float | None = 1.0) -> AudioChunk | None:
        """Bloque jusqu'à `timeout`s pour le prochain chunk ; None si rien
        (timeout, en pause, ou arrêté) — jamais une exception pour un cas normal."""
        ...

    @abstractmethod
    def device_info(self) -> AudioDeviceInfo: ...

    @abstractmethod
    def is_active(self) -> bool: ...


@dataclass
class _FakeState:
    active: bool = False
    paused: bool = False


class FakeAudioInput(AudioInput):
    """Source audio scriptée — chunks fournis par le test, jamais de vrai
    matériel. `push_chunk`/`push_silence` alimentent la file consommée par
    `read_chunk`. Utilisé par tous les tests unitaires VAD/STT/barge-in qui
    n'ont pas besoin d'un vrai microphone (consigne §2 : "le système doit
    fonctionner sans microphone réel dans les tests unitaires")."""

    def __init__(self, sample_rate: int = 16_000) -> None:
        self._sample_rate = sample_rate
        self._chunks: list[AudioChunk] = []
        self._cursor = 0
        self._state = _FakeState()

    def start(self) -> None:
        self._state.active = True

    def stop(self) -> None:
        self._state.active = False

    def pause(self) -> None:
        self._state.paused = True

    def resume(self) -> None:
        self._state.paused = False

    def push_chunk(self, data: bytes) -> None:
        self._chunks.append(AudioChunk(data=data, timestamp=time.monotonic(), sample_rate=self._sample_rate))

    def read_chunk(self, timeout: float | None = 1.0) -> AudioChunk | None:
        if not self._state.active or self._state.paused:
            return None
        if self._cursor >= len(self._chunks):
            return None
        chunk = self._chunks[self._cursor]
        self._cursor += 1
        return chunk

    def device_info(self) -> AudioDeviceInfo:
        return AudioDeviceInfo(name="fake", sample_rate=self._sample_rate, channels=1, is_real_hardware=False)

    def is_active(self) -> bool:
        return self._state.active
