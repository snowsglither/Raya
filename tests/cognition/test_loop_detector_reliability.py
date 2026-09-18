"""LoopDetector reliability — tests for the two new behaviors:
1. Success of tool X does not clear failure counts of tool Y
2. Same tool failing max_same_tool_failures times → ESCALATE regardless of args

Regression guard: exact-signature detection still works.
"""
from __future__ import annotations

from raya.cognition.recovery import LoopDetector, RecoveryAction
from raya.cognition.verification import VerificationOutcome


# ---------------------------------------------------------------------------
# 15. Bounded wait doesn't add latency when element found immediately
#     (tested via _find_clickable mock — structural test)
# ---------------------------------------------------------------------------
# Note: controller tests are in test_browser_recovery.py

# ---------------------------------------------------------------------------
# 16. Success of read_page does NOT clear browser.click failure history
# ---------------------------------------------------------------------------

def test_read_page_success_preserves_click_failures():
    """The Amazon pattern: click fails, read_page succeeds, click fails again.
    Before the fix, read_page success cleared click's failure history."""
    detector = LoopDetector(max_identical_failures=2, max_same_tool_failures=4)
    key = "turn:1"

    # First click fails
    r1 = detector.record(key, "browser.click", {"target": "add to cart"}, VerificationOutcome.FAILURE)
    assert r1 == RecoveryAction.REPLAN

    # read_page succeeds — should NOT clear click failure
    r2 = detector.record(key, "browser.read_page", {}, VerificationOutcome.SUCCESS)
    assert r2 == RecoveryAction.CONTINUE

    # Same click fails again → should still see the previous failure → ESCALATE
    r3 = detector.record(key, "browser.click", {"target": "add to cart"}, VerificationOutcome.FAILURE)
    assert r3 == RecoveryAction.ESCALATE, "Identical click failure should ESCALATE after read_page success"


# ---------------------------------------------------------------------------
# 17. Timeout falls back cleanly (controller bounded wait)
# ---------------------------------------------------------------------------
# Note: tested in test_browser_recovery.py

# ---------------------------------------------------------------------------
# 18. ELEMENT_NOT_FOUND doesn't immediately mean objective failure
#     — needs at least max_identical_failures attempts
# ---------------------------------------------------------------------------

def test_single_element_not_found_is_replan_not_escalate():
    detector = LoopDetector(max_identical_failures=2, max_same_tool_failures=4)
    key = "turn:2"
    r = detector.record(key, "browser.click", {"target": "library link"}, VerificationOutcome.FAILURE)
    assert r == RecoveryAction.REPLAN, "Single failure should be REPLAN, not ESCALATE"


# ---------------------------------------------------------------------------
# 19. Repeated failure of same tool eventually stops
# ---------------------------------------------------------------------------

def test_same_tool_different_args_escalates_after_max():
    """Amazon pattern: click('add to cart'), click('Ajouter au panier'),
    click('add to your cart')... — different args, same tool, ESCALATE."""
    detector = LoopDetector(max_identical_failures=2, max_same_tool_failures=4)
    key = "turn:3"
    targets = ["add to cart", "Ajouter au panier", "add to your cart", "ajouter"]
    actions = []
    for i, t in enumerate(targets):
        r = detector.record(key, "browser.click", {"target": t}, VerificationOutcome.FAILURE)
        actions.append(r)

    assert actions[-1] == RecoveryAction.ESCALATE, \
        f"After 4 click failures (different args), should ESCALATE. Got: {actions}"


# ---------------------------------------------------------------------------
# 20. Interleaved read_page does NOT hide repeated click failure
# ---------------------------------------------------------------------------

def test_interleaved_read_page_does_not_hide_click_failure():
    detector = LoopDetector(max_identical_failures=2, max_same_tool_failures=4)
    key = "turn:4"

    targets = ["add to cart", "Ajouter", "ajouter au panier", "add-to-cart button"]
    final_action = RecoveryAction.REPLAN
    for t in targets:
        r = detector.record(key, "browser.click", {"target": t}, VerificationOutcome.FAILURE)
        # read_page success interleaved
        detector.record(key, "browser.read_page", {}, VerificationOutcome.SUCCESS)
        final_action = r

    # After 4 click failures regardless of interleaved read_page, must ESCALATE
    last_click = detector.record(key, "browser.click", {"target": "cart btn"}, VerificationOutcome.FAILURE)
    # By now counts[browser.click] ≥ 4 → ESCALATE
    assert last_click == RecoveryAction.ESCALATE


# ---------------------------------------------------------------------------
# 21. Genuine new strategy (different tool) is allowed
# ---------------------------------------------------------------------------

def test_different_tool_after_click_failures_is_allowed():
    detector = LoopDetector(max_identical_failures=2, max_same_tool_failures=4)
    key = "turn:5"

    for t in ["add to cart", "Ajouter"]:
        detector.record(key, "browser.click", {"target": t}, VerificationOutcome.FAILURE)

    # Switching to browser.navigate is a new strategy — should be REPLAN, not ESCALATE
    r = detector.record(key, "browser.navigate", {"url": "https://example.com/cart"}, VerificationOutcome.FAILURE)
    assert r == RecoveryAction.REPLAN, "Different tool failure should start fresh (REPLAN)"


# ---------------------------------------------------------------------------
# Regression: exact same (tool+args) still escalates after 2 failures
# ---------------------------------------------------------------------------

def test_exact_same_signature_escalates_after_2():
    detector = LoopDetector(max_identical_failures=2, max_same_tool_failures=10)
    key = "turn:6"
    detector.record(key, "browser.click", {"target": "buy"}, VerificationOutcome.FAILURE)
    r = detector.record(key, "browser.click", {"target": "buy"}, VerificationOutcome.FAILURE)
    assert r == RecoveryAction.ESCALATE


def test_success_resets_same_tool_count():
    detector = LoopDetector(max_identical_failures=2, max_same_tool_failures=3)
    key = "turn:7"
    detector.record(key, "browser.click", {"target": "link"}, VerificationOutcome.FAILURE)
    detector.record(key, "browser.click", {"target": "link 2"}, VerificationOutcome.FAILURE)
    # SUCCESS resets click count
    detector.record(key, "browser.click", {"target": "found it"}, VerificationOutcome.SUCCESS)
    # Now a fresh failure should be REPLAN, not ESCALATE
    r = detector.record(key, "browser.click", {"target": "something else"}, VerificationOutcome.FAILURE)
    assert r == RecoveryAction.REPLAN
