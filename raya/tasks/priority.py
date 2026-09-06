"""Priorité minimale (RAYA_V2_MIGRATION_PLAN.md §13 de la consigne Phase 2).

Le contrat `Task.priority` figé est un `int` brut (RAYA_V2_CONTRACTS.md §5) —
ce module fournit "l'équivalent déjà défini dans les contrats" demandé par la
consigne : des NIVEAUX NOMMÉS mappés sur des valeurs entières, sans toucher au
contrat lui-même. Convention simple, documentée, utilisée par le scheduler,
Attention et le CLI.
"""

from __future__ import annotations

LOW = 0
NORMAL = 5
HIGH = 10
CRITICAL = 20

_NAMES = {"low": LOW, "normal": NORMAL, "high": HIGH, "critical": CRITICAL}


def from_name(name: str) -> int:
    return _NAMES.get(name.strip().lower(), NORMAL)


def to_name(value: int) -> str:
    if value >= CRITICAL:
        return "critical"
    if value >= HIGH:
        return "high"
    if value >= NORMAL:
        return "normal"
    return "low"
