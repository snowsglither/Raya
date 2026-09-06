"""ActiveWindowSensor (RAYA_V2_TECHNICAL_ARCHITECTURE.md §11.1) — capteur
léger réel. `read_foreground_window` injecté pour un test déterministe, sans
dépendre d'un vrai focus de fenêtre OS ; un test LIVE séparé
(tests/perception/test_windows_sensors_live.py) exerce l'implémentation
Windows réelle."""

from __future__ import annotations

from raya.contracts import Confidence
from raya.perception.windows_sensors import ActiveWindowSensor


def test_no_event_on_first_sample_when_nothing_changed_from_none():
    sensor = ActiveWindowSensor(read_foreground_window=lambda: None)
    assert sensor.sample() is None


def test_event_published_on_first_real_observation():
    sensor = ActiveWindowSensor(read_foreground_window=lambda: {"title": "Notepad", "process": "notepad.exe"})
    event = sensor.sample()
    assert event is not None
    assert event.type == "perception.window_changed"
    assert event.source == "perception"
    assert event.payload["domain"] == "pc"
    assert event.payload["key"] == "active_window"
    assert event.payload["value"] == {"title": "Notepad", "process": "notepad.exe"}
    assert event.payload["source"] == "perception:foreground_window"
    assert event.payload["confidence"] == Confidence.KNOWN_FACT.value


def test_no_event_when_window_unchanged_between_samples():
    calls = {"n": 0}

    def read():
        calls["n"] += 1
        return {"title": "Notepad", "process": "notepad.exe"}

    sensor = ActiveWindowSensor(read_foreground_window=read)
    first = sensor.sample()
    second = sensor.sample()
    assert first is not None
    assert second is None
    assert calls["n"] == 2  # le capteur a bien re-échantillonné, juste rien de nouveau à publier


def test_event_published_when_window_changes():
    windows = iter([{"title": "Notepad", "process": "notepad.exe"}, {"title": "Calculatrice", "process": "calculatorapp.exe"}])
    sensor = ActiveWindowSensor(read_foreground_window=lambda: next(windows))
    first = sensor.sample()
    second = sensor.sample()
    assert first.payload["value"]["title"] == "Notepad"
    assert second.payload["value"]["title"] == "Calculatrice"


def test_sensor_never_raises_when_read_function_fails():
    def failing():
        raise RuntimeError("win32 indisponible")

    sensor = ActiveWindowSensor(read_foreground_window=failing)
    assert sensor.sample() is None  # jamais de crash du thread de poll


def test_default_read_foreground_window_degrades_honestly_without_win32():
    """Sans pywin32 installé (ou hors Windows), l'implémentation réelle par
    défaut retourne None plutôt que de lever — jamais de crash du poller."""
    import raya.perception.windows_sensors as mod

    result = mod._read_foreground_window()
    assert result is None or isinstance(result, dict)
