"""Migrations de schéma (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.15).

Migrations idempotentes et rejouables sans perte, dans l'esprit de
modules/conversations/store.py V1 (bon précédent — ADAPT). Une seule table
générique `kv_store` sert TOUS les repositories (world_state/memory/tasks/
execution_records/events) — le reste du code ne connaît jamais le SQL.
"""

from __future__ import annotations

import sqlite3

MIGRATIONS: list[tuple[int, list[str]]] = [
    (
        1,
        [
            """
            CREATE TABLE IF NOT EXISTS kv_store (
                collection TEXT NOT NULL,
                item_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (collection, item_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_kv_collection ON kv_store(collection)",
        ],
    ),
]


def current_version(conn: sqlite3.Connection) -> int:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_meta (version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_meta").fetchone()
    if row is None:
        conn.execute("INSERT INTO schema_meta(version) VALUES (0)")
        return 0
    return row[0]


def migrate(conn: sqlite3.Connection) -> int:
    """Applique les migrations en attente. Idempotent : rejouer ne casse rien."""
    version = current_version(conn)
    applied = 0
    for target_version, statements in MIGRATIONS:
        if target_version <= version:
            continue
        for stmt in statements:
            conn.execute(stmt)
        conn.execute("UPDATE schema_meta SET version = ?", (target_version,))
        version = target_version
        applied += 1
    conn.commit()
    return applied
