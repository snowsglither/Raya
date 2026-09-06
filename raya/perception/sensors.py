"""Perception — capteurs légers (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.3, §11).

Phase 0 : interface seulement. Les capteurs réels (fenêtre active, CPU/RAM,
EXTRACT de modules/context/monitor.py + modules/hud/monitor.py) arrivent en
Phase 4 avec les Device Agents Windows (RAYA_V2_MIGRATION_PLAN.md Phase 4).
Règle absolue : aucun capteur, léger ou non, n'importe raya.models.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from raya.contracts import Event


class LightSensor(ABC):
    """Capteur autorisé à tourner en continu (RAYA_V2_TECHNICAL_ARCHITECTURE.md §11.1)."""

    @abstractmethod
    def sample(self) -> Event | None: ...
