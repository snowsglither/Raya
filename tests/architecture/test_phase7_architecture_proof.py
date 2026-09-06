"""Preuve architecturale — Perception & Environment (RAYA V2 Phase 7).

Prouve la chaîne imposée par la consigne §4 :
    Environment -> Device/Perception Agent -> Observation -> World State
    -> ContextEngine -> Harness -> Model
et JAMAIS : Device Agent -> Model direct, Device Agent -> ContextEngine
direct, Model -> Device Agent sans Harness, ni un `if tool_name == ...`
codé en dur dans le mécanisme générique de promotion d'observation."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import arch_lint  # noqa: E402

_PERCEPTION_DIR = arch_lint.RAYA_ROOT / "perception"


def _py_files(path: Path) -> list[Path]:
    return list(path.rglob("*.py"))


def test_perception_never_imports_devices_tools_models_harness_attention():
    """perception/ n'est autorisé qu'à dépendre de world_state/observability
    (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.3) — en particulier JAMAIS devices/
    (invariant #3 : un Device n'exécute jamais rien sans passer par tools/,
    perception ne doit donc même pas pouvoir en construire un Command)."""
    for path in _py_files(_PERCEPTION_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        violations = arch_lint.check_dependency_graph(path, tree, "perception")
        assert violations == [], f"{path}: {violations}"
        for module, _names in arch_lint._raya_imports(tree):
            for forbidden in ("devices", "tools", "models", "harness", "attention", "cognition", "interfaces"):
                assert not module.startswith(f"raya.{forbidden}"), f"{path} importe raya.{forbidden}"


def test_perception_never_imports_runtime_composition_root():
    for path in _py_files(_PERCEPTION_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module, _names in arch_lint._raya_imports(tree):
            assert not module.startswith("raya.runtime"), f"{path} importe raya.runtime"


def test_world_state_never_imports_perception_decoupled_via_eventbus_only():
    """Le couplage perception -> world_state passe ENTIÈREMENT par
    l'EventBus (WorldStateStore._on_perception_event s'abonne, jamais
    l'inverse) — aucun import direct dans un sens ou l'autre."""
    path = arch_lint.RAYA_ROOT / "world_state" / "store.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for module, _names in arch_lint._raya_imports(tree):
        assert not module.startswith("raya.perception"), f"{path} importe raya.perception"


def test_harness_observation_promotion_contains_no_hardcoded_tool_name_branching():
    """Consigne §13 : la promotion d'observation est pilotée par
    `Tool.observation` (déclaré dans tools/catalog/), jamais par un
    `if requested.tool_name == "pc.application.launch"` dans le Harness."""
    path = arch_lint.RAYA_ROOT / "harness" / "loop.py"
    source = path.read_text(encoding="utf-8")
    start = source.index("_promote_observations_and_verify")
    method_source = source[start:start + 2000]
    for forbidden_literal in ("pc.application.launch", "pc.window.focus", "browser.navigate", "\"calculatrice\"", "'calculatrice'"):
        assert forbidden_literal not in method_source, f"nom d'outil/valeur en dur trouvé dans la promotion : {forbidden_literal!r}"


def test_tools_catalog_observation_specs_declared_once_not_in_harness():
    """Les ObservationSpec vivent dans tools/catalog/{pc,browser}.py — jamais
    construits dynamiquement dans le Harness à partir du nom de l'outil."""
    harness_source = (arch_lint.RAYA_ROOT / "harness" / "loop.py").read_text(encoding="utf-8")
    assert "ObservationSpec(" not in harness_source


def test_real_devices_tree_still_never_imports_models_harness_cognition_safety():
    """Non-régression Phase 4 (invariant #5) — inchangée par Phase 7."""
    for path in (arch_lint.RAYA_ROOT / "devices").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        violations = arch_lint.check_dependency_graph(path, tree, "devices")
        assert violations == [], f"{path}: {violations}"
        for module, _names in arch_lint._raya_imports(tree):
            for forbidden in ("models", "harness", "cognition", "safety"):
                assert not module.startswith(f"raya.{forbidden}"), f"{path} importe raya.{forbidden}"


def test_context_engine_still_never_writes_to_world_state_or_memory():
    """Non-régression invariant #9, étendue à render.py (Phase 7) : lecture
    seule stricte, aucune méthode d'écriture appelée."""
    import re

    for path in (arch_lint.RAYA_ROOT / "context_engine").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"\.apply_update\(|\.write\(", source), f"{path} appelle une méthode d'écriture"


def test_no_second_agentic_loop_introduced_by_perception():
    """perception/runtime.py ne doit jamais combiner models+tools/devices —
    même heuristique que arch_lint.check_second_agent_loop, vérifiée
    explicitement sur le vrai fichier."""
    for path in _py_files(_PERCEPTION_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        violations = arch_lint.check_second_agent_loop(path, tree, "perception")
        assert violations == []


def test_full_arch_lint_zero_violations():
    assert arch_lint.run() == []
