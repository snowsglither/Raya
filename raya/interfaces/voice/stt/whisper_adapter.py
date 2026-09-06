"""WhisperSTTAdapter — EXTRACT du mécanisme de transcription de
modules/voice/stt.py (`_transcribe()`, faster-whisper), sans la capture/VAD/
echo-gate/prints mêlés dans le même fichier V1. Le nom "Whisper" n'apparaît
que dans ce fichier — le reste du système ne voit que `SpeechToText`
(consigne §4 : "Ne hardcode pas Whisper dans tout le système")."""

from __future__ import annotations

import numpy as np

from .base import STTError, SpeechToText, TranscriptionResult

_DEFAULT_MODEL_SIZE = "base"


class WhisperSTTAdapter(SpeechToText):
    """`device="auto"` par défaut chez faster-whisper choisit CUDA si un GPU
    est détecté — mais un GPU détecté sans runtime cuBLAS installé (cas réel
    rencontré sur cette machine, cf. rapport Phase 5 §Bugs) fait planter
    l'inférence. Repli PERMANENT vers CPU à la première erreur d'inférence
    liée au GPU — même mécanisme que V1 (`modules/voice/stt.py`:
    "GPU→CPU permanent fallback on inference failure"), jamais un retry
    aveugle à chaque appel."""

    def __init__(self, model_size: str = _DEFAULT_MODEL_SIZE, device: str = "auto", compute_type: str = "default",
                 language: str | None = None, silence_rms_threshold: float = 0.003) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type
        self._language = language  # None = auto-détection (moins fiable sur un clip court/quantifié, bug réel observé — voir rapport)
        self._silence_rms_threshold = silence_rms_threshold
        self._model = None
        self._cancelled = False
        self._forced_cpu = False

    def _ensure_model(self, force_cpu: bool = False):
        if force_cpu:
            self._forced_cpu = True
            self._model = None
        if self._model is None:
            from faster_whisper import WhisperModel

            device = "cpu" if self._forced_cpu else self._device
            compute_type = "int8" if self._forced_cpu and self._compute_type == "default" else self._compute_type
            self._model = WhisperModel(self._model_size, device=device, compute_type=compute_type)
        return self._model

    def transcribe_final(self, pcm_bytes: bytes, sample_rate: int) -> TranscriptionResult:
        self._cancelled = False
        if not pcm_bytes:
            return TranscriptionResult(text="", is_final=True, no_speech=True)
        audio = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        rms = float(np.sqrt(np.mean(audio ** 2))) if audio.size else 0.0
        if rms < self._silence_rms_threshold:
            # Court-circuit AVANT tout appel modèle : Whisper peut halluciner
            # du texte sur du silence quasi pur (comportement réel observé,
            # cf. rapport §Bugs) — un silence mesurable ne doit jamais
            # produire un texte inventé.
            return TranscriptionResult(text="", is_final=True, no_speech=True)
        try:
            model = self._ensure_model()
            return self._run_inference(model, audio)
        except STTError:
            raise
        except Exception as exc:
            if self._forced_cpu:
                raise STTError("WHISPER_INFERENCE_ERROR", str(exc)) from exc
            # repli GPU->CPU permanent (une seule fois), pas un retry par appel
            try:
                model = self._ensure_model(force_cpu=True)
                return self._run_inference(model, audio)
            except Exception as exc2:
                raise STTError("WHISPER_INFERENCE_ERROR", str(exc2)) from exc2

    def _run_inference(self, model, audio: "np.ndarray") -> TranscriptionResult:
        segments, info = model.transcribe(audio, language=self._language, vad_filter=False)
        texts: list[str] = []
        avg_logprobs: list[float] = []
        for seg in segments:
            if self._cancelled:
                raise STTError("CANCELLED", "transcription annulée en cours de segmentation")
            texts.append(seg.text)
            avg_logprobs.append(getattr(seg, "avg_logprob", 0.0))
        text = "".join(texts).strip()
        if not text:
            return TranscriptionResult(text="", is_final=True, language=info.language, no_speech=True)
        confidence = float(np.exp(np.mean(avg_logprobs))) if avg_logprobs else None
        return TranscriptionResult(text=text, is_final=True, confidence=confidence, language=info.language, no_speech=False)

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def supports_partial(self) -> bool:
        return False  # honnête : faster-whisper transcrit un segment complet, pas de streaming token-à-token


def whisper_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:
        return False
