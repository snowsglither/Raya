"""VoiceSession (consigne Phase 5 §21) — état TRANSITOIRE minimal. Ne
devient jamais une deuxième mémoire, ne stocke jamais la conversation
(ça, c'est `raya.memory`, déjà géré par le Harness), ne devient jamais un
état d'agent global. Un objet en mémoire process, jamais persisté tel quel."""

from __future__ import annotations

from dataclasses import dataclass, field

from raya.contracts import utc_now_iso

from .stt.base import TranscriptionResult
from .tts.base import TTSState
from .vad.base import VADState


@dataclass
class VoiceSession:
    session_id: str
    channel: str = "voice"
    correlation_id: str | None = None
    vad_state: VADState = VADState.SILENCE
    tts_state: TTSState = TTSState.IDLE
    current_turn: int = 0
    last_partial: TranscriptionResult | None = None
    session_language: str | None = None  # préférence de langue persistante entre tours (§LANGUAGE AWARENESS)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)

    def touch(self) -> None:
        self.updated_at = utc_now_iso()

    def set_vad_state(self, state: VADState) -> None:
        self.vad_state = state
        self.touch()

    def set_tts_state(self, state: TTSState) -> None:
        self.tts_state = state
        self.touch()

    def set_partial(self, result: TranscriptionResult | None) -> None:
        """Un partial écrase le précédent, jamais accumulé — les partials
        sont transitoires par nature (consigne §5/§23)."""
        self.last_partial = result
        self.touch()

    def new_turn(self) -> None:
        self.current_turn += 1
        self.last_partial = None
        self.touch()

    def set_session_language(self, language: str | None) -> None:
        """Persiste entre les tours — un changement de langue NE crée jamais
        une nouvelle session (même objet, juste ce champ mis à jour)."""
        self.session_language = language
        self.touch()
