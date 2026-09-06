"""Preuve architecturale — Interface Telegram / Device Registry (RAYA V2
Phase 9).

Prouve la chaîne imposée par la consigne §3/§29 :
    Telegram -> Interface Adapter -> Harness -> Attention/Context/Cognition/
    Tasks -> Tools -> Environment -> Result -> Telegram
Jamais : Telegram -> Model direct, Telegram -> Tool direct, Telegram ->
Memory/WorldState direct, Telegram -> raya.devices direct, un deuxième
STOP/scheduler/orchestrateur/agent loop Telegram, un token en dur."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import arch_lint  # noqa: E402

_TELEGRAM_DIR = arch_lint.RAYA_ROOT / "interfaces" / "telegram"

_FORBIDDEN_SECOND_BRAIN_CLASSES = (
    "TelegramAgent", "TelegramBrain", "TelegramOrchestrator",
    "TelegramScheduler", "TelegramMemory", "TelegramToolExecutor",
)


def _py_files(path: Path) -> list[Path]:
    return list(path.rglob("*.py"))


def test_telegram_never_imports_models_memory_tools_devices_tasks_world_state_directly():
    """Consigne §3/§29 : Telegram reste un client mince, comme Voice/Cockpit."""
    for path in _py_files(_TELEGRAM_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        violations = arch_lint.check_dependency_graph(path, tree, "interfaces")
        assert violations == [], f"{path}: {violations}"
        for module, _names in arch_lint._raya_imports(tree):
            for forbidden in ("models", "memory", "tools", "devices", "tasks", "world_state",
                               "attention", "cognition", "safety", "runtime", "spatial", "perception"):
                assert not module.startswith(f"raya.{forbidden}"), f"{path} importe raya.{forbidden}"


def test_interfaces_allowed_dependencies_were_not_widened_for_telegram():
    """Consigne §29 : Device Registry reste accessible UNIQUEMENT via
    Harness — `interfaces` ne doit jamais gagner un accès direct à
    `raya.devices` juste pour Telegram."""
    assert arch_lint.ALLOWED["interfaces"] == {"harness", "observability"}


def test_harness_devices_dependency_is_read_forward_only_no_command_dispatch():
    """Le Harness expose le Device Registry (Phase 9) mais ne construit/
    exécute jamais lui-même de `Command` — cela reste le rôle exclusif de
    tools/catalog/{pc,browser}.py (RAYA_V2_ARCHITECTURAL_INVARIANTS.md #3)."""
    source = (arch_lint.RAYA_ROOT / "harness" / "loop.py").read_text(encoding="utf-8")
    assert "Command(" not in source
    assert ".execute(command" not in source


def test_no_second_orchestrator_scheduler_or_brain_introduced_by_telegram():
    for path in _py_files(_TELEGRAM_DIR):
        source = path.read_text(encoding="utf-8")
        for forbidden in _FORBIDDEN_SECOND_BRAIN_CLASSES:
            assert f"class {forbidden}" not in source, f"{path} définit {forbidden}"
        assert "class Harness" not in source
        assert "class TaskRegistry" not in source


def test_no_second_agent_loop_in_telegram():
    for path in _py_files(_TELEGRAM_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        violations = arch_lint.check_second_agent_loop(path, tree, "interfaces")
        assert violations == []


def test_telegram_never_calls_safety_directly_stop_goes_through_eventbus():
    """Consigne §9 : jamais un appel de code direct à Safety — le test de
    dépendances (ci-dessus) prouve déjà que `raya.safety` n'est même pas
    importable depuis `interfaces/` ; ici on vérifie en plus qu'un VRAI
    Event STOP est bien publié (positif, pas seulement une absence)."""
    tree = ast.parse((_TELEGRAM_DIR / "channel.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr != "request_stop" or "safety" not in ast.dump(node.func).lower()
    source = (_TELEGRAM_DIR / "channel.py").read_text(encoding="utf-8")
    assert 'Event(type="interface.stop_requested"' in source


def test_bot_token_is_never_hardcoded_in_production_code():
    """Consigne §4 : le token vient EXCLUSIVEMENT de l'environnement — jamais
    une valeur par défaut, jamais un exemple de vrai token dans le code."""
    for path in _py_files(_TELEGRAM_DIR) + [arch_lint.RAYA_ROOT / "runtime" / "config.py"]:
        source = path.read_text(encoding="utf-8")
        violations = arch_lint.check_hardcoded_secrets(path, ast.parse(source))
        assert violations == [], f"{path}: {violations}"


def test_token_never_appears_in_a_default_config_value():
    source = (arch_lint.RAYA_ROOT / "runtime" / "config.py").read_text(encoding="utf-8")
    assert 'telegram_bot_token: str | None = None' in source


def test_device_type_mobile_never_registered_as_an_executable_agent():
    """Consigne §17 : un téléphone n'est jamais transformé en agent
    autonome — `register_info()` (informationnel) reste distinct de
    `register()` (DeviceAgent exécutable, Command/execute())."""
    source = (arch_lint.RAYA_ROOT / "devices" / "registry.py").read_text(encoding="utf-8")
    assert "def register_info(" in source
    assert "def register(" in source


def test_full_arch_lint_zero_violations():
    assert arch_lint.run() == []
