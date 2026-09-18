"""Audit tests for multimodal repair — GAP 1 (grounding) and GAP 2 (DOM/WorldState).

8 new automated tests covering:
- Pixel coord normalization (current behavior = fail, proposed fix behavior = pass)
- capture_browser_fn missing dimensions (current gap)
- capture_screen_fn has dimensions (working)
- DOM title absent from WorldState (documented asymmetry)
- URL present in WorldState after navigate (working)

These tests document CURRENT behavior. Where the current behavior is the documented gap,
the test is marked xfail to signal "this is what we're fixing".
Run: pytest tests/audit/test_multimodal_repair_audit.py -v
"""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

import pytest

# ── Helper: simulated _parse_grounding with proposed fix ─────────────────────

def _parse_grounding_with_normalization(response_text: str, observation_id: str, viewport):
    """Simulated _parse_grounding with the proposed normalization fix.
    Used to DOCUMENT the desired behavior without modifying source code."""
    from raya.contracts import BoundingBox, ViewportInfo, VisualTarget

    _FOUND_PATTERN = re.compile(
        r"FOUND:\s*bbox=\[([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+)\]"
        r'\s+label="([^"]+)"\s+confidence=([0-9.]+)',
        re.IGNORECASE,
    )
    m = _FOUND_PATTERN.search(response_text)
    if m is None:
        return None
    try:
        x_min, y_min, x_max, y_max = float(m.group(1)), float(m.group(2)), float(m.group(3)), float(m.group(4))
        label = m.group(5).strip()
        confidence = float(m.group(6))
    except ValueError:
        return None

    if max(x_min, y_min, x_max, y_max) > 1.0:
        if viewport is None:
            return None  # Cannot normalize without dimensions
        x_min = x_min / viewport.image_width
        y_min = y_min / viewport.image_height
        x_max = x_max / viewport.image_width
        y_max = y_max / viewport.image_height

    try:
        bbox = BoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)
    except (ValueError, Exception):
        return None

    vp = viewport or ViewportInfo(image_width=1, image_height=1)
    return VisualTarget(
        label=label, bbox=bbox, viewport=vp,
        observation_id=observation_id,
        confidence=min(1.0, max(0.0, confidence)),
    )


# ── T1: Proposed fix: pixel coords + viewport → normalized VisualTarget ───────

def test_parse_grounding_normalizes_pixel_coords_with_viewport():
    """Fix confirmed (Chantier 18D): pixel coordinates (>1.0) are normalized using viewport dims.
    GAP 1 is resolved — _parse_grounding now produces a valid VisualTarget for pixel coords.
    """
    from raya.contracts import ViewportInfo
    from raya.models.vision import _parse_grounding

    # Real data: Wikipedia search box returned by gemma4:cloud on 1912x948
    response = 'FOUND: bbox=[241,17,453,52] label="search box" confidence=0.9\n\nWikipedia page.'
    viewport = ViewportInfo(image_width=1912, image_height=948)

    result = _parse_grounding(response, "obs1", viewport)
    assert result is not None, "After fix: pixel coords + viewport must produce a VisualTarget"
    assert 0 <= result.bbox.x_min < result.bbox.x_max <= 1.0
    assert 0 <= result.bbox.y_min < result.bbox.y_max <= 1.0
    assert result.label == "search box"
    assert abs(result.bbox.x_min - 241/1912) < 0.001
    assert abs(result.bbox.y_min - 17/948) < 0.001

    # Simulation still valid for reference
    fixed_result = _parse_grounding_with_normalization(response, "obs1", viewport)
    assert fixed_result is not None
    assert abs(result.bbox.x_min - fixed_result.bbox.x_min) < 0.0001


# ── T2: Proposed fix: pixel coords + no viewport → None (cannot normalize) ───

def test_parse_grounding_returns_none_pixel_no_viewport():
    """Pixel coords without viewport: cannot normalize → return None.
    This is correct behavior in both current and proposed implementations.
    """
    from raya.models.vision import _parse_grounding

    # GitHub Sign in button with pixel coords (real data from gemma4:cloud)
    response = 'FOUND: bbox=[892,14,927,46] label="Sign in button" confidence=0.95'

    # Current behavior: None (pixel rejected by BoundingBox)
    current = _parse_grounding(response, "obs2", None)
    assert current is None

    # Proposed fix behavior: also None (no viewport to normalize)
    fixed = _parse_grounding_with_normalization(response, "obs2", None)
    assert fixed is None, "Without viewport, pixel coords cannot be normalized"


# ── T3: Normalized coords pass through unchanged in both implementations ──────

