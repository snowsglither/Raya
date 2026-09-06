"""Priorité E (idempotence) — ExecutionRecord : EXECUTING -> UNKNOWN au crash,
règle de récupération stricte (jamais de retry aveugle, jamais UNKNOWN -> COMPLETED
automatique). RAYA_V2_TECHNICAL_ARCHITECTURE.md §14, RAYA_V2_CONTRACTS.md §18."""

from __future__ import annotations

import pytest

from raya.contracts import ExecutionRecord, ExecutionState, VerificationState
from raya.harness import ExecutionRecordRepository, RecoveryDecision, decide_recovery
from raya.persistence import SqliteBackend


def _repo(tmp_path) -> ExecutionRecordRepository:
    return ExecutionRecordRepository(SqliteBackend(tmp_path / "exec.sqlite3"))


def _record(**overrides) -> ExecutionRecord:
    defaults = dict(operation_id="op1", tool_call_id="tc1", correlation_id="c1", idempotency_key="k1")
    defaults.update(overrides)
    return ExecutionRecord(**defaults)


def test_start_persists_executing_before_any_execution(tmp_path):
    repo = _repo(tmp_path)
    repo.start(_record())
    loaded = repo.get("op1")
    assert loaded.execution_state == ExecutionState.EXECUTING
    assert loaded.started_at is not None


def test_complete_marks_completed(tmp_path):
    repo = _repo(tmp_path)
    repo.start(_record())
    repo.complete("op1")
    assert repo.get("op1").execution_state == ExecutionState.COMPLETED


def test_crash_between_start_and_complete_reinterpreted_as_unknown(tmp_path):
    path = tmp_path / "exec.sqlite3"
    repo1 = ExecutionRecordRepository(SqliteBackend(path))
    repo1.start(_record())
    # crash simulé : repo1.complete() jamais appelé

    repo2 = ExecutionRecordRepository(SqliteBackend(path))
    recovered = repo2.load_and_reinterpret("op1")
    assert recovered.execution_state == ExecutionState.UNKNOWN


def test_recover_all_unknown_finds_all_executing_records(tmp_path):
    path = tmp_path / "exec.sqlite3"
    repo1 = ExecutionRecordRepository(SqliteBackend(path))
    repo1.start(_record(operation_id="op1"))
    repo1.start(_record(operation_id="op2", tool_call_id="tc2"))
    repo1.complete("op2")  # celui-ci a bien fini avant le crash

    repo2 = ExecutionRecordRepository(SqliteBackend(path))
    recovered = repo2.recover_all_unknown()
    assert {r.operation_id for r in recovered} == {"op1"}


def test_decide_recovery_requires_unknown_state(tmp_path):
    record = _record(execution_state=ExecutionState.COMPLETED)
    with pytest.raises(ValueError):
        decide_recovery(record, idempotent=True, verify=lambda: VerificationState.NOT_VERIFIED)


def test_decide_recovery_idempotent_retries_directly():
    record = _record(execution_state=ExecutionState.UNKNOWN)
    decision = decide_recovery(record, idempotent=True, verify=lambda: (_ for _ in ()).throw(AssertionError("verify ne doit pas être appelé")))
    assert decision == RecoveryDecision.RETRY


def test_decide_recovery_non_idempotent_verified_success_never_replayed():
    record = _record(execution_state=ExecutionState.UNKNOWN)
    decision = decide_recovery(record, idempotent=False, verify=lambda: VerificationState.VERIFIED_SUCCESS)
    assert decision == RecoveryDecision.ALREADY_DONE
    assert record.execution_state == ExecutionState.COMPLETED


def test_decide_recovery_non_idempotent_verified_failure_retries():
    record = _record(execution_state=ExecutionState.UNKNOWN)
    decision = decide_recovery(record, idempotent=False, verify=lambda: VerificationState.VERIFIED_FAILURE)
    assert decision == RecoveryDecision.RETRY


def test_decide_recovery_non_idempotent_unverifiable_escalates_never_retries_blindly():
    record = _record(execution_state=ExecutionState.UNKNOWN)
    decision = decide_recovery(record, idempotent=False, verify=lambda: VerificationState.UNVERIFIABLE)
    assert decision == RecoveryDecision.ESCALATE


def test_decide_recovery_non_idempotent_not_verified_escalates():
    record = _record(execution_state=ExecutionState.UNKNOWN)
    decision = decide_recovery(record, idempotent=False, verify=lambda: VerificationState.NOT_VERIFIED)
    assert decision == RecoveryDecision.ESCALATE


def test_idempotency_key_stable_across_record(tmp_path):
    repo = _repo(tmp_path)
    repo.start(_record(idempotency_key="stable-key-1"))
    assert repo.get("op1").idempotency_key == "stable-key-1"
