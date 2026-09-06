"""VoiceResponsePolicy (consigne Phase 5 §13) — SPEAK / TEXT_ONLY / SILENT.

Fonction PURE, déterministe, sans I/O ni LLM : prend en entrée l'état déjà
connu (décision Attention, focus utilisateur, état de tâche, urgence,
importance, interruption, capacité TTS) et renvoie une décision. Aucune
liste de phrases ("if 'merci': ..."), conformément à la consigne."""

from __future__ import annotations

import enum
from dataclasses import dataclass


class VoiceResponseDecision(str, enum.Enum):
    SPEAK = "speak"
    TEXT_ONLY = "text_only"
    SILENT = "silent"


@dataclass
class VoiceResponseContext:
    has_response_text: bool = True
    attention_decision: str | None = None  # "PROCESS_NOW" | "BACKGROUND" | "INTERRUPT" | "IGNORE" | None
    user_is_focus: bool = True
    tts_capable: bool = True
    urgency: float = 0.0
    importance: float = 0.0
    interrupted_by_user: bool = False


def decide_voice_response(ctx: VoiceResponseContext) -> VoiceResponseDecision:
    if not ctx.has_response_text:
        return VoiceResponseDecision.SILENT
    if ctx.attention_decision == "IGNORE":
        return VoiceResponseDecision.SILENT
    if ctx.interrupted_by_user:
        # L'utilisateur vient de couper la parole à RAYA — on ne relance pas
        # une synthèse tant qu'on n'a pas traité ce qu'il vient de dire.
        return VoiceResponseDecision.TEXT_ONLY
    if not ctx.tts_capable:
        return VoiceResponseDecision.TEXT_ONLY
    if not ctx.user_is_focus and ctx.attention_decision == "BACKGROUND" and ctx.importance < 0.5:
        # Résultat de tâche de fond, peu important, utilisateur occupé
        # ailleurs (autre canal en focus) -> pas d'interruption vocale.
        return VoiceResponseDecision.TEXT_ONLY
    return VoiceResponseDecision.SPEAK
