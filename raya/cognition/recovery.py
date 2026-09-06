"""Recovery / replanning / détection de boucle (RAYA_V2_TECHNICAL_ARCHITECTURE.md
§9 ; consigne Phase 3 §12).

Règle : ne JAMAIS répéter automatiquement la même action indéfiniment. Un
détecteur de boucle simple et déterministe (pas de ML) suit, par tâche/tour,
la séquence (tool_name, signature des arguments, résultat) — si le MÊME appel
échoue deux fois de suite sans nouvelle information, c'est une absence de
progrès : escalade, jamais un troisième essai aveugle.
"""

from __future__ import annotations

import enum
import hashlib
import json
import threading

from .verification import VerificationOutcome

_DEFAULT_MAX_IDENTICAL_FAILURES = 2


class RecoveryAction(str, enum.Enum):
    CONTINUE = "CONTINUE"       # progrès normal, pas de problème détecté
    REPLAN = "REPLAN"            # un échec isolé — une nouvelle stratégie peut être tentée
    ESCALATE = "ESCALATE"        # boucle détectée ou échec répété — ne pas réessayer aveuglément


def _signature(tool_name: str, arguments: dict) -> str:
    raw = json.dumps({"tool": tool_name, "args": arguments}, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class LoopDetector:
    """Une instance par Harness (état en mémoire, jamais persisté comme
    chain-of-thought — consigne §3 : seuls des états structurés le sont,
    ceci reste un compteur transitoire, pas une mémoire durable)."""

    def __init__(self, max_identical_failures: int = _DEFAULT_MAX_IDENTICAL_FAILURES) -> None:
        self._max_identical_failures = max_identical_failures
        self._history: dict[str, list[str]] = {}
        self._lock = threading.Lock()

    def record(self, key: str, tool_name: str, arguments: dict, outcome: VerificationOutcome) -> RecoveryAction:
        sig = _signature(tool_name, arguments)
        with self._lock:
            history = self._history.setdefault(key, [])
            if outcome == VerificationOutcome.SUCCESS:
                # un succès efface l'historique d'échecs pour cette clé — on
                # a progressé, la détection de boucle repart de zéro.
                history.clear()
                return RecoveryAction.CONTINUE

            history.append(sig)
            repeats = sum(1 for s in history[-self._max_identical_failures:] if s == sig)
            if repeats >= self._max_identical_failures and len(history) >= self._max_identical_failures:
                return RecoveryAction.ESCALATE
            return RecoveryAction.REPLAN

    def forget(self, key: str) -> None:
        with self._lock:
            self._history.pop(key, None)
