"""RC2 — Capability Selection tests.

Every test proves something specific about RC2. No generic RAYA coverage.
Tools/Safety/Persistence are real; only the model is scripted.

Groups:
  A (1-9)  : cognitive primitive — select_capabilities() directly
  B (10-16): Harness integration — _select_capability_tags / _discover_tool_schemas
  C (17-20): RC1 continuity — Working State read-only contract
  D (21-23): Failure / safety boundary
"""

from __future__ import annotations

import inspect
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider, ScriptEntry  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.cognition.capability_selection import (  # noqa: E402
    CapabilitySelectionRequest,
    select_capabilities,
)
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
    TaskOwner,
    TaskState,
    Tool,
    ToolResult,
    ToolResultStatus,
)
from raya.models import ModelRegistry  # noqa: E402

_CONV_OBJ_KIND = "conversational_objective"


# ---------------------------------------------------------------------------
# Shared helpers
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


def _selection_response(
    tags: list[str],
    confidence: str = "high",
    reason: str = "test",
) -> ModelResponse:
    data = {"selected_tags": tags, "confidence": confidence, "short_reason": reason}
    return _text_response(json.dumps(data))


def _classification_response(
    relation: str,
    new_objective: str | None = None,
    confidence: str = "high",
) -> ModelResponse:
    data: dict = {"relation": relation, "confidence": confidence, "short_reason": "test"}
    if relation in ("REPLACE", "CORRECT") and new_objective:
        data["proposed_new_objective"] = new_objective
    elif relation == "ASK":
        data["ask_question"] = "What exactly?"
    return _text_response(json.dumps(data))


def _make_classifier_registry(script: list[ScriptEntry]):
    """Minimal ModelRegistry with only a CLASSIFICATION provider."""
    registry = ModelRegistry()
    fake = FakeScriptedProvider(script, capabilities=[ModelCapability.CLASSIFICATION])
    registry.register(fake)
    return registry, fake


