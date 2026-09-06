"""StopController (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.12).

Règle verrouillée (RAYA_V2_ARCHITECTURE_FINAL_REVIEW.md §2) :
    Interface → Event "interface.stop_requested" → EventBus → Safety (s'abonne)
    → met à jour son flag interne → tout le reste lit should_stop() en SYNCHRONE.

should_stop() n'est JAMAIS reconstruit depuis un event à chaque lecture — seule
la mise à jour initiale transite par le bus. La lecture reste un simple accès
mémoire pour garantir la priorité absolue.
"""

from __future__ import annotations

import threading

from raya.contracts import Event
from raya.event_bus import BackpressurePolicy, EventBus

_STOP_EVENT_TYPES = (
    "interface.stop_requested",
    "task.stop_requested",
    "device.stop_requested",
)


class StopController:
    def __init__(self, bus: EventBus | None = None) -> None:
        self._flag = threading.Event()
        self._last_source: str | None = None
        self._bus = bus
        self._handle = None
        if bus is not None:
            for event_type in _STOP_EVENT_TYPES:
                self._handle = bus.subscribe(
                    event_type,
                    self._on_stop_event,
                    subscriber="safety.stop",
                    # Abonné critique : jamais de perte silencieuse d'un STOP.
                    backpressure_policy=BackpressurePolicy.BLOCK_PUBLISHER_WITH_TIMEOUT,
                )

    def _on_stop_event(self, event: Event) -> None:
        self.request_stop(source=event.source)

    def request_stop(self, source: str) -> None:
        self._last_source = source
        self._flag.set()

    def should_stop(self) -> bool:
        """Lecture synchrone directe — jamais via l'EventBus."""
        return self._flag.is_set()

    def last_source(self) -> str | None:
        return self._last_source

    def reset(self) -> None:
        """Un redémarrage clean réinitialise le STOP (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.12)."""
        self._flag.clear()
        self._last_source = None
