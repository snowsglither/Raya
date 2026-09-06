"""PresenceState + PresenceTracker (consigne Phase 5 §8).

La présence DÉCRIT l'état, elle ne décide jamais rien elle-même — ce n'est
PAS un second orchestrateur. `PresenceTracker` se construit uniquement à
partir d'événements déjà publiés sur l'EventBus (task.*, attention.*,
voice.*) — aucun appel LLM, aucune décision métier (consigne §26 : jamais
de LLM par événement de présence)."""

from __future__ import annotations

import enum
import threading
from dataclasses import dataclass, field

from raya.contracts import Event, utc_now_iso


class PresenceLabel(str, enum.Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PROCESSING = "processing"
    SPEAKING = "speaking"
    WORKING = "working"
    INTERRUPTED = "interrupted"
    WAITING = "waiting"
    NEEDS_ATTENTION = "needs_attention"
    UNAVAILABLE = "unavailable"


@dataclass
class PresenceState:
    state: PresenceLabel
    timestamp: str
    active_session: str | None
    active_tasks: list[str] = field(default_factory=list)
    speaking: bool = False
    listening: bool = False
    user_speaking: bool = False
    attention_state: str | None = None
    processing: bool = False
    needs_attention: bool = False


def _derive_label(*, speaking: bool, user_speaking: bool, listening: bool, interrupted: bool,
                   active_tasks: list[str], unavailable: bool, processing: bool = False,
                   needs_attention: bool = False) -> PresenceLabel:
    if unavailable:
        return PresenceLabel.UNAVAILABLE
    if interrupted:
        return PresenceLabel.INTERRUPTED
    if needs_attention:
        return PresenceLabel.NEEDS_ATTENTION
    if speaking:
        return PresenceLabel.SPEAKING
    if user_speaking or listening:
        return PresenceLabel.LISTENING
    if processing:
        return PresenceLabel.PROCESSING
    if active_tasks:
        return PresenceLabel.WORKING
    return PresenceLabel.IDLE


class PresenceTracker:
    """État mutable, protégé par lock — mis à jour par des appels explicites
    depuis `runtime.py` (jamais par un import direct de `attention`/`tasks`,
    interfaces/ ne les importe jamais). `snapshot()` produit un `PresenceState`
    immuable (dataclass copiée) à un instant T."""

    def __init__(self, session_id: str | None = None) -> None:
        self._lock = threading.Lock()
        self._session_id = session_id
        self._speaking = False
        self._user_speaking = False
        self._listening = False
        self._interrupted = False
        self._unavailable = False
        self._active_tasks: set[str] = set()
        self._attention_state: str | None = None
        self._processing = False
        self._needs_attention = False

    def set_speaking(self, value: bool) -> None:
        with self._lock:
            self._speaking = value
            if value:
                self._interrupted = False

    def set_user_speaking(self, value: bool) -> None:
        with self._lock:
            self._user_speaking = value

    def set_listening(self, value: bool) -> None:
        with self._lock:
            self._listening = value

    def set_interrupted(self, value: bool) -> None:
        with self._lock:
            self._interrupted = value

    def set_unavailable(self, value: bool) -> None:
        with self._lock:
            self._unavailable = value

    def set_attention_state(self, value: str | None) -> None:
        with self._lock:
            self._attention_state = value

    def set_processing(self, value: bool) -> None:
        """Reflète un appel Harness synchrone RÉELLEMENT en vol (le canal met
        ce flag AVANT `harness.handle_request()` et le retire juste après —
        jamais une supposition, l'appelant sait avec certitude qu'il attend
        une réponse, exactement comme `set_speaking` autour de `tts.speak()`)."""
        with self._lock:
            self._processing = value

    def set_needs_attention(self, value: bool) -> None:
        """Reflète une confirmation Safety RÉELLEMENT en attente (mise à jour
        uniquement depuis `harness.confirmation_required`/`_resolved` sur
        l'EventBus, jamais devinée)."""
        with self._lock:
            self._needs_attention = value

    def task_started(self, task_id: str) -> None:
        with self._lock:
            self._active_tasks.add(task_id)

    def task_ended(self, task_id: str) -> None:
        with self._lock:
            self._active_tasks.discard(task_id)

    def snapshot(self) -> PresenceState:
        with self._lock:
            label = _derive_label(
                speaking=self._speaking, user_speaking=self._user_speaking, listening=self._listening,
                interrupted=self._interrupted, active_tasks=list(self._active_tasks), unavailable=self._unavailable,
                processing=self._processing, needs_attention=self._needs_attention,
            )
            return PresenceState(
                state=label, timestamp=utc_now_iso(), active_session=self._session_id,
                active_tasks=sorted(self._active_tasks), speaking=self._speaking, listening=self._listening,
                user_speaking=self._user_speaking, attention_state=self._attention_state,
                processing=self._processing, needs_attention=self._needs_attention,
            )

    # --- Consommation d'events (aucune décision, uniquement mise à jour d'état) ---

    @staticmethod
    def _payload_get(payload: object, key: str) -> object | None:
        """Un event `task.*` RÉEL publié par raya/tasks/registry.py porte un
        `TaskEventPayload` (dataclass), jamais un dict — seuls les tests
        synthétiques utilisaient un dict brut. Bug trouvé Phase 6 : l'ancien
        `isinstance(payload, dict)` ne matchait donc JAMAIS un vrai TaskEvent
        en production, et `active_tasks`/WORKING ne se mettait jamais à jour
        pour une tâche de fond réelle. Corrigé ici en acceptant les deux formes."""
        if payload is None:
            return None
        if isinstance(payload, dict):
            return payload.get(key)
        return getattr(payload, key, None)

    def on_event(self, event: Event) -> None:
        if event.type == "task.started":
            task_id = self._payload_get(event.payload, "task_id")
            if task_id:
                self.task_started(task_id)
        elif event.type in ("task.completed", "task.failed", "task.cancelled"):
            task_id = self._payload_get(event.payload, "task_id")
            if task_id:
                self.task_ended(task_id)
        elif event.type == "attention.decision_made":
            decision = self._payload_get(event.payload, "decision")
            if decision:
                self.set_attention_state(decision)
        elif event.type == "harness.confirmation_required":
            session_id = self._payload_get(event.payload, "session_id")
            if session_id is None or session_id == self._session_id:
                self.set_needs_attention(True)
        elif event.type == "harness.confirmation_resolved":
            session_id = self._payload_get(event.payload, "session_id")
            if session_id is None or session_id == self._session_id:
                self.set_needs_attention(False)
