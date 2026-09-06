"""TelegramChannel — LE client mince du Harness pour Telegram (RAYA V2 Phase
9, consigne §3 : même principe que UIChannel/VoiceChannel). Traduit un
message Telegram en `HarnessRequest`, appelle `harness.handle_request()`
(jamais cognition/tools/devices/models/tasks/safety/memory/world_state
directement — même invariant vérifié pour toutes les interfaces), lit l'état
via l'API PUBLIQUE du Harness uniquement (jamais `harness._...`).

STOP suit EXACTEMENT le même chemin que CLI/Cockpit/Voix (consigne §9) :
publication d'un `Event(type="interface.stop_requested")` — jamais un
`safety.request_stop()` direct, jamais un "TelegramStop" indépendant.

Notification proactive (consigne §28 Phase 9, étendue §19 Phase 10 à
"task.started") : ce canal s'abonne à `task.*` sur l'EventBus (même
mécanisme que `PresenceTracker.on_event`, Phase 5/6) et envoie un message
SEULEMENT quand la tâche appartient réellement à une session Telegram
(`Task.owner`) — jamais une notification à un chat qui n'a rien demandé, et
jamais un second système de notification spécifique au Long-Horizon
(consigne Phase 10 §14 : réutiliser EventBus/Task events tels quels)."""

from __future__ import annotations

from typing import Callable

from raya.contracts import Channel, Event, HarnessRequest, InterfaceInput
from raya.event_bus import EventBus
from raya.harness import Harness
from raya.observability import log

from .session import chat_id_from_session, session_id_for_chat

SendMessageFn = Callable[[int, str], None]

_TASK_TERMINAL_EVENTS = ("task.completed", "task.failed", "task.cancelled")
_TASK_NOTIFIABLE_EVENTS = ("task.started",) + _TASK_TERMINAL_EVENTS


def _payload_get(payload: object, key: str) -> object | None:
    """Un `task.*` réel porte un `TaskEventPayload` (dataclass), pas un dict
    (même piège déjà corrigé dans `raya/interfaces/voice/presence.py` Phase
    6) — accepte les deux formes."""
    if payload is None:
        return None
    if isinstance(payload, dict):
        return payload.get(key)
    return getattr(payload, key, None)


