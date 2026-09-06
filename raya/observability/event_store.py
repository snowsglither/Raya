"""Event Repository — persistance CURATÉE des events significatifs
(RAYA_V2_MIGRATION_PLAN.md §9 de la consigne Phase 1).

EventBus != persistence : ce store ne s'abonne qu'à un sous-ensemble
d'events significatifs (task/harness/safety/memory/world_state/runtime),
jamais à absolument tout (contrairement à ObservabilityTracer, qui reste
en mémoire pour le debug immédiat). correlation_id est toujours conservé.
"""

from __future__ import annotations

from raya.contracts import Event, from_dict, to_dict
from raya.event_bus import BackpressurePolicy, EventBus
from raya.persistence import PersistenceBackend

_COLLECTION = "events"

_SIGNIFICANT_PREFIXES = (
    "task.",
    "harness.",
    "safety.",
    "memory.entry_written",
    "memory.entry_updated",
    "memory.entry_obsoleted",
    "world_state.updated",
    "runtime.",
)


def _is_significant(event_type: str) -> bool:
    return any(event_type.startswith(p) for p in _SIGNIFICANT_PREFIXES)


class EventStore:
    def __init__(self, backend: PersistenceBackend, bus: EventBus) -> None:
        self._backend = backend
        bus.subscribe(
            "*",
            self._on_event,
            subscriber="observability.event_store",
            backpressure_policy=BackpressurePolicy.DROP_OLDEST,
        )

    def _on_event(self, event: Event) -> None:
        if not _is_significant(event.type):
            return
        self._backend.save(_COLLECTION, event.id, to_dict(event))

    def timeline(self, correlation_id: str) -> list[Event]:
        rows = self._backend.query(_COLLECTION, correlation_id=correlation_id)
        events = [from_dict(Event, r) for r in rows]
        return sorted(events, key=lambda e: e.timestamp)

    def recent(self, limit: int = 20) -> list[Event]:
        rows = self._backend.query(_COLLECTION)
        events = [from_dict(Event, r) for r in rows]
        events.sort(key=lambda e: e.timestamp, reverse=True)
        return events[:limit]
