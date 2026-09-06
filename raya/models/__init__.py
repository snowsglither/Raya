"""Model Layer (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.11, §4).

Feuille du graphe de dépendance : ne dépend d'aucun autre subsystem RAYA
au-delà de contracts/ (RAYA_V2_REPOSITORY_STRUCTURE.md §20).
"""

from .registry import ModelRegistry
from .router import describe_active, route

__all__ = ["ModelRegistry", "describe_active", "route"]
