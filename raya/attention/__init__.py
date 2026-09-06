"""Attention (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.4, §7) — décide QUOI mérite
du traitement, jamais COMMENT. Dépendances autorisées : world_state, tasks
(lecture seule), observability, event_bus. Interdites : harness, models,
tools, devices, interfaces (vérifié par le lint architectural).
"""

from .evaluator import AttentionEngine, AttentionEvaluator
from .policy import FocusTracker

__all__ = ["AttentionEngine", "AttentionEvaluator", "FocusTracker"]
