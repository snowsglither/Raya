"""PhoneEventHook (Chantier 13D, généralisé Chantier 13G) — écoute PASSIVE,
event-driven, des événements d'accessibilité Windows natifs
(`SetWinEventHook`, API Win32, PAS UI Automation ni WinRT) émis par un
process/classe de fenêtre cible configurable — par défaut
`PhoneExperienceHost.exe` (Chantier 13D), mais réutilisé TEL QUEL (§2 du
Chantier 13G : "réutiliser le pattern existant") pour observer
`ShellExperienceHost.exe` (la vraie surface de notification, Chantier 13F)
en passant `target_process_name`/`target_class_name` au constructeur —
aucune deuxième classe de hook, aucun deuxième mécanisme.

============================================================================
INVESTIGATION RÉELLE (Chantier 13D) — deux mécanismes essayés AVANT celui-ci
============================================================================

1. `Windows.UI.Notifications.Management.UserNotificationListener.
   NotificationChanged` (WinRT, package `winrt-Windows.UI.Notifications.
   Management`) : `get_access_status()` retourne bien ALLOWED sur cette
   machine, et LIRE les notifications existantes (`get_notifications_async`/
   `get_notification`, y compris une notification réelle de Phone Link) a
   RÉUSSI. Mais `add_notification_changed()` (l'ÉVÉNEMENT lui-même) échoue
   systématiquement avec `OSError: [WinError -2147023728] Élément
   introuvable` (HRESULT 0x80070490), y compris après initialisation COM
   explicite (STA). Cause documentée : cet événement spécifique nécessite
   une identité d'application EMPAQUETÉE (MSIX) avec la capability
   restreinte `userNotificationListener` — inaccessible à un process Win32
   non empaqueté comme RAYA. BLOQUÉ, pas une erreur de code.

2. `SetWinEventHook` (ce module) : testé en conditions réelles (appels
   entrants déclenchés volontairement, à deux reprises, avec consentement)
   — CONFIRMÉ fonctionnel : filtré sur le PID de `PhoneExperienceHost.exe`,
   la plage `EVENT_MIN..EVENT_MAX` a produit 20 événements réels
   (`EVENT_OBJECT_LOCATIONCHANGE` sur "Non Client Input Sink Window",
   `EVENT_OBJECT_CREATE` sur "OLEChannelWnd") exactement pendant qu'un
   appel entrant réel sonnait et qu'une notification était visible côté PC.
   Aucune capability Windows restreinte requise — c'est l'API native sous-
   jacente aux event handlers UI Automation eux-mêmes (que la bibliothèque
   `uiautomation` déjà utilisée n'expose pas, confirmé Chantier 13B).

LIMITATION HONNÊTE : les événements captés (LOCATIONCHANGE/CREATE sur des
fenêtres internes WinUI/OLE) ne sont PAS sémantiquement spécifiques à "appel
entrant" — ils signalent seulement "quelque chose a changé dans le process
Phone Link". La confirmation de CE QUI a changé reste la responsabilité de
`phone_sensors.py::_read_phone_call_activity()` (UI Automation ciblée),
appelée UNIQUEMENT quand ce hook signale une activité — jamais sur un
minuteur fixe.

============================================================================
ARCHITECTURE — AUCUN NOUVEAU THREAD (consigne Chantier 13D §7/§8)
============================================================================

`SetWinEventHook(..., WINEVENT_OUTOFCONTEXT)` délivre ses callbacks via la
file de messages Win32 du thread QUI A ENREGISTRÉ le hook — recevoir ces
callbacks exige donc que CE thread pompe ses messages. Plutôt que de créer
un thread dédié (interdit explicitement), `pump()` (non-bloquant,
`PeekMessage`, jamais `GetMessage` qui bloquerait) est appelé depuis
`PhoneCallActivitySensor.sample()`, donc depuis le thread EXISTANT de
`PerceptionRuntime` — à chaque tick, gratuit (quelques microsecondes) tant
qu'aucun message n'est en attente.

Conséquence honnête : la CAPTURE de l'événement OS est instantanée et
gratuite (zéro coût tant que rien ne se passe — l'OS ne réveille personne,
aucun code RAYA ne s'exécute). Sa DÉTECTION par RAYA reste bornée par
l'intervalle du tick `PerceptionRuntime` existant (quelques secondes,
inchangé depuis Chantier 13B) — ce n'est pas un push strictement instantané
de bout en bout, mais ce n'est JAMAIS `while True: check_phone()` : le hook
lui-même ne coûte rien, et `PhoneCallActivitySensor` ne fait plus AUCUNE
lecture UI Automation coûteuse tant que ce hook n'a rien signalé (contre un
minuteur fixe auparavant, Chantier 13B)."""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

