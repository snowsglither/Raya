"""Souris — EXTRACT quasi verbatim de modules/pc_control/mouse.py (Win32 natif,
supporte les coordonnées négatives multi-écran, ce que pyautogui gère mal)."""

from __future__ import annotations

import ctypes
import time

_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040


def set_cursor_pos(x: int, y: int) -> bool:
    try:
        x_c = ctypes.c_int(int(x)).value
        y_c = ctypes.c_int(int(y)).value
        return bool(ctypes.windll.user32.SetCursorPos(x_c, y_c))
    except Exception:
        return False


def click_at(x: int, y: int, button: str = "left", clicks: int = 1, interval: float = 0.1) -> bool:
    if not set_cursor_pos(x, y):
        return False
    time.sleep(0.05)
    down_flag, up_flag = _MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP
    if button == "right":
        down_flag, up_flag = _MOUSEEVENTF_RIGHTDOWN, _MOUSEEVENTF_RIGHTUP
    elif button == "middle":
        down_flag, up_flag = _MOUSEEVENTF_MIDDLEDOWN, _MOUSEEVENTF_MIDDLEUP
    for i in range(clicks):
        ctypes.windll.user32.mouse_event(down_flag, 0, 0, 0, 0)
        time.sleep(0.02)
        ctypes.windll.user32.mouse_event(up_flag, 0, 0, 0, 0)
        if i < clicks - 1:
            time.sleep(interval)
    return True


def move_to(x: int, y: int) -> bool:
    return set_cursor_pos(x, y)
