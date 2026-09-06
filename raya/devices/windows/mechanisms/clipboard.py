"""Presse-papier — EXTRACT quasi verbatim de modules/pc_control/clipboard.py.
Règle : ne jamais écraser définitivement le presse-papier de l'utilisateur —
save() empile, restore() remet (utilisé par keyboard.type_text)."""

from __future__ import annotations

import pyperclip

_STACK: list[str] = []


def get_text() -> dict:
    try:
        text = pyperclip.paste() or ""
        return {"status": "ok", "text": text, "length": len(text)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def set_text(text: str) -> dict:
    try:
        pyperclip.copy(text if text is not None else "")
        return {"status": "ok", "length": len(text or "")}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def save() -> dict:
    try:
        _STACK.append(pyperclip.paste() or "")
        return {"status": "ok", "depth": len(_STACK)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def restore() -> dict:
    if not _STACK:
        return {"status": "empty"}
    try:
        prev = _STACK.pop()
        pyperclip.copy(prev)
        return {"status": "ok", "restored_length": len(prev), "depth": len(_STACK)}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
