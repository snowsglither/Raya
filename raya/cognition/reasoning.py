"""Utilitaires Cognition transversaux. La vraie logique de raisonnement vit
maintenant dans `verification.py` (évidence) et `recovery.py` (boucle/replan) —
Phase 3 remplace le stub Phase 0 (`verify()` toujours UNKNOWN, `classify_intent()`
jamais appelé) par ces modules réels plutôt que de les faire grossir ici.
"""

from __future__ import annotations

from raya.contracts import ErrorInfo


def not_implemented_error(what: str) -> ErrorInfo:
    return ErrorInfo(code="NOT_IMPLEMENTED", message=f"{what} — non implémenté", retryable=False)
