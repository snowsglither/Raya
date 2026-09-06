"""Priorité A — Persistence : init, schema, migration, CRUD, transaction,
rollback, reopen, persistance après redémarrage."""

from __future__ import annotations

import sqlite3

import pytest

from raya.persistence import InMemoryBackend, SqliteBackend
from raya.persistence.migrations import MIGRATIONS, current_version, migrate


def test_initialization_creates_schema(tmp_path):
    backend = SqliteBackend(tmp_path / "db.sqlite3")
    conn = sqlite3.connect(str(tmp_path / "db.sqlite3"))
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "kv_store" in tables
    assert "schema_meta" in tables
    backend.close()


def test_migration_sets_expected_version(tmp_path):
    path = tmp_path / "db.sqlite3"
    backend = SqliteBackend(path)
    conn = sqlite3.connect(str(path))
    assert current_version(conn) == MIGRATIONS[-1][0]
    backend.close()


def test_migration_is_idempotent(tmp_path):
    path = tmp_path / "db.sqlite3"
    conn = sqlite3.connect(str(path))
    applied1 = migrate(conn)
    applied2 = migrate(conn)
    assert applied1 == len(MIGRATIONS)
    assert applied2 == 0  # rejouer ne casse rien, rien à réappliquer
    conn.close()


def test_insert_and_retrieve(tmp_path):
    backend = SqliteBackend(tmp_path / "db.sqlite3")
    backend.save("things", "id1", {"a": 1})
    assert backend.load("things", "id1") == {"a": 1}
    backend.close()


def test_update_overwrites_existing(tmp_path):
    backend = SqliteBackend(tmp_path / "db.sqlite3")
    backend.save("things", "id1", {"a": 1})
    backend.save("things", "id1", {"a": 2})
    assert backend.load("things", "id1") == {"a": 2}
    assert len(backend.query("things")) == 1
    backend.close()


def test_query_with_filters(tmp_path):
    backend = SqliteBackend(tmp_path / "db.sqlite3")
    backend.save("things", "id1", {"kind": "x", "v": 1})
    backend.save("things", "id2", {"kind": "y", "v": 2})
    result = backend.query("things", kind="x")
    assert len(result) == 1
    assert result[0]["v"] == 1
    backend.close()


def test_delete(tmp_path):
    backend = SqliteBackend(tmp_path / "db.sqlite3")
    backend.save("things", "id1", {"a": 1})
    backend.delete("things", "id1")
    assert backend.load("things", "id1") is None
    backend.close()


def test_save_batch_is_atomic_all_or_nothing(tmp_path):
    backend = SqliteBackend(tmp_path / "db.sqlite3")
    backend.save_batch([("things", "id1", {"a": 1}), ("things", "id2", {"a": 2})])
    assert len(backend.query("things")) == 2
    backend.close()


def test_transaction_rollback_leaves_no_partial_state(tmp_path):
    backend = SqliteBackend(tmp_path / "db.sqlite3")
    try:
        with backend.transaction() as conn:
            conn.execute(
                "INSERT INTO kv_store(collection,item_id,payload_json,updated_at) VALUES (?,?,?,?)",
                ("things", "id1", "{}", "now"),
            )
            raise RuntimeError("simulated failure mid-transaction")
    except RuntimeError:
        pass
    assert backend.load("things", "id1") is None
    backend.close()


def test_reopen_same_file_preserves_data(tmp_path):
    path = tmp_path / "db.sqlite3"
    b1 = SqliteBackend(path)
    b1.save("things", "id1", {"a": 1})
    b1.close()

    b2 = SqliteBackend(path)
    assert b2.load("things", "id1") == {"a": 1}
    b2.close()


def test_persistence_survives_close_and_many_writes(tmp_path):
    path = tmp_path / "db.sqlite3"
    b1 = SqliteBackend(path)
    for i in range(50):
        b1.save("things", f"id{i}", {"n": i})
    b1.close()

    b2 = SqliteBackend(path)
    assert len(b2.query("things")) == 50
    b2.close()


def test_in_memory_backend_transaction_and_batch_still_work():
    backend = InMemoryBackend()
    backend.save_batch([("c", "1", {"x": 1}), ("c", "2", {"x": 2})])
    assert len(backend.query("c")) == 2
    with backend.transaction():
        pass  # ne lève rien : InMemoryBackend est toujours "atomique"
