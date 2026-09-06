"""Découverte ciblée du système de fichiers réel (Chantier 12 §C, Living
Environment Awareness) — délibérément absent depuis Phase 4 (voir le
docstring de `agent.py` : "filesystem.* non implémenté cette phase").

Générique, jamais un cas par dossier utilisateur codé en dur (consigne §4,
"si folder == 'Projets' interdit") : cherche par NOM sous le dossier
personnel de l'utilisateur, borné en profondeur et en nombre de résultats —
une découverte CIBLÉE (consigne Chantier 12 §4), jamais un scan massif de
tout le disque comme l'ancien `apps/scanner.py` V1."""

from __future__ import annotations

import os
from pathlib import Path

_MAX_DEPTH = 3
_MAX_RESULTS = 5
# Dossiers volumineux/non pertinents à ne jamais descendre — générique
# (extensions d'outils/VCS/dépendances), jamais un nom d'application/site.
_SKIP_DIR_NAMES = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "AppData", "$Recycle.Bin", "System Volume Information", ".cache",
}


def find_folder(name: str, max_depth: int = _MAX_DEPTH, max_results: int = _MAX_RESULTS) -> dict:
    """Cherche un dossier dont le NOM correspond exactement (insensible à la
    casse) sous le dossier personnel — jamais une recherche floue qui
    inventerait une correspondance approximative."""
    query = (name or "").strip()
    if not query:
        return {"status": "error", "error": "nom de dossier vide"}
    home = Path.home()
    query_lower = query.lower()
    matches: list[str] = []

    def _walk(path: Path, depth: int) -> None:
        if depth > max_depth or len(matches) >= max_results:
            return
        try:
            entries = list(os.scandir(path))
        except (PermissionError, OSError):
            return
        for entry in entries:
            if len(matches) >= max_results:
                return
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
            except OSError:
                continue
            if entry.name.startswith(".") or entry.name in _SKIP_DIR_NAMES:
                continue
            if entry.name.lower() == query_lower:
                matches.append(entry.path)
            _walk(Path(entry.path), depth + 1)

    _walk(home, 0)
    return {"status": "ok", "query": query, "matches": matches, "count": len(matches)}


def open_path(path: str) -> dict:
    """Ouvre un dossier/fichier réel dans l'application par défaut de l'OS
    (Explorer pour un dossier) — `path` doit être une valeur déjà résolue
    (issue de `find_folder` ou d'un fait World State connu), jamais construite
    ici (aucune logique de résolution de nom dans cette fonction)."""
    target = Path(path)
    if not target.exists():
        return {"status": "error", "error": f"chemin introuvable: {path!r}"}
    try:
        os.startfile(str(target))  # noqa: S606 — ouverture OS standard, pas une exécution de commande arbitraire
    except OSError as exc:
        return {"status": "error", "error": str(exc)}
    return {"status": "ok", "path": str(target)}
