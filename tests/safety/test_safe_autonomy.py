"""Safe Autonomy — regression tests for classify_risk() contextual classification.

Verifies that:
- browser.click with a non-dangerous target → SAFE (no confirmation prompt)
- browser.click with a dangerous target → SENSITIVE (confirmation required)
- pc.ui.click with a named selector → SAFE
- pc.mouse.click with raw coordinates → SENSITIVE (opaque, no semantic description)
- browser.interact with no arguments → SAFE (contextual fallback)
"""
from __future__ import annotations

from raya.contracts import PermissionLevel
from raya.safety.risk import classify_risk


# ---------------------------------------------------------------------------
# browser.interact — contextual classification
# ---------------------------------------------------------------------------

def test_browser_click_navigation_safe():
    assert classify_risk(["browser.interact"], {"target": "library link"}) == PermissionLevel.SAFE


def test_browser_click_play_safe():
    assert classify_risk(["browser.interact"], {"target": "play button"}) == PermissionLevel.SAFE


def test_browser_click_open_menu_safe():
    assert classify_risk(["browser.interact"], {"target": "hamburger menu"}) == PermissionLevel.SAFE


def test_browser_click_buy_sensitive():
    assert classify_risk(["browser.interact"], {"target": "buy now"}) == PermissionLevel.SENSITIVE


def test_browser_click_delete_sensitive():
    assert classify_risk(["browser.interact"], {"target": "delete account"}) == PermissionLevel.SENSITIVE


def test_browser_click_submit_order_sensitive():
    assert classify_risk(["browser.interact"], {"target": "submit order"}) == PermissionLevel.SENSITIVE


def test_browser_click_publish_sensitive():
    assert classify_risk(["browser.interact"], {"target": "publish post"}) == PermissionLevel.SENSITIVE


def test_browser_interact_no_args_safe():
    # dismiss_overlay called without arguments — SAFE contextual fallback
    assert classify_risk(["browser.interact"], {}) == PermissionLevel.SAFE


# ---------------------------------------------------------------------------
# pc.interact — contextual classification
# ---------------------------------------------------------------------------

def test_pc_ui_click_named_selector_safe():
    assert classify_risk(["pc.interact"], {"selector": {"name": "Library"}}) == PermissionLevel.SAFE


def test_pc_mouse_click_raw_coordinates_sensitive():
    # No text values → no semantic description → SENSITIVE fallback
    assert classify_risk(["pc.interact"], {"x": 100, "y": 200}) == PermissionLevel.SENSITIVE


# ---------------------------------------------------------------------------
# pc.read — always SAFE (battery, window list, process list…)   [test 24-26]
# ---------------------------------------------------------------------------

def test_pc_read_battery_safe():
    assert classify_risk(["pc.read"], {}) == PermissionLevel.SAFE


def test_pc_read_process_list_safe():
    assert classify_risk(["pc.read"], {"limit": 50}) == PermissionLevel.SAFE


def test_pc_launch_safe():
    assert classify_risk(["pc.launch"], {"target": "calc"}) == PermissionLevel.SAFE


# ---------------------------------------------------------------------------
# pc.shell — always SENSITIVE                                    [test 27-28]
# ---------------------------------------------------------------------------

def test_shell_execute_command_sensitive():
    assert classify_risk(["pc.shell"], {"command": "Get-BatteryInfo"}) == PermissionLevel.SENSITIVE


def test_shell_execute_read_only_still_sensitive():
    # Even a "read-only" shell command must remain SENSITIVE — the capability
    # has no way to distinguish read from write at classification time.
    assert classify_risk(["pc.shell"], {"command": "Get-WmiObject Win32_Battery"}) == PermissionLevel.SENSITIVE


# ---------------------------------------------------------------------------
# Raw mouse coordinates remain SENSITIVE                         [test 29]
# ---------------------------------------------------------------------------

def test_mouse_raw_coords_always_sensitive():
    assert classify_risk(["pc.interact"], {"x": 0, "y": 0}) == PermissionLevel.SENSITIVE


# ---------------------------------------------------------------------------
# No claim without evidence — battery result required            [test 30]
# ---------------------------------------------------------------------------

def test_no_claim_without_evidence_directive_present():
    """render.py must contain the NO-CLAIM directive so the model is reminded
    not to state a battery value it did not receive from a tool."""
    from raya.context_engine.render import render_system_prompt
    from raya.contracts import Context, ContextSection, SectionKind

    ctx = Context(
        session_id="s1", budget_tokens=4096,
        sections=[ContextSection(
            kind=SectionKind.SYSTEM_RULES,
            content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
            provenance="test",
        )],
        used_tokens_estimate=0,
    )
    rendered = render_system_prompt(ctx)
    # The NO-CLAIM directive uses the word "invent" (never invent it / never guess)
    assert "invent" in rendered.lower() or "never invent" in rendered.lower() or "Observed environment" in rendered