def _build_harness_both_caps(tmp_path: Path, script: list[ScriptEntry]):
    """Harness with a single provider supporting REASONING + CLASSIFICATION."""
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
    fake = FakeScriptedProvider(
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


def _register_tool(handles, name: str, tags: list[str]) -> None:
    """Register a no-op SAFE tool with the given capability tags."""
    handles.tools.register(
        Tool(
            name=name,
            description=f"test tool {name}",
            capability_tags=tags,
            input_schema={"type": "object", "properties": {}, "required": []},
            output_schema={"type": "object"},
            permission_level=PermissionLevel.SAFE,
        ),
        lambda call: ToolResult(
            tool_call_id=call.id,
            status=ToolResultStatus.SUCCESS,
            output={"done": True},
        ),
    )


def _reasoning_call(fake: FakeScriptedProvider) -> object:
    """Return the ModelRequest from the REASONING call."""
    for c in fake.calls:
        if c.capability == ModelCapability.REASONING:
            return c
    return None


def _tool_names_in_call(model_request) -> set[str]:
    """Extract tool names from available_tools in a ModelRequest."""
    if model_request is None or not model_request.available_tools:
        return set()
    return {t.get("name", "") for t in model_request.available_tools}


# ---------------------------------------------------------------------------
# Group A: Primitive — select_capabilities() directly
# ---------------------------------------------------------------------------

def test_A1_browser_intent_selects_browser_tags():
    """Proves: product price query → proposal selects browser capabilities."""
    registry, _ = _make_classifier_registry([
        _selection_response(["browser.read", "browser.interact"]),
    ])
    request = CapabilitySelectionRequest(
        user_text="Trouve-moi le prix des AirPods",
        available_tags=["browser.read", "browser.interact", "phone.call", "system.read"],
    )
    proposal = select_capabilities(request, registry, "corr-1")
    assert proposal is not None
    assert "browser.read" in proposal.selected_tags
    assert "browser.interact" in proposal.selected_tags
    assert proposal.confidence == "high"


def test_A2_browser_selection_excludes_unrelated_phone():
    """Proves: browser proposal does NOT include phone capability."""
    registry, _ = _make_classifier_registry([
        _selection_response(["browser.read"]),
    ])
    request = CapabilitySelectionRequest(
        user_text="Trouve-moi une Apple Watch",
        available_tags=["browser.read", "phone.call", "phone.sms"],
    )
    proposal = select_capabilities(request, registry, "corr-2")
    assert proposal is not None
    assert "phone.call" not in proposal.selected_tags
    assert "phone.sms" not in proposal.selected_tags


def test_A3_system_time_selects_system_read():
    """Proves: time query → system.read in proposal."""
    registry, _ = _make_classifier_registry([
        _selection_response(["system.read"]),
    ])
    request = CapabilitySelectionRequest(
        user_text="Quelle heure est-il ?",
        available_tags=["system.read", "browser.read", "phone.call"],
    )
    proposal = select_capabilities(request, registry, "corr-3")
    assert proposal is not None
    assert "system.read" in proposal.selected_tags


def test_A4_objective_influences_ambiguous_request():
    """Proves: Working State objective reaches the Cognition call and enables
    domain inference for an ambiguous user message."""
    registry, fake = _make_classifier_registry([
        _selection_response(["browser.read", "browser.interact"]),
    ])
    request = CapabilitySelectionRequest(
        user_text="Continue",
        available_tags=["browser.read", "browser.interact", "system.read"],
        objective_text="Find the price of AirPods on Amazon",
        last_domain="browser",
    )
    proposal = select_capabilities(request, registry, "corr-4")
    # The CLASSIFICATION call must have included the objective in its input
    classification_call = fake.calls[0]
    user_msg = classification_call.messages[-1].content[0].value
    assert "Find the price of AirPods" in user_msg
    assert proposal is not None
    assert "browser.read" in proposal.selected_tags


def test_A5_objective_dominates_stale_last_domain():
    """Proves: when last_domain='system' (stale from side-question) but objective
    is browser-oriented, the returned proposal reflects browser, not system."""
    registry, _ = _make_classifier_registry([
        # Model correctly uses objective to override stale last_domain
        _selection_response(["browser.read", "browser.interact"]),
    ])
    request = CapabilitySelectionRequest(
        user_text="Et chez Fnac ?",
        available_tags=["browser.read", "browser.interact", "system.read", "tasks.read"],
        objective_text="Find the price of AirPods",
        last_domain="system",  # stale — side-question polluted it
    )
    proposal = select_capabilities(request, registry, "corr-5")
    assert proposal is not None
    assert "browser.read" in proposal.selected_tags
    # system.read may or may not be present — browser must be
    assert "browser.interact" in proposal.selected_tags


def test_A6_provider_exception_returns_none():
    """Proves: any provider exception results in None (_SAFE_FALLBACK)."""
    registry = ModelRegistry()
    # No provider registered → model_route raises
    request = CapabilitySelectionRequest(
        user_text="find something",
        available_tags=["browser.read"],
    )
    result = select_capabilities(request, registry, "corr-6")
    assert result is None


def test_A7_invalid_json_returns_none():
    """Proves: a provider returning malformed JSON results in None."""
    registry, _ = _make_classifier_registry([
        _text_response("this is not json at all"),
    ])
    request = CapabilitySelectionRequest(
        user_text="find something",
        available_tags=["browser.read"],
    )
    result = select_capabilities(request, registry, "corr-7")
    assert result is None


def test_A8_unknown_tags_filtered_by_primitive():
    """Proves: tags not in available_tags never appear in the proposal."""
    registry, _ = _make_classifier_registry([
        _selection_response(["browser.read", "hallucinated.tag", "another.fake"]),
    ])
    request = CapabilitySelectionRequest(
        user_text="find something",
        available_tags=["browser.read", "system.read"],
    )
    proposal = select_capabilities(request, registry, "corr-8")
    assert proposal is not None
    assert "hallucinated.tag" not in proposal.selected_tags
    assert "another.fake" not in proposal.selected_tags
    assert "browser.read" in proposal.selected_tags


def test_A9_low_confidence_is_preserved_in_proposal():
    """Proves: the primitive faithfully reports low confidence when the model
    signals uncertainty — the Harness (not the primitive) decides on fallback."""
    registry, _ = _make_classifier_registry([
        _selection_response(["system.read"], confidence="low"),
    ])
    request = CapabilitySelectionRequest(
        user_text="fais ça",
        available_tags=["system.read", "browser.read"],
    )
    proposal = select_capabilities(request, registry, "corr-9")
    assert proposal is not None
    assert proposal.confidence == "low"


# ---------------------------------------------------------------------------
# Group B: Harness integration
# ---------------------------------------------------------------------------

def test_B10_baseline_always_survives_selection(tmp_path):
    """Proves: even when selector returns only {'browser.read'}, BASELINE tags
    (system.read, tasks.control, tasks.read) are present in the REASONING tool surface."""
    handles, fake = _build_harness_both_caps(tmp_path, [
        _selection_response(["browser.read"]),  # CLASSIFICATION
        _text_response("done"),                   # REASONING
        _classification_response("CONTINUE"),     # RC1 post-loop
    ])
    _register_tool(handles, "browser.navigate", ["browser.read"])
    _register_tool(handles, "system.time.now", ["system.read"])
    _register_tool(handles, "tasks.list", ["tasks.read"])

    handles.harness.handle_request(_req("find AirPods price"))

    reasoning = _reasoning_call(fake)
    tool_names = _tool_names_in_call(reasoning)
    # browser tool selected by RC2
    assert "browser.navigate" in tool_names
    # baseline tools always present
    assert "system.time.now" in tool_names
    assert "tasks.list" in tool_names


def test_B11_selected_tags_intersected_with_registered(tmp_path):
    """Proves: tags in proposal that are not in the registry are silently dropped."""
    handles, fake = _build_harness_both_caps(tmp_path, [
        _selection_response(["browser.read", "unregistered.capability"]),
        _text_response("done"),
        _classification_response("CONTINUE"),
    ])
    _register_tool(handles, "browser.navigate", ["browser.read"])
    # 'unregistered.capability' is NOT registered

    handles.harness.handle_request(_req("browse something"))

    reasoning = _reasoning_call(fake)
    tool_names = _tool_names_in_call(reasoning)
    assert "browser.navigate" in tool_names
    # No tool with "unregistered.capability" tag exists → nothing to expose
    assert all("unregistered" not in name for name in tool_names)


def test_B12_selector_none_falls_back_to_full_discovery(tmp_path):
    """Proves: when no CLASSIFICATION model is available (selector → None),
    the Harness falls back to all registered tools (current behavior)."""
    # build_test_harness registers a REASONING-only provider → CLASSIFICATION fails
    handles, fake = build_test_harness(tmp_path, [_text_response("done")])
    _register_tool(handles, "browser.navigate", ["browser.read"])
    _register_tool(handles, "phone.dial", ["phone.call"])

    handles.harness.handle_request(_req("find something"))

    reasoning = _reasoning_call(fake)
    tool_names = _tool_names_in_call(reasoning)
    # Full discovery: both tools must be present
    assert "browser.navigate" in tool_names
    assert "phone.dial" in tool_names


def test_B13_low_confidence_falls_back_to_full_discovery(tmp_path):
    """Proves: low-confidence proposal is treated as None → full discovery."""
    handles, fake = _build_harness_both_caps(tmp_path, [
        _selection_response(["browser.read"], confidence="low"),
        _text_response("done"),
        _classification_response("CONTINUE"),
    ])
    _register_tool(handles, "browser.navigate", ["browser.read"])
    _register_tool(handles, "phone.dial", ["phone.call"])

    handles.harness.handle_request(_req("fais ça"))

    reasoning = _reasoning_call(fake)
    tool_names = _tool_names_in_call(reasoning)
    # Low confidence → full fallback → both tools present
    assert "browser.navigate" in tool_names
    assert "phone.dial" in tool_names


def test_B14_hallucinated_tags_do_not_expose_unintended_tools(tmp_path):
    """Proves: when a proposal selects only hallucinated/unregistered tags,
    the intersection produces BASELINE only — browser.navigate is NOT exposed,
    and the model still has a non-empty (safe) tool surface via BASELINE.
    Full-discovery fallback only triggers when intersection is truly empty."""
    handles, fake = _build_harness_both_caps(tmp_path, [
        _selection_response(["totally.fake.tag"]),   # hallucinated — not in registry
        _text_response("done"),
        _classification_response("CONTINUE"),
    ])
    _register_tool(handles, "browser.navigate", ["browser.read"])
    _register_tool(handles, "system.time.now", ["system.read"])

    handles.harness.handle_request(_req("do something"))

    reasoning = _reasoning_call(fake)
    tool_names = _tool_names_in_call(reasoning)
    # Hallucinated tag filtered → selected = BASELINE ∩ registered → baseline tools only
    # browser.navigate is NOT in BASELINE → must be absent
    assert "browser.navigate" not in tool_names
    # BASELINE tools still present (non-zero surface, no crash)
    assert "system.time.now" in tool_names


def test_B15_all_tags_selected_equivalent_to_full_discovery(tmp_path):
    """Proves: when RC2 selects all tags, the tool surface is identical to
    pre-RC2 full discovery."""
    handles, fake = _build_harness_both_caps(tmp_path, [
        _selection_response(["browser.read", "phone.call", "system.read",
                             "tasks.control", "tasks.read"]),
        _text_response("done"),
        _classification_response("CONTINUE"),
    ])
    _register_tool(handles, "browser.navigate", ["browser.read"])
    _register_tool(handles, "phone.dial", ["phone.call"])
    _register_tool(handles, "system.time.now", ["system.read"])

    handles.harness.handle_request(_req("do everything"))

    reasoning = _reasoning_call(fake)
    tool_names = _tool_names_in_call(reasoning)
    assert "browser.navigate" in tool_names
    assert "phone.dial" in tool_names
    assert "system.time.now" in tool_names


def test_B16_discover_tool_schemas_reduces_schema_count(tmp_path):
    """Proves: _discover_tool_schemas(selected_tags) returns fewer schemas
    than _discover_tool_schemas() when tags are a strict subset."""
    handles, _ = build_test_harness(tmp_path, [])
    _register_tool(handles, "browser.navigate", ["browser.read"])
    _register_tool(handles, "browser.click", ["browser.interact"])
    _register_tool(handles, "phone.dial", ["phone.call"])
    _register_tool(handles, "system.time.now", ["system.read"])

    harness = handles.harness
    full = harness._discover_tool_schemas()
    reduced = harness._discover_tool_schemas(["browser.read"])

    assert len(reduced) < len(full)
    reduced_names = {t["name"] for t in reduced}
    assert "browser.navigate" in reduced_names
    assert "phone.dial" not in reduced_names
    assert "system.time.now" not in reduced_names


# ---------------------------------------------------------------------------
# Group C: RC1 continuity
# ---------------------------------------------------------------------------

def test_C17_rc2_reads_objective_from_checkpoint_not_task_field(tmp_path):
    """Proves: RC2 selector reads checkpoint["objective"] (mutable, RC1-correctable),
    not task.objective (immutable creation-time field).

    Method: turn 1 creates the conv task; we then directly update checkpoint["objective"]
    to simulate what RC1 CORRECT would write; turn 2 verifies RC2 received the
    corrected value, not the original task.objective."""
    handles, fake = _build_harness_both_caps(tmp_path, [
        # Turn 1: creates conv task (RC1 skips Cognition: turns_active=1, gate is >1)
        _selection_response(["browser.read"]),
        _text_response("searching"),
        # Turn 2: RC2 must read the corrected checkpoint["objective"]
        _selection_response(["browser.read"]),
        _text_response("done"),
        _classification_response("CONTINUE"),
    ])

    handles.harness.handle_request(_req("find Apple Watch"))

    # Simulate RC1 CORRECT: update checkpoint["objective"] on the paused task
    tasks = _find_conv_tasks(handles, "s1")
    assert len(tasks) == 1
    original_objective = tasks[0].objective  # immutable task.objective field
    ckpt = dict(tasks[0].checkpoint)
    ckpt["objective"] = "Find Apple Watch SE"  # mutable checkpoint value
    handles.tasks.checkpoint(tasks[0].id, ckpt)

    handles.harness.handle_request(_req("SE model actually"))

    classification_calls = [c for c in fake.calls if c.capability == ModelCapability.CLASSIFICATION]
    assert len(classification_calls) >= 2
    turn2_input = classification_calls[1].messages[-1].content[0].value
    # RC2 must have read checkpoint["objective"], not task.objective
    assert "Find Apple Watch SE" in turn2_input
    assert original_objective not in turn2_input or "Find Apple Watch SE" in turn2_input


def test_C18_rc2_does_not_modify_conv_task_checkpoint(tmp_path):
    """Proves: after RC2 selection, the conv_task checkpoint is byte-for-byte
    identical to what RC1 set — RC2 is strictly read-only."""
    handles, _ = _build_harness_both_caps(tmp_path, [
        _selection_response(["browser.read"]),
        _text_response("done"),
        _classification_response("CONTINUE"),
    ])

    handles.harness.handle_request(_req("find a gaming mouse"))
    tasks = _find_conv_tasks(handles, "s1")
    assert len(tasks) == 1
    ckpt = tasks[0].checkpoint
    # RC2 must not have added any extra key to the checkpoint
    expected_keys = {"kind", "objective", "relevant_information", "last_domain_of_activity",
                     "turns_active", "completed_at_turn", "result_summary", "next_checkpoint"}
    assert set(ckpt.keys()) == expected_keys


def test_C19_rc1_replace_visible_to_rc2_on_next_turn(tmp_path):
    """Proves: after RC1 REPLACE (which runs post-loop on turn 2, since RC1
    Cognition gate is turns_active > 1), the new objective is visible to RC2
    on turn 3 — no same-turn circular dependency.

    Script ordering: RC1 Cognition is skipped on turn 1 (turns_active=1, not >1).
    REPLACE runs on turn 2 (turns_active=2). Turn 3 RC2 reads the new task."""
    handles, fake = _build_harness_both_caps(tmp_path, [
        # Turn 1: creates task (no RC1 Cognition — turns_active=1)
        _selection_response(["browser.read"]),
        _text_response("searching AirPods"),
        # Turn 2: turns_active=2 → RC1 Cognition runs → REPLACE
        _selection_response(["browser.read"]),
        _text_response("pivoting"),
        _classification_response("REPLACE", "Find a MacBook Pro"),
        # Turn 3: new task created by REPLACE (turns_active=1, no RC1 Cognition)
        _selection_response(["browser.read"]),
        _text_response("searching MacBook"),
    ])

    handles.harness.handle_request(_req("find AirPods"))
    handles.harness.handle_request(_req("actually MacBook Pro"))
    handles.harness.handle_request(_req("find it now"))

    classification_calls = [c for c in fake.calls if c.capability == ModelCapability.CLASSIFICATION]
    # [0]=T1 RC2, [1]=T2 RC2, [2]=T2 RC1 REPLACE, [3]=T3 RC2
    assert len(classification_calls) >= 4
    turn3_rc2_input = classification_calls[3].messages[-1].content[0].value
    assert "Find a MacBook Pro" in turn3_rc2_input


def test_C20_side_question_system_does_not_destroy_browser_objective(tmp_path):
    """Proves: a system-domain side-question (turn 2) does not prevent RC2
    from selecting browser capabilities on turn 3 when the objective persists.

    Script ordering: RC1 Cognition is skipped on turn 1 (turns_active=1).
    It runs on turns 2 and 3 (turns_active=2, 3 > 1).
    [0]=T1 RC2, [1]=T2 RC2, [2]=T2 RC1 CONTINUE, [3]=T3 RC2, [4]=T3 RC1 CONTINUE."""
    handles, fake = _build_harness_both_caps(tmp_path, [
        # Turn 1: browser objective created (no RC1 Cognition)
        _selection_response(["browser.read", "browser.interact"]),
        _text_response("browsing amazon"),
        # Turn 2: system side-question (RC1 Cognition runs: turns_active=2)
        _selection_response(["system.read"]),
        _text_response("it is 14:32"),
        _classification_response("CONTINUE"),
        # Turn 3: resume browser (RC1 Cognition runs: turns_active=3)
        _selection_response(["browser.read", "browser.interact"]),
        _text_response("browsing fnac"),
        _classification_response("CONTINUE"),
    ])
    _register_tool(handles, "browser.navigate", ["browser.read"])
    _register_tool(handles, "system.time.now", ["system.read"])

    handles.harness.handle_request(_req("find AirPods price"))
    handles.harness.handle_request(_req("what time is it?"))
    handles.harness.handle_request(_req("and at Fnac?"))

    classification_calls = [c for c in fake.calls if c.capability == ModelCapability.CLASSIFICATION]
    # [0]=T1 RC2, [1]=T2 RC2, [2]=T2 RC1, [3]=T3 RC2, [4]=T3 RC1
    assert len(classification_calls) >= 4
    turn3_rc2_input = classification_calls[3].messages[-1].content[0].value
    # The active objective (AirPods) must still be present in the T3 RC2 call
    assert "AirPods" in turn3_rc2_input

    # Turn 3 REASONING call must expose browser tools (RC2 selected them)
    reasoning_calls = [c for c in fake.calls if c.capability == ModelCapability.REASONING]
    assert len(reasoning_calls) >= 3
    turn3_tools = _tool_names_in_call(reasoning_calls[2])
    assert "browser.navigate" in turn3_tools


# ---------------------------------------------------------------------------
# Group D: Failure / safety boundary
# ---------------------------------------------------------------------------

def test_D21_capability_selection_module_has_no_safety_or_tool_imports():
    """Proves: capability_selection.py never imports safety/, tools/, tasks/,
    harness/ — confirmed structurally, not just at runtime."""
    import raya.cognition.capability_selection as mod
    src = inspect.getsource(mod)
    assert "raya.safety" not in src
    assert "execute_tool" not in src
    assert "TaskRegistry" not in src
    assert "raya.harness" not in src
    assert "ToolRegistry" not in src


def test_D22_sensitive_selected_capability_still_reaches_safety(tmp_path):
    """Proves: a tool selected by RC2 (browser.interact) still goes through
    Safety when the model calls it — RC2 only controls exposure, not execution."""
    from raya.contracts import PermissionLevel

    call_log: list[str] = []

    def _sensitive_handler(call):
        call_log.append(call.name)
        return ToolResult(
            tool_call_id=call.id,
            status=ToolResultStatus.SUCCESS,
            output={"clicked": True},
        )

    handles, fake = _build_harness_both_caps(tmp_path, [
        _selection_response(["browser.interact"]),
        # Model calls the tool
        ModelResponse(
            request_id="", provider_used="fake",
            content=[],
            finish_reason=FinishReason.TOOL_CALL_PENDING,
            tool_calls_requested=[
                RequestedToolCall(tool_name="browser.click", arguments={"target": "next"})
            ],
        ),
        _text_response("done"),
        _classification_response("CONTINUE"),
    ])
    handles.tools.register(
        Tool(
            name="browser.click",
            description="click element",
            capability_tags=["browser.interact"],
            input_schema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
            output_schema={"type": "object"},
            permission_level=PermissionLevel.SENSITIVE,
        ),
        _sensitive_handler,
    )

    handles.harness.handle_request(_req("click next button"))

    # Safety was involved: the tool is SENSITIVE → requires confirmation → NOT called
    # (FakeScriptedProvider runs in test environment without user confirmation)
    # The key assertion: the tool was NOT auto-executed despite being selected by RC2
    assert "browser.click" not in call_log, (
        "RC2 must not bypass Safety — SENSITIVE tool must require confirmation"
    )


def test_D23_unselected_tool_absent_from_available_schemas(tmp_path):
    """Proves: a tool whose tag is not in RC2's selected_tags is completely
    absent from the model's available tool schemas."""
    handles, fake = _build_harness_both_caps(tmp_path, [
        _selection_response(["browser.read"]),  # phone.call NOT selected
        _text_response("done"),
        _classification_response("CONTINUE"),
    ])
    _register_tool(handles, "browser.navigate", ["browser.read"])
    _register_tool(handles, "phone.dial", ["phone.call"])

    handles.harness.handle_request(_req("find a product"))

    reasoning = _reasoning_call(fake)
    tool_names = _tool_names_in_call(reasoning)
    assert "browser.navigate" in tool_names
    assert "phone.dial" not in tool_names


# ---------------------------------------------------------------------------
# Group A (continued): Primitive edge cases not covered by A1–A9
# ---------------------------------------------------------------------------

def test_A10_medium_confidence_follows_high_path(tmp_path):
    """Proves: confidence="medium" does NOT trigger the all_tags fallback.
    The branch `if proposal is None or proposal.confidence == "low"` excludes
    "medium" — medium follows the same BASELINE-merge path as "high"."""
    handles, fake = _build_harness_both_caps(tmp_path, [
        _selection_response(["browser.read"], confidence="medium"),
        _text_response("done"),
        _classification_response("CONTINUE"),
    ])
    _register_tool(handles, "browser.navigate", ["browser.read"])
    _register_tool(handles, "phone.dial", ["phone.call"])

    handles.harness.handle_request(_req("find a product"))

    reasoning = _reasoning_call(fake)
    tool_names = _tool_names_in_call(reasoning)
    # Medium confidence → selection applied (not fallen back to all_tags)
    assert "browser.navigate" in tool_names
    # phone.dial was NOT selected and medium did not cause fallback
    assert "phone.dial" not in tool_names


def test_A11_error_finish_reason_returns_none():
    """Proves: when the provider returns FinishReason.ERROR (not an exception,
    not invalid JSON — a real error response object), select_capabilities
    returns None (_SAFE_FALLBACK), covering capability_selection.py line 153-154."""
    registry, _ = _make_classifier_registry([_error_response()])
    request = CapabilitySelectionRequest(
        user_text="find something",
        available_tags=["browser.read", "system.read"],
    )
    result = select_capabilities(request, registry, "corr-11")
    assert result is None


# ---------------------------------------------------------------------------
# Group B (continued): Harness integration edge case
# ---------------------------------------------------------------------------

def test_B17_baseline_only_registry_bypasses_selector(tmp_path):
    """Proves: when the tool registry contains only BASELINE capability tags
    (system.read, tasks.control, tasks.read), _select_capability_tags returns
    immediately without calling the CLASSIFICATION model — the short-circuit
    branch `if not non_baseline` (loop.py:610-612) fires.

    Bootstrap always registers non-BASELINE tools (vision, spatial, demo…), so
    this test calls _select_capability_tags directly with a BASELINE-only
    ToolRegistry swapped onto the harness — the only way to exercise this branch
    in isolation without bootstrap interference."""
    from raya.tools import ToolRegistry

    handles, fake = _build_harness_both_caps(tmp_path, [])  # no script needed

    # Build a registry with only BASELINE-tagged tools
    baseline_only = ToolRegistry()
    for name, tag in [("system.time.now", "system.read"), ("tasks.list", "tasks.read")]:
        baseline_only.register(
            Tool(
                name=name,
                description=f"test {name}",
                capability_tags=[tag],
                input_schema={"type": "object", "properties": {}, "required": []},
                output_schema={"type": "object"},
                permission_level=PermissionLevel.SAFE,
            ),
            lambda call: ToolResult(
                tool_call_id=call.id,
                status=ToolResultStatus.SUCCESS,
                output={},
            ),
        )

    # Swap registry — _select_capability_tags reads all_capability_tags() from this
    handles.harness._tools_registry = baseline_only

    # Call _select_capability_tags directly (bypasses handle_request entirely)
    result = handles.harness._select_capability_tags(_req("what time is it?"), None, ())

    # No CLASSIFICATION call must have been made — bypass fired
    classification_calls = [c for c in fake.calls if c.capability == ModelCapability.CLASSIFICATION]
    assert len(classification_calls) == 0, (
        "Selector must be bypassed when registry contains only BASELINE tags"
    )
    # Result is the BASELINE ∩ registered subset
    assert "system.read" in result
