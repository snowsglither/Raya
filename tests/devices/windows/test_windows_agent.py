"""WindowsDeviceAgent — tests RÉELS (consigne Phase 4 §27 : "un mock ne
prouve pas le computer use réel"). Toute cette suite pilote le VRAI Windows
de cette machine (vraies fenêtres, vrai clavier/souris, vraie UIA) — c'est
le point central de la preuve REAL EXECUTION (consigne §38)."""

from __future__ import annotations

import ctypes
import time

import pytest

from raya.contracts import Command, CommandStatus, DeviceStatus
from raya.devices.windows import WindowsDeviceAgent
from raya.devices.windows.mechanisms import window_mgmt


def _cmd(capability_name: str, arguments: dict) -> Command:
    return Command(device_id="windows_agent", capability_name=capability_name, arguments=arguments, correlation_id="c1")


@pytest.fixture
def agent(tmp_path):
    a = WindowsDeviceAgent(tmp_path / "screens")
    yield a
    # Nettoyage : ne laisse jamais un Bloc-notes de test ouvert, et ATTEND
    # réellement la fermeture (WM_CLOSE est asynchrone) — sinon un test
    # suivant qui relance "notepad" retrouve parfois l'ancienne fenêtre
    # encore en cours de fermeture (course réelle observée entre tests).
    window_mgmt.close_window("notepad")
    deadline = time.time() + 3.0
    while time.time() < deadline:
        found, _ = window_mgmt.is_open("notepad")
        if not found:
            break
        time.sleep(0.1)


def test_health_reports_online_when_uia_responds(agent):
    h = agent.health()
    assert h.device_id == "windows_agent"
    assert h.status == DeviceStatus.ONLINE


def test_list_capabilities_matches_declared_set(agent):
    names = {c.name for c in agent.list_capabilities()}
    for expected in ("application.launch", "application.close", "window.list", "keyboard.type",
                      "mouse.click", "screen.capture", "ui.inspect", "ui.click", "ui.type", "process.list"):
        assert expected in names


def test_window_list_returns_real_open_windows(agent):
    r = agent.execute(_cmd("window.list", {}))
    assert r.status == CommandStatus.SUCCESS
    assert r.output["count"] >= 1  # au minimum le processus de test/terminal


def test_process_list_returns_real_running_processes(agent):
    r = agent.execute(_cmd("process.list", {}))
    assert r.status == CommandStatus.SUCCESS
    assert "python.exe" in r.output["processes"] or any("python" in p.lower() for p in r.output["processes"])


# --- application.launch / close / focus (REAL notepad) ---

def test_application_launch_opens_a_real_window(agent):
    r = agent.execute(_cmd("application.launch", {"target": "notepad"}))
    assert r.status == CommandStatus.SUCCESS
    assert r.output["already_open"] is False
    assert r.evidence["process"].lower() == "notepad.exe"
    # Preuve indépendante du framework de test : la VRAIE fenêtre existe.
    found, matches = window_mgmt.is_open("notepad")
    assert found is True


def test_application_launch_already_open_focuses_instead_of_relaunching(agent):
    agent.execute(_cmd("application.launch", {"target": "notepad"}))
    r = agent.execute(_cmd("application.launch", {"target": "notepad"}))
    assert r.status == CommandStatus.SUCCESS
    assert r.output["already_open"] is True


def test_application_close_removes_the_real_window(agent):
    agent.execute(_cmd("application.launch", {"target": "notepad"}))
    r = agent.execute(_cmd("application.close", {"target": "notepad"}))
    assert r.status == CommandStatus.SUCCESS
    time.sleep(0.5)
    found, _ = window_mgmt.is_open("notepad")
    assert found is False


def test_application_close_unknown_target_fails_honestly(agent):
    r = agent.execute(_cmd("application.close", {"target": "aucune_appli_qui_existe_xyz"}))
    assert r.status == CommandStatus.FAILURE
    assert r.error.code == "WINDOW_NOT_FOUND"


def test_application_focus_brings_real_window_to_foreground(agent):
    agent.execute(_cmd("application.launch", {"target": "notepad"}))
    r = agent.execute(_cmd("application.focus", {"target": "notepad"}))
    assert r.status == CommandStatus.SUCCESS


def test_application_list_reflects_real_open_windows(agent):
    agent.execute(_cmd("application.launch", {"target": "notepad"}))
    r = agent.execute(_cmd("application.list", {}))
    assert r.status == CommandStatus.SUCCESS
    procs = {a["process"].lower() for a in r.output["applications"]}
    assert "notepad.exe" in procs


# --- ui.inspect / ui.click / ui.type (REAL UIA on real notepad) ---

def test_ui_inspect_lists_real_elements_of_notepad(agent):
    agent.execute(_cmd("application.launch", {"target": "notepad"}))
    r = agent.execute(_cmd("ui.inspect", {"window": "notepad"}))
    assert r.status == CommandStatus.SUCCESS
    assert r.evidence["count"] > 0
    assert len(r.output["elements"]) == r.evidence["count"]
    assert any(e["control_type"] == "document" for e in r.output["elements"])


