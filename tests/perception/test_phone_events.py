"""PhoneEventHook (Chantier 13D) — hook Win32 natif (SetWinEventHook), pas
de nouveau thread (consigne §7/§8) : `pump()` est non-bloquant (PeekMessage),
jamais un GetMessage() qui bloquerait. Testé ici sur cette VRAIE machine
Windows (le hook s'enregistre réellement) — la validation qu'un appel réel
le déclenche est un test réel séparé (voir rapport Chantier 13D), pas ici."""

from __future__ import annotations

from raya.perception.phone_events import PhoneEventHook


def test_available_registers_the_real_hook_on_this_windows_machine():
    """Preuve que SetWinEventHook s'enregistre réellement (pas juste que le
    code ne lève pas) — sur une machine Windows avec pywin32 installé."""
    hook = PhoneEventHook()
    assert hook.available() is True
    hook.close()


def test_available_is_idempotent_and_cached():
    hook = PhoneEventHook()
    first = hook.available()
    second = hook.available()
    assert first == second == True  # noqa: E712 — lisibilité du test
    hook.close()


def test_consume_dirty_resets_the_flag():
    hook = PhoneEventHook()
    hook.available()
    hook._dirty = True  # simule un callback réel sans dépendre d'un vrai event OS
    assert hook.consume_dirty() is True
    assert hook.consume_dirty() is False
    hook.close()


def test_pump_never_raises_when_nothing_is_pending():
    hook = PhoneEventHook()
    hook.available()
    hook.pump()
    hook.pump()  # appelable plusieurs fois de suite sans effet indésirable
    hook.close()


def test_close_is_safe_to_call_multiple_times():
    hook = PhoneEventHook()
    hook.available()
    hook.close()
    hook.close()  # ne doit jamais lever


def test_on_event_never_raises_for_a_null_or_unrelated_window():
    """Le callback lui-même ne doit jamais planter, même sur hwnd=0 ou une
    fenêtre appartenant à un tout autre process (cas normal : le hook capte
    TOUT le système, filtré à l'intérieur du callback)."""
    hook = PhoneEventHook()
    hook._on_event(0, 0x8000, 0, 0, 0, 0, 0)  # hwnd nul
    hook._on_event(0, 0x8000, 999999999, 0, 0, 0, 0)  # hwnd invalide/inexistant
    assert hook.consume_dirty() is False  # ni l'un ni l'autre n'a marqué dirty
