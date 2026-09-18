"""ScreenLightSensor — dhash change detection, no model calls."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from raya.contracts import Confidence
from raya.perception.screen_sensor import ScreenLightSensor, _compute_dhash, _hamming


# ─── Unit: dhash + hamming ────────────────────────────────────────────────────

def test_dhash_returns_bytes_of_expected_length():
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("PIL not installed")
    img = Image.new("RGB", (100, 80), color=(128, 128, 128))
    h = _compute_dhash(img)
    assert isinstance(h, bytes)
    assert len(h) == 8  # 9×8 = 72 bits → 9 bytes, packed as 8 (64 bits in 8 bytes)


def test_hamming_identical_images_returns_zero():
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("PIL not installed")
    img = Image.new("RGB", (200, 200), color=(64, 64, 64))
    h = _compute_dhash(img)
    assert _hamming(h, h) == 0


def test_hamming_different_images_returns_nonzero():
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        pytest.skip("PIL or numpy not installed")
    # Use checker-board patterns so adjacent pixels differ — uniform images have 0 gradient
    arr1 = np.tile(np.array([[0, 255], [255, 0]], dtype=np.uint8), (40, 50))[:80, :100]
    arr2 = np.tile(np.array([[255, 0], [0, 255]], dtype=np.uint8), (40, 50))[:80, :100]
    img1 = Image.fromarray(arr1.astype(np.uint8)).convert("RGB")
    img2 = Image.fromarray(arr2.astype(np.uint8)).convert("RGB")
    h1, h2 = _compute_dhash(img1), _compute_dhash(img2)
    assert _hamming(h1, h2) > 0


def test_hamming_same_length_identical_returns_zero():
    assert _hamming(b"\xff\xff", b"\xff\xff") == 0


def test_hamming_same_length_all_bits_differ():
    assert _hamming(b"\xff", b"\x00") == 8


# ─── ScreenLightSensor ────────────────────────────────────────────────────────

def _make_fake_screenshot(gradient_direction="left", size=(100, 80)):
    """Crée une image avec gradient horizontal — dhash non-trivial et distinct selon la direction."""
    try:
        from PIL import Image
        import numpy as np
        w, h = size
        if gradient_direction == "left":
            arr = np.tile(np.linspace(0, 255, w, dtype=np.uint8), (h, 1))
        else:
            arr = np.tile(np.linspace(255, 0, w, dtype=np.uint8), (h, 1))
        rgb = np.stack([arr, arr, arr], axis=-1)
        return Image.fromarray(rgb.astype(np.uint8), mode="RGB")
    except ImportError:
        return None


def test_first_sample_returns_none_no_baseline():
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("PIL not installed")
    sensor = ScreenLightSensor(diff_threshold=5)
    fake = _make_fake_screenshot()
    with patch("pyautogui.screenshot", return_value=fake):
        result = sensor.sample()
    assert result is None  # aucun baseline encore


def test_identical_frames_returns_none():
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("PIL not installed")
    sensor = ScreenLightSensor(diff_threshold=5)
    fake = _make_fake_screenshot()
    with patch("pyautogui.screenshot", return_value=fake):
        sensor.sample()  # baseline
        result = sensor.sample()  # identical → None
    assert result is None


def test_changed_frame_publishes_event():
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("PIL not installed")
    sensor = ScreenLightSensor(diff_threshold=5, cooldown_s=0.0)
    frame1 = _make_fake_screenshot(gradient_direction="left")
    frame2 = _make_fake_screenshot(gradient_direction="right")
    with patch("pyautogui.screenshot", side_effect=[frame1, frame2]):
        sensor.sample()  # baseline
        event = sensor.sample()
    assert event is not None
    assert event.type == "perception.visual.screen_changed"
    assert event.payload["value"]["change_detected"] is True


def test_screen_event_payload_has_expected_structure():
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("PIL not installed")
    sensor = ScreenLightSensor(diff_threshold=5, cooldown_s=0.0)
    with patch("pyautogui.screenshot", side_effect=[
        _make_fake_screenshot(gradient_direction="left"),
        _make_fake_screenshot(gradient_direction="right"),
    ]):
        sensor.sample()
        event = sensor.sample()
    assert event is not None
    assert "diff_score" in event.payload["value"]
    assert event.payload["domain"] == "visual"
    assert event.payload["key"] == "screen_state"
    assert event.payload["confidence"] == Confidence.INFERRED.value


def test_cooldown_suppresses_second_event():
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("PIL not installed")
    sensor = ScreenLightSensor(diff_threshold=5, cooldown_s=60.0)
    f_left = _make_fake_screenshot(gradient_direction="left")
    f_right = _make_fake_screenshot(gradient_direction="right")
    f_left2 = _make_fake_screenshot(gradient_direction="left")
    with patch("pyautogui.screenshot", side_effect=[f_left, f_right, f_left2]):
        sensor.sample()
        e1 = sensor.sample()  # publié
        e2 = sensor.sample()  # supprimé par cooldown
    assert e1 is not None
    assert e2 is None


def test_pyautogui_import_error_returns_none():
    sensor = ScreenLightSensor()
    with patch("pyautogui.screenshot", side_effect=ImportError("pyautogui non disponible")):
        result = sensor.sample()
    assert result is None


def test_no_model_call_in_sensor():
    """ScreenLightSensor ne contient aucun appel modèle — vérifiable statiquement."""
    import ast
    from pathlib import Path
    src = Path(__file__).parents[2] / "raya" / "perception" / "screen_sensor.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "models" not in alias.name, "import models trouvé dans ScreenLightSensor"
        if isinstance(node, ast.ImportFrom):
            if node.module:
                assert "models" not in node.module, "from raya.models trouvé dans ScreenLightSensor"
