"""Clavier — EXTRACT du mécanisme de modules/pc_control/keyboard.py +
modules/computeruse/actions.py. `type_text` passe par le presse-papier
(Ctrl+V) pour un support Unicode fiable (accents/emoji), avec repli
`pyautogui.write` — mécanisme pur, aucune décision."""

from __future__ import annotations

import time

import pyautogui

from . import clipboard as _clipboard

pyautogui.FAILSAFE = False  # désactivé délibérément (V1 : un coin d'écran involontaire paralysait tout) ; le vrai killswitch est STOP


def type_text(text: str, use_clipboard: bool = True) -> bool:
    if not text:
        return True
    if use_clipboard:
        saved = _clipboard.save()
        try:
            if _clipboard.set_text(text)["status"] == "ok":
                time.sleep(0.05)
                pyautogui.hotkey("ctrl", "v")
                time.sleep(0.05)
                return True
        finally:
            if saved.get("status") == "ok":
                _clipboard.restore()
    try:
        pyautogui.write(text, interval=0.01)
        return True
    except Exception:
        return False


def press(combo: str) -> bool:
    """combo: 'enter', 'ctrl+l', 'alt+tab', etc."""
    keys = [k.strip() for k in combo.lower().split("+") if k.strip()]
    if not keys:
        return False
    try:
        if len(keys) == 1:
            pyautogui.press(keys[0])
        else:
            pyautogui.hotkey(*keys)
        return True
    except Exception:
        return False
