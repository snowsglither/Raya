"""ExecutionRecordRepository + règle de récupération (RAYA_V2_TECHNICAL_ARCHITECTURE.md
§14, RAYA_V2_CONTRACTS.md §18). Propriétaire = harness (§14.4 : "harness orchestre
la vérification et la décision... persistence garantit que l'écriture...
est durable"). Aucun vrai Tool n'existe encore en Phase 1 (Phase 3) — ce module
prouve le MÉCANISME d'idempotence avec des vérifications injectées (callables),
pas avec de vraies capacités.
"""

from __future__ import annotations

import enum
import threading
from typing import Callable

from raya.contracts import ExecutionRecord, ExecutionState, VerificationState, from_dict, to_dict
from raya.persistence import PersistenceBackend

_COLLECTION = "execution_records"


class RecoveryDecision(str, enum.Enum):
    RETRY = "RETRY"
    ALREADY_DONE = "ALREADY_DONE"
    ESCALATE = "ESCALATE"


class ExecutionRecordRepository:
    def __init__(self, backend: PersistenceBackend) -> None:
        self._backend = backend
        self._lock = threading.Lock()

    def start(self, record: ExecutionRecord) -> ExecutionRecord:
        """Écriture SYNCHRONE et DURABLE de execution_state=EXECUTING, AVANT
        que le Device ne soit invoqué (RAYA_V2_TECHNICAL_ARCHITECTURE.md §14.2,
        étape 2) — c'est cette séquence qui rend un crash détectable."""
        record.execution_state = ExecutionState.EXECUTING
        from raya.contracts import utc_now_iso

        record.started_at = utc_now_iso()
        with self._lock:
            self._backend.save(_COLLECTION, record.operation_id, to_dict(record))
        return record

    def complete(self, operation_id: str) -> ExecutionRecord | None:
        record = self.get(operation_id)
        if record is None:
            return None
        record.execution_state = ExecutionState.COMPLETED
        from raya.contracts import utc_now_iso

        record.completed_at = utc_now_iso()
        with self._lock:
            self._backend.save(_COLLECTION, operation_id, to_dict(record))
        return record

    def get(self, operation_id: str) -> ExecutionRecord | None:
        raw = self._backend.load(_COLLECTION, operation_id)
        return from_dict(ExecutionRecord, raw) if raw else None

    def load_and_reinterpret(self, operation_id: str) -> ExecutionRecord | None:
        """Chargement au moment de la reprise : EXECUTING -> UNKNOWN, jamais
        COMPLETED (optimisme dangereux) ni NOT_STARTED (risque de double
        exécution) — RAYA_V2_TECHNICAL_ARCHITECTURE.md §14.2."""
        record = self.get(operation_id)
        if record is None:
            return None
        record.reinterpret_after_restart()
        with self._lock:
            self._backend.save(_COLLECTION, operation_id, to_dict(record))
        return record

    def recover_all_unknown(self) -> list[ExecutionRecord]:
        raw = self._backend.query(_COLLECTION, execution_state=ExecutionState.EXECUTING.value)
        records = [from_dict(ExecutionRecord, r) for r in raw]
        result = []
        for record in records:
            record.reinterpret_after_restart()
            with self._lock:
                self._backend.save(_COLLECTION, record.operation_id, to_dict(record))
            result.append(record)
        return result


def decide_recovery(
    record: ExecutionRecord,
    idempotent: bool,
    verify: Callable[[], VerificationState],
) -> RecoveryDecision:
    """Règle stricte de RAYA_V2_TECHNICAL_ARCHITECTURE.md §14.3.

    idempotent=True  -> retry direct sûr (même idempotency_key).
    idempotent=False -> vérification obligatoire avant toute décision :
        VERIFIED_SUCCESS -> ALREADY_DONE (jamais rejoué)
        VERIFIED_FAILURE -> RETRY (l'action n'a en fait pas eu lieu)
        UNVERIFIABLE / NOT_VERIFIED -> ESCALADE (jamais de retry aveugle,
            jamais de faux succès silencieux).
    """
    if record.execution_state != ExecutionState.UNKNOWN:
        raise ValueError(
            f"decide_recovery appelé sur un ExecutionRecord qui n'est pas UNKNOWN "
            f"(state={record.execution_state.value}) — vérifier reinterpret_after_restart()."
        )

    if idempotent:
        return RecoveryDecision.RETRY

    verification = verify()
    record.verification_state = verification

    if verification == VerificationState.VERIFIED_SUCCESS:
        record.execution_state = ExecutionState.COMPLETED
        return RecoveryDecision.ALREADY_DONE
    if verification == VerificationState.VERIFIED_FAILURE:
        return RecoveryDecision.RETRY
    return RecoveryDecision.ESCALATE
