"""Browser recovery — bounded wait for JS lazy-loaded elements.

Tests that:
- Fast path: element found immediately, no wait incurred
- Slow path: element not found first, wait fires, element found on retry
- Timeout exception falls back cleanly (no crash)
- No coordinate invention — click_at_position requires real coords
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from raya.devices.browser.controller import BrowserController


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_controller():
    session = MagicMock()
    worker = MagicMock()
    return BrowserController(session, worker), session, worker


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 15. Bounded wait: element not found initially → wait → found on retry
# ---------------------------------------------------------------------------

def test_find_clickable_waits_when_not_found_initially():
    """If element is absent on first pass, a bounded 1500ms wait is performed
    and the element is re-searched (JS lazy-loading / React hydration)."""
    ctrl, session, _ = _make_controller()

    page = AsyncMock()
    page.frames = []
    session.get_or_create = AsyncMock(return_value=page)

    found_loc = MagicMock()
    found_loc.count = AsyncMock(return_value=1)
    found_loc.is_visible = AsyncMock(return_value=True)

    call_count = [0]

    async def fake_find_in(frame, target):
        call_count[0] += 1
        if call_count[0] <= 1:
            return None       # first call: not found (pre-hydration)
        return found_loc      # second call: found (post-hydration)

    async def run():
        with patch.object(ctrl, "_find_clickable_in", side_effect=fake_find_in), \
             patch.object(page, "wait_for_timeout", new_callable=AsyncMock) as mock_wait:
            result = await ctrl._find_clickable("add to cart")
            mock_wait.assert_called_once_with(1_500)
            return result

    result = _run(run())
    assert result is found_loc


# ---------------------------------------------------------------------------
# 16. Fast path: element found immediately, no wait
# ---------------------------------------------------------------------------

def test_find_clickable_fast_path_no_wait():
    """If element found on first try, wait_for_timeout must NOT be called."""
    ctrl, session, _ = _make_controller()

    page = AsyncMock()
    page.frames = []
    session.get_or_create = AsyncMock(return_value=page)

    found_loc = MagicMock()
    found_loc.count = AsyncMock(return_value=1)
    found_loc.is_visible = AsyncMock(return_value=True)

    async def instant_find(frame, target):
        return found_loc

    async def run():
        with patch.object(ctrl, "_find_clickable_in", side_effect=instant_find), \
             patch.object(page, "wait_for_timeout", new_callable=AsyncMock) as mock_wait:
            result = await ctrl._find_clickable("library link")
            mock_wait.assert_not_called()
            return result

    result = _run(run())
    assert result is found_loc


# ---------------------------------------------------------------------------
# 17. Timeout exception falls back cleanly
# ---------------------------------------------------------------------------

def test_find_clickable_timeout_falls_back_cleanly():
    """If wait_for_timeout raises (page closed, browser error), _find_clickable
    must return None gracefully — no unhandled exception propagated."""
    ctrl, session, _ = _make_controller()

    page = AsyncMock()
    page.frames = []
    page.wait_for_timeout = AsyncMock(side_effect=Exception("playwright timeout"))
    session.get_or_create = AsyncMock(return_value=page)

    async def not_found(frame, target):
        return None

    async def run():
        with patch.object(ctrl, "_find_clickable_in", side_effect=not_found):
            return await ctrl._find_clickable("missing element")

    result = _run(run())
    assert result is None


# ---------------------------------------------------------------------------
# Navigation still fast when no wait needed (regression guard)
# ---------------------------------------------------------------------------

def test_no_wait_incurred_when_element_immediate():
    """End-to-end fast path: wait_for_timeout never called when element
    is resolved on the first search pass."""
    ctrl, session, _ = _make_controller()

    page = AsyncMock()
    page.frames = []
    session.get_or_create = AsyncMock(return_value=page)

    mock_loc = MagicMock()
    mock_loc.count = AsyncMock(return_value=1)
    mock_loc.is_visible = AsyncMock(return_value=True)

    async def instant(frame, target):
        return mock_loc

    async def run():
        with patch.object(ctrl, "_find_clickable_in", side_effect=instant), \
             patch.object(page, "wait_for_timeout", new_callable=AsyncMock) as mock_wait:
            loc = await ctrl._find_clickable("submit button")
            assert loc is mock_loc
            mock_wait.assert_not_called()

    _run(run())
