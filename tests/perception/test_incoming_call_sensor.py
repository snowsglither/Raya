"""IncomingCallNotificationSensor (Chantier 13G) — `event_hook`/
`read_with_retry`/`classify` injectés pour un test déterministe, sans
dépendre d'un vrai Windows/Phone Link (même style que test_phone_sensors.py)."""

from __future__ import annotations

from raya.contracts import Confidence
from raya.perception.incoming_call_sensor import IncomingCallNotificationSensor


class _FakeEventHook:
    def __init__(self, is_available: bool = True, hwnd: int | None = 42) -> None:
        self._available = is_available
        self.dirty = False
        self._hwnd = hwnd
        self.pump_calls = 0

    def available(self) -> bool:
        return self._available

    def pump(self) -> None:
        self.pump_calls += 1

    def consume_dirty(self) -> bool:
        was = self.dirty
        self.dirty = False
        return was

    def last_hwnd(self):
        return self._hwnd


_INCOMING = {
    "toast_view_type": "PriorityToastView", "sender_name": "Appels", "title": "Papa",
    "message_text": "Appel entrant", "attribution": "via Mobile connecté",
    "has_action_buttons": True, "action_button_texts": ["Refuser"],
}
_MISSED = {
    "toast_view_type": "NormalToastView", "sender_name": "Téléphone", "title": "Téléphone",
    "message_text": "Papa\nAppel manqué", "attribution": "via Mobile connecté",
    "has_action_buttons": False, "action_button_texts": [],
}


def _classify(content):
    if content is None or not content.get("toast_view_type"):
        return "not_identified"
    if content["toast_view_type"] == "PriorityToastView" and content["has_action_buttons"]:
        return "incoming_call"
    return "other"


# --- gating par le hook (aucune lecture UIA sans event réel) ---

def test_no_read_when_not_dirty():
    calls = {"n": 0}

    def reader(hwnd, **kw):
        calls["n"] += 1
        return _INCOMING

    hook = _FakeEventHook()
    sensor = IncomingCallNotificationSensor(event_hook=hook, read_with_retry=reader, classify=_classify)
    assert sensor.sample() is None
    assert calls["n"] == 0


def test_unavailable_hook_never_falls_back_to_polling():
    """Contrairement à PhoneCallActivitySensor (13D), ce capteur n'a
    délibérément AUCUN repli temporel — sans le hook, il reste indisponible."""
    calls = {"n": 0}

    def reader(hwnd, **kw):
        calls["n"] += 1
        return _INCOMING

    hook = _FakeEventHook(is_available=False)
    sensor = IncomingCallNotificationSensor(event_hook=hook, read_with_retry=reader, classify=_classify)
    for _ in range(5):
        assert sensor.sample() is None
    assert calls["n"] == 0


# --- publication : incoming vs other vs not_identified ---

def test_incoming_call_publishes_event_with_correct_shape():
    hook = _FakeEventHook()
    hook.dirty = True
    sensor = IncomingCallNotificationSensor(event_hook=hook, read_with_retry=lambda hwnd, **kw: _INCOMING, classify=_classify)
    event = sensor.sample()
    assert event is not None
    assert event.type == "perception.incoming_call_notification"
    assert event.payload["domain"] == "phone"
    assert event.payload["key"] == "incoming_call"
    assert event.payload["value"]["call_state"] == "incoming"
    assert event.payload["value"]["caller"] == "Papa"
    assert event.payload["value"]["source_text"] == "via Mobile connecté"
    assert event.payload["confidence"] == Confidence.INFERRED.value


def test_other_notification_still_publishes_but_with_other_state():
    """Un 'manqué' doit être publié (pour qu'Attention puisse explicitement
    ne PAS interrompre), jamais silencieusement avalé."""
    hook = _FakeEventHook()
    hook.dirty = True
    sensor = IncomingCallNotificationSensor(event_hook=hook, read_with_retry=lambda hwnd, **kw: _MISSED, classify=_classify)
    event = sensor.sample()
    assert event is not None
    assert event.payload["value"]["call_state"] == "other"


def test_not_identified_never_publishes_anything():
    hook = _FakeEventHook()
    hook.dirty = True
    empty = {"toast_view_type": None, "sender_name": None, "title": None, "message_text": None,
              "attribution": None, "has_action_buttons": False, "action_button_texts": []}
    sensor = IncomingCallNotificationSensor(event_hook=hook, read_with_retry=lambda hwnd, **kw: empty, classify=_classify)
    assert sensor.sample() is None


def test_no_hwnd_never_reads_anything():
    calls = {"n": 0}

    def reader(hwnd, **kw):
        calls["n"] += 1
        return _INCOMING

    hook = _FakeEventHook(hwnd=None)
    hook.dirty = True
    sensor = IncomingCallNotificationSensor(event_hook=hook, read_with_retry=reader, classify=_classify)
    assert sensor.sample() is None
    assert calls["n"] == 0


# --- anti-bruit : même contenu -> pas de republication ---

def test_identical_content_across_ticks_is_not_republished():
    hook = _FakeEventHook()
    sensor = IncomingCallNotificationSensor(event_hook=hook, read_with_retry=lambda hwnd, **kw: _INCOMING, classify=_classify)
    hook.dirty = True
    first = sensor.sample()
    hook.dirty = True
    second = sensor.sample()
    assert first is not None
    assert second is None


def test_reader_exception_never_crashes_the_sensor():
    def failing(hwnd, **kw):
        raise RuntimeError("uiautomation indisponible")

    hook = _FakeEventHook()
    hook.dirty = True
    sensor = IncomingCallNotificationSensor(event_hook=hook, read_with_retry=failing, classify=_classify)
    assert sensor.sample() is None


def test_default_construction_uses_a_shell_experience_host_hook():
    from raya.perception.phone_events import PhoneEventHook

    sensor = IncomingCallNotificationSensor()
    assert isinstance(sensor._hook, PhoneEventHook)
    assert sensor._hook._target_process_name == "shellexperiencehost.exe"
    assert sensor._hook._target_class_name == "windows.ui.core.corewindow"
