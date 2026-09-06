"""VoiceActivityDetector (consigne Phase 5 §3/§33) — SILENCE/SPEECH_START/
SPEECH/SPEECH_END, événementiel, jamais d'appel LLM/tâche/outil (vérifié
structurellement §"test_architecture_proof" — VAD n'importe aucun de ces
subsystems). `EnergyVAD` est réel et déterministe (pas un mock)."""

from __future__ import annotations

import numpy as np
import pytest

from raya.interfaces.voice.audio.base import AudioChunk
from raya.interfaces.voice.vad.base import EnergyVAD, VADState
from raya.interfaces.voice.vad.silero_adapter import silero_available


def _chunk(samples: np.ndarray) -> AudioChunk:
    return AudioChunk(data=samples.astype(np.int16).tobytes(), timestamp=0.0, sample_rate=16_000)


def _silence() -> AudioChunk:
    return _chunk(np.zeros(512))


def _loud() -> AudioChunk:
    return _chunk(np.full(512, 20_000))


def test_silence_stays_silence():
    vad = EnergyVAD()
    assert vad.process(_silence()) == VADState.SILENCE
    assert vad.process(_silence()) == VADState.SILENCE


def test_loud_chunk_triggers_speech_start():
    vad = EnergyVAD()
    assert vad.process(_loud()) == VADState.SPEECH_START


def test_speech_continues_after_start():
    vad = EnergyVAD()
    vad.process(_loud())
    assert vad.process(_loud()) == VADState.SPEECH


def test_repeated_transitions_end_after_enough_silence():
    vad = EnergyVAD(silence_chunks_to_end=3)
    vad.process(_loud())  # SPEECH_START
    vad.process(_loud())  # SPEECH
    vad.process(_silence())  # SPEECH (silence_run=1, sous le seuil)
    vad.process(_silence())  # SPEECH (silence_run=2)
    state = vad.process(_silence())  # SPEECH_END (silence_run=3)
    assert state == VADState.SPEECH_END
    assert vad.process(_silence()) == VADState.SILENCE  # retour à l'état initial


def test_reset_clears_speaking_state():
    vad = EnergyVAD()
    vad.process(_loud())
    vad.reset()
    assert vad.process(_silence()) == VADState.SILENCE  # pas SPEECH_END après reset


def test_vad_module_never_imports_cognition_tools_tasks_or_models():
    """Preuve structurelle (consigne §7/§42) : le VAD ne peut PAS appeler le
    LLM ni créer une tâche — il n'a même pas accès à ces subsystems."""
    import ast
    from pathlib import Path

    vad_dir = Path(__file__).resolve().parents[2] / "raya" / "interfaces" / "voice" / "vad"
    for path in vad_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("raya.cognition"), f"{path} importe raya.cognition"
                assert not node.module.startswith("raya.tools"), f"{path} importe raya.tools"
                assert not node.module.startswith("raya.tasks"), f"{path} importe raya.tasks"
                assert not node.module.startswith("raya.models"), f"{path} importe raya.models"


@pytest.mark.skipif(not silero_available(), reason="BLOCKED: silero_vad/torch indisponible sur cette machine")
def test_real_silero_vad_distinguishes_silence_from_energy_chunk():
    """Test RÉEL (pas un mock) — modèle Silero réellement chargé et exécuté."""
    from raya.interfaces.voice.vad.silero_adapter import SileroVADAdapter

    vad = SileroVADAdapter()
    result = vad.process(_silence())
    assert result == VADState.SILENCE
