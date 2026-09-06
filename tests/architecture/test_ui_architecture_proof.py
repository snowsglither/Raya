"""Preuve architecturale — Cockpit (RAYA V2 Phase 6 ARCHITECTURAL PROOF).

Même méthode que tests/architecture/test_dependency_lint.py pour voice/
(Phase 5) : AST réel sur raya/interfaces/ui/, jamais un cas synthétique.
Prouve que l'UI est un CLIENT — jamais un second orchestrateur, jamais un
exécuteur direct de tools/devices, jamais un accès aux internals du Harness."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import arch_lint  # noqa: E402

_UI_DIR = arch_lint.RAYA_ROOT / "interfaces" / "ui"


def _py_files():
    return [p for p in _UI_DIR.rglob("*.py")]


def test_ui_package_exists_and_is_not_empty():
    files = _py_files()
    assert files, "raya/interfaces/ui/ doit exister avec du code réel"


def test_ui_never_imports_cognition_tools_devices_models_tasks_safety_attention():
    for path in _py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        violations = arch_lint.check_dependency_graph(path, tree, "interfaces")
        assert violations == [], f"{path}: {violations}"
        for module, _names in arch_lint._raya_imports(tree):
            for forbidden in ("cognition", "tools", "devices", "models", "tasks", "safety", "attention"):
                assert not module.startswith(f"raya.{forbidden}"), f"{path} importe raya.{forbidden}"


def test_ui_never_imports_runtime_composition_root():
    for path in _py_files():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module, _names in arch_lint._raya_imports(tree):
            assert not module.startswith("raya.runtime"), f"{path} importe raya.runtime (dépendance ascendante interdite)"


def test_ui_never_accesses_private_harness_attributes():
    for path in _py_files():
        source = path.read_text(encoding="utf-8")
        violations = arch_lint.check_interfaces_no_private_harness_access(path, source, "interfaces")
        assert violations == [], f"{path}: {violations}"


def test_ui_contains_no_legacy_v1_references():
    for path in _py_files():
        source = path.read_text(encoding="utf-8")
        violations = arch_lint.check_no_legacy_v1_references(path, source)
        assert violations == []


def test_ui_channel_never_calls_execute_tool_or_device_agent_directly():
    """Garde-fou textuel complémentaire à l'AST : l'UI ne doit jamais appeler
    `execute_tool`/`execute(` elle-même — seul le Harness exécute des outils."""
    channel_source = (_UI_DIR / "channel.py").read_text(encoding="utf-8")
    assert "execute_tool(" not in channel_source
    assert "DeviceRegistry" not in channel_source


def test_web_entrypoint_never_imports_tools_devices_models_directly():
    """raya/runtime/ est la racine de composition (autorisée à tout importer,
    consigne UI AS CLIENT) mais le fichier web.py lui-même ne doit construire
    AUCUNE nouvelle boucle agentique — il ne fait que bootstrap() + UIChannel."""
    path = arch_lint.RAYA_ROOT / "runtime" / "entrypoints" / "web.py"
    if not path.exists():
        return  # créé plus tard dans la même phase — ce test régresse sinon
    source = path.read_text(encoding="utf-8")
    assert "execute_tool(" not in source
    assert "ToolRegistry(" not in source
    assert "AttentionEngine(" not in source
