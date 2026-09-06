"""ExecutionRecord (RAYA_V2_CONTRACTS.md §18) — idempotence et reprise après crash.

Ajouté lors de la revue de cohérence finale (RAYA_V2_ARCHITECTURE_FINAL_REVIEW.md §3).
Garantit qu'une action à effet externe n'est jamais rejouée aveuglément après un crash.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class ExecutionState(str, enum.Enum):
    NOT_STARTED = "NOT_STARTED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    UNKNOWN = "UNKNOWN"


class VerificationState(str, enum.Enum):
    NOT_VERIFIED = "NOT_VERIFIED"
    VERIFIED_SUCCESS = "VERIFIED_SUCCESS"
    VERIFIED_FAILURE = "VERIFIED_FAILURE"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass
class ExecutionRecord:
    operation_id: str
    tool_call_id: str
    correlation_id: str
    idempotency_key: str
    execution_state: ExecutionState = ExecutionState.NOT_STARTED
    verification_state: VerificationState = VerificationState.NOT_VERIFIED
    started_at: str | None = None
    completed_at: str | None = None
    # AJOUTÉS Phase 10 (consigne §11, "task id, step id" traçables) — additifs,
    # défaut None : un ExecutionRecord issu d'un tour conversationnel normal
    # (Phase 0-9) n'a ni task_id ni step_id, seul un step long-horizon les
    # renseigne (raya/harness/loop.py::_run_long_horizon_step).
    task_id: str | None = None
    step_id: str | None = None

    def reinterpret_after_restart(self) -> None:
        """RAYA_V2_TECHNICAL_ARCHITECTURE.md §14.2 : EXECUTING retrouvé après un
        redémarrage est TOUJOURS réinterprété comme UNKNOWN — jamais COMPLETED
        (optimisme dangereux) ni NOT_STARTED (risque de double exécution)."""
        if self.execution_state == ExecutionState.EXECUTING:
            self.execution_state = ExecutionState.UNKNOWN
