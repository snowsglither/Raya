"""SpeechSynthesizer — abstraction TTS (consigne Phase 5 §10-11).

`speak()` ne doit JAMAIS bloquer l'appelant (le Harness doit pouvoir
continuer à travailler pendant que TTS parle) — la synthèse/lecture tourne
sur un thread dédié. État machine explicite (consigne §11) :

    IDLE -> SPEAKING -> [INTERRUPTING -> CANCELLED] | COMPLETED | ERROR
"""

from __future__ import annotations

import enum
import threading
from abc import ABC, abstractmethod
from typing import Callable


class TTSState(str, enum.Enum):
    IDLE = "idle"
    SPEAKING = "speaking"
    INTERRUPTING = "interrupting"
    CANCELLED = "cancelled"
    COMPLETED = "completed"
    ERROR = "error"


class TTSError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class SpeechSynthesizer(ABC):
    @abstractmethod
    def speak(self, text: str, language: str | None = None,
              on_complete: Callable[[TTSState], None] | None = None) -> None:
        """Démarre la synthèse+lecture de façon ASYNCHRONE — retourne
        immédiatement. `on_complete` est appelé (depuis le thread de lecture)
        avec l'état final réel (COMPLETED/CANCELLED/ERROR), jamais un succès
        supposé avant que la lecture soit réellement terminée.

        `language` (consigne "TTS LANGUAGE ROUTING") : la langue de RÉPONSE
        décidée en amont (`language.decide_response_language`) — jamais une
        langue devinée ici. Si aucune voix compatible n'existe pour cette
        langue, l'implémentation DOIT utiliser un repli documenté et exposer
        honnêtement (`language_used` / `language_fallback`) qu'elle n'a pas
        parlé dans la langue demandée — jamais prétendre le contraire."""
        ...

    @abstractmethod
    def cancel(self) -> None:
        """SPEAKING -> INTERRUPTING -> CANCELLED. No-op sûr si déjà IDLE/terminé
        (idempotent — consigne §20 "double cancellation")."""
        ...

    @abstractmethod
    def is_speaking(self) -> bool: ...

    @abstractmethod
    def state(self) -> TTSState: ...


class FakeTTS(SpeechSynthesizer):
    """TTS scripté et synchrone-mais-déterministe — aucune dépendance à un
    vrai moteur audio. `speak()` appelle `on_complete` immédiatement avec
    COMPLETED, sauf si `fail_next`/`hang_next` est armé par le test."""

    #: langues pour lesquelles ce Fake dispose d'une "voix" — imite le fait
    #: qu'un vrai moteur ne couvre pas toutes les langues (ex: Kokoro/nl).
    SUPPORTED_LANGUAGES = {"fr", "en", "es"}

    def __init__(self) -> None:
        self._state = TTSState.IDLE
        self.spoken: list[str] = []
        self.spoken_languages: list[str | None] = []
        self.cancel_calls = 0
        self._fail_next = False
        self._hang_next = False
        self._pending_complete: Callable[[TTSState], None] | None = None
        self.last_language_requested: str | None = None
        self.last_language_used: str | None = None
        self.last_language_fallback: bool = False

    def arm_failure(self) -> None:
        self._fail_next = True

    def arm_hang(self) -> None:
        """Le prochain speak() reste SPEAKING jusqu'à cancel() explicite —
        pour tester le barge-in/l'annulation en cours de parole."""
        self._hang_next = True

    def speak(self, text: str, language: str | None = None,
              on_complete: Callable[[TTSState], None] | None = None) -> None:
        self.spoken.append(text)
        self.spoken_languages.append(language)
        self.last_language_requested = language
        if language and language not in self.SUPPORTED_LANGUAGES:
            self.last_language_used = "en"  # repli documenté, jamais silencieux
            self.last_language_fallback = True
        else:
            self.last_language_used = language
            self.last_language_fallback = False
        if self._fail_next:
            self._fail_next = False
            self._state = TTSState.ERROR
            if on_complete:
                on_complete(TTSState.ERROR)
            return
        if self._hang_next:
            self._hang_next = False
            self._state = TTSState.SPEAKING
            self._pending_complete = on_complete
            return
        self._state = TTSState.SPEAKING
        self._state = TTSState.COMPLETED
        if on_complete:
            on_complete(TTSState.COMPLETED)

    def cancel(self) -> None:
        self.cancel_calls += 1
        if self._state != TTSState.SPEAKING:
            return  # idempotent — pas d'effet si rien n'est en cours
        self._state = TTSState.INTERRUPTING
        self._state = TTSState.CANCELLED
        if self._pending_complete:
            cb, self._pending_complete = self._pending_complete, None
            cb(TTSState.CANCELLED)

    def is_speaking(self) -> bool:
        return self._state == TTSState.SPEAKING

    def state(self) -> TTSState:
        return self._state
