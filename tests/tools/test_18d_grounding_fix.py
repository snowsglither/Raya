"""Chantier 18D — targeted tests for the grounding fix (10 robustness conditions).

Tests cover:
1.  Pixel coords + viewport → VisualTarget with normalized BoundingBox
2.  Pixel coords + no viewport → None (cannot normalize)
3.  Normalized [0,1] coords → unchanged, not re-normalized
4.  Degenerate bbox after normalization → None (BoundingBox contract)
5.  Only x_max > 1.0 (partially pixel) → treated as pixel mode, normalized
6.  capture_browser_fn returns {path, width, height} → ViewportInfo passed to observe_fn
7.  capture_browser_fn returns {path} only → viewport=None (graceful degradation)
8.  capture_browser_fn raises → BROWSER_CAPTURE_FAILED result
9.  viewport.image_width=0 → None (division-by-zero guard)
10. Confidence clamped: value > 1.0 is clipped to 1.0

Run: pytest tests/tools/test_18d_grounding_fix.py -v
"""
from __future__ import annotations

import pytest


# ── 1. Pixel coords + viewport → normalized VisualTarget ─────────────────────

def test_pixel_coords_with_viewport_returns_normalized_target():
    from raya.contracts import ViewportInfo
    from raya.models.vision import _parse_grounding

    # Real data: Wikipedia 1912x948, search box
    response = 'FOUND: bbox=[239,11,456,48] label="search box" confidence=0.9'
    vp = ViewportInfo(image_width=1912, image_height=948)

    result = _parse_grounding(response, "obs1", vp)

    assert result is not None, "Must produce VisualTarget for pixel coords + viewport"
    assert 0.0 <= result.bbox.x_min < result.bbox.x_max <= 1.0
    assert 0.0 <= result.bbox.y_min < result.bbox.y_max <= 1.0
    assert result.label == "search box"
    assert abs(result.bbox.x_min - 239 / 1912) < 0.001
    assert abs(result.bbox.y_min - 11 / 948) < 0.001
    assert abs(result.bbox.x_max - 456 / 1912) < 0.001
    assert abs(result.bbox.y_max - 48 / 948) < 0.001


# ── 2. Pixel coords + no viewport → None ─────────────────────────────────────

def test_pixel_coords_without_viewport_returns_none():
    from raya.models.vision import _parse_grounding

    # Real data: GitHub 1912x948, Sign in button
    response = 'FOUND: bbox=[892,14,927,46] label="Sign in button" confidence=0.95'

    result = _parse_grounding(response, "obs2", None)
    assert result is None, "Must return None when pixel coords and no viewport"


# ── 3. Normalized [0,1] coords → unchanged ───────────────────────────────────

def test_normalized_coords_not_renormalized():
    from raya.contracts import ViewportInfo
    from raya.models.vision import _parse_grounding

    # Real data: Google 1912x948, search input normalized by gemma4
    response = 'FOUND: bbox=[0.318,0.386,0.682,0.447] label="search input field" confidence=0.95'
    vp = ViewportInfo(image_width=1912, image_height=948)

    result = _parse_grounding(response, "obs3", vp)

    assert result is not None, "Must handle normalized coords"
    assert abs(result.bbox.x_min - 0.318) < 0.001, "x_min must not be re-normalized"
    assert abs(result.bbox.y_min - 0.386) < 0.001, "y_min must not be re-normalized"
    assert abs(result.bbox.x_max - 0.682) < 0.001
    assert abs(result.bbox.y_max - 0.447) < 0.001


# ── 4. Degenerate bbox after normalization → None ────────────────────────────

def test_degenerate_bbox_after_normalization_returns_none():
    from raya.contracts import ViewportInfo
    from raya.models.vision import _parse_grounding

    # x_min == x_max after normalization: 500/1000 == 500/1000
    response = 'FOUND: bbox=[500,100,500,200] label="zero-width" confidence=0.8'
    vp = ViewportInfo(image_width=1000, image_height=1000)

    result = _parse_grounding(response, "obs4", vp)
    assert result is None, "Degenerate bbox must be rejected after normalization"


# ── 5. Only x_max > 1.0 (one coord exceeds 1.0) → treated as pixel, normalized

def test_single_coord_above_one_triggers_pixel_mode():
    from raya.contracts import ViewportInfo
    from raya.models.vision import _parse_grounding

    # x_max = 1.5 which is > 1.0 but could be an edge of a 1-normalized space
    # max(0.1, 0.1, 1.5, 0.9) > 1.0 → pixel mode → normalize
    response = 'FOUND: bbox=[0.1,0.1,1.5,0.9] label="full-width element" confidence=0.7'
    vp = ViewportInfo(image_width=1000, image_height=1000)

    # After normalization: 0.1/1000, 0.1/1000, 1.5/1000, 0.9/1000 — all tiny, valid
    result = _parse_grounding(response, "obs5", vp)
    # These normalize to very small values; degenerate check: x_min=0.0001 < x_max=0.0015 OK
    assert result is not None
    assert 0.0 <= result.bbox.x_min < result.bbox.x_max <= 1.0


# ── 6. capture_browser_fn returns dims → ViewportInfo passed to observe_fn ───

