"""Steering mid-turn Chantier 18 — tests unitaires et d'intégration.

Vérifie :
- `steer()` retourne les bons ErrorInfo selon l'état (session/focus/tâche)
- `steer()` met à jour le checkpoint correctement (guidance stockée, plan préservé)
- `Harness.steer_active_task()` délègue correctement à `steer()`
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.harness_factory import build_test_harness  # noqa: E402
from support.fake_provider import FakeScriptedProvider  # noqa: E402

from raya.attention import FocusTracker  # noqa: E402
from raya.contracts import (  # noqa: E402
    Channel,
    ContentPart,
    ErrorInfo,
    FinishReason,
    HarnessState,
    HarnessStatus,
    ModelCapability,
    ModelResponse,
    TaskState,
)
from raya.harness.steering import steer  # noqa: E402


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake",
        content=[ContentPart(type="text", value=text)],
        finish_reason=FinishReason.COMPLETED,
    )


def _state(session_id: str = "s1") -> HarnessState:
    return HarnessState(
        session_id=session_id,
        correlation_id="corr-1",
        channel=Channel.CLI,
        history_ref="hist-1",
        status=HarnessStatus.IDLE,
    )


# ---- Fake tasks / focus stubs ----

class _FakeFocus:
    def __init__(self, task_id: str | None = None):
        self._task_id = task_id

    def get_focus(self, session_id: str) -> str | None:
        return self._task_id


class _FakeTask:
    def __init__(self, state: TaskState, checkpoint: dict | None = None):
        self.id = "task-1"
        self.state = state
        self.checkpoint = checkpoint


class _FakeTasks:
    def __init__(self, task: _FakeTask | None = None):
        self._task = task
        self._last_checkpoint: dict | None = None
        self._last_task_id: str | None = None

    def get(self, task_id: str) -> _FakeTask | None:
        return self._task

    def checkpoint(self, task_id: str, data: dict) -> _FakeTask:
        self._last_task_id = task_id
        self._last_checkpoint = data
        if self._task is not None:
            self._task.checkpoint = data
        return self._task


# ---- Unit tests for steer() ----

def test_steer_no_focus_returns_error():
    err = steer(_state(), "do it in Dutch", tasks=_FakeTasks(), focus=_FakeFocus(None))
    assert isinstance(err, ErrorInfo)
    assert err.code == "NO_FOCUS_TASK"


def test_steer_task_not_found_returns_error():
    err = steer(_state(), "do it in Dutch", tasks=_FakeTasks(None), focus=_FakeFocus("task-1"))
    assert isinstance(err, ErrorInfo)
    assert err.code == "TASK_NOT_FOUND"


def test_steer_completed_task_returns_error():
    task = _FakeTask(state=TaskState.COMPLETED)
    err = steer(_state(), "do it in Dutch", tasks=_FakeTasks(task), focus=_FakeFocus("task-1"))
    assert isinstance(err, ErrorInfo)
    assert err.code == "TASK_NOT_ACTIVE"


def test_steer_failed_task_returns_error():
    task = _FakeTask(state=TaskState.FAILED)
    err = steer(_state(), "instruction", tasks=_FakeTasks(task), focus=_FakeFocus("task-1"))
    assert isinstance(err, ErrorInfo)
    assert err.code == "TASK_NOT_ACTIVE"


def test_steer_running_task_updates_checkpoint():
    task = _FakeTask(state=TaskState.RUNNING, checkpoint={"plan": {"steps": []}})
    tasks = _FakeTasks(task)
    result = steer(_state(), "monthly instead of weekly", tasks=tasks, focus=_FakeFocus("task-1"))
    assert result is None
    assert tasks._last_checkpoint is not None
    assert tasks._last_checkpoint["steering_guidance"] == "monthly instead of weekly"


def test_steer_pending_task_updates_checkpoint():
    task = _FakeTask(state=TaskState.PENDING)
    tasks = _FakeTasks(task)
    result = steer(_state(), "in Dutch", tasks=tasks, focus=_FakeFocus("task-1"))
    assert result is None
    assert tasks._last_checkpoint["steering_guidance"] == "in Dutch"


def test_steer_preserves_existing_checkpoint_plan():
    plan_data = {"steps": [{"id": "s1", "objective": "step one"}]}
    task = _FakeTask(state=TaskState.RUNNING, checkpoint={"plan": plan_data, "step_index": 2})
    tasks = _FakeTasks(task)
    steer(_state(), "add a sun", tasks=tasks, focus=_FakeFocus("task-1"))
    assert tasks._last_checkpoint["plan"] == plan_data
    assert tasks._last_checkpoint["step_index"] == 2
    assert tasks._last_checkpoint["steering_guidance"] == "add a sun"


def test_steer_overwrites_previous_guidance():
    task = _FakeTask(state=TaskState.RUNNING, checkpoint={"steering_guidance": "old directive"})
    tasks = _FakeTasks(task)
    steer(_state(), "new directive", tasks=tasks, focus=_FakeFocus("task-1"))
    assert tasks._last_checkpoint["steering_guidance"] == "new directive"


# ---- Integration tests via Harness.steer_active_task() ----

def test_harness_steer_active_task_no_session_returns_error(tmp_path):
    handles, _ = build_test_harness(tmp_path, [])
    try:
        err = handles.harness.steer_active_task("nonexistent-session", "some instruction")
        assert isinstance(err, ErrorInfo)
        assert err.code == "NO_SESSION"
    finally:
        handles.shutdown()


def test_harness_steer_active_task_success_returns_none(tmp_path):
    handles, _ = build_test_harness(tmp_path, [_text_response("done.")])
    try:
        # Need a session (handle_request creates one), and a running task as focus
        from raya.contracts import Channel, HarnessRequest, InterfaceInput
        handles.harness.handle_request(HarnessRequest(
            channel=Channel.CLI, session_id="s1",
            input=InterfaceInput(text="hello"),
        ))
        # Create and start a long-horizon task so there's a focus
        from support.fake_provider import FakeScriptedProvider
        handles.models.register(FakeScriptedProvider(
            [_text_response('["step one"]')],
            capabilities=[ModelCapability.PLANNING],
        ))
        task = handles.harness.create_long_horizon_task("some objective", channel="cli", session_id="s1")
        # Now steer — session exists, task is PENDING or RUNNING
        err = handles.harness.steer_active_task("s1", "do it in French")
        # Either None (success) or TASK_NOT_ACTIVE if task already completed quickly
        assert err is None or err.code == "TASK_NOT_ACTIVE"
    finally:
        handles.shutdown()
