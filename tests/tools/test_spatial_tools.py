"""scene.* Tools (RAYA V2 Phase 8) — enregistrement direct, sans Harness,
même discipline que test_ui_view_tools.py. Les scénarios bout-en-bout (via
un vrai Harness scripté) vivent dans tests/harness/test_spatial_integration.py."""

from __future__ import annotations

from raya.contracts import PermissionLevel, ToolCall, ToolCallRequester, ToolResultStatus
from raya.event_bus import EventBus
from raya.spatial import SceneStore
from raya.tools.catalog.spatial import register_spatial_tools
from raya.tools.registry import ToolRegistry
from raya.safety.risk import classify_risk


def _setup(tmp_path):
    registry = ToolRegistry()
    scene_store = SceneStore()
    bus = EventBus()
    register_spatial_tools(registry, scene_store, bus, tmp_path / "workspace")
    return registry, scene_store, bus


def _call(tool_name: str, arguments: dict, session_id: str = "s1") -> ToolCall:
    return ToolCall(tool_name=tool_name, arguments=arguments, correlation_id="corr1",
                     requested_by=ToolCallRequester(subsystem="harness", session_id=session_id))


def test_all_spatial_tags_are_safe_never_require_confirmation():
    for tag in ("spatial.create", "spatial.modify", "spatial.read", "spatial.render", "spatial.export"):
        assert classify_risk([tag]) == PermissionLevel.SAFE


def test_scene_create_returns_real_scene_id(tmp_path):
    registry, scene_store, bus = _setup(tmp_path)
    result = registry.handler_for("scene.create")(_call("scene.create", {"label": "Solar System"}))
    assert result.status == ToolResultStatus.SUCCESS
    assert scene_store.get_scene(result.output["scene_id"]) is not None


def test_scene_add_object_and_describe(tmp_path):
    registry, scene_store, bus = _setup(tmp_path)
    scene_id = registry.handler_for("scene.create")(_call("scene.create", {})).output["scene_id"]
    add_result = registry.handler_for("scene.add_object")(
        _call("scene.add_object", {"scene_id": scene_id, "label": "Sun", "object_type": "star"})
    )
    assert add_result.status == ToolResultStatus.SUCCESS
    assert add_result.evidence["object_count"] == 1

    describe_result = registry.handler_for("scene.describe")(_call("scene.describe", {"scene_id": scene_id}))
    assert describe_result.output["object_count"] == 1


def test_scene_add_object_unknown_scene_fails_honestly(tmp_path):
    registry, scene_store, bus = _setup(tmp_path)
    result = registry.handler_for("scene.add_object")(
        _call("scene.add_object", {"scene_id": "ghost", "label": "X"})
    )
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "SPATIAL_ERROR"


def test_scene_update_object_transform(tmp_path):
    registry, scene_store, bus = _setup(tmp_path)
    scene_id = registry.handler_for("scene.create")(_call("scene.create", {})).output["scene_id"]
    obj_id = registry.handler_for("scene.add_object")(
        _call("scene.add_object", {"scene_id": scene_id, "label": "Cube"})
    ).output["object_id"]
    result = registry.handler_for("scene.update_object")(
        _call("scene.update_object", {"scene_id": scene_id, "object_id": obj_id, "position": {"x": 5.0}})
    )
    assert result.status == ToolResultStatus.SUCCESS
    assert scene_store.get_scene(scene_id).objects[obj_id].transform.position.x == 5.0


def test_scene_remove_object(tmp_path):
    registry, scene_store, bus = _setup(tmp_path)
    scene_id = registry.handler_for("scene.create")(_call("scene.create", {})).output["scene_id"]
    obj_id = registry.handler_for("scene.add_object")(
        _call("scene.add_object", {"scene_id": scene_id, "label": "Cube"})
    ).output["object_id"]
    result = registry.handler_for("scene.remove_object")(
        _call("scene.remove_object", {"scene_id": scene_id, "object_id": obj_id})
    )
    assert result.status == ToolResultStatus.SUCCESS
    assert result.evidence["object_count"] == 0


def test_scene_render_mounts_and_publishes_ui_view_requested(tmp_path):
    registry, scene_store, bus = _setup(tmp_path)
    received = []
    bus.subscribe("ui.view_requested", lambda e: received.append(e), subscriber="test")
    scene_id = registry.handler_for("scene.create")(_call("scene.create", {})).output["scene_id"]

    result = registry.handler_for("scene.render")(_call("scene.render", {"scene_id": scene_id}, session_id="ui-session"))
    bus.wait_idle(timeout_s=1.0)

    assert result.status == ToolResultStatus.SUCCESS
    assert scene_store.mounted_scene_id("ui-session") == scene_id
    assert len(received) == 1
    assert received[0].payload == {"session_id": "ui-session", "view": "spatial", "action": "show"}


def test_scene_close_unmounts_and_publishes_hide(tmp_path):
    registry, scene_store, bus = _setup(tmp_path)
    received = []
    bus.subscribe("ui.view_requested", lambda e: received.append(e), subscriber="test")
    scene_id = registry.handler_for("scene.create")(_call("scene.create", {})).output["scene_id"]
    registry.handler_for("scene.render")(_call("scene.render", {"scene_id": scene_id}, session_id="ui-session"))

    result = registry.handler_for("scene.close")(_call("scene.close", {"scene_id": scene_id}, session_id="ui-session"))
    bus.wait_idle(timeout_s=1.0)

    assert result.status == ToolResultStatus.SUCCESS
    assert scene_store.mounted_scene_id("ui-session") is None
    assert received[-1].payload["action"] == "hide"


def test_scene_export_returns_json_without_file_by_default(tmp_path):
    registry, scene_store, bus = _setup(tmp_path)
    scene_id = registry.handler_for("scene.create")(_call("scene.create", {"label": "X"})).output["scene_id"]
    result = registry.handler_for("scene.export")(_call("scene.export", {"scene_id": scene_id}))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["scene"]["label"] == "X"
    assert result.evidence["exported"] is False


def test_scene_export_writes_real_sandboxed_file_when_filename_given(tmp_path):
    registry, scene_store, bus = _setup(tmp_path)
    scene_id = registry.handler_for("scene.create")(_call("scene.create", {"label": "X"})).output["scene_id"]
    result = registry.handler_for("scene.export")(
        _call("scene.export", {"scene_id": scene_id, "filename": "out/scene.json"})
    )
    assert result.status == ToolResultStatus.SUCCESS
    real_file = tmp_path / "workspace" / "out" / "scene.json"
    assert real_file.exists()
    import json

    assert json.loads(real_file.read_text(encoding="utf-8"))["label"] == "X"


def test_scene_export_rejects_path_traversal(tmp_path):
    registry, scene_store, bus = _setup(tmp_path)
    scene_id = registry.handler_for("scene.create")(_call("scene.create", {})).output["scene_id"]
    result = registry.handler_for("scene.export")(
        _call("scene.export", {"scene_id": scene_id, "filename": "../../evil.json"})
    )
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "INVALID_ARGUMENT"


def test_all_spatial_tools_declared_as_safe_permission_level(tmp_path):
    registry, scene_store, bus = _setup(tmp_path)
    for name in ("scene.create", "scene.add_object", "scene.update_object", "scene.remove_object",
                 "scene.describe", "scene.render", "scene.close", "scene.export"):
        assert registry.get(name).permission_level == PermissionLevel.SAFE
