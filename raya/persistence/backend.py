"""PersistenceBackend — abstraction (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.15).

Ne connaît aucun concept métier (Task/MemoryEntry/...), seulement des
collections/id/payload sérialisés. `save_batch` est le primitif
transactionnel utilisé quand plusieurs états liés doivent être persistés
ensemble sans état partiellement écrit (RAYA_V2_MIGRATION_PLAN.md §14 —
"SQLite local suffit pour Phase 1", pas de distributed transaction system).
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from contextlib import contextmanager


class PersistenceBackend(ABC):
    @abstractmethod
    def save(self, collection: str, item_id: str, payload: dict) -> None: ...

    @abstractmethod
    def load(self, collection: str, item_id: str) -> dict | None: ...

    @abstractmethod
    def query(self, collection: str, **filters: object) -> list[dict]: ...

    @abstractmethod
    def delete(self, collection: str, item_id: str) -> None: ...

    @abstractmethod
    def save_batch(self, items: list[tuple[str, str, dict]]) -> None:
        """Écrit plusieurs (collection, item_id, payload) dans UNE transaction —
        soit tout est persisté, soit rien ne l'est (RAYA_V2_MIGRATION_PLAN.md §14)."""
        ...

    @abstractmethod
    def close(self) -> None: ...


class InMemoryBackend(PersistenceBackend):
    """Backend de test rapide, sans I/O disque. Toujours "atomique" par
    construction (un seul process, un seul verrou)."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict]] = {}
        self._lock = threading.RLock()

    def save(self, collection: str, item_id: str, payload: dict) -> None:
        with self._lock:
            self._data.setdefault(collection, {})[item_id] = payload

    def load(self, collection: str, item_id: str) -> dict | None:
        with self._lock:
            return self._data.get(collection, {}).get(item_id)

    def query(self, collection: str, **filters: object) -> list[dict]:
        with self._lock:
            items = list(self._data.get(collection, {}).values())
        for key, value in filters.items():
            items = [i for i in items if i.get(key) == value]
        return items

    def delete(self, collection: str, item_id: str) -> None:
        with self._lock:
            self._data.get(collection, {}).pop(item_id, None)

    def save_batch(self, items: list[tuple[str, str, dict]]) -> None:
        with self._lock:
            for collection, item_id, payload in items:
                self._data.setdefault(collection, {})[item_id] = payload

    @contextmanager
    def transaction(self):
        with self._lock:
            yield self

    def close(self) -> None:
        pass
