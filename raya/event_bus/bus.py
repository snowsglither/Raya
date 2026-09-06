"""EventBus — infrastructure transversale (RAYA_V2_TECHNICAL_ARCHITECTURE.md §13,
RAYA_V2_CONTRACTS.md §1.1).

Ce n'est PAS un 17e subsystem métier : il route des `Event` par `type`, ne lit
jamais `payload`, ne filtre jamais par état métier, ne devient jamais un
second orchestrateur. contracts/ est la seule dépendance RAYA autorisée ici.
"""

from __future__ import annotations

import fnmatch
import queue
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from raya.contracts import Event


class BackpressurePolicy(str, Enum):
    DROP_OLDEST = "drop_oldest"
    BLOCK_PUBLISHER_WITH_TIMEOUT = "block_publisher_with_timeout"


EventHandler = Callable[[Event], None]

_DEFAULT_QUEUE_DEPTH = 256
_DEFAULT_BLOCK_TIMEOUT_S = 2.0


@dataclass
class SubscriptionHandle:
    subscriber: str
    event_type_pattern: str
    _id: int = field(repr=False)


class _Subscription:
    __slots__ = ("subscriber", "pattern", "handler", "policy", "queue", "thread", "_stop")

    def __init__(
        self,
        subscriber: str,
        pattern: str,
        handler: EventHandler,
        policy: BackpressurePolicy,
        queue_depth: int,
    ) -> None:
        self.subscriber = subscriber
        self.pattern = pattern
        self.handler = handler
        self.policy = policy
        self.queue: "queue.Queue[Event]" = queue.Queue(maxsize=queue_depth)
        self._stop = threading.Event()
        self.thread = threading.Thread(
            target=self._deliver_loop, name=f"eventbus-{subscriber}", daemon=True
        )
        self.thread.start()

    def matches(self, event_type: str) -> bool:
        return fnmatch.fnmatchcase(event_type, self.pattern)

    def enqueue(self, event: Event) -> bool:
        """Retourne False si l'event a été abandonné (backpressure drop_oldest)."""
        if self.policy == BackpressurePolicy.DROP_OLDEST:
            try:
                self.queue.put_nowait(event)
                return True
            except queue.Full:
                try:
                    self.queue.get_nowait()  # abandonne le plus ancien
                except queue.Empty:
                    pass
                try:
                    self.queue.put_nowait(event)
                except queue.Full:
                    pass
                return False
        else:  # BLOCK_PUBLISHER_WITH_TIMEOUT
            try:
                self.queue.put(event, timeout=_DEFAULT_BLOCK_TIMEOUT_S)
                return True
            except queue.Full:
                # Timeout dépassé sur un abonné critique (ex: safety) : à signaler
                # à observability avec sévérité haute (RAYA_V2_TECHNICAL_ARCHITECTURE.md §13.3).
                return False

    def _deliver_loop(self) -> None:
        while not self._stop.is_set():
            try:
                event = self.queue.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                self.handler(event)
            except Exception:
                # Un abonné qui échoue ne doit jamais casser le bus ni les autres abonnés.
                pass

    def stop(self) -> None:
        self._stop.set()


class EventBus:
    """publish/subscribe in-process. Ordering garanti par (correlation_id, subscriber)
    via la file FIFO propre à chaque abonné — pas d'ordre global entre correlation_id
    différents (RAYA_V2_TECHNICAL_ARCHITECTURE.md §13.2)."""

    def __init__(self) -> None:
        self._subscriptions: list[_Subscription] = []
        self._lock = threading.Lock()
        self._next_id = 0
        self.dropped_count = 0
        self.timeout_count = 0

    def publish(self, event: Event) -> None:
        """Retourne immédiatement (fire-and-forget côté appelant)."""
        with self._lock:
            subs = [s for s in self._subscriptions if s.matches(event.type)]
        for sub in subs:
            delivered = sub.enqueue(event)
            if not delivered:
                if sub.policy == BackpressurePolicy.DROP_OLDEST:
                    self.dropped_count += 1
                else:
                    self.timeout_count += 1

    def subscribe(
        self,
        event_type_pattern: str,
        handler: EventHandler,
        subscriber: str,
        backpressure_policy: BackpressurePolicy = BackpressurePolicy.DROP_OLDEST,
        queue_depth: int = _DEFAULT_QUEUE_DEPTH,
    ) -> SubscriptionHandle:
        sub = _Subscription(subscriber, event_type_pattern, handler, backpressure_policy, queue_depth)
        with self._lock:
            self._next_id += 1
            handle_id = self._next_id
            self._subscriptions.append(sub)
        handle = SubscriptionHandle(subscriber=subscriber, event_type_pattern=event_type_pattern, _id=handle_id)
        self._by_handle_id = getattr(self, "_by_handle_id", {})
        self._by_handle_id[handle_id] = sub
        return handle

    def unsubscribe(self, handle: SubscriptionHandle) -> None:
        sub = getattr(self, "_by_handle_id", {}).pop(handle._id, None)
        if sub is None:
            return
        sub.stop()
        with self._lock:
            if sub in self._subscriptions:
                self._subscriptions.remove(sub)

    def wait_idle(self, timeout_s: float = 1.0) -> bool:
        """Utilitaire de test : attend que toutes les files d'abonnés soient vides."""
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            with self._lock:
                subs = list(self._subscriptions)
            if all(s.queue.empty() for s in subs):
                time.sleep(0.02)  # laisse le temps au handler en cours de finir
                return True
            time.sleep(0.01)
        return False
