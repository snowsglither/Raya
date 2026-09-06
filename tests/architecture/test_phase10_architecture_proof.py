"""Preuve architecturale — Long-Horizon Autonomy (RAYA V2 Phase 10).

Prouve que le pipeline existant est RENFORCÉ, jamais dupliqué : pas de
LongHorizonAgent/AutonomousBrain/PlannerAgent/TaskOrchestrator, le Harness
reste le SEUL point d'orchestration, Cognition ne fait que produire un plan
(jamais l'exécuter), le planner n'importe jamais tools/devices/safety."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import arch_lint  # noqa: E402

_FORBIDDEN_SECOND_BRAIN_CLASSES = (
    "LongHorizonAgent", "AutonomousBrain", "PlannerAgent", "TaskOrchestrator",
    "LongHorizonOrchestrator", "LongHorizonScheduler",
)


def test_cognition_planning_never_imports_tools_devices_safety_or_tasks():
    """Consigne §3 : "Le planner ne doit PAS exécuter les Tools" — vérifié
    structurellement : cognition/planning.py ne peut même pas les importer."""
    path = arch_lint.RAYA_ROOT / "cognition" / "planning.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations = arch_lint.check_dependency_graph(path, tree, "cognition")
    assert violations == [], f"{path}: {violations}"
    for module, _names in arch_lint._raya_imports(tree):
        for forbidden in ("tools", "devices", "safety", "tasks", "harness", "interfaces"):
            assert not module.startswith(f"raya.{forbidden}"), f"{path} importe raya.{forbidden}"


def test_planning_module_never_executes_a_tool_call():
    """Heuristique textuelle complémentaire : aucune trace de `execute_tool`/
    `ToolCall(` dans le module de planification lui-même."""
    source = (arch_lint.RAYA_ROOT / "cognition" / "planning.py").read_text(encoding="utf-8")
    assert "execute_tool" not in source
    assert "ToolCall(" not in source


def test_no_second_orchestrator_or_brain_class_anywhere_in_raya():
    for path in arch_lint.RAYA_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for forbidden in _FORBIDDEN_SECOND_BRAIN_CLASSES:
            assert f"class {forbidden}" not in source, f"{path} définit {forbidden}"


def test_harness_remains_the_only_place_defining_the_long_horizon_step():
    """`_run_long_horizon_step` ne vit QUE dans harness/loop.py — jamais
    dupliqué ailleurs (une seule implémentation du moteur d'exécution).
    Une simple mention en commentaire/docstring (référence croisée) reste
    légitime ailleurs — seule une VRAIE redéfinition (`def ...`) est interdite."""
    hits = [
        p for p in arch_lint.RAYA_ROOT.rglob("*.py")
        if "def _run_long_horizon_step" in p.read_text(encoding="utf-8") and p.name != "loop.py"
    ]
    assert hits == []


def test_harness_long_horizon_step_reuses_execute_tool_never_a_second_executor():
    source = (arch_lint.RAYA_ROOT / "harness" / "loop.py").read_text(encoding="utf-8")
    # Une seule fonction d'exécution d'outil importée (`execute_tool`,
    # alias de raya.tools.execute) — jamais un second point d'exécution.
    assert "execute_tool" in source
    assert source.count("def execute_tool") == 0  # jamais redéfini localement


def test_harness_long_horizon_step_reuses_the_existing_loop_detector():
    """Consigne §5 : "Le système doit réutiliser les mécanismes existants :
    LoopDetector..." — jamais un second détecteur de boucle Phase 10."""
    source = (arch_lint.RAYA_ROOT / "harness" / "loop.py").read_text(encoding="utf-8")
    assert source.count("class LoopDetector") == 0  # jamais redéfini ici
    assert "self._loop_detector.record(" in source


def test_no_second_agent_loop_introduced_by_planning():
    path = arch_lint.RAYA_ROOT / "cognition" / "planning.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations = arch_lint.check_second_agent_loop(path, tree, "cognition")
    assert violations == []


def test_tasks_create_tool_never_imports_harness_directly():
    path = arch_lint.RAYA_ROOT / "tools" / "catalog" / "tasks.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for module, _names in arch_lint._raya_imports(tree):
        assert not module.startswith("raya.harness"), f"{path} importe raya.harness"


def test_no_second_notification_system_for_long_horizon_in_telegram():
    """Consigne §14 : "NE PAS créer de système de notification spécifique au
    Long-Horizon" — Telegram continue de s'appuyer sur les mêmes `task.*`
    events, jamais un canal Phase 10 séparé."""
    source = (arch_lint.RAYA_ROOT / "interfaces" / "telegram" / "channel.py").read_text(encoding="utf-8")
    assert 'bus.subscribe("task.*"' in source
    assert "long_horizon" not in source.lower()  # aucun concept Phase 10 spécifique côté Telegram


def test_full_arch_lint_zero_violations():
    assert arch_lint.run() == []
