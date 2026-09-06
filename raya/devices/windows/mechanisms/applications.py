"""Cycle de vie applicatif — mécanisme GÉNÉRIQUE (lance/ferme/focus/liste
n'importe quel exécutable), volontairement dépourvu des tables V1
`_STANDARD_APPS`/`_KNOWN_WEB_SERVICES` et du hint de recette clavier codé en
dur trouvé dans `modules/pc_control/applications.py` (consigne Phase 4 §2 —
c'est exactement le pattern "si phrase X alors action Y" interdit).

Simplification assumée (documentée dans le rapport Phase 4) : `list_apps()`
liste les applications actuellement OUVERTES (réutilise `window_mgmt`), pas
l'inventaire complet des applications installées (Start Menu/Steam/Epic/UWP —
`apps/scanner.py` en V1) — hors scope de ce périmètre réduit."""

from __future__ import annotations

import os
import subprocess

from . import wait as _wait
from . import window_mgmt as _win


def launch(target: str, wait_timeout_s: float = 8.0) -> dict:
    """Lance `target` (nom résolu par Windows App Paths, ou chemin) et attend
    qu'une VRAIE fenêtre apparaisse — jamais "le process existe" = succès."""
    already, matches = _win.is_open(target)
    if already:
        return {**_win.focus_window(target), "already_open": True}

    launched = False
    error: str | None = None
    try:
        os.startfile(target)  # résout via App Paths registry pour les noms courts (notepad, calc, mspaint...)
        launched = True
    except Exception as exc:
        error = str(exc)
        try:
            subprocess.Popen(target, shell=True)
            launched = True
            error = None
        except Exception as exc2:
            error = str(exc2)

    if not launched:
        return {"status": "error", "action": "launch", "target": target, "error": error}

    ready = _win.wait_for_window(target, timeout_s=wait_timeout_s)
    if ready["status"] != "ready":
        return {"status": "timeout", "action": "launch", "target": target,
                "detail": "lancé mais aucune fenêtre détectée dans le délai"}
    return {"status": "ok", "action": "launch", "target": target, "window": ready["window"], "already_open": False}


def close(target: str) -> dict:
    return _win.close_window(target)


def focus(target: str) -> dict:
    return _win.focus_window(target)


def list_apps() -> dict:
    """Applications actuellement ouvertes (fenêtres visibles), dédupliquées
    par process — pas l'inventaire complet des logiciels installés."""
    result = _win.list_windows()
    seen = set()
    apps = []
    for w in result.get("windows", []):
        proc = w.get("process") or ""
        if proc and proc not in seen:
            seen.add(proc)
            apps.append({"process": proc, "title": w.get("title", "")})
    return {"status": "ok", "count": len(apps), "applications": apps}
