"""Isolation SQLite pour les tests (RAYA_V2_MIGRATION_PLAN.md §8.2 de la
consigne Phase 1) : "Les tests doivent pouvoir utiliser une SQLite temporaire
isolée." Chaque test reçoit son propre RAYA_DATA_DIR (tmp_path, unique par
test) — un appel `bootstrap()` "nu" (sans backend explicite) ne touche donc
JAMAIS le repository source, et n'accumule jamais d'état entre deux tests.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolated_raya_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RAYA_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("RAYA_DB_PATH", raising=False)
    yield
