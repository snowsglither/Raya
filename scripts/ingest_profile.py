#!/usr/bin/env python3
"""Ingestion structurée du profil utilisateur historique (stabilisation
pré-Phase 7, consigne §2) :

    SOURCE (fichier markdown) -> MemoryEntry (provenance, lifecycle,
    confidence, channel_scope) -> retrieval -> ContextEngine -> ModelRequest.

Ce script est un outil de MIGRATION explicite, exécuté à la demande — jamais
appelé automatiquement par bootstrap()/le runtime (consigne : "ne pas créer
un énorme prompt statique" chargé à chaque démarrage). Il ne crée aucun
nouveau Memory Manager : il n'écrit que via l'API PUBLIQUE existante de
`raya.memory.MemoryStore.write()`.

Chaque puce `- ...` d'une section `## SECTION` du fichier source devient UNE
MemoryEntry distincte (jamais un unique blob géant) — c'est ce qui permet à
`raya/context_engine/assembler.py::_memory_sections()` de ne récupérer QUE
les faits pertinents à la question posée, au lieu d'injecter tout le profil
dans chaque prompt. Les puces de la section IDENTITÉ reçoivent une provenance
spéciale (`profile_migration:identity`) reconnue par
`_identity_baseline_sections()` — ce sont les seules TOUJOURS incluses,
exactement comme les system_rules (nom, préférence de tutoiement), jamais le
reste (famille, santé, loisirs) qui reste filtré par pertinence textuelle.

Usage :
    python scripts/ingest_profile.py [--source PATH] [--dry-run] [--force]

Le contenu du profil (identité, famille, préférences...) n'est jamais
recopié en dur dans ce fichier Python — il est lu dynamiquement depuis
`--source` (ou RAYA_PROFILE_SOURCE, ou le défaut ci-dessous) à chaque
exécution, jamais hardcodé dans le code RAYA V2."""

from __future__ import annotations

import argparse
import os
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raya.contracts import ChannelScope, Confidence, MemoryEntry, MemoryLayer, MemoryLifecycle, MemoryType  # noqa: E402
from raya.memory import MemoryStore  # noqa: E402
from raya.persistence import SqliteBackend  # noqa: E402
from raya.runtime.config import load_config  # noqa: E402

_DEFAULT_SOURCE = Path(__file__).resolve().parents[2] / "RAYA" / "profil_ruben.md"

# La seule section dont les puces sont TOUJOURS injectées dans le contexte
# (voir raya/context_engine/assembler.py::_IDENTITY_BASELINE_PROVENANCE_PREFIX) —
# toute autre section reste filtrée par pertinence textuelle à la demande.
_IDENTITY_SLUG_OVERRIDE = {"identite": "identity"}
_PREFERENCE_SLUG_MARKERS = ("preference",)

_HEADING_RE = re.compile(r"^##\s+(.+?)\s*$")
_BULLET_RE = re.compile(r"^-\s+(.+?)\s*$")


def _slugify(heading: str) -> str:
    normalized = unicodedata.normalize("NFKD", heading).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^a-zA-Z0-9]+", "_", normalized).strip("_").lower()
    return _IDENTITY_SLUG_OVERRIDE.get(normalized, normalized)


def parse_profile(text: str) -> list[tuple[str, str]]:
    """Retourne [(section_slug, bullet_text), ...] — aucune section, aucune
    puce n'est filtrée ici : le filtrage sémantique (pertinent ou non pour
    une question donnée) reste le rôle du Context Engine, pas de ce script."""
    entries: list[tuple[str, str]] = []
    current_slug: str | None = None
    for line in text.splitlines():
        heading_match = _HEADING_RE.match(line)
        if heading_match:
            current_slug = _slugify(heading_match.group(1))
            continue
        bullet_match = _BULLET_RE.match(line)
        if bullet_match and current_slug:
            entries.append((current_slug, bullet_match.group(1)))
    return entries


def _memory_type_for(slug: str) -> MemoryType:
    return MemoryType.PREFERENCE if any(marker in slug for marker in _PREFERENCE_SLUG_MARKERS) else MemoryType.FACT


def ingest(source: Path, *, dry_run: bool = False, force: bool = False) -> dict:
    text = source.read_text(encoding="utf-8")
    parsed = parse_profile(text)

    config = load_config()
    backend = SqliteBackend(config.db_path)
    memory = MemoryStore(backend, bus=None)

    existing_pairs: set[tuple[str, str]] = set()
    if not force:
        existing = memory.search(query="", channel_scope=ChannelScope.SHARED, limit=1000)
        existing_pairs = {(e.provenance, str(e.content)) for e in existing}

    written, skipped = [], []
    for slug, bullet in parsed:
        provenance = f"profile_migration:{slug}"
        if (provenance, bullet) in existing_pairs:
            skipped.append((provenance, bullet))
            continue
        entry = MemoryEntry(
            type=_memory_type_for(slug),
            layer=MemoryLayer.PERSONAL,
            channel_scope=ChannelScope.SHARED,
            content=bullet,
            provenance=provenance,
            confidence=Confidence.KNOWN_FACT,
            lifecycle=MemoryLifecycle.CONFIRMED,
        )
        if not dry_run:
            memory.write(entry)
        written.append((provenance, bullet))

    backend.close()
    return {"source": str(source), "written": written, "skipped": skipped, "db_path": str(config.db_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, default=Path(os.environ.get("RAYA_PROFILE_SOURCE", _DEFAULT_SOURCE)))
    parser.add_argument("--dry-run", action="store_true", help="Parse et affiche sans écrire dans MemoryStore.")
    parser.add_argument("--force", action="store_true", help="Réingère même si des entrées identiques existent déjà.")
    args = parser.parse_args()

    if not args.source.exists():
        print(f"Source introuvable : {args.source}", file=sys.stderr)
        return 1

    result = ingest(args.source, dry_run=args.dry_run, force=args.force)
    print(f"Source     : {result['source']}")
    print(f"Base cible : {result['db_path']}")
    print(f"Écrites    : {len(result['written'])}" + (" (dry-run, rien n'a été persisté)" if args.dry_run else ""))
    for provenance, bullet in result["written"]:
        print(f"  + [{provenance}] {bullet}")
    print(f"Ignorées (déjà présentes) : {len(result['skipped'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