def test_ui_type_writes_real_text_into_notepad_via_uia(agent):
    agent.execute(_cmd("application.launch", {"target": "notepad"}))
    r = agent.execute(_cmd("ui.type", {"window": "notepad", "selector": {"control_type": "document"}, "text": "Bonjour RAYA Phase 4"}))
    assert r.status == CommandStatus.SUCCESS
    assert r.evidence["method"] == "uia_set_value"


def test_ui_click_activates_real_element(agent):
    agent.execute(_cmd("application.launch", {"target": "notepad"}))
    r = agent.execute(_cmd("ui.click", {"window": "notepad", "selector": {"control_type": "document"}}))
    assert r.status == CommandStatus.SUCCESS


def test_ui_inspect_unknown_window_not_found(agent):
    r = agent.execute(_cmd("ui.inspect", {"window": "fenetre_qui_nexiste_absolument_pas_xyz"}))
    assert r.status == CommandStatus.FAILURE
    assert r.error.code == "WINDOW_NOT_FOUND"


# --- keyboard / mouse (REAL win32 input) ---

def test_keyboard_type_sends_real_clipboard_paste(agent):
    r = agent.execute(_cmd("keyboard.type", {"text": "test clavier réel"}))
    assert r.status == CommandStatus.SUCCESS


def test_keyboard_press_invalid_combo_fails_honestly(agent):
    r = agent.execute(_cmd("keyboard.press", {"combo": ""}))
    assert r.status == CommandStatus.FAILURE


def test_mouse_move_actually_moves_the_real_cursor(agent):
    r = agent.execute(_cmd("mouse.move", {"x": 200, "y": 200}))
    assert r.status == CommandStatus.SUCCESS
    import ctypes.wintypes as wintypes

    pt = wintypes.POINT()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
    assert (pt.x, pt.y) == (200, 200)


def test_mouse_click_reports_success(agent):
    r = agent.execute(_cmd("mouse.click", {"x": 300, "y": 300}))
    assert r.status == CommandStatus.SUCCESS


# --- screen.capture (REAL screenshot file) ---

def test_screen_capture_creates_a_real_png_file(agent, tmp_path):
    r = agent.execute(_cmd("screen.capture", {"filename": "shot.png"}))
    assert r.status == CommandStatus.SUCCESS
    path = tmp_path / "screens" / "shot.png"
    assert path.exists()
    assert path.stat().st_size > 1000  # un vrai screenshot, pas un fichier vide
    assert r.evidence["width"] > 0 and r.evidence["height"] > 0


# --- Erreurs / robustesse ---

def test_unknown_capability_fails_honestly(agent):
    r = agent.execute(_cmd("teleport.instantly", {}))
    assert r.status == CommandStatus.FAILURE
    assert r.error.code == "UNKNOWN_CAPABILITY"


def test_launch_timeout_when_no_real_window_ever_appears(agent, monkeypatch):
    """Le lancement lui-même est simulé réussi (monkeypatch), mais AUCUNE
    fenêtre réelle n'apparaît jamais (target garanti inexistant) — la
    recherche de fenêtre reste réelle, seul le déclencheur est stubé pour un
    test rapide et déterministe."""
    from raya.devices.windows import agent as agent_module

    monkeypatch.setattr(agent_module.applications, "launch",
                         lambda target, wait_timeout_s=0.0: {"status": "ok"})
    r = agent.execute(_cmd("application.launch", {"target": "cible_totalement_inexistante_xyz_999", "wait_timeout_s": 0.4}))
    assert r.status == CommandStatus.TIMEOUT
    assert r.error.code == "WINDOW_NOT_APPEARED"


def test_device_exception_is_caught_and_reported_never_crashes(agent, monkeypatch):
    from raya.devices.windows import agent as agent_module

    def _boom(*a, **kw):
        raise RuntimeError("panne simulée du mécanisme")

    monkeypatch.setattr(agent_module.window_mgmt, "list_windows", _boom)
    r = agent.execute(_cmd("window.list", {}))
    assert r.status == CommandStatus.FAILURE
    assert r.error.code == "DEVICE_EXCEPTION"


# --- STOP coopératif ---

def test_should_stop_active_before_execution_returns_cancelled(agent):
    r = agent.execute(_cmd("window.list", {}), should_stop=lambda: True)
    assert r.status == CommandStatus.CANCELLED
    assert r.error.code == "STOP_ACTIVE"


def test_stop_during_launch_wait_interrupts_before_timeout(agent, monkeypatch):
    from raya.devices.windows import agent as agent_module

    monkeypatch.setattr(agent_module.applications, "launch",
                         lambda target, wait_timeout_s=0.0: {"status": "ok"})
    calls = {"n": 0}

    def _stop_after_first_check():
        calls["n"] += 1
        return calls["n"] > 1

    r = agent.execute(
        _cmd("application.launch", {"target": "cible_inexistante_stop_test_xyz", "wait_timeout_s": 5.0}),
        should_stop=_stop_after_first_check,
    )
    assert r.status == CommandStatus.CANCELLED
    assert r.error.code == "STOP_ACTIVE"
