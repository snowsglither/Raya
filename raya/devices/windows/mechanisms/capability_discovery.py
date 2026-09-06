"""Découverte BORNÉE d'exécutables/outils réellement disponibles (Chantier
16, Capability Discovery §12) — JAMAIS un scan de disque : uniquement PATH
(`shutil.which`, mécanisme OS standard), jamais de recherche récursive ni de
lecture de registre exhaustive. Empêche RAYA d'inventer un exécutable/chemin
(consigne §14, "NO PATH / TOOL HALLUCINATION") en lui donnant un moyen réel
de VÉRIFIER avant d'utiliser.

`_KNOWN_CAPABILITIES` est délibérément PETITE — reprend uniquement les
exemples déjà cités explicitement par la consigne elle-même (§9/§12), jamais
une liste exhaustive d'applications. Un exécutable absent de cette table
reste découvrable (available/path) mais sans capability associée (`None`) —
jamais une capability devinée."""

from __future__ import annotations

import shutil

_KNOWN_CAPABILITIES: dict[str, str] = {
    "nmap": "network.scan",
    "ping": "network.probe",
    "tracert": "network.trace",
    "nslookup": "network.dns",
    "ipconfig": "network.config",
    "netsh": "network.config",
    "curl": "network.http",
    "git": "vcs.git",
    "python": "scripting.python",
    "powershell": "shell",
    "cmd": "shell",
}


def find(name: str) -> dict:
    """Résout un nom d'exécutable via PATH (comportement identique à la
    résolution que le shell ferait lui-même) — jamais un chemin inventé si
    absent."""
    query = (name or "").strip()
    if not query:
        return {"name": name, "available": False, "path": None, "capability": None}
    path = shutil.which(query)
    return {
        "name": query,
        "available": path is not None,
        "path": path,
        "capability": _KNOWN_CAPABILITIES.get(query.lower()) if path is not None else None,
    }
