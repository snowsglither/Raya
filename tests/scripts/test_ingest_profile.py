"""scripts/ingest_profile.py — parsing pur + idempotence réelle (SQLite).

Le contenu de test ci-dessous est FICTIF (jamais les vraies données de
Ruben) — seule la FORME du fichier source (## SECTION / - puce) doit être
identique à profil_ruben.md pour que le test soit représentatif."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import ingest_profile  # noqa: E402

from raya.contracts import ChannelScope, MemoryLayer, MemoryType  # noqa: E402
from raya.memory import MemoryStore  # noqa: E402
from raya.persistence import SqliteBackend  # noqa: E402

_SAMPLE = """# PROFIL FICTIF DE TEST
## IDENTITÉ
- Nom complet : Jane Doe
- Réside à Testville

## CENTRES D'INTÉRÊT
- ÉCHECS : joue le dimanche

## PRÉFÉRENCES DE COMPORTEMENT (comment agir)
- Tutoiement, ton direct
"""


def test_parse_profile_extracts_sections_and_bullets_only():
    entries = ingest_profile.parse_profile(_SAMPLE)
    assert ("identity", "Nom complet : Jane Doe") in entries
    assert ("identity", "Réside à Testville") in entries
    assert ("centres_d_interet", "ÉCHECS : joue le dimanche") in entries
    assert not any("PROFIL FICTIF" in b for _s, b in entries)  # titre ignoré, pas une puce


def test_slugify_identity_override_and_generic_ascii_normalization():
    assert ingest_profile._slugify("IDENTITÉ") == "identity"
    assert ingest_profile._slugify("CENTRES D'INTÉRÊT") == "centres_d_interet"


def test_memory_type_classification():
    assert ingest_profile._memory_type_for("preferences_de_comportement_comment_agir") == MemoryType.PREFERENCE
    assert ingest_profile._memory_type_for("identity") == MemoryType.FACT
    assert ingest_profile._memory_type_for("relations") == MemoryType.FACT


def test_ingest_writes_structured_entries_not_one_giant_blob(tmp_path):
    # RAYA_DATA_DIR pointe déjà sur CE tmp_path (fixture autouse
    # tests/conftest.py::_isolated_raya_data_dir) — isolation automatique,
    # jamais la vraie base de données de l'utilisateur.
    source = tmp_path / "profile.md"
    source.write_text(_SAMPLE, encoding="utf-8")

    result = ingest_profile.ingest(source, dry_run=False)
    assert len(result["written"]) == 4  # jamais UN seul bloc géant

    backend = SqliteBackend(tmp_path / "raya_v2.sqlite3")
    memory = MemoryStore(backend)
    entries = memory.search(query="", channel_scope=ChannelScope.SHARED, limit=100)
    assert len(entries) == 4
    assert all(e.layer == MemoryLayer.PERSONAL for e in entries)
    identity_entries = [e for e in entries if e.provenance == "profile_migration:identity"]
    assert len(identity_entries) == 2
    backend.close()


def test_ingest_is_idempotent_on_rerun(tmp_path):
    source = tmp_path / "profile.md"
    source.write_text(_SAMPLE, encoding="utf-8")

    ingest_profile.ingest(source, dry_run=False)
    second = ingest_profile.ingest(source, dry_run=False)
    assert len(second["written"]) == 0
    assert len(second["skipped"]) == 4

    backend = SqliteBackend(tmp_path / "raya_v2.sqlite3")
    memory = MemoryStore(backend)
    entries = memory.search(query="", channel_scope=ChannelScope.SHARED, limit=100)
    assert len(entries) == 4  # pas de doublon après un deuxième run
    backend.close()


def test_dry_run_never_writes(tmp_path):
    source = tmp_path / "profile.md"
    source.write_text(_SAMPLE, encoding="utf-8")

    ingest_profile.ingest(source, dry_run=True)
    db_path = tmp_path / "raya_v2.sqlite3"
    if db_path.exists():
        backend = SqliteBackend(db_path)
        memory = MemoryStore(backend)
        entries = memory.search(query="", channel_scope=ChannelScope.SHARED, limit=100)
        assert entries == []
        backend.close()
