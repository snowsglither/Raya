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
import winreg

from . import wait as _wait
from . import window_mgmt as _win


def _resolve_app_path(name: str) -> str | None:
    """Résout un nom court (ex: "steam") vers le chemin complet via le registre
    App Paths — même résolution que ShellExecute, mais explicite et fiable pour
    les noms sans extension .exe."""
    key_name = name if name.lower().endswith(".exe") else name + ".exe"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            subkey = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{key_name}"
            with winreg.OpenKey(hive, subkey) as k:
                path = winreg.QueryValue(k, None)
                if path and os.path.isfile(path.strip('"')):
                    return path.strip('"')
        except (FileNotFoundError, OSError):
            continue
    return None


def launch(target: str, wait_timeout_s: float = 8.0) -> dict:
    """Lance `target` (nom résolu par registre App Paths, ou chemin) et attend
    qu'une VRAIE fenêtre apparaisse — jamais "le process existe" = succès."""
    already, matches = _win.is_open(target)
    if already:
        return {**_win.focus_window(target), "already_open": True}

    launched = False
    error: str | None = None
    try:
        os.startfile(target)  # résout via ShellExecute (App Paths registry, associations fichier…)
        launched = True
    except Exception as exc:
        error = str(exc)
        # Résolution explicite via App Paths avant le fallback shell — évite le
        # faux positif de subprocess.Popen(shell=True) qui lance cmd.exe sans
        # lever d'exception même quand la commande n'est pas trouvée.
        resolved = _resolve_app_path(target)
        try_target = resolved if resolved else target
        try:
            if resolved:
                os.startfile(resolved)
                launched = True
                error = None
            else:
                # Fallback shell : vérifie le code de retour pour détecter
                # "'steam' n'est pas reconnu" silencieux de cmd.exe.
                proc = subprocess.Popen(
                    f'start "" "{target}"', shell=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                proc.wait(timeout=3)
                if proc.returncode == 0:
                    launched = True
                    error = None
                else:
                    error = f"commande non reconnue par le shell (exit {proc.returncode}): {target!r}"
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
