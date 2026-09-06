"""FocusTracker — notion minimale de focus actif (RAYA_V2_MIGRATION_PLAN.md §6
de la consigne Phase 2). Une session a au plus une Task "en focus" à la fois.
Purement en mémoire (le focus est une préférence d'attention transitoire, pas
une donnée métier durable — ne PAS le persister comme fait World State ou Memory).
"""

from __future__ import annotations

import threading


class FocusTracker:
    def __init__(self) -> None:
        self._focus: dict[str, str] = {}  # session_id -> task_id
        self._lock = threading.Lock()

    def set_focus(self, session_id: str, task_id: str) -> None:
        with self._lock:
            self._focus[session_id] = task_id

    def get_focus(self, session_id: str) -> str | None:
        with self._lock:
            return self._focus.get(session_id)

    def clear_focus(self, session_id: str) -> None:
        with self._lock:
            self._focus.pop(session_id, None)
