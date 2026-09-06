"""Vérification STOP aux points de contrôle (RAYA_V2_TECHNICAL_ARCHITECTURE.md §3.2).

safety.should_stop() est consulté avant CHAQUE appel modèle/outil/device —
invariant #6. Petite fonction dédiée pour que ces points de contrôle soient
grep-ables et uniformes dans harness/loop.py.
"""

from __future__ import annotations

from raya.contracts import ErrorInfo, HarnessState, HarnessStatus
from raya.safety import SafetyService


def checkpoint_or_abort(state: HarnessState, safety: SafetyService) -> bool:
    """Retourne True si l'exécution doit s'arrêter ici (STOP actif)."""
    if safety.should_stop():
        state.status = HarnessStatus.FAILED
        state.error = ErrorInfo(
            code="STOP_ACTIVE", message="Interrompu par safety.should_stop()", retryable=False
        )
        state.checkpoint()
        return True
    return False
