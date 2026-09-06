"""Priorité A — Contracts : sérialisation, désérialisation, validation, invariants."""

from __future__ import annotations

import pytest

from raya.contracts import (
    Confidence,
    Context,
    ContextSection,
    ErrorInfo,
    Event,
    ExecutionRecord,
    ExecutionState,
    FactStatus,
    FinishReason,
    Freshness,
    GrantedBy,
    ModelResponse,
    Geometry,
    Material,
    ObservationSpec,
    PerceptionObservation,
    Permission,
    PermissionDecision,
    PermissionLevel,
    Scene,
    SectionKind,
    SpatialError,
    SpatialObject,
    Task,
    TaskOwner,
    TaskState,
    Tool,
    Transform,
    Vec3,
    ToolResult,
    ToolResultStatus,
    WorldStateFact,
    can_transition,
    from_dict,
    slugify_object_id,
    to_dict,
    to_json,
    from_json,
    validate_object_id,
)


def test_event_roundtrip():
    e = Event(type="task.completed", source="tasks", payload={"x": 1}, correlation_id="corr_1")
    e2 = from_dict(Event, to_dict(e))
    assert e2.id == e.id
    assert e2.type == e.type
    assert e2.payload == {"x": 1}


def test_event_type_requires_dot():
    with pytest.raises(ValueError):
        Event(type="badtype", source="x")


def test_world_state_fact_expiry():
    import time

    fact = WorldStateFact(
        domain="pc", key="foreground_window", value="Chrome", source="perception:window",
        confidence=Confidence.KNOWN_FACT, freshness_ttl_s=0,
    )
    time.sleep(0.01)
    assert fact.is_expired() is True


def test_context_section_requires_freshness_for_world_state():
    with pytest.raises(ValueError):
        ContextSection(kind=SectionKind.WORLD_STATE, content={}, provenance="x")


def test_context_section_freshness_optional_for_other_kinds():
    sec = ContextSection(kind=SectionKind.SYSTEM_RULES, content={}, provenance="x")
    assert sec.freshness is None


# --- Phase 7 : Perception / Observation contracts ---

def test_world_state_fact_rejects_hypothesis_from_perception_source():
    with pytest.raises(ValueError):
        WorldStateFact(domain="pc", key="k", value="v", source="perception:foreground_window", confidence=Confidence.HYPOTHESIS)


def test_world_state_fact_allows_hypothesis_from_non_perception_source():
    fact = WorldStateFact(domain="pc", key="k", value="v", source="cognition:inference", confidence=Confidence.HYPOTHESIS)
    assert fact.confidence == Confidence.HYPOTHESIS


def test_perception_observation_roundtrip():
    obs = PerceptionObservation(domain="pc", key="active_window", value={"title": "Notepad"}, source="perception:foreground_window")
    obs2 = from_dict(PerceptionObservation, to_dict(obs))
    assert obs2 == obs


def test_perception_observation_rejects_hypothesis():
    with pytest.raises(ValueError):
        PerceptionObservation(domain="pc", key="k", value="v", source="perception:x", confidence=Confidence.HYPOTHESIS)


def test_perception_observation_requires_perception_source_prefix():
    with pytest.raises(ValueError):
        PerceptionObservation(domain="pc", key="k", value="v", source="tool:pc.window.list")


def test_observation_spec_roundtrip():
    spec = ObservationSpec(domain="pc", key="active_window", evidence_field="window", expected_argument="target")
    spec2 = from_dict(ObservationSpec, to_dict(spec))
    assert spec2 == spec


def test_tool_observation_defaults_to_empty_tuple():
    tool = Tool(name="pc.window.list", description="d", capability_tags=["pc.read"],
                input_schema={}, output_schema={}, permission_level=PermissionLevel.SAFE)
    assert tool.observation == ()


def test_tool_observation_can_declare_specs():
    spec = ObservationSpec(domain="pc", key="active_window", evidence_field="window")
    tool = Tool(name="pc.application.launch", description="d", capability_tags=["pc.launch"],
                input_schema={}, output_schema={}, permission_level=PermissionLevel.SAFE, observation=(spec,))
    assert tool.observation == (spec,)


def test_context_freshness_roundtrip_preserved():
    sec = ContextSection(
        kind=SectionKind.WORLD_STATE, content={"v": 1}, provenance="perception:window",
        freshness=Freshness(status=FactStatus.STALE, as_of="2026-09-04T00:00:00.000Z"),
    )
    ctx = Context(session_id="s1", budget_tokens=100, sections=[sec])
    ctx2 = from_dict(Context, to_dict(ctx))
    assert ctx2.sections[0].freshness.status == FactStatus.STALE


def test_context_budget_invariant():
    with pytest.raises(ValueError):
        Context(session_id="s1", budget_tokens=10, used_tokens_estimate=11)


def test_task_transition_allowed():
    assert can_transition(TaskState.PENDING, TaskState.RUNNING)
    assert not can_transition(TaskState.COMPLETED, TaskState.RUNNING)


