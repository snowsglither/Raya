"""Audit trail (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.12).

Phase 0 : en mémoire uniquement. La persistance durable arrive avec
raya/persistence/ en Phase 1 (RAYA_V2_MIGRATION_PLAN.md Phase 1).
"""

from __future__ import annotations

import threading

from raya.contracts import Permission


class AuditTrail:
    def __init__(self) -> None:
        self._entries: list[Permission] = []
        self._lock = threading.Lock()

    def record(self, permission: Permission) -> None:
        with self._lock:
            self._entries.append(permission)

    def all(self) -> list[Permission]:
        with self._lock:
            return list(self._entries)
