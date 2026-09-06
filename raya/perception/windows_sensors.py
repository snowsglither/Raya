"""ActiveWindowSensor — capteur léger réel (RAYA_V2_TECHNICAL_ARCHITECTURE.md
§1.3, §11.1 : "Fenêtre active/process au premier plan (modules/context/monitor.py,
poll ~3s, EXTRACT)").

Implémentation DÉLIBÉRÉMENT indépendante de `raya/devices/windows/mechanisms/
window_mgmt.py` — pas une réutilisation, pas une duplication du même rôle :
`window_mgmt.py` sert le Tool System (actions décidées par le modèle, passées
par Safety, invariant #3 "un Device n'exécute jamais rien sans recevoir un
Command de tools/"). Un capteur léger continu n'est PAS une action du modèle,
n'a pas besoin de Safety (lecture seule, sans risque), et `perception/`
n'a de toute façon pas le droit d'importer `raya.devices`/`raya.tools`
(graphe de dépendance ALLOWED, `scripts/arch_lint.py`) — cette indépendance
est le fil conducteur explicite de l'architecture (deux lignées V1 distinctes :
`modules/context/monitor.py` pour la perception légère,
`modules/pc_control/windows.py` pour l'action outillée), pas un oubli.

Dégrade honnêtement (retourne toujours `None`, jamais d'exception) si pywin32
n'est pas disponible ou si la plateforme n'est pas Windows — même politique
que `raya/runtime/bootstrap.py::_register_devices()`.
"""

from __future__ import annotations

from raya.contracts import Event, PerceptionObservation, to_dict

from .sensors import LightSensor

_SOURCE = "perception:foreground_window"
_DEFAULT_FRESHNESS_TTL_S = 20  # ~7x l'intervalle de poll recommandé (~3s) — tolère quelques cycles manqués


def _read_foreground_window() -> dict | None:
    """Implémentation réelle par défaut (Windows/pywin32). Retourne
    {"title": str, "process": str} ou None si indisponible/pas de fenêtre
    au premier plan — jamais une exception qui remonterait au thread de poll."""
    try:
        import psutil
        import win32gui
        import win32process
    except ImportError:
        return None
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return None
        title = win32gui.GetWindowText(hwnd) or ""
        process = ""
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            process = psutil.Process(pid).name()
        except Exception:
            pass
        if not title and not process:
            return None
        return {"title": title, "process": process}
    except Exception:
        return None


class ActiveWindowSensor(LightSensor):
    """`read_foreground_window` est injectable (tests unitaires déterministes
    sans dépendre d'un vrai focus de fenêtre OS) — défaut = implémentation
    Windows réelle ci-dessus. Ne publie un `Event` QUE lorsque la fenêtre
    active a réellement CHANGÉ depuis le dernier `sample()` — un poll qui ne
    change rien reste silencieux (consigne Phase 7 §10 : ne pas transformer
    chaque tick en bruit)."""

    def __init__(self, read_foreground_window=_read_foreground_window) -> None:
        self._read = read_foreground_window
        self._last: dict | None = None

    def sample(self) -> Event | None:
        try:
            current = self._read()
        except Exception:
            # Défense en profondeur : même une fonction `read_foreground_window`
            # injectée (test, futur mécanisme) ne doit jamais faire planter le
            # thread de poll (PerceptionRuntime.poll_once() protège aussi au
            # niveau runtime, mais le capteur tient la même promesse seul).
            return None
        if current == self._last:
            return None
        self._last = current
        if current is None:
            return None  # rien à publier de positif — l'absence n'est pas une observation
        observation = PerceptionObservation(
            domain="pc", key="active_window", value=current,
            source=_SOURCE, freshness_ttl_s=_DEFAULT_FRESHNESS_TTL_S,
        )
        return Event(type="perception.window_changed", source="perception", payload=to_dict(observation))