def test_task_transition_terminal_never_reactivates():
    t = Task(objective="x", owner=TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    t.transition_to(TaskState.RUNNING)
    t.transition_to(TaskState.COMPLETED)
    with pytest.raises(ValueError):
        t.transition_to(TaskState.RUNNING)


# --- Chantier 15 (Axe D) : TaskState.BLOCKED, distinct de FAILED ---

def test_running_can_transition_to_blocked_and_resume():
    t = Task(objective="x", owner=TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    t.transition_to(TaskState.RUNNING)
    t.transition_to(TaskState.BLOCKED)
    assert t.state == TaskState.BLOCKED
    t.transition_to(TaskState.RUNNING)  # résumable, comme PAUSED -- jamais terminal
    assert t.state == TaskState.RUNNING


def test_blocked_can_be_cancelled_but_never_directly_completed():
    t = Task(objective="x", owner=TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    t.transition_to(TaskState.RUNNING)
    t.transition_to(TaskState.BLOCKED)
    with pytest.raises(ValueError):
        t.transition_to(TaskState.COMPLETED)  # doit d'abord repasser par RUNNING
    t.transition_to(TaskState.CANCELLED)
    assert t.state == TaskState.CANCELLED


def test_tool_result_success_forbids_error():
    with pytest.raises(ValueError):
        ToolResult(tool_call_id="tc1", status=ToolResultStatus.SUCCESS, error=ErrorInfo(code="x", message="y"))


def test_tool_result_failure_requires_error():
    with pytest.raises(ValueError):
        ToolResult(tool_call_id="tc1", status=ToolResultStatus.FAILURE)


def test_permission_destructive_requires_granted_by():
    with pytest.raises(ValueError):
        Permission(
            action_ref="rm -rf", risk_level=PermissionLevel.DESTRUCTIVE,
            decision=PermissionDecision.ALLOWED, reason="x",
        )
    # avec granted_by, ça passe
    Permission(
        action_ref="rm -rf", risk_level=PermissionLevel.DESTRUCTIVE,
        decision=PermissionDecision.ALLOWED, reason="x", granted_by=GrantedBy.USER_CONFIRMATION,
    )


def test_model_response_error_requires_error_info():
    with pytest.raises(ValueError):
        ModelResponse(request_id="r1", provider_used="x", content=[], finish_reason=FinishReason.ERROR)


def test_execution_record_reinterpret_after_restart():
    rec = ExecutionRecord(
        operation_id="op1", tool_call_id="tc1", correlation_id="c1", idempotency_key="k1",
        execution_state=ExecutionState.EXECUTING,
    )
    rec.reinterpret_after_restart()
    assert rec.execution_state == ExecutionState.UNKNOWN


def test_json_roundtrip_generic():
    e = Event(type="perception.window_changed", source="perception", payload={"window": "Chrome"})
    raw = to_json(e)
    e2 = from_json(Event, raw)
    assert e2.payload == e.payload
    assert e2.type == e.type


# --- Phase 8 : Spatial Scene Model ---

def test_vec3_defaults_to_origin():
    assert Vec3() == Vec3(0.0, 0.0, 0.0)


def test_transform_defaults_scale_to_one_not_zero():
    t = Transform()
    assert t.scale == Vec3(1.0, 1.0, 1.0)
    assert t.position == Vec3(0.0, 0.0, 0.0)


def test_spatial_object_to_dict_serializes_nested_transform_and_geometry():
    obj = SpatialObject(
        id="earth", label="Earth", object_type="planet", parent="sun",
        transform=Transform(position=Vec3(10, 0, 0)),
        geometry=Geometry(kind="sphere", params={"radius": 2.0}),
        material=Material(color="#3355ff"),
        relationships={"orbits": "sun"},
    )
    d = to_dict(obj)
    assert d["id"] == "earth"
    assert d["transform"]["position"] == {"x": 10.0, "y": 0.0, "z": 0.0}
    assert d["geometry"] == {"kind": "sphere", "params": {"radius": 2.0}}
    assert d["material"] == {"color": "#3355ff", "opacity": 1.0}
    assert d["relationships"] == {"orbits": "sun"}


def test_scene_to_dict_serializes_objects_dict_recursively():
    scene = Scene(label="Solar System")
    scene.objects["sun"] = SpatialObject(id="sun", label="Sun")
    scene.root_ids.append("sun")
    d = to_dict(scene)
    assert d["label"] == "Solar System"
    assert d["objects"]["sun"]["label"] == "Sun"
    assert d["root_ids"] == ["sun"]


def test_validate_object_id_rejects_invalid_characters():
    with pytest.raises(SpatialError):
        validate_object_id("has a space")


def test_validate_object_id_accepts_snake_case():
    validate_object_id("server_01")  # ne doit pas lever


def test_slugify_object_id_deduplicates():
    existing = {"cube"}
    assert slugify_object_id("Cube", existing=existing) == "cube_2"


def test_slugify_object_id_handles_empty_label():
    assert slugify_object_id("") == "object"