_EVENT_MIN = 0x00000001
_EVENT_MAX = 0x7FFFFFFF
_WINEVENT_OUTOFCONTEXT = 0x0000
_DEFAULT_TARGET_PROCESS_NAME = "phoneexperiencehost.exe"

_WinEventProcType = ctypes.WINFUNCTYPE(
    None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND, wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD,
)


class PhoneEventHook:
    """Enregistrement PARESSEUX (au premier `pump()`), jamais au chargement
    du module — dégrade honnêtement (`available()` reste False) si
    `SetWinEventHook` échoue (plateforme non-Windows, pywin32/ctypes
    indisponible, etc.), jamais une exception qui remonterait au thread de
    poll de PerceptionRuntime.

    `target_process_name` (Chantier 13G, additif) : process dont les
    fenêtres marquent le drapeau `dirty` — défaut inchangé
    (`PhoneExperienceHost.exe`, Chantier 13D). `target_class_name`
    (Chantier 13G, additif, optionnel) : si renseigné, ne considère que les
    fenêtres de cette classe (ex: "windows.ui.core.corewindow" pour ignorer
    la fenêtre IME parasite observée à côté de la vraie surface de
    notification, Chantier 13F)."""

    def __init__(self, target_process_name: str = _DEFAULT_TARGET_PROCESS_NAME,
                 target_class_name: str | None = None) -> None:
        self._hook = None
        self._callback_ref = None  # garde une référence vivante (ctypes ne le fait pas lui-même)
        self._lock = threading.Lock()
        self._dirty = False
        self._last_hwnd: int | None = None
        self._target_process_name = target_process_name.lower()
        self._target_class_name = target_class_name.lower() if target_class_name else None
        self._init_attempted = False
        self._init_failed_reason: str | None = None

    def _matches_target(self, hwnd: int) -> bool:
        try:
            import psutil
            import win32gui
            import win32process

            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            if psutil.Process(pid).name().lower() != self._target_process_name:
                return False
            if self._target_class_name is not None:
                if win32gui.GetClassName(hwnd).lower() != self._target_class_name:
                    return False
            return True
        except Exception:
            return False

    def _on_event(self, hWinEventHook, event, hwnd, idObject, idChild, idEventThread, dwmsEventTime) -> None:
        try:
            if not hwnd:
                return
            if self._matches_target(hwnd):
                with self._lock:
                    self._dirty = True
                    self._last_hwnd = hwnd
        except Exception:
            pass  # un callback ne doit jamais lever — coûterait le hook entier

    def _ensure_registered(self) -> None:
        if self._init_attempted:
            return
        self._init_attempted = True
        try:
            import win32con  # noqa: F401 — force un échec clair tôt si pywin32 absent

            user32 = ctypes.windll.user32
            ole32 = ctypes.windll.ole32
            ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED — requis pour SetWinEventHook
            self._callback_ref = _WinEventProcType(self._on_event)
            hook = user32.SetWinEventHook(
                _EVENT_MIN, _EVENT_MAX, 0, self._callback_ref, 0, 0, _WINEVENT_OUTOFCONTEXT,
            )
            if not hook:
                self._init_failed_reason = "SetWinEventHook a retourné NULL"
                return
            self._hook = hook
        except Exception as exc:
            self._init_failed_reason = str(exc)

    def available(self) -> bool:
        self._ensure_registered()
        return self._hook is not None

    def pump(self) -> None:
        """Draine la file de messages Win32 du thread appelant SANS bloquer
        (`PeekMessage`, jamais `GetMessage`) — permet aux callbacks déjà
        déposés par Windows d'être réellement invoqués. Gratuit si aucun
        message n'est en attente."""
        self._ensure_registered()
        if self._hook is None:
            return
        try:
            user32 = ctypes.windll.user32
            msg = wintypes.MSG()
            while user32.PeekMessageW(ctypes.byref(msg), 0, 0, 0, 1):  # PM_REMOVE
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception:
            pass

    def consume_dirty(self) -> bool:
        """Retourne True si une activité a été captée depuis le dernier
        appel, et remet le drapeau à False (consommation atomique)."""
        with self._lock:
            was_dirty = self._dirty
            self._dirty = False
            return was_dirty

    def last_hwnd(self) -> int | None:
        """HWND de la DERNIÈRE fenêtre correspondante ayant déclenché le
        drapeau `dirty` (Chantier 13G, additif) — évite à l'appelant de
        devoir ré-énumérer toutes les fenêtres pour retrouver celle
        concernée. `None` si aucun événement n'a encore été capté."""
        with self._lock:
            return self._last_hwnd

    def close(self) -> None:
        if self._hook is not None:
            try:
                ctypes.windll.user32.UnhookWinEvent(self._hook)
            except Exception:
                pass
            self._hook = None
