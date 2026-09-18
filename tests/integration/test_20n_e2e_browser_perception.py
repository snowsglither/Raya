"""Chantier 20N-B — 3 vrais E2E pour les nouvelles capacités de perception browser.

PAS de fake HTML. PAS de site local. PAS de mock. Vrai Edge/Playwright.
Skippés automatiquement si Playwright ou Edge ne sont pas disponibles.

  E2E-N1 : navigate + read_page → evidence a title + page_fingerprint
  E2E-N2 : read_page sur site avec inputs → enrichissement input_type/disabled/aria_role
  E2E-N3 : check_confirmation sur page non-panier → inconclusive honnête
"""

from __future__ import annotations

import sys
import tempfile
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
                   arguments=args or {}, correlation_id="e2e-test")


# ─── E2E-N1 : read_page evidence — title + page_fingerprint ──────────────────

@requires_browser
def test_e2e_n1_read_page_evidence_title_fingerprint(tmp_path):
    """Navigate to fr.wikipedia.org, call browser.read_page, verify that:
    - evidence contains a non-empty title
    - evidence contains a 12-char page_fingerprint (md5 hex)
    Prouve le wiring Modification B (agent.py::_read_page → hashlib.md5)."""
    agent = _make_agent(tmp_path)
    try:
        r_nav = agent.execute(_cmd("browser.navigate", {"url": "https://fr.wikipedia.org"}))
        assert r_nav.status.value == "success", f"navigate failed: {r_nav.error}"

        r_read = agent.execute(_cmd("browser.read_page"))
        assert r_read.status.value == "success", f"read_page failed: {r_read.error}"

        ev = r_read.evidence or {}
        assert ev.get("title"), f"title absent de l'evidence: {ev}"
        assert ev.get("page_fingerprint"), f"page_fingerprint absent de l'evidence: {ev}"
        assert len(ev["page_fingerprint"]) == 12, (
            f"page_fingerprint doit faire 12 chars hex: {ev['page_fingerprint']!r}")
        assert ev.get("url"), f"url absent de l'evidence: {ev}"
    finally:
        agent.shutdown()


# ─── E2E-N2 : _STRUCT_JS enrichment — inputs avec input_type ─────────────────

@requires_browser
def test_e2e_n2_struct_js_enriched_inputs(tmp_path):
    """Navigate to duckduckgo.com (has a visible search input), call browser.read_page.
    Verify that at least one input element has the `input_type` field populated.
    Prouve Modification A (_STRUCT_JS grab_inputs enrichment avec input_type/disabled/aria_role)."""
    agent = _make_agent(tmp_path)
    try:
        r_nav = agent.execute(_cmd("browser.navigate", {"url": "https://duckduckgo.com"}))
        assert r_nav.status.value == "success", f"navigate failed: {r_nav.error}"

        r_read = agent.execute(_cmd("browser.read_page"))
        assert r_read.status.value == "success", f"read_page failed: {r_read.error}"

        output = r_read.output or {}
        inputs = output.get("inputs", [])
        assert inputs, "duckduckgo doit avoir au moins un champ de saisie visible"

        # Enrichment present if any input has input_type, aria_role, or disabled
        enriched = ("input_type", "aria_role", "disabled")
        has_enrichment = any(any(k in inp for k in enriched) for inp in inputs)
        assert has_enrichment, (
            f"Aucun input n'a de champ enrichi {enriched} dans: {inputs[:3]}\n"
            "Vérifier que _STRUCT_JS::grab_inputs est correctement câblé."
        )
    finally:
        agent.shutdown()


# ─── E2E-N3 : check_confirmation → inconclusive sur page non-panier ──────────

@requires_browser
def test_e2e_n3_check_confirmation_inconclusive_on_non_cart(tmp_path):
    """Navigate to github.com (NOT a cart page), call browser.check_confirmation.
    Must return confirmed=False, inconclusive=True — never a false positive.
    Prouve Modification D (browser.check_confirmation exposé et honnête)."""
    agent = _make_agent(tmp_path)
    try:
        r_nav = agent.execute(_cmd("browser.navigate", {"url": "https://github.com"}))
        assert r_nav.status.value == "success", f"navigate failed: {r_nav.error}"

        r_check = agent.execute(_cmd("browser.check_confirmation"))
        assert r_check.status.value == "success", f"check_confirmation failed: {r_check.error}"

        output = r_check.output or {}
        assert not output.get("confirmed"), (
            f"check_confirmation doit être False sur github.com (pas un panier): {output}"
        )
        assert output.get("inconclusive"), (
            f"check_confirmation doit être inconclusive sur github.com: {output}"
        )
    finally:
        agent.shutdown()