def test_parse_grounding_normalized_unchanged():
    """Normalized [0,1] coords work in current implementation and remain unchanged."""
    from raya.contracts import ViewportInfo
    from raya.models.vision import _parse_grounding

    # Real data: Google search box returned normalized by gemma4:cloud
    response = 'FOUND: bbox=[0.318,0.386,0.682,0.447] label="search input field" confidence=0.95'
    viewport = ViewportInfo(image_width=1912, image_height=948)

    # Both current and proposed behave identically for normalized coords
    current = _parse_grounding(response, "obs3", viewport)
    fixed = _parse_grounding_with_normalization(response, "obs3", viewport)

    assert current is not None, "Current implementation handles normalized coords"
    assert fixed is not None, "Proposed fix handles normalized coords"
    assert abs(current.bbox.x_min - 0.318) < 0.001
    assert abs(fixed.bbox.x_min - 0.318) < 0.001


# ── T4: Degenerate bbox rejected after normalization ─────────────────────────

def test_normalization_preserves_degenerate_rejection():
    """A bbox that is degenerate after normalization is still rejected.
    Proposed fix does not weaken BoundingBox contract.
    """
    from raya.contracts import ViewportInfo

    # Degenerate: x_min == x_max after normalization
    response = 'FOUND: bbox=[500,100,500,200] label="zero-width" confidence=0.8'
    viewport = ViewportInfo(image_width=1000, image_height=1000)

    fixed = _parse_grounding_with_normalization(response, "obs4", viewport)
    # After normalization: [0.5, 0.1, 0.5, 0.2] → x_min=x_max → degenerate
    assert fixed is None, "Degenerate bbox (x_min==x_max after normalization) must be rejected"


# ── T5: vision.find_on_screen has viewport from capture ──────────────────────

def test_find_on_screen_has_viewport_from_capture():
    """vision.find_on_screen correctly extracts viewport from capture_screen_fn output.
    capture_screen_fn returns {"path", "width", "height"} → ViewportInfo created.
    """
    from raya.contracts import Confidence, VisualObservation, VisualTarget, BoundingBox, ViewportInfo, new_id, ToolCall, ToolCallRequester
    from raya.tools import ToolRegistry
    from raya.tools.catalog.visual import register_visual_tools

    captured_viewport = {}

    def mock_capture_screen():
        return {"path": "/tmp/fake.png", "width": 1920, "height": 1080}

    def mock_observe(path, prompt, find_target, corr_id, prefer_local, viewport, source):
        captured_viewport["viewport"] = viewport
        if find_target:
            return VisualObservation(
                observation_id=new_id("obs"), source="perception:screen",
                description="test", confidence=Confidence.INFERRED,
                target=VisualTarget(
                    label="test", confidence=0.9, observation_id="obs",
                    bbox=BoundingBox(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5),
                    viewport=viewport or ViewportInfo(image_width=1, image_height=1),
                ),
            )
        return VisualObservation(
            observation_id=new_id("obs"), source="perception:screen",
            description="desc", confidence=Confidence.INFERRED,
        )

    reg = ToolRegistry()
    register_visual_tools(reg, mock_observe, mock_capture_screen, lambda: {"path": ""})

    tc = ToolCall(
        tool_name="vision.find_on_screen", arguments={"target": "button"},
        correlation_id="test",
        requested_by=ToolCallRequester(subsystem="test", session_id="s"),
    )
    reg.handler_for("vision.find_on_screen")(tc)

    assert captured_viewport.get("viewport") is not None, (
        "vision.find_on_screen must pass ViewportInfo from capture_screen_fn dimensions"
    )
    assert captured_viewport["viewport"].image_width == 1920
    assert captured_viewport["viewport"].image_height == 1080


# ── T6: vision.find_in_browser has NO viewport (current gap) ─────────────────

def test_find_in_browser_no_viewport_from_capture():
    """Current gap: capture_browser_fn returns only {"path"}, no dimensions.
    vision.find_in_browser passes viewport=None → normalization impossible.
    """
    from raya.contracts import Confidence, VisualObservation, new_id, ToolCall, ToolCallRequester
    from raya.tools import ToolRegistry
    from raya.tools.catalog.visual import register_visual_tools

    captured_viewport = {}

    def mock_capture_browser():
        # Current bootstrap behavior: only path, no dimensions
        return {"path": "/tmp/fake_browser.png"}

    def mock_observe(path, prompt, find_target, corr_id, prefer_local, viewport, source):
        captured_viewport["viewport"] = viewport
        return VisualObservation(
            observation_id=new_id("obs"), source="perception:browser",
            description="desc", confidence=Confidence.INFERRED,
        )

    reg = ToolRegistry()
    register_visual_tools(reg, mock_observe, lambda: {"path": ""}, mock_capture_browser)

    tc = ToolCall(
        tool_name="vision.find_in_browser", arguments={"target": "button"},
        correlation_id="test",
        requested_by=ToolCallRequester(subsystem="test", session_id="s"),
    )
    reg.handler_for("vision.find_in_browser")(tc)

    assert captured_viewport.get("viewport") is None, (
        "Current gap: vision.find_in_browser passes viewport=None "
        "because capture_browser_fn does not return image dimensions."
    )


