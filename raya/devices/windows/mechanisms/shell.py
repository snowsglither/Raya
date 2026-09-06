"""Exécution de commande shell/CLI BORNÉE (Chantier 16, §8-11) — capability
GÉNÉRIQUE (n'importe quel exécutable réellement disponible), jamais un outil
dédié par application. Exécute TOUJOURS en mode caché (`CREATE_NO_WINDOW`) :
principe "USE ≠ SHOW" (consigne §10) — cette capability ne rend jamais une
fenêtre visible. Rendre une application visible reste `application.launch`
(mechanisms/applications.py), une capability séparée et délibérément
distincte.

Classée SENSITIVE inconditionnellement au niveau Tool (tools/catalog/pc.py,
raya/safety/risk.py) — jamais de confirmation contournée, jamais un bypass
Safety, exactement comme n'importe quel autre Tool SENSITIVE existant.

Sortie/erreur tronquées (jamais un dump illimité dans le contexte modèle) ;
timeout borné même si l'appelant en demande un plus grand (jamais un process
qui reste accroché indéfiniment)."""

from __future__ import annotations

import subprocess

_MAX_OUTPUT_CHARS = 4000
_MAX_TIMEOUT_S = 30.0
_DEFAULT_TIMEOUT_S = 15.0


def _truncate(text: str) -> tuple[str, bool]:
    if len(text) <= _MAX_OUTPUT_CHARS:
        return text, False
    return text[:_MAX_OUTPUT_CHARS], True


def run(command: str, timeout_s: float = _DEFAULT_TIMEOUT_S) -> dict:
    command = (command or "").strip()
    if not command:
        return {"status": "error", "error": "commande vide"}
    bounded_timeout = min(float(timeout_s), _MAX_TIMEOUT_S)
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True, timeout=bounded_timeout,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        stdout, stdout_truncated = _truncate(proc.stdout or "")
        stderr, stderr_truncated = _truncate(proc.stderr or "")
        return {
            "status": "ok",
            "exit_code": proc.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "truncated": stdout_truncated or stderr_truncated,
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "timeout_s": bounded_timeout}
    except Exception as exc:  # jamais planter le Device Agent pour une commande invalide
        return {"status": "error", "error": str(exc)}
