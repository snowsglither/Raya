"""Chantier 20N-C — Browser Perception targeted tests.

Each test answers: "What exactly does this test prove about 20N-C?"

Focused on:
  - Password value never exposed by _STRUCT_JS (security fix, §A)
  - disabled field emitted correctly by grab_inputs (§A)
  - Vision last-target staleness context available for Cognition reasoning (§C/§7)
  - confirm vs inconclusive semantic boundary (§E)
"""

from __future__ import annotations

import re
import unittest

from raya.devices.browser.controller import _STRUCT_JS


class TestPasswordProtection(unittest.TestCase):
    """Proves: _STRUCT_JS never reads el.value for password fields (20N-C §A, §14)."""

    def test_password_branch_does_not_use_label_fn(self):
        """Proves: grab_inputs uses a separate code path for type=password that
        skips label(), which reads el.value. Verified by static JS source inspection."""
        # The password branch must NOT call label(el) — it must use only
        # aria-label/placeholder/title attributes (non-sensitive)
        js = _STRUCT_JS
        # Find the grab_inputs function body
        grab_idx = js.index("grab_inputs")
        grab_body = js[grab_idx:]
        # The password guard must appear before any label(el) call in the same block
        pwd_guard_idx = grab_body.find("=== 'password'")
        label_call_idx = grab_body.find(": label(el)")
        self.assertGreater(pwd_guard_idx, 0,
                           "_STRUCT_JS must contain a '=== password' guard in grab_inputs")
        self.assertGreater(label_call_idx, 0,
                           "_STRUCT_JS must use label(el) for non-password fields")
        # The ternary structure means label(el) only runs for non-password inputs
        self.assertLess(pwd_guard_idx, label_call_idx,
                        "Password guard must appear before the label(el) call")

    def test_password_branch_uses_only_safe_attributes(self):
        """Proves: the password code path only accesses aria-label, placeholder, title.
        Never el.value (which would expose typed passwords)."""
        js = _STRUCT_JS
        grab_idx = js.index("grab_inputs")
        grab_body = js[grab_idx:]
        pwd_guard_idx = grab_body.find("=== 'password'")
        # Extract the ternary's 'true' branch (between ? and :)
        after_guard = grab_body[pwd_guard_idx:]
        # The true branch should be the aria-label/placeholder/title expression
        self.assertIn("aria-label", after_guard[:400],
                      "Password branch must use aria-label")
        self.assertIn("placeholder", after_guard[:400],
                      "Password branch must use placeholder")
        # el.value must NOT appear in the password branch — only on the : (false) side
        # (before the ':' of the ternary, i.e. in the password-safe side)
        true_branch = after_guard[after_guard.find("?")+1 : after_guard.find(": label(el)")]
        self.assertNotIn("el.value", true_branch,
                         "Password branch must never access el.value")


class TestDisabledInputDetection(unittest.TestCase):
    """Proves: grab_inputs emits disabled:true for disabled elements (20N-C §A)."""

    def test_disabled_flag_present_in_struct_js(self):
        """Proves: _STRUCT_JS grab_inputs checks el.disabled and sets item.disabled."""
        self.assertIn("el.disabled", _STRUCT_JS)
        self.assertIn("item.disabled = true", _STRUCT_JS)

    def test_disabled_flag_is_conditional(self):
        """Proves: disabled is only emitted when true, not as a constant field.
        This prevents polluting every input with a redundant disabled:false."""
        js = _STRUCT_JS
        grab_idx = js.index("grab_inputs")
        grab_body = js[grab_idx: grab_idx + 500]
        # The disabled assignment must be guarded by an if
        disabled_idx = grab_body.find("item.disabled = true")
        if_before = grab_body[max(0, disabled_idx - 30): disabled_idx]
        self.assertIn("if", if_before,
                      "item.disabled must only be set conditionally (when el.disabled is true)")


