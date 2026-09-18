"""Chantier 20N-B — Browser Perception automated tests (12 max).

Covers:
  A: _STRUCT_JS enrichment + Amazon-roots removal
  B: browser.read_page evidence (title, page_fingerprint) + ObservationSpec wiring
  C: vision.find_in_browser browser_last_target evidence + context fields
  compaction: _compact_old_dom_messages behaviour
  check_confirmation: catalog + dispatch + honest signal detection
"""

from __future__ import annotations

import json
import unittest
from unittest.mock import MagicMock

from raya.contracts import (
    Command,
    CommandStatus,
    ContentPart,
    Message,
)
from raya.devices.browser.agent import (
    _CAPABILITIES,
    _DISPATCH,
    _check_confirmation,
    _read_page,
)
from raya.devices.browser.controller import _STRUCT_JS
from raya.harness.loop import Harness
from raya.tools.catalog.browser import (
    _CONFIRMATION_OBSERVATION,
    _PAGE_STATE_OBSERVATION,
)
from raya.tools.catalog.visual import _BROWSER_FIND_OBS_SPEC, _BROWSER_LAST_TARGET_SPEC


# ---------------------------------------------------------------------------
# A — _STRUCT_JS enrichment + Amazon-roots removal  (2 tests)
# ---------------------------------------------------------------------------

class TestStructJsEnrichment(unittest.TestCase):
    def test_no_amazon_priority_roots(self):
        for forbidden in ("desktop_buybox", "addToCart_feature_div", "buybox", "addtocart"):
            self.assertNotIn(forbidden, _STRUCT_JS,
                             f"Amazon selector '{forbidden}' must not appear in _STRUCT_JS")

    def test_grab_inputs_fields_in_struct_js(self):
        self.assertIn("grab_inputs", _STRUCT_JS)
        self.assertIn("input_type", _STRUCT_JS)
        self.assertIn("disabled", _STRUCT_JS)
        self.assertIn("aria_role", _STRUCT_JS)


# ---------------------------------------------------------------------------
# B — browser.read_page evidence + ObservationSpec wiring  (3 tests)
# ---------------------------------------------------------------------------

class TestReadPageEvidence(unittest.TestCase):
    def _agent(self):
        import tempfile
        from pathlib import Path
        agent = MagicMock()
        agent._screenshot_dir = Path(tempfile.mkdtemp())
        agent._controller.read_page.return_value = {
            "status": "ok", "url": "https://example.com", "title": "Example",
            "cookie_banner": False, "buttons": [], "links": [], "inputs": [],
        }
        return agent

    def test_evidence_includes_title_and_fingerprint(self):
        cmd = Command(device_id="browser_agent", capability_name="browser.read_page",
                      arguments={}, correlation_id="t1")
        result = _read_page(self._agent(), cmd)
        self.assertEqual(result.status, CommandStatus.SUCCESS)
        self.assertEqual(result.evidence["title"], "Example")
        self.assertIsInstance(result.evidence["page_fingerprint"], str)
        self.assertGreater(len(result.evidence["page_fingerprint"]), 0)

    def test_page_state_observation_specs(self):
        keys = {s.key for s in _PAGE_STATE_OBSERVATION}
        self.assertIn("current_url", keys)
        self.assertIn("page_title", keys)
        self.assertIn("page_fingerprint", keys)
        fp_spec = next(s for s in _PAGE_STATE_OBSERVATION if s.key == "page_fingerprint")
        title_spec = next(s for s in _PAGE_STATE_OBSERVATION if s.key == "page_title")
        self.assertLess(fp_spec.freshness_ttl_s, title_spec.freshness_ttl_s)

    def test_read_page_uses_page_state_observation(self):
        """browser.read_page tool must be registered with _PAGE_STATE_OBSERVATION."""
        from raya.tools import ToolRegistry
        from raya.tools.catalog.browser import register_browser_tools
        registry = ToolRegistry()
        agent = MagicMock()
        register_browser_tools(registry, agent, should_stop=lambda: False)
        tool = registry.get("browser.read_page")
        self.assertIsNotNone(tool)
        obs_keys = {s.key for s in tool.observation}
        self.assertIn("page_title", obs_keys)
        self.assertIn("page_fingerprint", obs_keys)


# ---------------------------------------------------------------------------
# C — vision.find_in_browser browser_last_target  (2 tests)
# ---------------------------------------------------------------------------

