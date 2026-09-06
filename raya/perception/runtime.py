"""PerceptionRuntime — boucle de poll légère (RAYA_V2_TECHNICAL_ARCHITECTURE.md
§1.3 : "API publique : start_light_sensors()"). Symétrique à
`raya/interfaces/voice/runtime.py::VoiceRuntime` : `poll_once()` séparé de
`run_forever()`/`start()`/`stop()` pour un contrôle déterministe en test (pas
de thread, pas de timing réel à synchroniser).

Ne décide jamais rien — publie tel quel ce que chaque `LightSensor.sample()`
retourne. Aucun appel modèle, aucune dépendance à `attention`/`harness`
(RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.3 "Dépendances interdites")."""

from __future__ import annotations

import threading

from raya.event_bus import EventBus

from .sensors import LightSensor

_DEFAULT_INTERVAL_S = 3.0


class PerceptionRuntime:
    def __init__(self, sensors: list[LightSensor], bus: EventBus, interval_s: float = _DEFAULT_INTERVAL_S) -> None:
        self._sensors = list(sensors)
        self._bus = bus
        self._interval_s = interval_s
        self._running = False
        self._thread: threading.Thread | None = None
        # threading.Event plutôt qu'un simple sleep(interval_s) : stop() doit
        # réveiller la boucle IMMÉDIATEMENT (jamais attendre jusqu'à 3s pour
        # arrêter proprement — chaque test qui démarre un vrai runtime ne doit
        # pas ralentir toute la suite).
        self._stop_signal = threading.Event()

    def poll_once(self) -> int:
        """Échantillonne chaque capteur une fois, publie les events non-nuls.
        Retourne le nombre d'events publiés (utile en test)."""
        published = 0
        for sensor in self._sensors:
            try:
                event = sensor.sample()
            except Exception:
                continue  # un capteur défaillant ne doit jamais arrêter les autres
            if event is not None:
                self._bus.publish(event)
                published += 1
        return published

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._stop_signal.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="raya-perception")
        self._thread.start()

    def _loop(self) -> None:
        while self._running:
            self.poll_once()
            if self._stop_signal.wait(timeout=self._interval_s):
                break

    def stop(self) -> None:
        self._running = False
        self._stop_signal.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