def test_capture_browser_with_dims_passes_viewport():
    from raya.contracts import Confidence, VisualObservation, VisualTarget, BoundingBox, ViewportInfo, new_id, ToolCall, ToolCallRequester
    from raya.tools import ToolRegistry
    from raya.tools.catalog.visual import register_visual_tools

    captured_viewport = {}

    def mock_capture_browser():
        return {"path": "/tmp/browser.png", "width": 1912, "height": 948}

    def mock_observe(path, prompt, find_target, corr_id, prefer_local, viewport, source):
        captured_viewport["vp"] = viewport
        if find_target:
            vp = viewport or ViewportInfo(image_width=1, image_height=1)
            return VisualObservation(
                observation_id=new_id("obs"), source="perception:browser",
                description="found", confidence=Confidence.INFERRED,
                target=VisualTarget(
                    label=find_target, confidence=0.9, observation_id="obs",
                    bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5),
                    viewport=vp,
                ),
            )
        return VisualObservation(
            observation_id=new_id("obs"), source="perception:browser",
            description="desc", confidence=Confidence.INFERRED,
        )

    reg = ToolRegistry()
    register_visual_tools(reg, mock_observe, lambda: {"path": ""}, mock_capture_browser)

    tc = ToolCall(
        tool_name="vision.find_in_browser", arguments={"target": "search box"},
        correlation_id="test", requested_by=ToolCallRequester(subsystem="test", session_id="s"),
    )
    reg.handler_for("vision.find_in_browser")(tc)

    assert captured_viewport.get("vp") is not None, "ViewportInfo must be passed when dims available"
    assert captured_viewport["vp"].image_width == 1912
    assert captured_viewport["vp"].image_height == 948


# ── 7. capture_browser_fn returns no dims → viewport=None (graceful) ─────────

def test_capture_browser_without_dims_passes_none_viewport():
    from raya.contracts import Confidence, VisualObservation, new_id, ToolCall, ToolCallRequester
    from raya.tools import ToolRegistry
    from raya.tools.catalog.visual import register_visual_tools

    captured_viewport = {}

    def mock_capture_browser():
        return {"path": "/tmp/browser.png"}  # no width/height

    def mock_observe(path, prompt, find_target, corr_id, prefer_local, viewport, source):
        captured_viewport["vp"] = viewport
        return VisualObservation(
            observation_id=new_id("obs"), source="perception:browser",
            description="desc", confidence=Confidence.INFERRED,
        )

    reg = ToolRegistry()
    register_visual_tools(reg, mock_observe, lambda: {"path": ""}, mock_capture_browser)

    tc = ToolCall(
        tool_name="vision.find_in_browser", arguments={"target": "button"},
        correlation_id="test", requested_by=ToolCallRequester(subsystem="test", session_id="s"),
    )
    reg.handler_for("vision.find_in_browser")(tc)

    assert captured_viewport.get("vp") is None, "viewport=None when dims absent (graceful degradation)"


# ── 8. capture_browser_fn raises → BROWSER_CAPTURE_FAILED ────────────────────

def test_capture_browser_exception_returns_error_result():
    from raya.contracts import ToolResultStatus, ToolCall, ToolCallRequester
    from raya.tools import ToolRegistry
    from raya.tools.catalog.visual import register_visual_tools

    def exploding_capture():
        raise RuntimeError("Playwright not running")

    reg = ToolRegistry()
    register_visual_tools(reg, lambda *a, **k: None, lambda: {"path": ""}, exploding_capture)

    tc = ToolCall(
        tool_name="vision.find_in_browser", arguments={"target": "button"},
        correlation_id="test", requested_by=ToolCallRequester(subsystem="test", session_id="s"),
    )
    result = reg.handler_for("vision.find_in_browser")(tc)

    assert result.status == ToolResultStatus.FAILURE
    assert result.error is not None
    assert result.error.code == "BROWSER_CAPTURE_FAILED"


# ── 9. viewport.image_width=0 → None (zero-dimension guard) ──────────────────

def test_viewport_zero_dimension_returns_none():
    from raya.contracts import ViewportInfo
    from raya.models.vision import _parse_grounding

    # Pixel coords but viewport has image_width=0 → cannot normalize
    response = 'FOUND: bbox=[200,100,400,200] label="button" confidence=0.8'
    vp_zero_w = ViewportInfo(image_width=0, image_height=948)

    result = _parse_grounding(response, "obs9w", vp_zero_w)
    assert result is None, "viewport with image_width=0 must return None"

    vp_zero_h = ViewportInfo(image_width=1912, image_height=0)
    result2 = _parse_grounding(response, "obs9h", vp_zero_h)
    assert result2 is None, "viewport with image_height=0 must return None"


# ── 10. Confidence clamped to [0,1] ──────────────────────────────────────────

def test_confidence_clamped_to_valid_range():
    from raya.contracts import ViewportInfo
    from raya.models.vision import _parse_grounding

    # Normalized coords; test that confidence > 1.0 is clamped down to 1.0
    response_high = 'FOUND: bbox=[0.1,0.1,0.5,0.5] label="element" confidence=1.5'
    # Regex ([0-9.]+) requires a leading digit — "confidence=0.0" is the floor
    response_zero = 'FOUND: bbox=[0.1,0.1,0.5,0.5] label="element" confidence=0.0'
    vp = ViewportInfo(image_width=100, image_height=100)

    result_high = _parse_grounding(response_high, "obs10h", vp)
    result_zero = _parse_grounding(response_zero, "obs10z", vp)

    assert result_high is not None
    assert result_high.confidence <= 1.0, "Confidence > 1.0 must be clamped to 1.0"
    assert result_high.confidence == 1.0

    assert result_zero is not None
    assert result_zero.confidence == 0.0, "Confidence 0.0 must not be altered"
