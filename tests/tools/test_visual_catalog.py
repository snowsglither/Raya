"""Tests des 5 tools vision.* — register_visual_tools."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from raya.contracts import (
    BoundingBox,
    Confidence,
    ToolCall,
    ToolCallRequester,
    ToolResultStatus,
    ViewportInfo,
    VisualObservation,
    VisualTarget,
    new_id,
)
from raya.event_bus import EventBus
from raya.safety import AuditTrail, SafetyService, StopController
from raya.tools import ToolRegistry, execute
from raya.tools.catalog.visual import register_visual_tools


def _make_obs(description="Scene description", target=None, source="perception:screen"):
    return VisualObservation(
        source=source,
        description=description,
        artifact_ref="/tmp/capture.png",
        raw_model_output=description,
        model_used="fake-vision",
        observation_id=new_id("obs"),
        semantic_entities=["browser", "button"],
        target=target,
    )


def _make_target():
    vp = ViewportInfo(image_width=1920, image_height=1080)
    bb = BoundingBox(x_min=0.4, y_min=0.3, x_max=0.6, y_max=0.5)
    return VisualTarget(label="Play button", bbox=bb, viewport=vp,
                        observation_id=new_id("obs"), confidence=0.9)


def _safety():
    return SafetyService(StopController(EventBus()), AuditTrail())


def _make_registry(observe_fn, capture_screen_fn, capture_browser_fn):
    reg = ToolRegistry()
    register_visual_tools(reg, observe_fn, capture_screen_fn, capture_browser_fn, prefer_local=False)
    return reg


def _call(tool_name, args=None):
    return ToolCall(
        tool_name=tool_name,
        arguments=args or {},
        correlation_id="corr-test",
        requested_by=ToolCallRequester(subsystem="harness", session_id="s1"),
    )


# ─── vision.observe_screen ───────────────────────────────────────────────────

def test_observe_screen_success(tmp_path):
    png = tmp_path / "screen.png"
    png.write_bytes(b"\x89PNG\r\n" + b"\x00" * 50)

    observe_fn = MagicMock(return_value=_make_obs())
    capture_fn = MagicMock(return_value={"path": str(png), "width": 1920, "height": 1080})
    reg = _make_registry(observe_fn, capture_fn, MagicMock())

    result = execute(reg, _safety(), _call("vision.observe_screen", {"prompt": "describe the screen"}))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["description"] == "Scene description"
    assert "observation_id" in result.output


def test_observe_screen_capture_failure():
    capture_fn = MagicMock(side_effect=RuntimeError("capture failed"))
    reg = _make_registry(MagicMock(), capture_fn, MagicMock())

    result = execute(reg, _safety(), _call("vision.observe_screen"))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "SCREEN_CAPTURE_FAILED"


def test_observe_screen_vision_unavailable(tmp_path):
    png = tmp_path / "s.png"
    png.write_bytes(b"\x89PNG\r\n" + b"\x00" * 50)
    observe_fn = MagicMock(return_value=None)
    capture_fn = MagicMock(return_value={"path": str(png), "width": 100, "height": 100})
    reg = _make_registry(observe_fn, capture_fn, MagicMock())

    result = execute(reg, _safety(), _call("vision.observe_screen"))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "VISION_UNAVAILABLE"


def test_observe_screen_empty_description_fails(tmp_path):
    png = tmp_path / "s.png"
    png.write_bytes(b"\x89PNG\r\n" + b"\x00" * 50)
    obs = _make_obs(description="")
    observe_fn = MagicMock(return_value=obs)
    capture_fn = MagicMock(return_value={"path": str(png), "width": 100, "height": 100})
    reg = _make_registry(observe_fn, capture_fn, MagicMock())

    result = execute(reg, _safety(), _call("vision.observe_screen"))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "VISION_ERROR"


# ─── vision.find_on_screen ───────────────────────────────────────────────────

def test_find_on_screen_success_with_target(tmp_path):
    png = tmp_path / "s.png"
    png.write_bytes(b"\x89PNG\r\n" + b"\x00" * 50)
    obs = _make_obs(target=_make_target())
    observe_fn = MagicMock(return_value=obs)
    capture_fn = MagicMock(return_value={"path": str(png), "width": 1920, "height": 1080})
    reg = _make_registry(observe_fn, capture_fn, MagicMock())

    result = execute(reg, _safety(), _call("vision.find_on_screen", {"target": "Play button"}))
    assert result.status == ToolResultStatus.SUCCESS
    assert "target" in result.output
    assert "screen_x" in result.output["target"]
    assert "screen_y" in result.output["target"]


def test_find_on_screen_missing_target_arg():
    reg = _make_registry(MagicMock(), MagicMock(), MagicMock())
    result = execute(reg, _safety(), _call("vision.find_on_screen", {"target": ""}))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "MISSING_TARGET"


def test_find_on_screen_target_not_found(tmp_path):
    png = tmp_path / "s.png"
    png.write_bytes(b"\x89PNG\r\n" + b"\x00" * 50)
    obs = _make_obs(description="A blank page", target=None)
    observe_fn = MagicMock(return_value=obs)
    capture_fn = MagicMock(return_value={"path": str(png), "width": 1920, "height": 1080})
    reg = _make_registry(observe_fn, capture_fn, MagicMock())

    result = execute(reg, _safety(), _call("vision.find_on_screen", {"target": "Play button"}))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "TARGET_NOT_FOUND"


# ─── vision.observe_browser ──────────────────────────────────────────────────

def test_observe_browser_success(tmp_path):
    png = tmp_path / "browser.png"
    png.write_bytes(b"\x89PNG\r\n" + b"\x00" * 50)
    obs = _make_obs(description="YouTube homepage", source="perception:browser")
    observe_fn = MagicMock(return_value=obs)
    capture_fn = MagicMock(return_value={"path": str(png)})
    reg = ToolRegistry()
    register_visual_tools(reg, observe_fn, MagicMock(), capture_fn)

    result = execute(reg, _safety(), _call("vision.observe_browser"))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["description"] == "YouTube homepage"


def test_observe_browser_capture_failure():
    capture_fn = MagicMock(side_effect=RuntimeError("browser not open"))
    reg = ToolRegistry()
    register_visual_tools(reg, MagicMock(), MagicMock(), capture_fn)

    result = execute(reg, _safety(), _call("vision.observe_browser"))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "BROWSER_CAPTURE_FAILED"


# ─── vision.find_in_browser ──────────────────────────────────────────────────

def test_find_in_browser_target_found(tmp_path):
    png = tmp_path / "b.png"
    png.write_bytes(b"\x89PNG\r\n" + b"\x00" * 50)
    obs = _make_obs(source="perception:browser", target=_make_target())
    observe_fn = MagicMock(return_value=obs)
    capture_fn = MagicMock(return_value={"path": str(png)})
    reg = ToolRegistry()
    register_visual_tools(reg, observe_fn, MagicMock(), capture_fn)

    result = execute(reg, _safety(), _call("vision.find_in_browser", {"target": "Play button"}))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["target"]["label"] == "Play button"


def test_find_in_browser_target_not_found(tmp_path):
    png = tmp_path / "b.png"
    png.write_bytes(b"\x89PNG\r\n" + b"\x00" * 50)
    obs = _make_obs(source="perception:browser", target=None)
    observe_fn = MagicMock(return_value=obs)
    capture_fn = MagicMock(return_value={"path": str(png)})
    reg = ToolRegistry()
    register_visual_tools(reg, observe_fn, MagicMock(), capture_fn)

    result = execute(reg, _safety(), _call("vision.find_in_browser", {"target": "missing element"}))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "TARGET_NOT_FOUND"


# ─── vision.observe_image ────────────────────────────────────────────────────

def test_observe_image_success(tmp_path):
    png = tmp_path / "img.png"
    png.write_bytes(b"\x89PNG\r\n" + b"\x00" * 50)
    obs = _make_obs(description="A chart showing data", source="perception:image")
    observe_fn = MagicMock(return_value=obs)
    reg = ToolRegistry()
    register_visual_tools(reg, observe_fn, MagicMock(), MagicMock())

    result = execute(reg, _safety(), _call("vision.observe_image", {"path": str(png)}))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["description"] == "A chart showing data"


def test_observe_image_missing_path():
    reg = ToolRegistry()
    register_visual_tools(reg, MagicMock(), MagicMock(), MagicMock())
    result = execute(reg, _safety(), _call("vision.observe_image", {"path": ""}))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "MISSING_PATH"


# ─── Evidence / WorldState promotion ─────────────────────────────────────────

def test_observe_screen_evidence_excludes_artifact_ref(tmp_path):
    png = tmp_path / "s.png"
    png.write_bytes(b"\x89PNG\r\n" + b"\x00" * 50)
    obs = _make_obs()
    observe_fn = MagicMock(return_value=obs)
    capture_fn = MagicMock(return_value={"path": str(png), "width": 100, "height": 100})
    reg = _make_registry(observe_fn, capture_fn, MagicMock())

    result = execute(reg, _safety(), _call("vision.observe_screen"))
    assert result.status == ToolResultStatus.SUCCESS
    assert "artifact_ref" not in str(result.evidence.get("visual_observation", {}))


def test_output_never_contains_raw_file_path(tmp_path):
    """Le chemin du fichier ne doit JAMAIS apparaître dans l'output retourné au modèle."""
    png = tmp_path / "secret_screen.png"
    png.write_bytes(b"\x89PNG\r\n" + b"\x00" * 50)

    obs = VisualObservation(
        source="perception:screen",
        description="A window",
        artifact_ref=str(png),
        raw_model_output="A window",
        model_used="fake-vision",
        observation_id="obs_privacy",
    )
    observe_fn = MagicMock(return_value=obs)
    capture_fn = MagicMock(return_value={"path": str(png), "width": 100, "height": 100})
    reg = _make_registry(observe_fn, capture_fn, MagicMock())

    result = execute(reg, _safety(), _call("vision.observe_screen"))
    assert result.status == ToolResultStatus.SUCCESS
    output_str = str(result.output)
    assert str(png) not in output_str
