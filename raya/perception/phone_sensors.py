"""PhoneCallActivitySensor — capteur léger réel (Chantier 13B/13D, Event-
Driven Phone Awareness). Détecte une activité d'appel téléphonique RÉELLE
(widget d'appel actif OU nouvelle entrée en tête de l'historique d'appels
de Phone Link) via UI Automation — implémentation DÉLIBÉRÉMENT indépendante
de raya/devices/ios/mechanisms/phone_link.py, même principe que
ActiveWindowSensor vs devices/windows/mechanisms/window_mgmt.py (voir
windows_sensors.py) : `perception/` n'a pas le droit d'importer
`raya.devices` (graphe de dépendance ALLOWED, scripts/arch_lint.py), et un
capteur léger continu n'est structurellement pas la même chose qu'une action
outillée passée par Safety (invariant #3).

CHANTIER 13D — CHANGEMENT DE MÉCANISME (voir phone_events.py pour
l'investigation complète des deux pistes essayées) : ce capteur ne fait
PLUS de lecture UI Automation sur un minuteur fixe. Il consulte d'abord
`PhoneEventHook` (raya/perception/phone_events.py, `SetWinEventHook`
Win32 natif, événement OS réel confirmé en conditions réelles) — la lecture
UIA (coûteuse) n'a lieu QUE si ce hook signale qu'une activité RÉELLE a été
captée dans le process Phone Link depuis le dernier tick. Tant que rien ne
se passe, `sample()` ne fait qu'un `pump()` quasi gratuit (drainage non-
bloquant d'une file de messages Win32, aucune lecture Phone Link) et
retourne `None` immédiatement.

LIMITATION HONNÊTE inchangée depuis 13B : le signal capté (activité
générique dans le process Phone Link) n'est pas sémantiquement spécifique à
"appel entrant" — la lecture UIA qui suit reste nécessaire pour déterminer
CE QUI a changé, et ne peut toujours pas distinguer avec certitude "sonne
encore" de "vient de se terminer" (reflété par `confidence=INFERRED`,
jamais KNOWN_FACT). Repli honnête si le hook n'est pas disponible sur cette
plateforme (ex: hors Windows, pywin32 absent) : ancien throttle temporel
Chantier 13B (une lecture au plus toutes les `_FALLBACK_MIN_INTERVAL_S`),
jamais un échec silencieux total."""

from __future__ import annotations

import time

from raya.contracts import Confidence, Event, PerceptionObservation, to_dict

from .phone_events import PhoneEventHook
from .sensors import LightSensor

_SOURCE = "perception:phone_link_call_activity"
_DEFAULT_FRESHNESS_TTL_S = 30
# Repli UNIQUEMENT si PhoneEventHook est indisponible sur cette plateforme
# (voir docstring) — plus le mécanisme principal depuis Chantier 13D.
_FALLBACK_MIN_INTERVAL_S = 8.0

_WINDOW_CLASS = "WinUIDesktopWin32WindowClass"
_PROCESS_NAME = "phoneexperiencehost.exe"
_END_CALL_AUTOMATION_ID = "EndCallButton"
_CALL_LOG_NAME_ID = "CallLogsDisplayName"
_CALL_LOG_TIME_ID = "CallLogTime"


def _find_phone_link_hwnd():
    """Résolution minimale et INDÉPENDANTE de devices/windows/mechanisms/
    window_mgmt.py — par NOM DE PROCESS + CLASSE, jamais le titre affiché
    (localisé, variable selon l'état de connexion — cf. découverte Chantier
    13 : Phone Link expose une seconde fenêtre "pont" sous le même process
    qu'il faut explicitement exclure)."""
    import psutil
    import win32gui
    import win32process

    target = {"hwnd": None}

    def _cb(hwnd, _lparam):
        if target["hwnd"] is not None or not win32gui.IsWindowVisible(hwnd):
            return
        try:
            if win32gui.GetClassName(hwnd) != _WINDOW_CLASS:
                return
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if psutil.Process(pid).name().lower() != _PROCESS_NAME:
                return
            target["hwnd"] = hwnd
        except Exception:
            pass

    win32gui.EnumWindows(_cb, None)
    return target["hwnd"]


def _read_phone_call_activity() -> dict | None:
    """Implémentation réelle (uiautomation direct). Retourne un dict décrivant
    l'activité détectée, ou `None` si Phone Link n'est pas visible ou si rien
    d'exploitable n'est présent — jamais une exception qui remonterait au
    thread de poll (PerceptionRuntime.poll_once() protège aussi au niveau
    runtime, mais le capteur tient la même promesse seul)."""
    try:
        import uiautomation as auto
    except ImportError:
        return None
    try:
        hwnd = _find_phone_link_hwnd()
        if hwnd is None:
            return None
        control = auto.ControlFromHandle(hwnd)
        if control is None:
            return None

        end_call_present = False
        latest_log_name = None
        latest_log_time = None
        for child, _depth in auto.WalkControl(control, includeTop=False, maxDepth=14):
            aid = getattr(child, "AutomationId", "") or ""
            if aid == _END_CALL_AUTOMATION_ID:
                end_call_present = True
            elif aid == _CALL_LOG_NAME_ID and latest_log_name is None:
                latest_log_name = getattr(child, "Name", "") or ""
            elif aid == _CALL_LOG_TIME_ID and latest_log_time is None:
                latest_log_time = getattr(child, "Name", "") or ""

        if not end_call_present and latest_log_name is None:
            return None
        return {
            "in_call": end_call_present,
            "latest_call_log_name": latest_log_name,
            "latest_call_log_time": latest_log_time,
        }
    except Exception:
        return None


class PhoneCallActivitySensor(LightSensor):
    """`read_activity`/`event_hook` injectables (tests unitaires
    déterministes, sans dépendre d'un vrai Phone Link/Windows). Ne publie un
    `Event` QUE lorsque l'activité a réellement CHANGÉ depuis le dernier
    `sample()` — même discipline anti-bruit que ActiveWindowSensor (consigne
    Phase 7 §10)."""

    def __init__(self, read_activity=_read_phone_call_activity, event_hook: PhoneEventHook | None = None,
                 fallback_min_interval_s: float = _FALLBACK_MIN_INTERVAL_S) -> None:
        self._read = read_activity
        self._last: dict | None = None
        self._hook = event_hook if event_hook is not None else PhoneEventHook()
        self._fallback_min_interval_s = fallback_min_interval_s
        self._last_query_at = 0.0

    def sample(self) -> Event | None:
        if self._hook.available():
            # Mécanisme principal (Chantier 13D) : draine la file de
            # messages (quasi gratuit) puis ne lit Phone Link QUE si le hook
            # a réellement capté une activité depuis le dernier tick.
            self._hook.pump()
            if not self._hook.consume_dirty():
                return None
        else:
            # Repli honnête (voir docstring du module) — plateforme sans
            # SetWinEventHook accessible : throttle temporel Chantier 13B.
            now = time.monotonic()
            if now - self._last_query_at < self._fallback_min_interval_s:
                return None
            self._last_query_at = now

        try:
            current = self._read()
        except Exception:
            return None
        if current == self._last:
            return None
        self._last = current
        if current is None:
            return None
        observation = PerceptionObservation(
            domain="phone", key="call_activity", value=current,
            source=_SOURCE, confidence=Confidence.INFERRED,
            freshness_ttl_s=_DEFAULT_FRESHNESS_TTL_S,
        )
        return Event(type="perception.phone_call_activity", source="perception", payload=to_dict(observation))
