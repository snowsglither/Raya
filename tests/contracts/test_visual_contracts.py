"""Tests des contrats visuels — BoundingBox, ViewportInfo, VisualTarget, VisualObservation."""

from __future__ import annotations

import pytest

from raya.contracts import (
    BoundingBox,
    Confidence,
    ViewportInfo,
    VisualObservation,
    VisualTarget,
)


# ─── BoundingBox ─────────────────────────────────────────────────────────────

def test_bounding_box_normalized_valid():
    bb = BoundingBox(x_min=0.1, y_min=0.2, x_max=0.8, y_max=0.9)
    assert bb.x_min == 0.1
    assert bb.x_max == 0.8


def test_bounding_box_rejects_out_of_range():
    with pytest.raises(ValueError):
        BoundingBox(x_min=-0.1, y_min=0.0, x_max=0.5, y_max=0.5)


def test_bounding_box_rejects_inverted():
    with pytest.raises(ValueError):
        BoundingBox(x_min=0.8, y_min=0.1, x_max=0.2, y_max=0.9)


def test_bounding_box_rejects_degenerate():
    with pytest.raises(ValueError):
        BoundingBox(x_min=0.5, y_min=0.1, x_max=0.5, y_max=0.9)


def test_bounding_box_to_pixel():
    bb = BoundingBox(x_min=0.0, y_min=0.0, x_max=0.5, y_max=1.0)
    x0, y0, x1, y1 = bb.to_pixel(width=100, height=200)
    assert x0 == 0 and y0 == 0 and x1 == 50 and y1 == 200


def test_bounding_box_center_pixel():
    bb = BoundingBox(x_min=0.0, y_min=0.0, x_max=1.0, y_max=1.0)
    cx, cy = bb.center_pixel(width=200, height=100)
    assert cx == 100 and cy == 50


# ─── ViewportInfo ─────────────────────────────────────────────────────────────

def test_viewport_default_fields():
    vp = ViewportInfo(image_width=1920, image_height=1080)
    assert vp.window_left == 0
    assert vp.window_top == 0
    assert vp.device_pixel_ratio == 1.0


# ─── VisualTarget ─────────────────────────────────────────────────────────────

def test_visual_target_screen_coordinates_no_offset():
    vp = ViewportInfo(image_width=1920, image_height=1080)
    bb = BoundingBox(x_min=0.5, y_min=0.5, x_max=0.6, y_max=0.6)
    target = VisualTarget(label="button", bbox=bb, viewport=vp, observation_id="o1", confidence=0.9)
    sx, sy = target.screen_coordinates()
    # center = (0.55, 0.55) × (1920, 1080) = (1056, 594)
    assert sx == 1056
    assert sy == 594


def test_visual_target_screen_coordinates_with_window_offset():
    vp = ViewportInfo(image_width=800, image_height=600, window_left=100, window_top=50)
    bb = BoundingBox(x_min=0.0, y_min=0.0, x_max=1.0, y_max=1.0)
    target = VisualTarget(label="center", bbox=bb, viewport=vp, observation_id="o2", confidence=0.8)
    sx, sy = target.screen_coordinates()
    assert sx == 100 + 400
    assert sy == 50 + 300


def test_visual_target_confidence_clamped():
    vp = ViewportInfo(image_width=100, image_height=100)
    bb = BoundingBox(x_min=0.1, y_min=0.1, x_max=0.9, y_max=0.9)
    # confidence is already valid
    t = VisualTarget(label="x", bbox=bb, viewport=vp, observation_id="o3", confidence=0.5)
    assert 0.0 <= t.confidence <= 1.0


# ─── VisualObservation ────────────────────────────────────────────────────────

def test_visual_observation_requires_perception_source():
    with pytest.raises(ValueError, match="perception:"):
        VisualObservation(
            source="llm:direct",  # invalide
            description="test",
            artifact_ref="/tmp/img.png",
            raw_model_output="test",
            model_used="gemma4",
            observation_id="obs1",
        )


def test_visual_observation_confidence_always_inferred():
    obs = VisualObservation(
        source="perception:screen",
        description="a window",
        artifact_ref="/tmp/img.png",
        raw_model_output="a window",
        model_used="gemma4",
        observation_id="obs2",
    )
    assert obs.confidence == Confidence.INFERRED


def test_visual_observation_confidence_cannot_be_overridden():
    with pytest.raises(ValueError):
        VisualObservation(
            source="perception:screen",
            description="test",
            artifact_ref="/tmp/img.png",
            raw_model_output="test",
            model_used="gemma4",
            observation_id="obs3",
            confidence=Confidence.KNOWN_FACT,
        )


def test_visual_observation_to_world_state_value_excludes_artifact():
    obs = VisualObservation(
        source="perception:screen",
        description="Desktop with browser open",
        artifact_ref="/private/path/screen.png",
        raw_model_output="Desktop with browser open",
        model_used="gemma4",
        observation_id="obs4",
        semantic_entities=["browser", "Desktop"],
    )
    wsv = obs.to_world_state_value()
    assert "artifact_ref" not in wsv
    assert "raw_model_output" not in wsv
    assert wsv["description"] == "Desktop with browser open"
    assert "browser" in wsv.get("semantic_entities", [])


def test_visual_observation_to_world_state_value_excludes_target_path():
    obs = VisualObservation(
        source="perception:browser",
        description="YouTube player",
        artifact_ref="/tmp/capture.png",
        raw_model_output="YouTube player",
        model_used="gemma4",
        observation_id="obs5",
    )
    wsv = obs.to_world_state_value()
    assert "/tmp" not in str(wsv)
