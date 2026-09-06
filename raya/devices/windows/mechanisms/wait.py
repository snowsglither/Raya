"""Attente événementielle bornée — EXTRACT quasi verbatim de
modules/pc_control/wait.py. Jamais un sleep fixe comme stratégie (consigne
Phase 4 §12) : `until()` sort DÈS que la condition est vraie, timeout garde-fou."""

from __future__ import annotations

import subprocess
import time
from typing import Callable

_DEFAULT_TIMEOUT = 10.0
_DEFAULT_POLL = 0.15


def until(pred: Callable[[], bool], timeout: float = _DEFAULT_TIMEOUT, poll: float = _DEFAULT_POLL) -> bool:
    deadline = time.time() + max(0.0, timeout)
    while True:
        try:
            if pred():
                return True
        except Exception:
            pass
        if time.time() >= deadline:
            return False
        time.sleep(poll)


def process_available(name: str, timeout: float = 10.0) -> bool:
    target = (name or "").lower()
    if not target:
        return False

    def _running() -> bool:
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            return target in (out.stdout or "").lower()
        except Exception:
            return False

    return until(_running, timeout=timeout, poll=0.25)
