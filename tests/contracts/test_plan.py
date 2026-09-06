"""Plan / PlanStep (RAYA V2 Phase 10, consigne §2/§3) — structure de données
explicite pour représenter une décomposition d'objectif. Vit dans
`Task.checkpoint["plan"]`, jamais un second système de persistance."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raya.contracts import (  # noqa: E402
    ErrorInfo,
    Plan,
    PlanStep,
    StepState,
    from_dict,
    next_runnable_step,
    plan_is_complete,
    plan_is_stuck,
    to_dict,
)


def _step(id_, status=StepState.PENDING, deps=()):
    return PlanStep(id=id_, objective=f"objective {id_}", status=status, dependencies=list(deps))


def test_plan_step_defaults_are_pending_zero_attempts_no_evidence():
    step = PlanStep(id="s1", objective="do X")
    assert step.status == StepState.PENDING
    assert step.attempts == 0
    assert step.evidence is None
    assert step.error is None


def test_next_runnable_step_returns_first_step_with_no_dependencies():
    plan = Plan(steps=[_step("s1"), _step("s2", deps=["s1"])])
    assert next_runnable_step(plan).id == "s1"


def test_next_runnable_step_waits_for_dependency_completion():
    plan = Plan(steps=[_step("s1", status=StepState.RUNNING), _step("s2", deps=["s1"])])
    assert next_runnable_step(plan) is None  # s2 dépend de s1, pas encore COMPLETED


def test_next_runnable_step_unblocks_once_dependency_completed():
    plan = Plan(steps=[_step("s1", status=StepState.COMPLETED), _step("s2", deps=["s1"])])
    assert next_runnable_step(plan).id == "s2"


def test_next_runnable_step_treats_skipped_as_satisfied():
    plan = Plan(steps=[_step("s1", status=StepState.SKIPPED), _step("s2", deps=["s1"])])
    assert next_runnable_step(plan).id == "s2"


def test_next_runnable_step_never_returns_a_step_blocked_by_a_failed_dependency():
    plan = Plan(steps=[_step("s1", status=StepState.FAILED), _step("s2", deps=["s1"])])
    assert next_runnable_step(plan) is None


def test_plan_is_complete_true_only_when_every_step_is_terminal_success():
    plan = Plan(steps=[_step("s1", status=StepState.COMPLETED), _step("s2", status=StepState.SKIPPED)])
    assert plan_is_complete(plan) is True


def test_plan_is_complete_false_with_pending_step():
    plan = Plan(steps=[_step("s1", status=StepState.COMPLETED), _step("s2")])
    assert plan_is_complete(plan) is False


def test_plan_is_complete_false_for_empty_plan():
    assert plan_is_complete(Plan(steps=[])) is False


def test_plan_is_stuck_when_only_failed_steps_block_everything():
    plan = Plan(steps=[_step("s1", status=StepState.FAILED), _step("s2", deps=["s1"])])
    assert plan_is_stuck(plan) is True


def test_plan_is_stuck_false_when_a_step_is_still_running():
    plan = Plan(steps=[_step("s1", status=StepState.RUNNING)])
    assert plan_is_stuck(plan) is False


def test_plan_is_stuck_false_when_complete():
    plan = Plan(steps=[_step("s1", status=StepState.COMPLETED)])
    assert plan_is_stuck(plan) is False


def test_plan_round_trips_through_to_dict_from_dict_with_nested_error():
    step = PlanStep(id="s1", objective="x", status=StepState.FAILED, attempts=2,
                     evidence={"tool": {"status": "failure"}}, error=ErrorInfo(code="E", message="m"))
    plan = Plan(steps=[step], current_step_id="s1")
    restored = from_dict(Plan, to_dict(plan))
    assert restored == plan
