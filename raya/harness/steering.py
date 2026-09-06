"""User steering mid-turn (RAYA_V2_TECHNICAL_ARCHITECTURE.md §3.2).

Phase 0 : la boucle est synchrone et se termine en un seul passage — il n'y a
pas encore de tour en vol dans lequel injecter une instruction. Retourne donc
explicitement NOT_IMPLEMENTED plutôt que de simuler un comportement inexistant
(section 8 de la consigne Phase 0). Le vrai steering arrive avec les Task
Actors asynchrones de Phase 2.
"""

from __future__ import annotations

from raya.contracts import ErrorInfo, HarnessState


def steer(state: HarnessState, instruction: str) -> ErrorInfo:
    return ErrorInfo(
        code="NOT_IMPLEMENTED",
        message="Steering mid-turn nécessite une boucle asynchrone (Phase 2) — Phase 0 stub.",
        retryable=False,
    )
