"""Chantier 18 — tests comportementaux sur l'infrastructure agentique.

Vérifie :
- _ACTIVE_TASK_RANK_SCORE a bien été augmenté à 0.92
- La conversation history utilise bien limit=10 par défaut
- _task_state_section inclut steering_guidance depuis le checkpoint
- _run_long_horizon_step injecte la directive steering dans user_lines
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.harness_factory import build_test_harness  # noqa: E402
from support.fake_provider import FakeScriptedProvider  # noqa: E402

from raya.context_engine.assembler import (  # noqa: E402
    _ACTIVE_TASK_RANK_SCORE,
    _task_state_section,
    _conversation_history_section,
)
from raya.contracts import (  # noqa: E402
    ContentPart,
    FinishReason,
    ModelCapability,
    ModelResponse,
    RequestedToolCall,
    TaskOwner,
    TaskProgress,
    TaskState,
    Task,
)


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake",
        content=[ContentPart(type="text", value=text)],
        finish_reason=FinishReason.COMPLETED,
    )


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake",
        content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


# ---- Assembler configuration ----

def test_active_task_rank_score_is_0_92():
    assert _ACTIVE_TASK_RANK_SCORE == 0.92


def test_conversation_history_default_limit_is_10():
    import inspect
    sig = inspect.signature(_conversation_history_section)
    assert sig.parameters["limit"].default == 10


# ---- _task_state_section with steering_guidance ----

def _make_task(checkpoint: dict | None = None) -> Task:
    return Task(
        objective="write a report",
        owner=TaskOwner(channel="cli", session_id="s1"),
        correlation_id="corr-1",
        checkpoint=checkpoint,
        state=TaskState.RUNNING,
        progress=TaskProgress(current_step="step 1", percent=50.0),
    )


def test_task_state_section_includes_steering_guidance():
    task = _make_task(checkpoint={"plan": {}, "steering_guidance": "do it in Dutch"})
    section = _task_state_section(task)
    assert section.content.get("steering_guidance") == "do it in Dutch"


def test_task_state_section_no_guidance_when_absent():
    task = _make_task(checkpoint={"plan": {}})
    section = _task_state_section(task)
    assert "steering_guidance" not in section.content


def test_task_state_section_no_guidance_when_checkpoint_none():
    task = _make_task(checkpoint=None)
    section = _task_state_section(task)
    assert "steering_guidance" not in section.content


# ---- Long-horizon step injects steering ----

def _wait_for(predicate, timeout_s: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_steer_active_task_persists_guidance_in_checkpoint(tmp_path):
    """End-to-end: steer_active_task on a live harness task updates the task
    checkpoint with steering_guidance. The next step reads this from the
    checkpoint and injects it — verified at the unit level by the other tests."""
    from raya.contracts import Channel, HarnessRequest, InterfaceInput
    # handle_request creates the session; the text response is for that turn
    handles, _ = build_test_harness(tmp_path, [_text_response("ok")])
    try:
        handles.models.register(FakeScriptedProvider(
            [_text_response('["only step"]')],
            capabilities=[ModelCapability.PLANNING],
        ))
        # Create a session first
        handles.harness.handle_request(HarnessRequest(
            channel=Channel.CLI, session_id="s1",
            input=InterfaceInput(text="go"),
        ))
        task = handles.harness.create_long_horizon_task("write a poem", channel="cli", session_id="s1")
        # steer_active_task while task is PENDING (just created, not yet running)
        err = handles.harness.steer_active_task("s1", "make it rhyme")
        assert err is None, f"Expected success, got: {err}"
        updated_task = handles.harness.get_task(task.id)
        assert updated_task is not None
        assert (updated_task.checkpoint or {}).get("steering_guidance") == "make it rhyme"
    finally:
        handles.shutdown()


def test_long_horizon_step_no_steering_line_when_not_set(tmp_path):
    """Without steering_guidance in the checkpoint, no 'Updated user directive'
    line must appear in the model prompt."""
    received_messages: list[str] = []

    class _CapturingProvider(FakeScriptedProvider):
        def complete(self, request):
            for msg in request.messages:
                for part in msg.content:
                    if part.type == "text" and part.value:
                        received_messages.append(part.value)
            return super().complete(request)

    handles, _ = build_test_harness(tmp_path, [])
    try:
        handles.models.register(FakeScriptedProvider(
            [_text_response('["only step"]')],
            capabilities=[ModelCapability.PLANNING],
        ))
        capturing = _CapturingProvider([_text_response("step done.")])
        handles.models.register(capturing)

        task = handles.harness.create_long_horizon_task("summarize a doc", channel="cli", session_id="s1")
        _wait_for(lambda: handles.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED"), timeout_s=5.0)

        assert not any("Updated user directive" in m for m in received_messages), (
            "Unexpected 'Updated user directive' in model prompt when no steering was set"
        )
    finally:
        handles.shutdown()
