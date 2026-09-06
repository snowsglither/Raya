"""raya/devices/windows/mechanisms/shell.py + capability_discovery.py
(Chantier 16) — tests directs des mécanismes, en plus de la couverture
Tool/Safety de tests/tools/test_pc_catalog.py. Réel (pas mocké) : un vrai
subprocess Windows est exécuté."""

from __future__ import annotations

from raya.devices.windows.mechanisms import capability_discovery, shell


def test_capability_discovery_find_resolves_a_real_path_via_path():
    result = capability_discovery.find("ping")
    assert result["available"] is True
    assert result["path"].lower().endswith("ping.exe")


def test_capability_discovery_find_empty_name_never_available():
    assert capability_discovery.find("") == {"name": "", "available": False, "path": None, "capability": None}


def test_shell_run_captures_real_stdout_and_exit_code():
    result = shell.run("cmd /c echo bonjour")
    assert result["status"] == "ok"
    assert result["exit_code"] == 0
    assert "bonjour" in result["stdout"]


def test_shell_run_truncates_long_output():
    result = shell.run('cmd /c for /L %i in (1,1,2000) do @echo line%i')
    assert result["status"] == "ok"
    assert len(result["stdout"]) <= shell._MAX_OUTPUT_CHARS
    assert result["truncated"] is True


def test_shell_run_times_out_honestly_never_hangs():
    result = shell.run('powershell -Command "Start-Sleep -Seconds 5"', timeout_s=1)
    assert result["status"] == "timeout"


def test_shell_run_empty_command_fails_honestly_without_crashing():
    result = shell.run("")
    assert result["status"] == "error"