# ── T7: browser.read_page output has title, but NOT in WorldState ─────────────

def test_browser_read_page_output_contains_title_not_in_worldstate():
    """Asymmetry: browser.read_page output has 'title' field but observation=().
    Title is never promoted to WorldState — this is the documented gap 2.
    """
    from raya.tools.catalog.browser import register_browser_tools
    from raya.tools import ToolRegistry
    from raya.contracts import ToolCall, ToolCallRequester, ToolResultStatus

    class FakeBrowserAgent:
        def execute(self, cmd, should_stop=None):
            from raya.contracts import CommandStatus
            class R:
                status = CommandStatus.SUCCESS
                output = {
                    "url": "https://en.wikipedia.org/wiki/Python",
                    "title": "Python - Wikipedia",
                    "cookie_banner": False,
                    "buttons": [], "links": [], "inputs": [], "status": "ok"
                }
                evidence = None
                error = None
            return R()

    reg = ToolRegistry()
    register_browser_tools(reg, FakeBrowserAgent(), lambda: False)

    tool_def = reg.get("browser.read_page")
    assert tool_def is not None

    # observation=() means nothing is promoted to WorldState
    assert len(tool_def.observation) == 0, (
        "browser.read_page has observation=() — DOM content including title "
        "is intentionally not promoted to WorldState (gap 2 is by design)."
    )

    # Title IS in the tool output but NOT via ObservationSpec
    tc = ToolCall(
        tool_name="browser.read_page", arguments={},
        correlation_id="test",
        requested_by=ToolCallRequester(subsystem="test", session_id="s"),
    )
    result = reg.handler_for("browser.read_page")(tc)
    assert result.status == ToolResultStatus.SUCCESS
    assert "title" in result.output, "Title is in read_page output"
    assert result.output["title"] == "Python - Wikipedia"


# ── T8: browser.navigate promotes URL to WorldState ──────────────────────────

def test_browser_navigate_promotes_url_to_worldstate():
    """browser.navigate has _CURRENT_URL_OBSERVATION → URL lands in WorldState.
    This is the EXISTING mechanism that provides browser context between turns.
    """
    import os
    import tempfile
    from raya.tools.catalog.browser import register_browser_tools
    from raya.tools import ToolRegistry
    from raya.world_state import WorldStateStore
    from raya.persistence.sqlite_backend import SqliteBackend
    from raya.contracts import (
        ToolCall, ToolCallRequester, ToolResultStatus, WorldStateFact, Confidence
    )

    class FakeBrowserAgent:
        def execute(self, cmd, should_stop=None):
            from raya.contracts import CommandStatus
            url = cmd.arguments.get("url", "")
            class R:
                status = CommandStatus.SUCCESS
                output = {"url": url}
                evidence = {"url": url}
                error = None
            return R()

    reg = ToolRegistry()
    register_browser_tools(reg, FakeBrowserAgent(), lambda: False)

    db = SqliteBackend(os.path.join(tempfile.mkdtemp(), "test.db"))
    ws = WorldStateStore(db)

    tool_def = reg.get("browser.navigate")
    assert len(tool_def.observation) > 0, "browser.navigate must have ObservationSpecs"

    url = "https://en.wikipedia.org/wiki/Python"
    tc = ToolCall(
        tool_name="browser.navigate", arguments={"url": url},
        correlation_id="test",
        requested_by=ToolCallRequester(subsystem="test", session_id="s"),
    )
    result = reg.handler_for("browser.navigate")(tc)
    assert result.status == ToolResultStatus.SUCCESS

    # Simulate Harness._promote_observations_and_verify
    for spec in tool_def.observation:
        value = (result.evidence or {}).get(spec.evidence_field)
        if value is not None:
            ws.apply_update(WorldStateFact(
                domain=spec.domain, key=spec.key, value=value,
                source="tool:browser.navigate", confidence=Confidence.KNOWN_FACT,
                freshness_ttl_s=spec.freshness_ttl_s,
            ))

    # Verify URL is in WorldState
    fact = ws.retrieve_fact("browser", "current_url")
    assert fact is not None, "browser/current_url should be in WorldState after navigate"
    assert fact.value == url
    assert fact.source == "tool:browser.navigate"
