"""Câblage des adapters RÉELS (Whisper/Kokoro/Silero/sounddevice) — délibérément
SÉPARÉ de `raya/runtime/bootstrap.py` : charger un modèle Whisper/Kokoro
prend plusieurs secondes et nécessite du matériel audio réel, ce que
`bootstrap()` (appelé par CHAQUE test Phase 0-4) ne doit jamais payer. La
voix reste opt-in, construite explicitement par un appelant qui en a
vraiment besoin (CLI voix, test E2E réel) — jamais chargée par défaut.

Composition-root miniature, symétrique à `runtime/bootstrap.py` (même
principe : câblage pur, aucune logique métier)."""

from __future__ import annotations

from typing import Protocol

from raya.event_bus import EventBus
from raya.harness import Harness

from .audio.base import AudioInput
from .audio.sounddevice_input import SounddeviceAudioInput
from .channel import VoiceChannel
from .runtime import VoiceRuntime
from .stt.base import SpeechToText
from .stt.whisper_adapter import WhisperSTTAdapter
from .tts.base import SpeechSynthesizer
from .tts.kokoro_adapter import KokoroTTSAdapter
from .vad.base import VoiceActivityDetector
from .vad.silero_adapter import SileroVADAdapter


class _RuntimeHandlesLike(Protocol):
    """Duck-typing local plutôt qu'un import `raya.runtime` (interdit :
    `runtime/` est la racine de composition, en dépendre depuis `interfaces/`
    créerait une dépendance ascendante — RAYA_V2_REPOSITORY_STRUCTURE.md §20).
    N'importe quel objet portant `.harness`/`.bus` convient (RuntimeHandles
    en production, un simple objet dans les tests)."""

    harness: Harness
    bus: EventBus


def build_real_voice_runtime(
    handles: _RuntimeHandlesLike, *, session_id: str = "voice-main",
    audio_input: AudioInput | None = None, vad: VoiceActivityDetector | None = None,
    stt: SpeechToText | None = None, tts: SpeechSynthesizer | None = None,
    language: str | None = "fr",
) -> tuple[VoiceChannel, VoiceRuntime]:
    """Construit une chaîne voix RÉELLE (ou partiellement injectée pour les
    tests) branchée sur un runtime déjà démarré (`bootstrap()`). Ne démarre
    RIEN elle-même — l'appelant décide quand `runtime.start()`."""
    audio_input = audio_input or SounddeviceAudioInput()
    vad = vad or SileroVADAdapter()
    stt = stt or WhisperSTTAdapter(language=language)
    tts = tts or KokoroTTSAdapter()

    channel = VoiceChannel(handles.harness, handles.bus, tts, session_id=session_id)
    runtime = VoiceRuntime(audio_input, vad, stt, channel)
    return channel, runtime