class TelegramChannel:
    def __init__(self, harness: Harness, bus: EventBus, send_message: SendMessageFn,
                 *, device_id: str = "telegram-mobile", allowed_user_ids: tuple[int, ...] = ()) -> None:
        self._harness = harness
        self._bus = bus
        self._send_message = send_message
        self._device_id = device_id
        # Passe "Targeted Fix" (Sujet 2) : repli pour `send_proactive()` —
        # voir sa docstring pour le raisonnement complet.
        self._allowed_user_ids = allowed_user_ids
        bus.subscribe("task.*", self._on_task_event, subscriber="telegram.task_notify")

    # ------------------------------------------------------------------
    # Message entrant -> Harness (SEUL point d'entrée cognitif)
    # ------------------------------------------------------------------

    def handle_message(self, chat_id: int, text: str) -> str:
        session_id = session_id_for_chat(chat_id)
        # RAYA_V2_PHASE11 (addendum Telegram outbound) : mémorise le dernier
        # chat_id ayant écrit sur ce device informationnel — seule donnée
        # nécessaire pour que `telegram.send_message` (tools/catalog/notify.py)
        # sache À QUI répondre depuis N'IMPORTE QUELLE interface, sans faire de
        # Telegram un second cerveau (aucune logique de décision ici).
        self._harness.touch_device(self._device_id, metadata={"last_chat_id": chat_id})
        log("info", "telegram.message_received", chat_id=chat_id)

        self._bus.publish(Event(
            type="interface.request_received", source="interfaces.telegram",
            payload={"channel": "mobile", "session_id": session_id},
        ))
        request = HarnessRequest(channel=Channel.MOBILE, session_id=session_id, input=InterfaceInput(text=text))
        self._harness.handle_request(request)
        return self._harness.response_text(session_id)

    def needs_confirmation(self, chat_id: int) -> bool:
        state = self._harness.session_state(session_id_for_chat(chat_id))
        return state is not None and state.pending_confirmation is not None

    def confirmation_reason(self, chat_id: int) -> str | None:
        state = self._harness.session_state(session_id_for_chat(chat_id))
        if state is None or state.pending_confirmation is None:
            return None
        return state.pending_confirmation.get("reason")

    def resolve_confirmation(self, chat_id: int, approved: bool) -> str:
        session_id = session_id_for_chat(chat_id)
        log("info", "telegram.confirmation_resolved", chat_id=chat_id, approved=approved)
        self._harness.confirm_pending(session_id, approved=approved)
        return self._harness.response_text(session_id)

    # ------------------------------------------------------------------
    # STOP global — Event -> EventBus -> Safety (jamais d'appel direct, §9)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Envoi proactif générique (Phase 11, addendum Telegram outbound) —
    # injecté dans `tools.catalog.notify.NotifyOps` par
    # `runtime/entrypoints/web.py::_maybe_start_telegram()`, permet à
    # N'IMPORTE QUELLE interface (pas seulement Telegram lui-même) de
    # demander "envoie ceci sur Telegram" via le Tool `telegram.send_message`
    # — jamais un bypass direct dans une autre interface, cette méthode reste
    # le SEUL point qui sait parler au client Telegram réel.
    # ------------------------------------------------------------------

    def send_proactive(self, text: str) -> bool:
        """Envoie `text` au chat_id connu. Résolution en deux temps :
        (1) le dernier chat_id ayant écrit (métadonnée `touch_device`,
        Phase 11) — le signal le plus frais, préféré s'il existe ;
        (2) à défaut (ex: aucun message reçu depuis le dernier redémarrage —
        cette métadonnée n'est PAS persistée, cause exacte du bug observé
        "aucun chat Telegram connu" en usage réel, passe "Targeted Fix" Sujet
        2), l'unique ID de `RAYA_TELEGRAM_ALLOWED_USER_IDS` s'il n'y en a
        QU'UN — jamais une invention : c'est le même propriétaire déjà
        explicitement autorisé pour les messages ENTRANTS (fail-closed,
        Phase 9), et pour un chat privé Telegram, chat_id == user_id
        (propriété stable de l'API). Si PLUSIEURS IDs sont autorisés, choisir
        parmi eux serait une supposition arbitraire — repli refusé dans ce
        cas, retourne False (jamais une invention/un choix arbitraire)."""
        device = self._harness.describe_device(self._device_id)
        chat_id = (device or {}).get("metadata", {}).get("last_chat_id")
        if chat_id is None and len(self._allowed_user_ids) == 1:
            chat_id = self._allowed_user_ids[0]
        if chat_id is None:
            return False
        self._send_message(chat_id, text)
        return True

    def request_stop(self, chat_id: int) -> None:
        session_id = session_id_for_chat(chat_id)
        self._bus.publish(Event(type="interface.stop_requested", source="interfaces.telegram",
                                 payload={"session_id": session_id}))

    # ------------------------------------------------------------------
    # Notification proactive (§28) — Task System reste l'unique source de
    # vérité, ce canal ne fait qu'écouter et relayer vers le bon chat_id.
    # ------------------------------------------------------------------

    def _on_task_event(self, event: Event) -> None:
        if event.type not in _TASK_NOTIFIABLE_EVENTS:
            return
        task_id = _payload_get(event.payload, "task_id")
        if not task_id:
            return
        task = self._harness.get_task(task_id)
        if task is None or task.owner.channel != "mobile":
            return
        chat_id = chat_id_from_session(task.owner.session_id)
        if chat_id is None:
            return
        label = {
            "task.started": "démarrée", "task.completed": "terminée",
            "task.failed": "échouée", "task.cancelled": "annulée",
        }[event.type]
        self._send_message(chat_id, f"Tâche {label} : {task.objective}")
