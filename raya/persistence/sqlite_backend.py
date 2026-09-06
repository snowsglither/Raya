"""SqliteBackend — backend de persistence réel (RAYA_V2_MIGRATION_PLAN.md Phase 1).

Une seule table générique `kv_store` (collection, item_id, payload_json) sert
tous les repositories — le reste du code (world_state/memory/tasks/harness)
ne voit jamais de SQL, seulement PersistenceBackend.save/load/query/delete.

WAL + verrou applicatif : "concurrent-safe usage raisonnable pour Phase 1",
pas un serveur multi-process — cohérent avec "SQLite local suffit".
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

from raya.contracts import utc_now_iso

from .backend import PersistenceBackend
from .migrations import migrate


class SqliteBackend(PersistenceBackend):
    def __init__(self, db_path: str | Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            migrate(self._conn)

    @property
    def path(self) -> Path:
        return self._path

    def save(self, collection: str, item_id: str, payload: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv_store(collection, item_id, payload_json, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(collection, item_id) DO UPDATE SET "
                "payload_json = excluded.payload_json, updated_at = excluded.updated_at",
                (collection, item_id, json.dumps(payload, ensure_ascii=False), utc_now_iso()),
            )
            self._conn.commit()

    def load(self, collection: str, item_id: str) -> dict | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload_json FROM kv_store WHERE collection = ? AND item_id = ?",
                (collection, item_id),
            ).fetchone()
        return json.loads(row[0]) if row else None

    def query(self, collection: str, **filters: object) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload_json FROM kv_store WHERE collection = ?", (collection,)
            ).fetchall()
        items = [json.loads(r[0]) for r in rows]
        for key, value in filters.items():
            items = [i for i in items if i.get(key) == value]
        return items

    def delete(self, collection: str, item_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM kv_store WHERE collection = ? AND item_id = ?", (collection, item_id)
            )
            self._conn.commit()

    def save_batch(self, items: list[tuple[str, str, dict]]) -> None:
        """UNE transaction pour tout le batch — soit tout est écrit, soit rien
        (RAYA_V2_MIGRATION_PLAN.md §14, testé par rollback explicite)."""
        with self._lock:
            try:
                for collection, item_id, payload in items:
                    self._conn.execute(
                        "INSERT INTO kv_store(collection, item_id, payload_json, updated_at) VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(collection, item_id) DO UPDATE SET "
                        "payload_json = excluded.payload_json, updated_at = excluded.updated_at",
                        (collection, item_id, json.dumps(payload, ensure_ascii=False), utc_now_iso()),
                    )
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    @contextmanager
    def transaction(self):
        """Primitif bas niveau pour les tests de rollback explicite."""
        with self._lock:
            try:
                yield self._conn
                self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()
