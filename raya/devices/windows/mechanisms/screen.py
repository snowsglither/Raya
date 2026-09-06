"""Capture d'écran — mécanisme simple, réel (pyautogui.screenshot), aucune
décision. SAFE (lecture seule, consigne Phase 4 §21)."""

from __future__ import annotations

from pathlib import Path

import pyautogui


def capture(output_dir: Path, filename: str = "screenshot.png") -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    img = pyautogui.screenshot()
    img.save(path)
    return {"status": "ok", "path": str(path), "width": img.width, "height": img.height}