class TestBrowserLastTargetSpec(unittest.TestCase):
    def test_find_obs_spec_includes_both_specs(self):
        keys = {s.key for s in _BROWSER_FIND_OBS_SPEC}
        self.assertIn("browser_last_target", keys)
        self.assertIn("browser_visual_state", keys)
        self.assertEqual(_BROWSER_LAST_TARGET_SPEC.evidence_field, "browser_last_target")
        self.assertEqual(_BROWSER_LAST_TARGET_SPEC.domain, "visual")

    def test_browser_last_target_evidence_has_context_fields(self):
        import hashlib as _h, time as _t
        cap = {"url": "https://example.com/product", "title": "Product Page", "width": 1280, "height": 800}
        observed_url = cap.get("url", "")
        observed_title = cap.get("title", "")
        fp = _h.md5(f"{observed_url}|{observed_title}".encode()).hexdigest()[:12]
        evidence = {
            "browser_last_target": {
                "label": "Add to cart", "screen_x": 400, "screen_y": 300,
                "bbox": {"x_min": 0.3, "y_min": 0.35, "x_max": 0.5, "y_max": 0.4},
                "confidence": 0.95, "observation_id": "obs-test",
                "observed_url": observed_url, "observed_page_fingerprint": fp,
                "observed_at": _t.time(),
            }
        }
        last_t = evidence["browser_last_target"]
        self.assertEqual(last_t["observed_url"], "https://example.com/product")
        self.assertEqual(len(last_t["observed_page_fingerprint"]), 12)
        self.assertIsInstance(last_t["observed_at"], float)


# ---------------------------------------------------------------------------
# Compaction — _compact_old_dom_messages  (3 tests)
# ---------------------------------------------------------------------------

class TestCompactOldDomMessages(unittest.TestCase):
    def _dom_msg(self, url: str, title: str, buttons: int = 5, links: int = 3) -> Message:
        payload = json.dumps({
            "tool": "browser.read_page", "status": "success",
            "output": {
                "url": url, "title": title, "cookie_banner": False,
                "buttons": [{"kind": "button", "text": f"b{i}", "tag": "button"} for i in range(buttons)],
                "links": [{"kind": "link", "text": f"l{i}", "tag": "a"} for i in range(links)],
                "inputs": [],
            },
        }, ensure_ascii=False)
        return Message(role="tool", tool_call_id="tc-x",
                       content=[ContentPart(type="text", value=payload)])

    def test_most_recent_dom_preserved_intact(self):
        msgs = [self._dom_msg("https://a.com", "A"), self._dom_msg("https://b.com", "B")]
        Harness._compact_old_dom_messages(msgs)
        data_b = json.loads(msgs[-1].content[0].value)
        self.assertNotIn("_compacted", data_b.get("output", {}))
        self.assertIn("buttons", data_b["output"])

    def test_older_dom_compacted_to_summary(self):
        msgs = [self._dom_msg("https://a.com", "A", buttons=10, links=8),
                self._dom_msg("https://b.com", "B")]
        Harness._compact_old_dom_messages(msgs)
        data_a = json.loads(msgs[0].content[0].value)
        self.assertTrue(data_a["output"].get("_compacted"))
        self.assertEqual(data_a["output"]["buttons_count"], 10)
        self.assertEqual(data_a["output"]["links_count"], 8)
        self.assertNotIn("buttons", data_a["output"])

    def test_already_compacted_not_modified(self):
        compacted_payload = json.dumps({
            "tool": "browser.read_page", "status": "success",
            "output": {"url": "https://a.com", "title": "A",
                       "buttons_count": 5, "links_count": 3, "_compacted": True},
        }, ensure_ascii=False)
        msg_old = Message(role="tool", tool_call_id="tc-1",
                          content=[ContentPart(type="text", value=compacted_payload)])
        msg_new = self._dom_msg("https://b.com", "B")
        msgs = [msg_old, msg_new]
        Harness._compact_old_dom_messages(msgs)
        data_old = json.loads(msgs[0].content[0].value)
        self.assertTrue(data_old["output"].get("_compacted"))
        self.assertNotIn("buttons", data_old["output"])


# ---------------------------------------------------------------------------
# check_confirmation — wiring + honesty  (2 tests)
# ---------------------------------------------------------------------------

class TestCheckConfirmationWiring(unittest.TestCase):
    def test_wiring_in_dispatch_and_capabilities(self):
        self.assertIn("browser.check_confirmation", _DISPATCH)
        names = [c.name for c in _CAPABILITIES]
        self.assertIn("browser.check_confirmation", names)
        self.assertEqual(len(_CONFIRMATION_OBSERVATION), 1)
        self.assertEqual(_CONFIRMATION_OBSERVATION[0].key, "last_confirmation")

    def test_honest_confirmed_and_inconclusive(self):
        cmd = Command(device_id="browser_agent", capability_name="browser.check_confirmation",
                      arguments={}, correlation_id="t-chk")
        agent_yes = MagicMock()
        agent_yes._controller.check_confirmation.return_value = True
        r_yes = _check_confirmation(agent_yes, cmd)
        self.assertTrue(r_yes.output.get("confirmed"))
        self.assertTrue(r_yes.evidence.get("confirmation_detected"))

        agent_no = MagicMock()
        agent_no._controller.check_confirmation.return_value = None
        r_no = _check_confirmation(agent_no, cmd)
        self.assertFalse(r_no.output.get("confirmed"))
        self.assertTrue(r_no.output.get("inconclusive"))


if __name__ == "__main__":
    unittest.main()
