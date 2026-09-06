"""UIEventBridge — traduit l'EventBus en notifications UI filtrées (RAYA V2
Phase 6 EVENT-DRIVEN UI). N'expose jamais l'objet `Event`/`TaskEvent` brut au
frontend (ni chain-of-thought, ni détail interne) — uniquement un petit dict
JSON-safe `{type, payload}` parmi une liste FERMÉE de types UI reconnus.

Ne poll rien : s'abonne une fois à l'EventBus, pousse un callback par event
pertinent. Le frontend décide s'il redessine ou non — cette classe ne fait
que filtrer/traduire, jamais de décision de présentation."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable

from raya.event_bus import EventBus, SubscriptionHandle

# Catalogue FERMÉ des types poussés au frontend — tout le reste du bus
# (tool.call_*, world_state.updated, harness.turn_*, ...) reste interne et
# n'est lu qu'à la demande via les endpoints REST (jamais poussé en continu,
# consigne "Do not create a permanent... dashboard").
_UI_EVENT_TYPES = (
    "task.started",
    "task.progress",
    "task.paused",
    "task.resumed",
    "task.completed",
    "task.failed",
    "task.cancelled",
    "attention.decision_made",
    "harness.confirmation_required",
    "harness.confirmation_resolved",
    "harness.turn_completed",
    "harness.turn_failed",
    "interface.stop_requested",
    "ui.view_requested",
)

# Types dont le payload porte un session_id et qui ne doivent JAMAIS fuiter
# vers une session Cockpit différente (ex: le modèle ouvre une vue pour LA
# session qui a posé la question, pas pour tous les onglets ouverts).
_SESSION_SCOPED_TYPES = frozenset({
    "harness.confirmation_required", "harness.confirmation_resolved",
    "harness.turn_completed", "harness.turn_failed", "ui.view_requested",
})

UINotification = Callable[[dict], None]


@dataclass
class _UIEvent:
    type: str
    payload: dict


def _payload_get(payload: object, key: str) -> object | None:
    if payload is None:
        return None
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


def _to_ui_payload(event) -> dict:
    payload = event.payload
    if isinstance(payload, dict):
        return dict(payload)
    if payload is None:
        return {}
    # TaskEventPayload (dataclass) ou tout autre payload structuré connu.
    return {
        "task_id": _payload_get(payload, "task_id"),
        "new_state": _payload_get(payload, "new_state"),
        "previous_state": _payload_get(payload, "previous_state"),
        "detail": _payload_get(payload, "detail"),
    }


class UIEventBridge:
    """Un bridge par session UI (WebSocket) — `on_notify` reçoit
    `{"type": <event.type>, "payload": {...}}` pour chaque event pertinent."""

    def __init__(self, bus: EventBus, session_id: str) -> None:
        self._bus = bus
        self._session_id = session_id
        self._lock = threading.Lock()
        self._listeners: list[UINotification] = []
        self._handles: list[SubscriptionHandle] = []
        for event_type in _UI_EVENT_TYPES:
            handle = bus.subscribe(event_type, self._on_event, subscriber=f"ui.bridge.{session_id}.{event_type}")
            self._handles.append(handle)

    def add_listener(self, callback: UINotification) -> None:
        with self._lock:
            self._listeners.append(callback)

    def remove_listener(self, callback: UINotification) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _on_event(self, event) -> None:
        payload = _to_ui_payload(event)
        if event.type in _SESSION_SCOPED_TYPES:
            event_session_id = payload.get("session_id")
            if event_session_id is not None and event_session_id != self._session_id:
                return
        notification = {"type": event.type, "payload": payload}
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(notification)
            except Exception:
                # Un listener défaillant (ex: WebSocket fermée) ne doit jamais
                # casser le bridge ni les autres listeners (même invariant
                # que raya/event_bus/bus.py::_Subscription._deliver_loop).
                pass

    def close(self) -> None:
        for handle in self._handles:
            self._bus.unsubscribe(handle)
        with self._lock:
            self._listeners.clear()
