"""Tool Discovery (RAYA_V2_TECHNICAL_ARCHITECTURE.md §8.3).

Le modèle ne reçoit JAMAIS "tous les outils" — seulement le sous-ensemble
pertinent pour l'objectif courant. C'est aussi le seul point d'accès que
context_engine a le droit d'utiliser sur tools/ (lecture seule, jamais
tools.execution — RAYA_V2_REPOSITORY_STRUCTURE.md §20).
"""

from __future__ import annotations

from raya.contracts import Tool

from .registry import ToolRegistry


def discover(registry: ToolRegistry, capability_tags: list[str]) -> list[Tool]:
    if not capability_tags:
        return []
    wanted = set(capability_tags)
    return [t for t in registry.all() if wanted & set(t.capability_tags)]
