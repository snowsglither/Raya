"""RC1 — Conversational Objective Working State tests.

Every test proves something specific about RC1. No generic coverage tests.
Tools/Safety/Persistence are real; only the model is scripted.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider, ScriptEntry  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import (  # noqa: E402
    Channel,
    ContentPart,
    ErrorInfo,
    FinishReason,
    HarnessRequest,
    InterfaceInput,
    ModelCapability,
    ModelResponse,
    PermissionLevel,
    RequestedToolCall,
    Task,
    TaskState,
    Tool,
    ToolResult,
    ToolResultStatus,
)

_CONV_OBJ_KIND = "conversational_objective"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _req(text: str, session_id: str = "s1") -> HarnessRequest:
    return HarnessRequest(
        channel=Channel.CLI, session_id=session_id, input=InterfaceInput(text=text)
    )


def _text_response(text: str = "ok") -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake",
        content=[ContentPart(type="text", value=text)],
        finish_reason=FinishReason.COMPLETED,
    )


def _error_response() -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake",
        content=[],
        finish_reason=FinishReason.ERROR,
        error=ErrorInfo(code="MODEL_ERROR", message="scripted error"),
    )


def _classification_response(
    relation: str,
    new_objective: str | None = None,
    confidence: str = "high",
) -> ModelResponse:
    data: dict = {"relation": relation, "confidence": confidence, "short_reason": "test"}
    if relation in ("REPLACE", "CORRECT") and new_objective:
        data["proposed_new_objective"] = new_objective
    elif relation == "ASK":
        data["ask_question"] = "What exactly are you looking for?"
    return _text_response(json.dumps(data))


class _RC2AwareFakeProvider(FakeScriptedProvider):
    """Like FakeScriptedProvider but silently absorbs RC2 capability-selector
    calls (identified by the system prompt prefix) with a low-confidence
    response — triggering full-discovery fallback in the Harness.

    RC1 classifier calls and REASONING calls still consume the scripted script,
    so no existing RC1 test script needs modification.
    """

    _RC2_PROMPT_PREFIX = "You are a capability tag selector"

    def request(self, req: ModelRequest) -> ModelResponse:
        if req.capability == ModelCapability.CLASSIFICATION and req.messages:
            sys_value = (
                req.messages[0].content[0].value
                if req.messages[0].content
                else ""
            )
            if sys_value.startswith(self._RC2_PROMPT_PREFIX):
                self._calls.append(req)
                return ModelResponse(
                    request_id=req.id,
                    provider_used="fake",
                    content=[ContentPart(
                        type="text",
                        value='{"selected_tags":[],"confidence":"low","short_reason":"rc2-test-fallback"}',
                    )],
                    finish_reason=FinishReason.COMPLETED,
                )
        return super().request(req)


def _build_harness_with_both_caps(tmp_path: Path, script: list[ScriptEntry]):
    """Harness with a provider supporting REASONING + CLASSIFICATION.
    RC2 selector calls are auto-answered with low-confidence (full discovery
    fallback) so existing RC1 scripts do not need to be updated."""
    from raya.persistence import SqliteBackend
    from raya.runtime.bootstrap import bootstrap
    from raya.runtime.config import load_config

    cfg = load_config()
    cfg.db_path = tmp_path / "test.sqlite3"
    cfg.tool_workspace_dir = tmp_path / "workspace"
    cfg.ollama_api_key = None
    cfg.model_pool = []
    cfg.enable_ollama_local = False
    cfg.enable_windows_device = False
    cfg.enable_browser_device = False
    cfg.enable_perception = False
    cfg.enable_phone_device = False
    cfg.device_screenshot_dir = tmp_path / "screens"

    handles = bootstrap(config=cfg, backend=SqliteBackend(cfg.db_path))
    fake = _RC2AwareFakeProvider(
        script,
        capabilities=[ModelCapability.REASONING, ModelCapability.CLASSIFICATION],
    )
    handles.models.register(fake)
    return handles, fake


def _find_conv_tasks(handles, session_id: str) -> list[Task]:
    return [
        t for t in handles.tasks.list()
        if isinstance(t.checkpoint, dict)
        and t.checkpoint.get("kind") == _CONV_OBJ_KIND
        and t.owner is not None
        and t.owner.session_id == session_id
    ]


def _register_evidence_tool(handles) -> None:
    """Tool that succeeds and returns evidence — used to test evidence promotion.
    Uses 'utils' capability tag (classified SAFE in risk.py, not 'utils.read' which is unknown)."""
    handles.tools.register(
        Tool(
            name="test.evidence_tool",
            description="always succeeds with evidence",
            capability_tags=["utils"],
            input_schema={"type": "object", "properties": {}, "required": []},
            output_schema={"type": "object"},
            permission_level=PermissionLevel.SAFE,
        ),
        lambda call: ToolResult(
            tool_call_id=call.id,
            status=ToolResultStatus.SUCCESS,
            output={"result": "found"},
            evidence={"url": "https://example.com", "item": "test_item"},
        ),
    )


# ---------------------------------------------------------------------------
# Group 1: Task lifecycle
# ---------------------------------------------------------------------------

def test_conv_objective_task_created_on_first_request(tmp_path):
    """First request creates a conversational_objective task for the session."""
    handles, _ = build_test_harness(tmp_path, [_text_response()])
    handles.harness.handle_request(_req("find me a gaming mouse"))
    tasks = _find_conv_tasks(handles, "s1")
    assert len(tasks) == 1
    assert tasks[0].checkpoint["objective"] == "find me a gaming mouse"


def test_conv_objective_task_paused_between_turns(tmp_path):
    """After handle_request completes, the conv_obj task is PAUSED."""
    handles, _ = build_test_harness(tmp_path, [_text_response()])
    handles.harness.handle_request(_req("find me a gaming mouse"))
    tasks = _find_conv_tasks(handles, "s1")
    assert tasks[0].state == TaskState.PAUSED


def test_conv_objective_task_running_is_single_task_after_two_turns(tmp_path):
    """Two turns on the same session produce exactly one conv_obj task, not two."""
    handles, _ = _build_harness_with_both_caps(tmp_path, [
        _text_response("turn1"),
        _text_response("turn2"),
        _classification_response("CONTINUE"),
    ])
    handles.harness.handle_request(_req("find a gaming mouse"))
    handles.harness.handle_request(_req("under 60 euros please"))
    tasks = _find_conv_tasks(handles, "s1")
    # Only one active (non-terminal) task
    active = [t for t in tasks if t.state != TaskState.COMPLETED]
    assert len(active) == 1


def test_conv_objective_task_session_isolation(tmp_path):
    """Different sessions get separate conv_obj tasks, never shared."""
    handles, _ = build_test_harness(tmp_path, [_text_response(), _text_response()])
    handles.harness.handle_request(_req("find a mouse", session_id="sess_a"))
    handles.harness.handle_request(_req("find a keyboard", session_id="sess_b"))
    tasks_a = _find_conv_tasks(handles, "sess_a")
    tasks_b = _find_conv_tasks(handles, "sess_b")
    assert len(tasks_a) == 1
    assert len(tasks_b) == 1
    assert tasks_a[0].id != tasks_b[0].id
    assert tasks_a[0].checkpoint["objective"] == "find a mouse"
    assert tasks_b[0].checkpoint["objective"] == "find a keyboard"


def test_expired_task_is_cancelled_and_replaced(tmp_path):
    """A task with turns_active >= 10 is cancelled and a fresh one created."""
    handles, _ = _build_harness_with_both_caps(tmp_path, [
        _text_response("new request"),
        # No classification on first turn of the new task
    ])
    # Manually inject a stale task
    from raya.contracts import TaskOwner
    stale_task = handles.tasks.create(
        objective="old objective",
        owner=TaskOwner(channel="conversation", session_id="s1"),
        correlation_id="stale-corr",
    )
    handles.tasks.start(stale_task.id)
    handles.tasks.checkpoint(stale_task.id, {
        "kind": _CONV_OBJ_KIND,
        "objective": "old objective",
        "relevant_information": [],
        "last_domain_of_activity": None,
        "turns_active": 10,  # at expiration threshold
        "completed_at_turn": None,
        "result_summary": None,
        "next_checkpoint": None,
    })
    handles.tasks.pause(stale_task.id)

    handles.harness.handle_request(_req("brand new topic"))

    stale = handles.tasks.get(stale_task.id)
    assert stale.state == TaskState.CANCELLED

    new_tasks = _find_conv_tasks(handles, "s1")
    active = [t for t in new_tasks if t.state != TaskState.CANCELLED]
    assert len(active) == 1
    assert active[0].checkpoint["objective"] == "brand new topic"


# ---------------------------------------------------------------------------
# Group 2: Evidence promotion / Working State
# ---------------------------------------------------------------------------

def test_no_evidence_promoted_when_no_tools_called(tmp_path):
    """A pure text turn (no tool calls) leaves relevant_information empty."""
    handles, _ = build_test_harness(tmp_path, [_text_response("sure, I'll help")])
    handles.harness.handle_request(_req("tell me about gaming mice"))
    tasks = _find_conv_tasks(handles, "s1")
    assert tasks[0].checkpoint["relevant_information"] == []


def test_evidence_promoted_from_successful_tool_result(tmp_path):
    """A successful tool call with evidence is promoted to relevant_information."""
    from raya.contracts import RequestedToolCall

    tool_call_resp = ModelResponse(
        request_id="", provider_used="fake", content=[],
        finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(
            tool_name="test.evidence_tool", arguments={}
        )],
    )
    handles, _ = build_test_harness(tmp_path, [tool_call_resp, _text_response("done")])
    _register_evidence_tool(handles)
    handles.harness.handle_request(_req("find something"))
    tasks = _find_conv_tasks(handles, "s1")
    rel_info = tasks[0].checkpoint["relevant_information"]
    assert len(rel_info) == 1
    assert rel_info[0]["source"] == "test.evidence_tool"
    assert "example.com" in rel_info[0]["content"]


def test_relevant_information_capped_at_limit(tmp_path):
    """relevant_information never exceeds _RELEVANT_INFO_CAP (20) entries."""
    from raya.contracts import RequestedToolCall

    def _tool_then_text():
        return [
            ModelResponse(
                request_id="", provider_used="fake", content=[],
                finish_reason=FinishReason.TOOL_CALL_PENDING,
                tool_calls_requested=[RequestedToolCall(
                    tool_name="test.evidence_tool", arguments={}
                )],
            ),
            _text_response("ok"),
        ]

    # 12 turns with tool calls; each produces 1 evidence entry
    # After cap (20), we still should have at most 20
    # We run 22 turns with evidence to exceed cap
    script = []
    for _ in range(22):
        script.extend(_tool_then_text())
    # Each turn after the first also gets a classification call
    for _ in range(21):
        script.append(_classification_response("CONTINUE"))

    # Script is consumed in order: for each turn:
    # turn 1: tool_call + text (no classification)
    # turn 2+: tool_call + text + classification
    # Reorder: interleave tool+text+[classification] per turn
    # Actually the script is consumed per model call order:
    # Turn 1: tool_call_resp[0], text_resp[1]
    # Turn 2: tool_call_resp[2], text_resp[3], classification[4]
    # etc.
    # Let's rebuild the script properly:
    correct_script = []
    for i in range(22):
        correct_script.append(ModelResponse(
            request_id="", provider_used="fake", content=[],
            finish_reason=FinishReason.TOOL_CALL_PENDING,
            tool_calls_requested=[RequestedToolCall(
                tool_name="test.evidence_tool", arguments={}
            )],
        ))
        correct_script.append(_text_response("ok"))
        if i > 0:  # classification only from turn 2 onward
            correct_script.append(_classification_response("CONTINUE"))

    handles, _ = _build_harness_with_both_caps(tmp_path, correct_script)
    _register_evidence_tool(handles)

    for i in range(22):
        handles.harness.handle_request(_req(f"query {i}"))

    tasks = _find_conv_tasks(handles, "s1")
    active = [t for t in tasks if t.state == TaskState.PAUSED]
    assert len(active) == 1
    rel_info = active[0].checkpoint["relevant_information"]
    assert len(rel_info) <= 20


# ---------------------------------------------------------------------------
# Group 3: Cognition call gate
# ---------------------------------------------------------------------------

def test_cognition_not_called_on_first_turn(tmp_path):
    """First turn: Cognition (CLASSIFICATION) is never called."""
    # Provider only supports REASONING — if CLASSIFICATION were called it would fail
    handles, fake = build_test_harness(tmp_path, [_text_response("response")])
    handles.harness.handle_request(_req("find a mouse"))
    # Exactly one model call: the REASONING call in the loop
    assert len(fake.calls) == 1
    assert fake.calls[0].capability == ModelCapability.REASONING


def test_cognition_called_on_second_turn(tmp_path):
    """Second turn: RC1 Cognition (objective classifier) is called exactly once
    after the loop. RC2 selector calls (pre-loop) are excluded from this count."""
    handles, fake = _build_harness_with_both_caps(tmp_path, [
        _text_response("turn1"),       # turn 1 REASONING
        _text_response("turn2"),       # turn 2 REASONING
        _classification_response("CONTINUE"),  # turn 2 RC1 CLASSIFICATION
    ])
    handles.harness.handle_request(_req("find a mouse"))
    handles.harness.handle_request(_req("under 60 euros"))

    # RC1 classifier system prompt starts with "You are a semantic objective classifier"
    # RC2 selector calls are intercepted by _RC2AwareFakeProvider and start differently
    _RC1_PROMPT = "You are a semantic objective classifier"
    rc1_calls = [
        c for c in fake.calls
        if c.capability == ModelCapability.CLASSIFICATION
        and c.messages
        and c.messages[0].content[0].value.startswith(_RC1_PROMPT)
    ]
    assert len(rc1_calls) == 1


def test_cognition_replace_completes_old_task_and_creates_new(tmp_path):
    """REPLACE: old task is COMPLETED, new task is created with new objective."""
    handles, _ = _build_harness_with_both_caps(tmp_path, [
        _text_response("turn1"),
        _text_response("turn2"),
        _classification_response("REPLACE", new_objective="find a mechanical keyboard"),
    ])
    handles.harness.handle_request(_req("find a gaming mouse"))
    handles.harness.handle_request(_req("actually forget that, find me a keyboard"))

    all_conv = _find_conv_tasks(handles, "s1")
    completed = [t for t in all_conv if t.state == TaskState.COMPLETED]
    paused = [t for t in all_conv if t.state == TaskState.PAUSED]

    assert len(completed) == 1
    assert completed[0].checkpoint["objective"] == "find a gaming mouse"
    assert completed[0].checkpoint["completed_at_turn"] is not None

    assert len(paused) == 1
    assert paused[0].checkpoint["objective"] == "find a mechanical keyboard"


def test_cognition_correct_updates_objective_text(tmp_path):
    """CORRECT: same task is kept but objective text is updated."""
    handles, _ = _build_harness_with_both_caps(tmp_path, [
        _text_response("turn1"),
        _text_response("turn2"),
        _classification_response("CORRECT", new_objective="find a wireless gaming mouse under 60€"),
    ])
    handles.harness.handle_request(_req("find a gaming mouse"))
    handles.harness.handle_request(_req("make it wireless and under 60 euros"))

    tasks = _find_conv_tasks(handles, "s1")
    active = [t for t in tasks if t.state == TaskState.PAUSED]
    assert len(active) == 1
    assert active[0].checkpoint["objective"] == "find a wireless gaming mouse under 60€"


def test_cognition_failure_preserves_task_unchanged(tmp_path):
    """Classification error → SAFE FALLBACK: original task preserved, state intact."""
    handles, _ = _build_harness_with_both_caps(tmp_path, [
        _text_response("turn1"),
        _text_response("turn2"),
        _error_response(),  # classification fails → SAFE FALLBACK
    ])
    handles.harness.handle_request(_req("find a gaming mouse"))
    handles.harness.handle_request(_req("under 60 euros please"))

    tasks = _find_conv_tasks(handles, "s1")
    # Only one task — no REPLACE happened despite classification failure
    active = [t for t in tasks if t.state == TaskState.PAUSED]
    assert len(active) == 1
    # Objective unchanged (CORRECT not applied)
    assert active[0].checkpoint["objective"] == "find a gaming mouse"


def test_cognition_failure_never_replaces_task(tmp_path):
    """Classification error: the existing task is NEVER replaced (cost-asymmetry safety)."""
    handles, _ = _build_harness_with_both_caps(tmp_path, [
        _text_response("turn1"),
        _text_response("turn2"),
        _error_response(),
    ])
    handles.harness.handle_request(_req("find a gaming mouse"))
    handles.harness.handle_request(_req("something completely different"))

    all_conv = _find_conv_tasks(handles, "s1")
    completed = [t for t in all_conv if t.state == TaskState.COMPLETED]
    # No task was completed (no REPLACE applied)
    assert len(completed) == 0


# ---------------------------------------------------------------------------
# Group 4: Recently-completed context window
# ---------------------------------------------------------------------------

def test_recently_completed_task_in_context_within_window(tmp_path):
    """After REPLACE, the completed task appears in the context for 3 turns."""
    handles, fake = _build_harness_with_both_caps(tmp_path, [
        _text_response("turn1"),
        _text_response("turn2"),
        _classification_response("REPLACE", new_objective="find a keyboard"),
        _text_response("turn3"),
        _classification_response("CONTINUE"),  # turn3 classification
    ])
    handles.harness.handle_request(_req("find a mouse"))
    handles.harness.handle_request(_req("actually find a keyboard"))
    # Turn 3: recently-completed should be visible in system prompt
    handles.harness.handle_request(_req("under 100 euros"))

    # Check last model call's system prompt
    reasoning_calls = [c for c in fake.calls if c.capability == ModelCapability.REASONING]
    last_reasoning = reasoning_calls[-1]
    system_texts = " ".join(
        p.value for m in last_reasoning.messages
        if m.role == "system"
        for p in m.content if p.type == "text"
    )
    assert "recently completed" in system_texts.lower()


def test_recently_completed_not_visible_after_window(tmp_path):
    """A task completed more than 3 turns ago is NOT shown in context."""
    # Turn 1: create task
    # Turn 2: REPLACE (old task completed at turn=2)
    # Turns 3-5: window fills (3 turns from turn 2 = turns 3,4,5)
    # Turn 6: completed_at_turn=2, current_turn=6, delta=4 > 3 → expired
    script = [
        _text_response("t1"),
        _text_response("t2"),
        _classification_response("REPLACE", new_objective="new topic"),
        _text_response("t3"),
        _classification_response("CONTINUE"),
        _text_response("t4"),
        _classification_response("CONTINUE"),
        _text_response("t5"),
        _classification_response("CONTINUE"),
        _text_response("t6"),
        _classification_response("CONTINUE"),
    ]
    handles, fake = _build_harness_with_both_caps(tmp_path, script)
    for i, text in enumerate(["find mouse", "find keyboard", "q3", "q4", "q5", "q6"]):
        handles.harness.handle_request(_req(text, session_id="s1"))

    reasoning_calls = [c for c in fake.calls if c.capability == ModelCapability.REASONING]
    last_reasoning = reasoning_calls[-1]
    system_texts = " ".join(
        p.value for m in last_reasoning.messages
        if m.role == "system"
        for p in m.content if p.type == "text"
    )
    assert "recently completed" not in system_texts.lower()


# ---------------------------------------------------------------------------
# Group 5: Context rendering and session isolation
# ---------------------------------------------------------------------------

def test_conv_objective_visible_in_own_session_context(tmp_path):
    """On turn 2, the conversational objective appears in the system prompt."""
    handles, fake = _build_harness_with_both_caps(tmp_path, [
        _text_response("turn1"),
        _text_response("turn2"),
        _classification_response("CONTINUE"),
    ])
    handles.harness.handle_request(_req("find a gaming mouse under 60€"))
    handles.harness.handle_request(_req("any suggestions?"))

    reasoning_calls = [c for c in fake.calls if c.capability == ModelCapability.REASONING]
    last_system_texts = " ".join(
        p.value for m in reasoning_calls[-1].messages
        if m.role == "system"
        for p in m.content if p.type == "text"
    )
    assert "gaming mouse" in last_system_texts


# ---------------------------------------------------------------------------
# Group 6: CORRECT visibility fix
# ---------------------------------------------------------------------------

def test_correct_task_objective_field_unchanged(tmp_path):
    """CORRECT: Task.objective (native dataclass field) is never modified — only checkpoint['objective'].

    Proves: Task.objective stays as the original creation text even after CORRECT.
    Task identity invariant: 'objective changes only on REPLACE'.
    """
    handles, _ = _build_harness_with_both_caps(tmp_path, [
        _text_response("turn1"),
        _text_response("turn2"),
        _classification_response("CORRECT", new_objective="Find me an Apple Watch SE"),
    ])
    handles.harness.handle_request(_req("Find me an Apple Watch"))
    handles.harness.handle_request(_req("No, I meant the SE"))

    tasks = _find_conv_tasks(handles, "s1")
    active = [t for t in tasks if t.state == TaskState.PAUSED]
    assert len(active) == 1
    task = active[0]
    assert task.checkpoint["objective"] == "Find me an Apple Watch SE"
    assert task.objective == "Find me an Apple Watch"


def test_active_context_renders_corrected_objective_after_correct(tmp_path):
    """After CORRECT, the active context renders checkpoint['objective'], not Task.objective.

    Proves: the context engine uses the corrected Working State text.
    Turn 3 system prompt must show 'Apple Watch SE', not original 'Apple Watch'.
    """
    handles, fake = _build_harness_with_both_caps(tmp_path, [
        _text_response("turn1"),
        _text_response("turn2"),
        _classification_response("CORRECT", new_objective="Find me an Apple Watch SE"),
        _text_response("turn3"),
        _classification_response("CONTINUE"),
    ])
    handles.harness.handle_request(_req("Find me an Apple Watch"))
    handles.harness.handle_request(_req("No, I meant the SE"))
    handles.harness.handle_request(_req("what options are available?"))

    reasoning_calls = [c for c in fake.calls if c.capability == ModelCapability.REASONING]
    assert len(reasoning_calls) == 3
    third_system = " ".join(
        p.value for m in reasoning_calls[2].messages
        if m.role == "system"
        for p in m.content if p.type == "text"
    )
    assert "Current conversational objective: 'Find me an Apple Watch SE'" in third_system
    assert "Current conversational objective: 'Find me an Apple Watch'" not in third_system


def test_active_context_falls_back_to_task_objective_when_no_checkpoint_objective(tmp_path):
    """Active context falls back to Task.objective when checkpoint has no 'objective' key.

    Proves: the fallback is correct — no crash and no empty objective rendered.
    """
    from raya.context_engine.assembler import _active_tasks_sections
    from raya.contracts import TaskOwner

    task = Task(
        objective="original from task field",
        owner=TaskOwner(channel="conversation", session_id="s1"),
        correlation_id="test-fallback",
    )
    task.checkpoint = {
        "kind": _CONV_OBJ_KIND,
        "relevant_information": [],
        "turns_active": 1,
        # no "objective" key — fallback must activate
    }

    sections = _active_tasks_sections((task,), exclude_task_id=None)
    assert len(sections) == 1
    assert sections[0].content["objective"] == "original from task field"


def test_continue_leaves_active_context_objective_unchanged(tmp_path):
    """CONTINUE: the active context still renders the original objective unchanged.

    Proves: CONTINUE does not alter checkpoint['objective'] or what the model sees.
    """
    handles, fake = _build_harness_with_both_caps(tmp_path, [
        _text_response("turn1"),
        _text_response("turn2"),
        _classification_response("CONTINUE"),
        _text_response("turn3"),
        _classification_response("CONTINUE"),
    ])
    handles.harness.handle_request(_req("Find me an Apple Watch"))
    handles.harness.handle_request(_req("what colors are available?"))
    handles.harness.handle_request(_req("and the price?"))

    reasoning_calls = [c for c in fake.calls if c.capability == ModelCapability.REASONING]
    assert len(reasoning_calls) == 3
    third_system = " ".join(
        p.value for m in reasoning_calls[2].messages
        if m.role == "system"
        for p in m.content if p.type == "text"
    )
    assert "Current conversational objective: 'Find me an Apple Watch'" in third_system


def test_replace_active_context_shows_new_objective_not_old(tmp_path):
    """After REPLACE, active context shows the new task's objective, not the replaced one.

    Proves: REPLACE is not accidentally treated as CORRECT — new Task.objective is used.
    """
    handles, fake = _build_harness_with_both_caps(tmp_path, [
        _text_response("turn1"),
        _text_response("turn2"),
        _classification_response("REPLACE", new_objective="Find a MacBook Pro"),
        _text_response("turn3"),
        _classification_response("CONTINUE"),
    ])
    handles.harness.handle_request(_req("Find me an Apple Watch"))
    handles.harness.handle_request(_req("actually I want a MacBook Pro"))
    handles.harness.handle_request(_req("which model?"))

    reasoning_calls = [c for c in fake.calls if c.capability == ModelCapability.REASONING]
    assert len(reasoning_calls) == 3
    third_system = " ".join(
        p.value for m in reasoning_calls[2].messages
        if m.role == "system"
        for p in m.content if p.type == "text"
    )
    assert "Current conversational objective: 'Find a MacBook Pro'" in third_system
    assert "Current conversational objective: 'Find me an Apple Watch'" not in third_system


def test_recently_completed_renders_corrected_objective(tmp_path):
    """After CORRECT then REPLACE, the recently-completed context shows the corrected text.

    Proves: recently_completed rendering reads checkpoint['objective'] (corrected),
    not Task.objective (original). Turn 4 must show 'Apple Watch SE' as the completed objective.
    """
    handles, fake = _build_harness_with_both_caps(tmp_path, [
        _text_response("turn1"),
        _text_response("turn2"),
        _classification_response("CORRECT", new_objective="Find me an Apple Watch SE"),
        _text_response("turn3"),
        _classification_response("REPLACE", new_objective="buy a MacBook"),
        _text_response("turn4"),
        _classification_response("CONTINUE"),
    ])
    handles.harness.handle_request(_req("Find me an Apple Watch"))
    handles.harness.handle_request(_req("No, I meant the SE"))
    handles.harness.handle_request(_req("actually, I want a MacBook now"))
    handles.harness.handle_request(_req("tell me about MacBooks"))

    reasoning_calls = [c for c in fake.calls if c.capability == ModelCapability.REASONING]
    assert len(reasoning_calls) == 4
    fourth_system = " ".join(
        p.value for m in reasoning_calls[3].messages
        if m.role == "system"
        for p in m.content if p.type == "text"
    )
    assert "Apple Watch SE" in fourth_system
    assert "recently completed" in fourth_system.lower()


def test_cognition_failure_active_context_shows_original_objective(tmp_path):
    """Cognition failure → _SAFE_FALLBACK (CONTINUE) → active context shows original objective.

    Proves: fallback leaves checkpoint['objective'] untouched, so the model sees
    the unchanged original on the next turn.
    """
    handles, fake = _build_harness_with_both_caps(tmp_path, [
        _text_response("turn1"),
        _text_response("turn2"),
        _error_response(),              # classification fails → SAFE FALLBACK
        _text_response("turn3"),
        _classification_response("CONTINUE"),
    ])
    handles.harness.handle_request(_req("Find me an Apple Watch"))
    handles.harness.handle_request(_req("something ambiguous"))
    handles.harness.handle_request(_req("still looking"))

    reasoning_calls = [c for c in fake.calls if c.capability == ModelCapability.REASONING]
    assert len(reasoning_calls) == 3
    third_system = " ".join(
        p.value for m in reasoning_calls[2].messages
        if m.role == "system"
        for p in m.content if p.type == "text"
    )
    assert "Current conversational objective: 'Find me an Apple Watch'" in third_system


def test_other_session_conv_objective_not_in_context(tmp_path):
    """Session B's conversational objective never appears as an active objective in session A's context.

    NOTE: memory is channel-scoped (not session-scoped), so session B's text CAN appear in
    conversation history. The invariant we test is narrower and correct: the ACTIVE
    conversational objective line must show session A's objective, not session B's.
    """
    handles, fake = _build_harness_with_both_caps(tmp_path, [
        _text_response("a1"),           # session A turn 1
        _text_response("b1"),           # session B turn 1
        _text_response("a2"),           # session A turn 2
        _classification_response("CONTINUE"),  # session A turn 2 classification
    ])
    handles.harness.handle_request(_req("find a gaming mouse", session_id="sess_a"))
    handles.harness.handle_request(_req("buy a PS5", session_id="sess_b"))
    handles.harness.handle_request(_req("any gaming mouse?", session_id="sess_a"))

    reasoning_calls = [c for c in fake.calls if c.capability == ModelCapability.REASONING]
    # Last reasoning call is session A's turn 2
    last_system_texts = " ".join(
        p.value for m in reasoning_calls[-1].messages
        if m.role == "system"
        for p in m.content if p.type == "text"
    )
    # Session B's objective must NOT appear as an active conversational objective
    assert "Current conversational objective: 'buy a PS5'" not in last_system_texts
    # Session A's own objective MUST appear as the active objective
    assert "Current conversational objective: 'find a gaming mouse'" in last_system_texts
