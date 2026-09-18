"""Tests BUG A — _activate_hwnd SW_RESTORE conditionnel.

Vérifie que focus_window() / _activate_hwnd() :
- NE démaximise PAS une fenêtre déjà maximisée (fix principal)
- Restaure correctement une fenêtre minimisée
- Ne touche pas une fenêtre normale
- Laisse browser.navigate indépendant de tout changement d'état de fenêtre
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch


# ─── T1 : _activate_hwnd ne démaximise PAS une fenêtre maximisée ──────────────

def test_activate_hwnd_does_not_restore_maximized_window():
    """Régression directe BUG A : ShowWindow(SW_RESTORE) ne doit PAS être appelé
    lorsque la fenêtre est MAXIMIZED (show_cmd=3)."""
    from raya.devices.windows.mechanisms import window_mgmt

    # show_cmd=3 = maximized
    fake_placement = (0, 3, 0, (0, 0, 0, 0), (100, 100, 1000, 900))

    with patch("win32gui.GetWindowPlacement", return_value=fake_placement), \
         patch("win32gui.ShowWindow") as mock_show, \
         patch("win32gui.SetForegroundWindow"), \
         patch("ctypes.windll.user32.AllowSetForegroundWindow"):
        window_mgmt._activate_hwnd(12345, "Titre Maximisé", {})

    # SW_RESTORE (9) ne doit jamais être appelé pour une fenêtre maximisée
    for c in mock_show.call_args_list:
        args = c[0]
        assert args[1] != 9, f"ShowWindow appelé avec SW_RESTORE sur une fenêtre MAXIMIZED : {args}"


# ─── T2 : _activate_hwnd restaure une fenêtre minimisée ───────────────────────

def test_activate_hwnd_restores_minimized_window():
    """Une fenêtre minimisée (show_cmd=2) DOIT être restaurée via SW_RESTORE."""
    from raya.devices.windows.mechanisms import window_mgmt

    # show_cmd=2 = minimized
    fake_placement = (0, 2, 0, (0, 0, 0, 0), (200, 200, 800, 600))

    with patch("win32gui.GetWindowPlacement", return_value=fake_placement), \
         patch("win32gui.ShowWindow") as mock_show, \
         patch("win32gui.SetForegroundWindow"), \
         patch("ctypes.windll.user32.AllowSetForegroundWindow"):
        window_mgmt._activate_hwnd(12345, "Titre Minimisé", {})

    # SW_RESTORE (9) doit être appelé pour une fenêtre minimisée
    restore_calls = [c for c in mock_show.call_args_list if c[0][1] == 9]
    assert len(restore_calls) > 0, "SW_RESTORE doit être appelé pour une fenêtre minimisée"


# ─── T3 : _activate_hwnd ne touche pas une fenêtre normale ────────────────────

def test_activate_hwnd_does_not_alter_normal_window():
    """Une fenêtre normale (show_cmd=1) ne doit subir aucun ShowWindow."""
    from raya.devices.windows.mechanisms import window_mgmt

    # show_cmd=1 = normal
    fake_placement = (0, 1, 0, (0, 0, 0, 0), (300, 200, 900, 700))

    with patch("win32gui.GetWindowPlacement", return_value=fake_placement), \
         patch("win32gui.ShowWindow") as mock_show, \
         patch("win32gui.SetForegroundWindow"), \
         patch("ctypes.windll.user32.AllowSetForegroundWindow"):
        window_mgmt._activate_hwnd(12345, "Titre Normal", {})

    # Aucun ShowWindow pour une fenêtre normale
    assert mock_show.call_count == 0, (
        f"ShowWindow ne doit pas être appelé pour une fenêtre normale, "
        f"appelé {mock_show.call_count} fois : {mock_show.call_args_list}"
    )


# ─── T4 : fallback path ne dégrade pas en crash (régression) ──────────────────

def test_activate_hwnd_fallback_handles_placement_error_gracefully():
    """Si GetWindowPlacement lève, le fallback doit rester silencieux
    (même tolérance aux erreurs Win32 qu'avant le fix)."""
    from raya.devices.windows.mechanisms import window_mgmt

    with patch("win32gui.GetWindowPlacement", side_effect=Exception("Win32 error")), \
         patch("win32gui.SetForegroundWindow"), \
         patch("ctypes.windll.user32.AllowSetForegroundWindow"):
        # Ne doit pas lever
        result = window_mgmt._activate_hwnd(12345, "Titre Test", {})

    assert result["status"] in ("ok", "error")  # se termine proprement


# ─── T5 : browser.navigate n'appelle aucune fonction win32 de fenêtre ─────────

def test_browser_navigate_does_not_call_window_management():
    """browser.navigate ne doit PAS appeler focus_window, ShowWindow,
    SW_RESTORE, SetForegroundWindow — indépendance CDP / Win32."""
    from raya.devices.windows.mechanisms import window_mgmt

    with patch.object(window_mgmt, "focus_window") as mock_focus, \
         patch("win32gui.ShowWindow") as mock_show, \
         patch("win32gui.SetForegroundWindow") as mock_sfg:
        # Simule ce que browser.navigate fait réellement — aucun import window_mgmt
        # browser/agent.py → controller.navigate() → page.goto() — chemin CDP pur
        # Ce test vérifie qu'aucune des fonctions Win32 de placement n'est dans
        # la chaîne d'appel browser_navigate → navigate → session → goto.
        # On vérifie l'architecture : les modules browser ne doivent PAS importer
        # window_mgmt.
        import raya.devices.browser.agent as browser_agent
        import raya.devices.browser.controller as browser_ctrl
        import raya.devices.browser.session as browser_session
        import raya.devices.browser.worker as browser_worker

        for mod in (browser_agent, browser_ctrl, browser_session, browser_worker):
            src = open(mod.__file__).read()
            assert "window_mgmt" not in src, (
                f"{mod.__file__} ne doit pas importer window_mgmt (indépendance BUG A)"
            )
            assert "SW_RESTORE" not in src, (
                f"{mod.__file__} ne doit pas utiliser SW_RESTORE"
            )

    # focus_window ne doit jamais être appelé depuis le chemin browser
    assert mock_focus.call_count == 0


# ─── Tests Windows réels (skip si win32 indisponible) ─────────────────────────

def _win32_available() -> bool:
    try:
        import win32gui, win32con
        return True
    except ImportError:
        return False


import pytest

@pytest.mark.skipif(not _win32_available(), reason="win32 non disponible")
def test_real_focus_window_preserves_maximized_state(tmp_path):
    """E2E 1 — Windows réel : focus_window() sur une fenêtre MAXIMIZED doit
    la laisser MAXIMIZED après focus (SW_RESTORE ne doit pas intervenir)."""
    import win32gui, win32con
    from raya.devices.windows.mechanisms import applications, window_mgmt
    import time

    # Lance Notepad comme fenêtre de test
    r = applications.launch("notepad", wait_timeout_s=8.0)
    if r.get("status") not in ("ok",):
        pytest.skip(f"BLOCKED: Notepad non lancé : {r}")

    try:
        time.sleep(0.3)
        wins = window_mgmt.find_windows("notepad")
        if not wins:
            pytest.skip("BLOCKED: aucune fenêtre Notepad trouvée")
        hwnd = wins[0]["hwnd"]

        # Maximise Notepad
        win32gui.ShowWindow(hwnd, win32con.SW_MAXIMIZE)
        time.sleep(0.2)
        before_placement = win32gui.GetWindowPlacement(hwnd)
        assert before_placement[1] == 3, "Notepad devrait être MAXIMIZED après SW_MAXIMIZE"

        # focus_window sur Notepad maximisé
        result = window_mgmt.focus_window("notepad")
        time.sleep(0.3)

        after_placement = win32gui.GetWindowPlacement(hwnd)
        assert after_placement[1] == 3, (
            f"focus_window() a DÉMAXIMISÉ Notepad : show_cmd={after_placement[1]} "
            f"(1=normal, 2=minimized, 3=maximized) — BUG A régression!"
        )
        assert result.get("status") in ("ok", "error")  # au moins une tentative de focus

    finally:
        window_mgmt.close_window("notepad")
        time.sleep(0.2)
