"""AudioInput (consigne Phase 5 §2/§33) — lifecycle, cancellation, device
unavailable. `FakeAudioInput` prouve que le système fonctionne sans
microphone réel en test unitaire ; `real_microphone_available()` distingue
honnêtement PASS (architecture) de BLOCKED (matériel)."""

from __future__ import annotations

from raya.interfaces.voice.audio.base import AudioChunk, FakeAudioInput
from raya.interfaces.voice.audio.sounddevice_input import real_microphone_available


def test_lifecycle_start_stop():
    audio = FakeAudioInput()
    assert audio.is_active() is False
    audio.start()
    assert audio.is_active() is True
    audio.stop()
    assert audio.is_active() is False


def test_pause_resume_blocks_and_unblocks_chunks():
    audio = FakeAudioInput()
    audio.start()
    audio.push_chunk(b"\x00\x00" * 100)
    audio.pause()
    assert audio.read_chunk() is None  # en pause -> rien, jamais une exception
    audio.resume()
    chunk = audio.read_chunk()
    assert isinstance(chunk, AudioChunk)


def test_stop_is_a_form_of_cancellation_no_more_chunks():
    audio = FakeAudioInput()
    audio.start()
    audio.push_chunk(b"\x00\x00")
    audio.stop()
    assert audio.read_chunk() is None


def test_read_chunk_never_raises_on_empty_queue():
    audio = FakeAudioInput()
    audio.start()
    assert audio.read_chunk(timeout=0.01) is None


def test_device_info_reports_fake_not_real_hardware():
    audio = FakeAudioInput()
    info = audio.device_info()
    assert info.is_real_hardware is False
    assert info.sample_rate == 16_000


def test_real_microphone_availability_check_never_crashes():
    """Ne devine jamais — interroge réellement l'API audio. Le résultat
    (True/False) sert à distinguer PASS/BLOCKED dans les tests d'intégration,
    jamais à transformer BLOCKED en PASS (consigne §44)."""
    result = real_microphone_available()
    assert isinstance(result, bool)
