"""PhoneCallActivitySensor (Chantier 13B/13D, Event-Driven Phone Awareness)
— capteur léger réel. `read_activity`/`event_hook` injectés pour un test
déterministe, sans dépendre d'un vrai Phone Link/Windows (même style que
test_windows_sensors.py)."""

from __future__ import annotations

import time

from raya.contracts import Confidence
from raya.perception.phone_sensors import PhoneCallActivitySensor


class _FakeEventHook:
    """Double de test pour PhoneEventHook — `dirty` contrôlé explicitement
    par le test, jamais de vrai SetWinEventHook/Windows impliqué."""

    def __init__(self, is_available: bool = True) -> None:
        self._available = is_available
        self.dirty = False
        self.pump_calls = 0

    def available(self) -> bool:
        return self._available

    def pump(self) -> None:
        self.pump_calls += 1

    def consume_dirty(self) -> bool:
        was = self.dirty
        self.dirty = False
        return was


# --- Mécanisme PRINCIPAL (Chantier 13D) : gated par PhoneEventHook ---

def test_no_read_at_all_when_hook_available_but_not_dirty():
    """Le point central du Chantier 13D : tant que l'OS n'a rien signalé,
    AUCUNE lecture UI Automation n'a lieu (coût quasi nul, jamais un poll)."""
    calls = {"n": 0}

    def read():
        calls["n"] += 1
        return {"in_call": True}

    hook = _FakeEventHook(is_available=True)
    sensor = PhoneCallActivitySensor(read_activity=read, event_hook=hook)
    assert sensor.sample() is None
    assert sensor.sample() is None
    assert calls["n"] == 0  # jamais appelé : le hook n'a jamais signalé d'activité
    assert hook.pump_calls == 2  # le drainage lui-même reste appelé à chaque tick (quasi gratuit)


def test_read_happens_only_when_hook_reports_dirty():
    activity = {"in_call": True, "latest_call_log_name": "Christopher", "latest_call_log_time": "10:34"}
    hook = _FakeEventHook(is_available=True)
    sensor = PhoneCallActivitySensor(read_activity=lambda: activity, event_hook=hook)

    assert sensor.sample() is None  # pas encore dirty

    hook.dirty = True
    event = sensor.sample()
    assert event is not None
    assert event.type == "perception.phone_call_activity"
    assert event.payload["value"] == activity
    assert event.payload["confidence"] == Confidence.INFERRED.value

    assert sensor.sample() is None  # consumé, redevenu propre


def test_dirty_flag_is_consumed_exactly_once():
    hook = _FakeEventHook(is_available=True)
    sensor = PhoneCallActivitySensor(read_activity=lambda: {"in_call": True}, event_hook=hook)
    hook.dirty = True
    sensor.sample()
    assert hook.dirty is False  # consume_dirty() l'a bien remis à False


def test_no_event_when_dirty_but_activity_content_unchanged():
    """Le hook peut signaler une activité (ex: une simple mise à jour de
    l'interface sans rapport avec un appel) sans que la VALEUR observée par
    la lecture UIA ait changé — dans ce cas, toujours pas de bruit."""
    activity = {"in_call": True, "latest_call_log_name": "Glodi", "latest_call_log_time": "09:59"}
    hook = _FakeEventHook(is_available=True)
    sensor = PhoneCallActivitySensor(read_activity=lambda: activity, event_hook=hook)
    hook.dirty = True
    first = sensor.sample()
    hook.dirty = True
    second = sensor.sample()
    assert first is not None
    assert second is None  # même valeur, jamais republié


def test_sensor_never_raises_when_read_function_fails():
    hook = _FakeEventHook(is_available=True)
    hook.dirty = True

    def failing():
        raise RuntimeError("uiautomation indisponible")

    sensor = PhoneCallActivitySensor(read_activity=failing, event_hook=hook)
    assert sensor.sample() is None


# --- Repli honnête (hook indisponible sur cette plateforme) : throttle temporel Chantier 13B ---

def test_fallback_throttles_expensive_query_below_min_interval_when_hook_unavailable():
    calls = {"n": 0}

    def read():
        calls["n"] += 1
        return None

    hook = _FakeEventHook(is_available=False)
    sensor = PhoneCallActivitySensor(read_activity=read, event_hook=hook, fallback_min_interval_s=5.0)
    sensor.sample()
    sensor.sample()
    sensor.sample()
    assert calls["n"] == 1  # les 2 derniers appels sont sous le throttle de repli


def test_fallback_queries_again_after_min_interval_elapses():
    calls = {"n": 0}

    def read():
        calls["n"] += 1
        return None

    hook = _FakeEventHook(is_available=False)
    sensor = PhoneCallActivitySensor(read_activity=read, event_hook=hook, fallback_min_interval_s=0.05)
    sensor.sample()
    time.sleep(0.08)
    sensor.sample()
    assert calls["n"] == 2


def test_default_read_phone_call_activity_degrades_honestly_without_dependencies():
    """Sans pywin32/uiautomation (ou hors Windows/Phone Link absent),
    l'implémentation réelle par défaut retourne None plutôt que de lever."""
    import raya.perception.phone_sensors as mod

    result = mod._read_phone_call_activity()
    assert result is None or isinstance(result, dict)


def test_default_construction_uses_a_real_phone_event_hook():
    """Sans injection explicite, le capteur utilise le VRAI PhoneEventHook
    (pas un double de test) — vérifié par le type, jamais en déclenchant une
    vraie lecture Windows dans ce test unitaire."""
    from raya.perception.phone_events import PhoneEventHook

    sensor = PhoneCallActivitySensor()
    assert isinstance(sensor._hook, PhoneEventHook)
