"""SounddeviceAudioInput — EXTRACT du mécanisme de capture de
modules/voice/stt.py (V1, `sounddevice.InputStream`), sans le VAD/Whisper/
prints/echo-gate mêlés dans le même fichier V1 (RAYA_V2_MIGRATION_MAP §Phase 5 :
"mécanismes purs... ce ne sont pas des cerveaux"). Callback → `queue.Queue`
→ `read_chunk()`, jamais de blocage du thread appelant au-delà du timeout
demandé."""

from __future__ import annotations

import queue
import time

from .base import AudioChunk, AudioDeviceInfo, AudioInput

_DEFAULT_SAMPLE_RATE = 16_000
_DEFAULT_BLOCKSIZE = 512  # 32ms à 16kHz — compatible Silero VAD (256/512/768/1024/1536)


class SounddeviceAudioInput(AudioInput):
    def __init__(self, sample_rate: int = _DEFAULT_SAMPLE_RATE, blocksize: int = _DEFAULT_BLOCKSIZE,
                 device: int | str | None = None) -> None:
        self._sample_rate = sample_rate
        self._blocksize = blocksize
        self._device = device
        self._queue: "queue.Queue[AudioChunk]" = queue.Queue(maxsize=200)
        self._stream = None
        self._active = False
        self._paused = False

    def _callback(self, indata, frames, time_info, status) -> None:  # signature imposée par sounddevice
        if self._paused:
            return
        try:
            self._queue.put_nowait(AudioChunk(data=bytes(indata), timestamp=time.monotonic(), sample_rate=self._sample_rate))
        except queue.Full:
            pass  # chunk perdu plutôt que de bloquer le driver audio (mieux vaut un trou qu'un blocage matériel)

    def start(self) -> None:
        import sounddevice as sd

        if self._active:
            return
        self._stream = sd.InputStream(
            samplerate=self._sample_rate, blocksize=self._blocksize, channels=1, dtype="int16",
            device=self._device, callback=self._callback,
        )
        self._stream.start()
        self._active = True

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._active = False

    def pause(self) -> None:
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def read_chunk(self, timeout: float | None = 1.0) -> AudioChunk | None:
        if not self._active:
            return None
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def device_info(self) -> AudioDeviceInfo:
        import sounddevice as sd

        try:
            name = sd.query_devices(self._device, "input")["name"] if self._device is not None else sd.query_devices(kind="input")["name"]
        except Exception:
            name = "unknown"
        return AudioDeviceInfo(name=name, sample_rate=self._sample_rate, channels=1, is_real_hardware=True)

    def is_active(self) -> bool:
        return self._active


def real_microphone_available() -> bool:
    """Vrai check matériel (consigne §2/§29) — ne devine jamais, interroge
    réellement l'API audio. Utilisé pour distinguer PASS (architecture) de
    BLOCKED (intégration matérielle) dans les tests."""
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        return any(d.get("max_input_channels", 0) > 0 for d in devices)
    except Exception:
        return False
