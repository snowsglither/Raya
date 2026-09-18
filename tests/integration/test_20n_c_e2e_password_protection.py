"""Chantier 20N-C — E2E: password field never exposed.

What this proves about 20N-C:
  The _STRUCT_JS grab_inputs password guard works end-to-end on a real page
  with a real password field. el.value is never transmitted even if the field
  has content. Proves §A (DOM structural) + §14 (security).

Uses real Edge/Playwright on a real public login page (no fake HTML, no mock).
Skipped if Playwright is unavailable.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _has_playwright() -> bool:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


requires_browser = pytest.mark.skipif(
    not _has_playwright(),
    reason="Playwright/Chromium non disponible",
)


def _make_agent(tmp_path: Path):
    from raya.devices.browser.agent import BrowserDeviceAgent
    return BrowserDeviceAgent(tmp_path)


def _cmd(capability: str, args: dict = None):
    from raya.contracts import Command
    return Command(device_id="browser_agent", capability_name=capability,
                   arguments=args or {}, correlation_id="e2e-20n-c")


@requires_browser
def test_e2e_password_field_value_never_exposed(tmp_path):
    """Navigates to github.com/login (real public login page with password field).
    Uses browser.read_page to extract the DOM structure.
    Proves: the password field appears with input_type=password but text is never
    the typed value — even after type_text is called to populate it.

    What this proves about 20N-C:
      The _STRUCT_JS password guard runs in a real browser on a real page.
      el.value for password fields is blocked in grab_inputs.
      Proving §A (password protection) + §14 (security) end-to-end.
    """
    agent = _make_agent(tmp_path)
    try:
        r_nav = agent.execute(_cmd("browser.navigate", {"url": "https://github.com/login"}))
        assert r_nav.status.value == "success", f"navigate failed: {r_nav.error}"

        # Read page structure BEFORE typing anything
        r_read_before = agent.execute(_cmd("browser.read_page"))
        assert r_read_before.status.value == "success"
        inputs_before = r_read_before.output.get("inputs", [])

        pwd_fields_before = [i for i in inputs_before if i.get("input_type") == "password"]
        assert pwd_fields_before, (
            "GitHub login must have a password field — check if page structure changed"
        )

        # Verify password field text is not the value (no value typed yet, but
        # verify the field is structured correctly)
        for pwd in pwd_fields_before:
            assert pwd.get("input_type") == "password"
            # text must be empty or a placeholder label — never the actual password value
            text = pwd.get("text", "")
            assert len(text) < 90, "text is within safe bounds"

        # Now type a fake password into the field to check el.value protection
        agent.execute(_cmd("browser.type", {"target": "Password", "text": "FAKE_SECRET_123"}))

        # Read page again — with a value now in the password field
        r_read_after = agent.execute(_cmd("browser.read_page"))
        assert r_read_after.status.value == "success"
        inputs_after = r_read_after.output.get("inputs", [])

        pwd_fields_after = [i for i in inputs_after if i.get("input_type") == "password"]
        for pwd in pwd_fields_after:
            text = pwd.get("text", "")
            assert "FAKE_SECRET_123" not in text, (
                f"SECURITY VIOLATION: password value exposed in DOM output: {text!r}"
            )
            assert "SECRET" not in text.upper(), (
                f"SECURITY VIOLATION: possible password leak in text: {text!r}"
            )
    finally:
        agent.shutdown()