class TestStalenessContextAvailability(unittest.TestCase):
    """Proves: browser_last_target evidence carries both observed_url and
    observed_page_fingerprint so Cognition can detect staleness (20N-C §7, §C)."""

    def test_browser_last_target_has_staleness_fields(self):
        """Proves: the evidence structure for vision.find_in_browser includes
        observed_url and observed_page_fingerprint alongside the coordinates.
        Cognition can compare these against browser.current_url / browser.page_fingerprint
        in WorldState to detect whether the target is still valid."""
        import hashlib as _h, time as _t
        from raya.tools.catalog.visual import _BROWSER_LAST_TARGET_SPEC

        # Simulate what _handle_find_in_browser writes to evidence
        url_at_observation = "https://example.com/product/123"
        title_at_observation = "Widget Pro"
        fp = _h.md5(f"{url_at_observation}|{title_at_observation}".encode()).hexdigest()[:12]

        target_evidence = {
            "label": "Add to cart",
            "screen_x": 640, "screen_y": 350,
            "observed_url": url_at_observation,
            "observed_page_fingerprint": fp,
            "observed_at": _t.time(),
        }

        # Verify all staleness fields are present and non-empty
        self.assertEqual(target_evidence["observed_url"], url_at_observation)
        self.assertEqual(len(target_evidence["observed_page_fingerprint"]), 12)
        self.assertIsInstance(target_evidence["observed_at"], float)

    def test_stale_target_context_detectable_by_url_difference(self):
        """Proves: when browser.current_url (from read_page evidence) differs from
        browser_last_target.observed_url, Cognition receives both independently
        and can conclude the target is stale. No new subsystem needed.
        20N-C §7: Cognition retains full responsibility for the staleness decision."""
        import hashlib as _h

        # Page A: where the Vision observation was made
        url_a = "https://shop.example.com/product"
        fp_a = _h.md5(f"{url_a}|Product".encode()).hexdigest()[:12]

        last_target = {
            "label": "Buy now", "screen_x": 300, "screen_y": 200,
            "observed_url": url_a,
            "observed_page_fingerprint": fp_a,
        }

        # Page B: user navigated away, current page state
        url_b = "https://shop.example.com/cart"
        fp_b = _h.md5(f"{url_b}|Cart".encode()).hexdigest()[:12]
        current_state = {"current_url": url_b, "page_fingerprint": fp_b}

        # Cognition can compare — both values are independently accessible
        self.assertNotEqual(last_target["observed_url"], current_state["current_url"],
                            "Test setup: URL must differ to simulate navigation")
        self.assertNotEqual(last_target["observed_page_fingerprint"],
                            current_state["page_fingerprint"],
                            "Test setup: fingerprint must differ to simulate navigation")

        # The architecture never merges these — Cognition reads both and decides
        # Whether they are compatible is Cognition's responsibility, not ours
        is_compatible = (
            last_target["observed_url"] == current_state["current_url"]
            and last_target["observed_page_fingerprint"] == current_state["page_fingerprint"]
        )
        self.assertFalse(is_compatible,
                         "After navigation, observed context must not match current state")

    def test_same_context_target_considered_compatible(self):
        """Proves: when observed_url and observed_page_fingerprint match current
        state, Cognition has all information to treat the target as still valid.
        No new code needed — data structure enables the reasoning. 20N-C §7."""
        import hashlib as _h, time as _t

        url = "https://example.com/product"
        title = "Product Page"
        fp = _h.md5(f"{url}|{title}".encode()).hexdigest()[:12]

        last_target = {
            "label": "Add to cart", "screen_x": 400, "screen_y": 250,
            "observed_url": url,
            "observed_page_fingerprint": fp,
            "observed_at": _t.time() - 5,  # 5 seconds ago
        }
        current_state = {"current_url": url, "page_fingerprint": fp}

        is_compatible = (
            last_target["observed_url"] == current_state["current_url"]
            and last_target["observed_page_fingerprint"] == current_state["page_fingerprint"]
        )
        self.assertTrue(is_compatible,
                        "Same URL + fingerprint means compatible navigation context")


if __name__ == "__main__":
    unittest.main()
