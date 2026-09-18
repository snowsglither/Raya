"""Tests E2E réels pour l'audit multimodal RAYA V2.

RÈGLES :
- Aucun mock Vision — gemma4:cloud réel (skip si OLLAMA_API_KEY absent)
- Aucun HTML local — sites réels (Wikipedia, Google, GitHub)
- Aucun serveur local fictif
Run: pytest tests/audit/test_real_e2e_multimodal.py -v -s
"""
from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path

import pytest


def _read_api_key() -> str | None:
    for candidate in (
        Path(r"C:\Users\ruben\OneDrive\Bureau\RAYA\.env"),
        Path(__file__).resolve().parents[2] / ".env",
    ):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OLLAMA_API_KEY="):
                val = line.split("=", 1)[1].split("#")[0].strip().strip('"').strip("'")
                return val or None
    return None


OLLAMA_KEY = _read_api_key()
skip_no_key = pytest.mark.skipif(OLLAMA_KEY is None, reason="OLLAMA_API_KEY not available")

# Mark that requires real browser + real vision
real_e2e = pytest.mark.skipif(OLLAMA_KEY is None, reason="needs OLLAMA_API_KEY for Vision")


@pytest.fixture(scope="module")
def vision_registry():
    from raya.models.registry import ModelRegistry
    from raya.models.providers.ollama_cloud import OllamaCloudAdapter
    from raya.contracts import ModelCapability
    if OLLAMA_KEY is None:
        pytest.skip("OLLAMA_API_KEY not available")
    registry = ModelRegistry()
    registry.register(OllamaCloudAdapter(
        "gemma4:cloud", [ModelCapability.VISION], OLLAMA_KEY
    ))
    return registry


@pytest.fixture(scope="module")
def browser_agent(tmp_path_factory):
    from raya.devices.browser import BrowserDeviceAgent
    tmpdir = tmp_path_factory.mktemp("audit_browser")
    agent = BrowserDeviceAgent(tmpdir)
    yield agent
    agent.shutdown()


def _nav(agent, url: str, corr: str = "audit"):
    from raya.contracts import Command
    return agent.execute(Command(
        device_id="browser_agent", capability_name="browser.navigate",
        arguments={"url": url}, correlation_id=corr,
    ))


def _read(agent, corr: str = "audit"):
    from raya.contracts import Command
    return agent.execute(Command(
        device_id="browser_agent", capability_name="browser.read_page",
        arguments={}, correlation_id=corr,
    ))


def _screenshot(agent, filename: str, corr: str = "audit"):
    from raya.contracts import Command
    return agent.execute(Command(
        device_id="browser_agent", capability_name="browser.screenshot",
        arguments={"filename": filename}, correlation_id=corr,
    ))


def _click(agent, target: str, corr: str = "audit"):
    from raya.contracts import Command
    return agent.execute(Command(
        device_id="browser_agent", capability_name="browser.click",
        arguments={"target": target}, correlation_id=corr,
    ))


# ── Test A : DOM + Vision simultanément sur Wikipedia ────────────────────────

@real_e2e
def test_e2e_a_wikipedia_dom_and_vision_simultaneously(browser_agent, vision_registry):
    """DOM et Vision décrivent le même contenu — Wikipedia Python article."""
    from raya.contracts import CommandStatus
    from raya.models.vision import observe_image

    r_nav = _nav(browser_agent, "https://en.wikipedia.org/wiki/Python_(programming_language)", "audit-a")
    assert r_nav.status == CommandStatus.SUCCESS, f"navigate failed: {r_nav.error}"

    # DOM read
    r_dom = _read(browser_agent, "audit-a-dom")
    assert r_dom.status == CommandStatus.SUCCESS, f"read_page failed: {r_dom.error}"

    links_count = len(r_dom.output.get("links", []))
    assert links_count > 10, f"Expected many links on Wikipedia, got {links_count}"

    # Screenshot + Vision
    r_shot = _screenshot(browser_agent, "wiki_python_a.png", "audit-a-shot")
    assert r_shot.status == CommandStatus.SUCCESS, f"screenshot failed: {r_shot.error}"

    t0 = time.time()
    obs = observe_image(
        vision_registry, r_shot.output["path"],
        prompt="What website is this? Describe the page topic.",
        correlation_id="audit-a-vision",
    )
    vision_latency = time.time() - t0

    assert obs is not None, "Vision returned None — no VISION provider available"
    assert obs.description, "Vision description is empty"
    assert vision_latency < 30.0, f"Vision too slow: {vision_latency:.1f}s"

    # Both DOM and Vision available simultaneously — check agreement
    vision_mentions_python = (
        "python" in obs.description.lower()
        or "wikipedia" in obs.description.lower()
        or "programming" in obs.description.lower()
    )
    assert vision_mentions_python, (
        f"Vision did not identify Python/Wikipedia page. Got: {obs.description[:200]!r}"
    )

    # Both data streams are available at this point in the same "turn"
    assert links_count > 0  # DOM data
    assert obs.description  # Vision data
    # => DOM + Vision simultaneously available: CONFIRMED


# ── Test B : Grounding visuel — gap détecté ──────────────────────────────────

