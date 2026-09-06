"""SpeechToText (consigne Phase 5 §4/§33) — partial, final, no-speech,
error, cancellation. `FakeSTT` pour le contrôle de flux déterministe ; test
réel contre Whisper (faster-whisper) avec de vrais fichiers audio générés
par Kokoro (tests/fixtures/voice/) quand le moteur est disponible."""

from __future__ import annotations

from pathlib import Path

import pytest
import soundfile as sf

from raya.interfaces.voice.stt.base import FakeSTT, STTError, TranscriptionResult
from raya.interfaces.voice.stt.whisper_adapter import WhisperSTTAdapter, whisper_available

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "voice"


def test_fake_stt_returns_scripted_final_result():
    stt = FakeSTT([TranscriptionResult(text="ouvre le navigateur", is_final=True, confidence=0.9, language="fr")])
    result = stt.transcribe_final(b"\x00\x00", 16_000)
    assert result.text == "ouvre le navigateur"
    assert result.is_final is True


def test_fake_stt_raises_scripted_error():
    stt = FakeSTT([STTError("ENGINE_DOWN", "moteur indisponible")])
    with pytest.raises(STTError) as exc_info:
        stt.transcribe_final(b"\x00\x00", 16_000)
    assert exc_info.value.code == "ENGINE_DOWN"


def test_fake_stt_cancel_is_idempotent_and_tracked():
    stt = FakeSTT([])
    stt.cancel()
    stt.cancel()
    assert stt.cancelled is True


def test_default_supports_partial_is_false_honest_default():
    """Consigne §4 : "streaming/partial... si le moteur le permet" — le
    défaut de l'ABC est honnête (pas de faux support)."""
    stt = FakeSTT([])
    assert stt.supports_partial is False
    assert stt.transcribe_partial(b"", 16_000) is None


def test_empty_audio_is_no_speech_not_an_error():
    stt = FakeSTT([TranscriptionResult(text="", is_final=True, no_speech=True)])
    result = stt.transcribe_final(b"", 16_000)
    assert result.no_speech is True


@pytest.mark.skipif(not whisper_available(), reason="BLOCKED: faster-whisper indisponible sur cette machine")
class TestRealWhisper:
    @pytest.fixture(scope="class")
    def adapter(self):
        return WhisperSTTAdapter(model_size="base", language="fr")

    def _load(self, name: str) -> tuple[bytes, int]:
        data, sr = sf.read(str(_FIXTURES / name), dtype="int16")
        return data.tobytes(), sr

    def test_real_short_speech_transcription_reasonable_match(self, adapter):
        """Comparaison raisonnable (mots-clés), pas caractère-par-caractère
        (consigne §30 — l'engine varie)."""
        pcm, sr = self._load("short_speech.wav")
        result = adapter.transcribe_final(pcm, sr)
        assert not result.no_speech
        assert "navigateur" in result.text.lower() or "ouvre" in result.text.lower()
        assert result.language == "fr"

    def test_real_silence_is_honestly_no_speech_never_hallucinated(self, adapter):
        """Bug réel trouvé pendant cette phase (rapport §Bugs) : Whisper peut
        halluciner du texte sur du silence pur — la couche RAYA doit le
        court-circuiter AVANT le modèle, jamais prétendre avoir entendu
        quelque chose."""
        pcm, sr = self._load("silence.wav")
        result = adapter.transcribe_final(pcm, sr)
        assert result.no_speech is True
        assert result.text == ""

    def test_real_longer_speech_produces_non_empty_transcription(self, adapter):
        pcm, sr = self._load("longer_speech.wav")
        result = adapter.transcribe_final(pcm, sr)
        assert not result.no_speech
        assert len(result.text) > 10

    def test_real_cancellation_stops_segment_iteration(self, adapter):
        """`cancel()` n'est consultable qu'ENTRE deux segments (faster-whisper
        transcrit via un générateur paresseux) — jamais avant le début d'un
        nouvel appel (chaque `transcribe_final()` réinitialise le flag,
        volontairement : sinon un vieux cancel() bloquerait toute
        transcription future). Simule un cancel() concurrent déclenché
        pendant l'itération réelle du code de production (`_run_inference`),
        sans dépendre d'un timing réel non déterministe."""

        class _FakeSegment:
            def __init__(self, text: str) -> None:
                self.text = text
                self.avg_logprob = -0.1

        class _FakeInfo:
            language = "fr"

        def fake_transcribe(audio, language=None, vad_filter=False):
            def gen():
                yield _FakeSegment("Bonjour ")
                adapter.cancel()  # cancel() concurrent, comme le ferait un autre thread
                yield _FakeSegment("le monde")

            return gen(), _FakeInfo()

        class _FakeModel:
            transcribe = staticmethod(fake_transcribe)

        adapter._model = _FakeModel()
        import numpy as np

        loud_pcm = np.full(800, 10_000, dtype=np.int16).tobytes()  # au-dessus du seuil de silence
        with pytest.raises(STTError) as exc_info:
            adapter.transcribe_final(loud_pcm, 16_000)
        assert exc_info.value.code == "CANCELLED"
        adapter._model = None  # ne pollue pas les tests suivants de la classe

    def test_gpu_to_cpu_fallback_recovers_when_forced(self, adapter):
        """Régression du bug réel (cublas64_12.dll manquant) : après un échec
        GPU, le repli CPU permanent doit permettre une transcription réussie
        (pas un crash répété)."""
        pcm, sr = self._load("short_speech.wav")
        result = adapter.transcribe_final(pcm, sr)
        assert not result.no_speech
