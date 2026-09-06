"""Autorisation Telegram (RAYA V2 Phase 9, consigne §8).

Concept LOCAL à l'interface Telegram — comme `raya/interfaces/voice/policy.py`
n'a jamais eu besoin d'un contrat dans `raya/contracts/` pour une décision
propre à la voix, cette classification n'a pas sa place dans `contracts/`
(rien d'autre dans RAYA n'a besoin de connaître "AUTHORIZED/UNAUTHORIZED").

FAIL CLOSED par construction : une liste vide n'autorise personne (jamais un
mode "ouvert par défaut" — un bot Telegram public sans configuration ne doit
jamais pouvoir piloter le PC de Ruben)."""

from __future__ import annotations

import enum


class TelegramAuthDecision(str, enum.Enum):
    AUTHORIZED = "authorized"
    UNAUTHORIZED = "unauthorized"
    UNKNOWN = "unknown"  # update Telegram sans user_id exploitable (jamais traité comme autorisé)


class TelegramAuthorizer:
    def __init__(self, allowed_user_ids: tuple[int, ...] = ()) -> None:
        self._allowed = frozenset(allowed_user_ids)

    def is_configured(self) -> bool:
        return bool(self._allowed)

    def check(self, user_id: int | None) -> TelegramAuthDecision:
        if user_id is None:
            return TelegramAuthDecision.UNKNOWN
        if user_id in self._allowed:
            return TelegramAuthDecision.AUTHORIZED
        return TelegramAuthDecision.UNAUTHORIZED
