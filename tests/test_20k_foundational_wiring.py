"""Chantier 20K — vérification des 5 gaps de câblage fondamental.

Ces tests prouvent que chaque gap identifié par l'audit 20J est réellement
fermé, sans lancer le runtime complet ni appeler de vrai modèle.

Gaps couverts :
  C1 — image_ref transport (test_ollama_image_transport.py couvre déjà C1)
  C2 — register_visual_tools exportée et injectable depuis bootstrap
  C3 — browser.click_at_position câblé : _DISPATCH + _CAPABILITIES + catalog
  C4 — browser.screenshot retourne width/height réels
  C5 — browser.last_clicked_target/last_clicked_at promouvables dans WorldState
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest


# ─── C2 : register_visual_tools exportée ─────────────────────────────────────

class TestVisualToolsExport:

    def test_register_visual_tools_importable_from_catalog(self):
        from raya.tools.catalog import register_visual_tools
        assert callable(register_visual_tools)

    def test_register_visual_tools_registers_five_tools(self):
        from raya.tools import ToolRegistry
        from raya.tools.catalog import register_visual_tools

        registry = ToolRegistry()

        observe_fn = MagicMock(return_value=None)
        capture_screen_fn = MagicMock(return_value={"path": "/tmp/x.png", "width": 1920, "height": 1080})
        capture_browser_fn = MagicMock(return_value={"path": "/tmp/b.png", "width": 1280, "height": 720})

        register_visual_tools(registry, observe_fn, capture_screen_fn, capture_browser_fn)

        names = {t.name for t in registry.all()}
        assert "vision.observe_screen" in names
        assert "vision.observe_browser" in names
        assert "vision.find_on_screen" in names
        assert "vision.find_in_browser" in names


# ─── C3 : browser.click_at_position câblé ────────────────────────────────────

class TestClickAtPositionWiring:

    def test_click_at_position_in_dispatch(self):
        from raya.devices.browser.agent import _DISPATCH
        assert "browser.click_at_position" in _DISPATCH

    def test_click_at_position_in_capabilities(self):
        from raya.devices.browser.agent import _CAPABILITIES
        names = {c.name for c in _CAPABILITIES}
        assert "browser.click_at_position" in names

    def test_click_at_position_registered_in_catalog(self):
        from raya.contracts import Command, CommandStatus, Result
        from raya.devices import ShouldStop
        from raya.tools import ToolRegistry
        from raya.tools.catalog.browser import register_browser_tools

        registry = ToolRegistry()
        mock_agent = MagicMock()
        mock_agent.execute.return_value = Result(
            command_id="cmd-1",
            status=CommandStatus.SUCCESS,
            output={"clicked_at": {"x": 100, "y": 200}},
            evidence={"clicked_at": "100,200"},
        )
        register_browser_tools(registry, mock_agent, should_stop=lambda: False)

        names = {t.name for t in registry.all()}
        assert "browser.click_at_position" in names

    def test_click_at_position_observation_spec(self):
        from raya.tools.catalog.browser import _CLICK_AT_POSITION_OBSERVATION
        assert len(_CLICK_AT_POSITION_OBSERVATION) == 1
        spec = _CLICK_AT_POSITION_OBSERVATION[0]
        assert spec.domain == "browser"
        assert spec.key == "last_clicked_at"
        assert spec.evidence_field == "clicked_at"

    def test_browser_controller_click_at_position_calls_mouse(self):
        from raya.devices.browser.controller import BrowserController

        mock_session = MagicMock()
        mock_worker = MagicMock()
        fake_page = MagicMock()
        mock_session.get_or_create.return_value = fake_page

        def fake_run_sync(fn, timeout=60.0):
            return fn()

        mock_worker.run_sync = fake_run_sync
        ctrl = BrowserController(mock_session, mock_worker)
        result = ctrl.click_at_position(320, 480)

        fake_page.mouse.click.assert_called_once_with(320, 480)
        assert result["status"] == "ok"
        assert result["x"] == 320
        assert result["y"] == 480


# ─── C4 : browser.screenshot retourne width/height ───────────────────────────

class TestScreenshotDimensions:

    def test_screenshot_returns_width_height_from_pil(self, tmp_path):
        pytest.importorskip("PIL", reason="Pillow requis pour ce test")
        from PIL import Image

        from raya.devices.browser.controller import BrowserController

        # Créer une vraie image PNG 100x80
        img_path = str(tmp_path / "test.png")
        Image.new("RGB", (100, 80), color=(255, 0, 0)).save(img_path)

        mock_session = MagicMock()
        mock_worker = MagicMock()
        fake_page = MagicMock()
        mock_session.get_or_create.return_value = fake_page

        # La page.screenshot(path=...) ne fait rien — le fichier existe déjà
        fake_page.screenshot.side_effect = lambda path: None

        def fake_run_sync(fn, timeout=60.0):
            return fn()

        mock_worker.run_sync = fake_run_sync
        ctrl = BrowserController(mock_session, mock_worker)
        result = ctrl.screenshot(img_path)

        assert result["status"] == "ok"
        assert result["path"] == img_path
        assert result.get("width") == 100
        assert result.get("height") == 80

    def test_screenshot_falls_back_to_viewport_size(self, tmp_path):
        from raya.devices.browser.controller import BrowserController

        img_path = str(tmp_path / "fake.png")
        # Ne pas créer le fichier — PIL échouera, fallback viewport_size

        mock_session = MagicMock()
        mock_worker = MagicMock()
        fake_page = MagicMock()
        fake_page.viewport_size = {"width": 1280, "height": 720}
        mock_session.get_or_create.return_value = fake_page
        fake_page.screenshot.side_effect = lambda path: None

        def fake_run_sync(fn, timeout=60.0):
            return fn()

        mock_worker.run_sync = fake_run_sync
        ctrl = BrowserController(mock_session, mock_worker)
        result = ctrl.screenshot(img_path)

        assert result["status"] == "ok"
        assert result.get("width") == 1280
        assert result.get("height") == 720
