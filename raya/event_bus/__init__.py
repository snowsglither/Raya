"""EventBus — infrastructure transversale (RAYA_V2_TECHNICAL_ARCHITECTURE.md §13).

N'est pas un subsystem métier. Ne dépend que de contracts/.
"""

from .bus import BackpressurePolicy, EventBus, SubscriptionHandle

__all__ = ["BackpressurePolicy", "EventBus", "SubscriptionHandle"]
