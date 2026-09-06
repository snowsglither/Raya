"""Trace passive de tous les events (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.16).

S'abonne à "*" sur l'EventBus (consommateur pur), n'appelle jamais rien
d'autre. Best-effort : backpressure drop_oldest (perte tolérée plutôt que
ralentir le système).
"""

from __future__ import annotations

import threading

from raya.contracts import Event
from raya.event_bus import BackpressurePolicy, EventBus

from .logger import log


class ObservabilityTracer:
    def __init__(self, bus: EventBus) -> None:
        self._events: list[Event] = []
        self._lock = threading.Lock()
        bus.subscribe(
            "*",
            self._on_event,
            subscriber="observability",
            backpressure_policy=BackpressurePolicy.DROP_OLDEST,
        )

    def _on_event(self, event: Event) -> None:
        with self._lock:
            self._events.append(event)
        log("debug", f"event {event.type}", correlation_id=event.correlation_id, source=event.source)

    def timeline(self, correlation_id: str) -> list[Event]:
        with self._lock:
            return [e for e in self._events if e.correlation_id == correlation_id]

    def all(self) -> list[Event]:
        with self._lock:
            return list(self._events)