@real_e2e
def test_e2e_b_visual_grounding_gap_with_gemma4(browser_agent, vision_registry):
    """Test de grounding — documente le GAP : gemma4 renvoie des coordonnées
    pixel au lieu de normalisées [0,1], rendant _parse_grounding inutilisable.

    Ce test DOCUMENTE le comportement réel, il ne vérifie pas que le grounding
    fonctionne (il ne fonctionne pas avec gemma4:cloud).
    """
    from raya.contracts import CommandStatus
    from raya.models.vision import observe_image, _parse_grounding

    r_nav = _nav(browser_agent, "https://www.wikipedia.org/", "audit-b")
    assert r_nav.status == CommandStatus.SUCCESS

    r_shot = _screenshot(browser_agent, "wiki_home_b.png", "audit-b-shot")
    assert r_shot.status == CommandStatus.SUCCESS

    obs = observe_image(
        vision_registry, r_shot.output["path"],
        find_target="search box",
        correlation_id="audit-b-grounding",
    )

    assert obs is not None, "Vision returned None"

    # DOCUMENT the gap: gemma4 returns pixel coordinates → target is None
    # The model says FOUND but BoundingBox validation rejects > 1.0 values
    if obs.target is None and "FOUND" in obs.description:
        # This is the documented gap: gemma4 pixel coords rejected by BoundingBox
        pytest.xfail(
            "KNOWN GAP: gemma4:cloud returns pixel coordinates instead of normalized [0,1]. "
            f"Raw: {obs.description[:100]!r}"
        )
    elif obs.target is not None:
        # If it works, validate the coordinates
        assert 0 <= obs.target.bbox.x_min < obs.target.bbox.x_max <= 1
        assert 0 <= obs.target.bbox.y_min < obs.target.bbox.y_max <= 1
    # else: NOT_FOUND response — also acceptable (element not visible)


# ── Test C : DOM/Vision disambiguation sur Google ────────────────────────────

@real_e2e
def test_e2e_c_dom_vision_disambiguation_google(browser_agent, vision_registry):
    """DOM et Vision identifient indépendamment Google Search."""
    from raya.contracts import CommandStatus
    from raya.models.vision import observe_image

    r_nav = _nav(browser_agent, "https://www.google.com/", "audit-c")
    assert r_nav.status == CommandStatus.SUCCESS

    # DOM
    r_dom = _read(browser_agent, "audit-c-dom")
    assert r_dom.status == CommandStatus.SUCCESS
    inputs = r_dom.output.get("inputs", [])

    # Vision
    r_shot = _screenshot(browser_agent, "google_c.png", "audit-c-shot")
    assert r_shot.status == CommandStatus.SUCCESS

    obs = observe_image(
        vision_registry, r_shot.output["path"],
        prompt="What is this website? What are the main UI elements?",
        correlation_id="audit-c-vision",
    )
    assert obs is not None
    assert obs.description

    vision_mentions_google = (
        "google" in obs.description.lower()
        or "search" in obs.description.lower()
    )
    dom_has_inputs = len(inputs) >= 1

    assert dom_has_inputs, f"DOM found no inputs on Google: {r_dom.output}"
    assert vision_mentions_google, f"Vision did not mention Google/search: {obs.description[:200]!r}"
    # Agreement: both DOM and Vision confirm this is a search page with an input


# ── Test D : Échec DOM + récupération Vision sur GitHub ──────────────────────

@real_e2e
def test_e2e_d_dom_failure_vision_recovery_github(browser_agent, vision_registry):
    """DOM click échoue (ELEMENT_NOT_FOUND) → Vision décrit la page réelle."""
    from raya.contracts import CommandStatus, ToolResultStatus
    from raya.models.vision import observe_image

    r_nav = _nav(browser_agent, "https://github.com/", "audit-d")
    assert r_nav.status == CommandStatus.SUCCESS
    time.sleep(0.5)

    # Try a click on a non-existent element
    r_click = _click(browser_agent, "NONEXISTENT_ELEMENT_XYZ_99999", "audit-d-click")
    assert r_click.status == CommandStatus.FAILURE
    assert r_click.error is not None
    assert r_click.error.code == "ELEMENT_NOT_FOUND"

    # Recovery path: Vision
    r_shot = _screenshot(browser_agent, "github_d.png", "audit-d-shot")
    assert r_shot.status == CommandStatus.SUCCESS

    obs = observe_image(
        vision_registry, r_shot.output["path"],
        prompt="Describe the main visible UI elements on this page.",
        correlation_id="audit-d-recovery",
    )
    assert obs is not None
    assert len(obs.description) > 20, "Vision recovery description too short"

    # Recovery successful: Vision provided useful info after DOM failure
    vision_mentions_github = (
        "github" in obs.description.lower()
        or "code" in obs.description.lower()
        or "repository" in obs.description.lower()
        or "sign" in obs.description.lower()
    )
    assert vision_mentions_github, f"Vision recovery not useful: {obs.description[:200]!r}"


# ── Test E : Écran réel (pyautogui) + Vision ─────────────────────────────────

@real_e2e
def test_e2e_e_real_screen_pyautogui_vision(vision_registry, tmp_path):
    """Capture d'écran réel via pyautogui + analyse Vision."""
    import pyautogui
    from raya.models.vision import observe_image

    # Real screen capture
    img = pyautogui.screenshot()
    assert img.width > 0 and img.height > 0

    img_path = tmp_path / "real_screen_e.png"
    img.save(str(img_path))
    assert img_path.exists()

    # Vision analysis
    t0 = time.time()
    obs = observe_image(
        vision_registry, str(img_path),
        prompt="What applications or windows are visible on this desktop?",
        correlation_id="audit-e-vision",
    )
    latency = time.time() - t0

    assert obs is not None, "Vision returned None"
    assert obs.description, "Vision description empty"
    assert latency < 30.0, f"Vision too slow: {latency:.1f}s"
    assert obs.source == "perception:screen"
    assert obs.confidence.value == "inferred"

    # WorldState value excludes artifact_ref (privacy contract)
    if obs.artifact_ref:
        ws_val = obs.to_world_state_value()
        assert "artifact_ref" not in ws_val
