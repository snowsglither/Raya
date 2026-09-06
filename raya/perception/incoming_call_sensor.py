"""IncomingCallNotificationSensor (Chantier 13G) — transforme la preuve
d'investigation du Chantier 13F en capteur léger réel : observe la vraie
bannière de notification Windows (`ShellExperienceHost.exe`,
`Windows.UI.Core.CoreWindow`) plutôt que l'activité interne de
`PhoneExperienceHost.exe` (Chantier 13D, conservé séparément — voir
`phone_sensors.py` — mais NE constitue PAS la source fiable du contenu,
consigne §2 de ce chantier).

Réutilise TEL QUEL le pattern `PhoneEventHook`/`LightSensor` déjà établi
(Chantier 13D) — même mécanisme `SetWinEventHook`, juste reconfiguré pour
cibler `ShellExperienceHost.exe` + la classe `Windows.UI.Core.CoreWindow`
(élimine le bruit de la fenêtre IME parasite observée à côté, Chantier
13F). Aucun nouveau thread, aucun polling périodique du téléphone —
`pump()`/`consume_dirty()` proviennent du même tick `PerceptionRuntime`
existant que tous les autres capteurs légers.

SÉPARATION OBSERVATION/ACTION (consigne §12, critique) : ce capteur ne fait
QUE lire et publier une observation structurée — jamais un appel à
`phone.answer`/`phone.reject`/`phone.end`/`phone.call`/`phone.sms.send`.
Les boutons d'action visibles dans la bannière (`VerbButton`) ne sont lus
que pour PROUVER structurellement qu'il s'agit d'un appel entrant (leur
présence), jamais invoqués."""

from __future__ import annotations

from raya.contracts import Confidence, Event, PerceptionObservation, to_dict

from .notification_extraction import classify_notification, read_notification_content_with_retry
from .phone_events import PhoneEventHook
from .sensors import LightSensor

_SOURCE = "perception:incoming_call_notification"
_DEFAULT_FRESHNESS_TTL_S = 30
_SHELL_PROCESS_NAME = "shellexperiencehost.exe"
_TOAST_WINDOW_CLASS = "windows.ui.core.corewindow"


def _default_event_hook() -> PhoneEventHook:
    return PhoneEventHook(target_process_name=_SHELL_PROCESS_NAME, target_class_name=_TOAST_WINDOW_CLASS)


class IncomingCallNotificationSensor(LightSensor):
    """`event_hook`/`read_with_retry`/`classify` injectables (tests
    déterministes, sans dépendre d'un vrai Windows/Phone Link). Ne publie
    un `Event` QUE lorsque :
    1. le hook a réellement signalé une activité depuis le dernier tick ;
    2. le contenu extrait (après stabilisation bornée) a pu être classé
       structurellement — `not_identified` ne publie JAMAIS rien (consigne
       §11 : ne jamais deviner) ;
    3. le contenu a réellement changé depuis la dernière publication (même
       discipline anti-bruit que les autres capteurs légers)."""

    def __init__(self, event_hook: PhoneEventHook | None = None,
                 read_with_retry=read_notification_content_with_retry,
                 classify=classify_notification) -> None:
        self._hook = event_hook if event_hook is not None else _default_event_hook()
        self._read_with_retry = read_with_retry
        self._classify = classify
        self._last_content: dict | None = None

    def sample(self) -> Event | None:
        if not self._hook.available():
            # Honnête : contrairement à PhoneCallActivitySensor (Chantier
            # 13D), cette capacité n'a pas de repli par minuteur — sans le
            # hook, elle reste simplement indisponible plutôt que de
            # réintroduire un polling périodique du téléphone.
            return None
        self._hook.pump()
        if not self._hook.consume_dirty():
            return None

        hwnd = self._hook.last_hwnd()
        if hwnd is None:
            return None

        try:
            content = self._read_with_retry(hwnd)
        except Exception:
            return None

        classification = self._classify(content)
        if classification == "not_identified":
            return None  # jamais une observation devinée (consigne §11)
        if content == self._last_content:
            return None
        self._last_content = content

        value = {
            "call_state": "incoming" if classification == "incoming_call" else "other",
            "caller": content.get("title") or "unknown",
            "source_text": content.get("attribution") or "",
            "sender_category": content.get("sender_name") or "",
            "toast_view_type": content.get("toast_view_type") or "",
        }
        observation = PerceptionObservation(
            domain="phone", key="incoming_call", value=value,
            source=_SOURCE, confidence=Confidence.INFERRED,
            freshness_ttl_s=_DEFAULT_FRESHNESS_TTL_S,
        )
        return Event(type="perception.incoming_call_notification", source="perception", payload=to_dict(observation))
