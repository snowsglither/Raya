"""Priorité D (suite) — CLI : démarrage réel, transmission au Harness, STOP via Event."""

from __future__ import annotations

import builtins

from raya.interfaces.cli.repl import run
from raya.runtime.bootstrap import bootstrap


def test_cli_transmits_input_to_harness(monkeypatch, capsys):
    handles = bootstrap()
    try:
        inputs = iter(["ouvre Chrome", "exit"])
        monkeypatch.setattr(builtins, "input", lambda prompt="": next(inputs))
        run(handles.harness, handles.bus, session_id="cli-test")
        out = capsys.readouterr().out
        assert "RAYA V2 — ONLINE" in out
        # Régression stabilisation pré-Phase 7 §10 : le banner ne doit plus
        # jamais afficher un numéro de phase (il redevient faux dès la phase
        # suivante) — vérifié par absence plutôt que par une valeur figée.
        assert "Phase" not in out
        assert "/tasks" in out
    finally:
        handles.shutdown()


def test_cli_stop_command_publishes_event_never_calls_safety_directly(monkeypatch, capsys):
    handles = bootstrap()
    try:
        inputs = iter(["stop", "exit"])
        monkeypatch.setattr(builtins, "input", lambda prompt="": next(inputs))
        run(handles.harness, handles.bus, session_id="cli-test")
        handles.bus.wait_idle(timeout_s=1.0)
        assert handles.safety.should_stop() is True
        assert handles.safety.last_stop_source() == "interfaces.cli"  # atteint via Event, pas appel direct
    finally:
        handles.shutdown()


def test_cli_interface_module_never_imports_safety():
    import ast
    from pathlib import Path

    src = Path("raya/interfaces/cli/repl.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("raya.safety"), "interfaces/cli ne doit jamais importer raya.safety"
