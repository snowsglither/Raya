"""Priorité E — Architecture : dependency lint réel + tests négatifs sur le lint lui-même."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import arch_lint  # noqa: E402


def test_current_source_tree_has_zero_violations():
    violations = arch_lint.run()
    assert violations == [], "\n".join(str(v) for v in violations)


def _violations_for_source(subsystem: str, filename: str, source: str) -> list:
    tree = ast.parse(source)
    path = arch_lint.RAYA_ROOT / subsystem / filename
    v = []
    v += arch_lint.check_dependency_graph(path, tree, subsystem)
    v += arch_lint.check_models_no_task_harness_concepts(path, tree, subsystem)
    v += arch_lint.check_context_engine_no_write(path, source, subsystem)
    v += arch_lint.check_provider_bypass(path, tree, subsystem)
    v += arch_lint.check_second_agent_loop(path, tree, subsystem)
    v += arch_lint.check_hardcoded_secrets(path, tree)
    return v


def test_detects_interface_importing_forbidden_subsystem():
    v = _violations_for_source("interfaces", "bad.py", "from raya.safety import SafetyService\n")
    assert any(x.rule == "dependency-graph" for x in v)


def test_detects_device_importing_models():
    v = _violations_for_source("devices", "bad.py", "from raya.models import route\n")
    assert any(x.rule == "dependency-graph" for x in v)


def test_detects_device_importing_harness_or_cognition():
    v = _violations_for_source("devices", "bad.py", "from raya.harness import Harness\n")
    assert any(x.rule == "dependency-graph" for x in v)


def test_detects_models_importing_task_concept():
    v = _violations_for_source("models", "bad.py", "from raya.contracts import Task\n")
    assert any(x.rule == "models-no-task-harness" for x in v)


def test_detects_attention_importing_tools_or_models_or_harness():
    v = _violations_for_source("attention", "bad.py", "from raya.tools import execute\n")
    assert any(x.rule == "dependency-graph" for x in v)


def test_detects_perception_importing_models():
    v = _violations_for_source("perception", "bad.py", "from raya.models import route\n")
    assert any(x.rule == "dependency-graph" for x in v)


def test_context_engine_tools_discovery_allowed():
    v = _violations_for_source("context_engine", "ok.py", "from raya.tools import discover\n")
    assert v == []


def test_context_engine_tools_execution_forbidden():
    v = _violations_for_source("context_engine", "bad.py", "from raya.tools import execute\n")
    assert any(x.rule == "context_engine->tools.execution" for x in v)


def test_context_engine_write_call_detected():
    v = _violations_for_source(
        "context_engine", "bad.py",
        "def f(store):\n    store.write(1)\n",
    )
    assert any(x.rule == "context_engine-no-write" for x in v)


def test_detects_second_agent_loop_combination():
    v = _violations_for_source(
        "pc_control_like", "bad.py",
        "from raya.models import route\nfrom raya.tools import execute\n",
    )
    assert any(x.rule == "second-agent-loop" for x in v)


def test_harness_is_exempt_from_second_agent_loop_rule():
    v = _violations_for_source(
        "harness", "loop.py",
        "from raya.models import route\nfrom raya.tools import execute\n",
    )
    assert not any(x.rule == "second-agent-loop" for x in v)


def test_detects_provider_bypass_outside_models_and_runtime():
    v = _violations_for_source("devices", "bad.py", "from raya.models.providers import NullProvider\n")
    assert any(x.rule == "provider-bypass" for x in v)


def test_detects_hardcoded_secret():
    v = _violations_for_source(
        "models", "bad.py",
        "OLLAMA_API_KEY = 'sk-thisisaveryrealsecrettoken123'\n",
    )
    assert any(x.rule == "hardcoded-secret" for x in v)


def test_does_not_flag_secret_read_from_env():
    v = _violations_for_source(
        "runtime", "ok.py",
        "import os\nOLLAMA_API_KEY = os.environ.get('OLLAMA_API_KEY')\n",
    )
    assert not any(x.rule == "hardcoded-secret" for x in v)


# --- Priorité H (Phase 1) : couplage Memory/WorldState, persistence-orchestrateur ---

def test_memory_cannot_import_world_state():
    v = _violations_for_source("memory", "bad.py", "from raya.world_state import WorldStateStore\n")
    assert any(x.rule == "dependency-graph" for x in v)


def test_world_state_cannot_import_memory():
    v = _violations_for_source("world_state", "bad.py", "from raya.memory import MemoryStore\n")
    assert any(x.rule == "dependency-graph" for x in v)


def test_persistence_cannot_import_business_subsystems():
    for subsystem_module in ("memory", "tasks", "world_state", "harness"):
        v = _violations_for_source("persistence", "bad.py", f"from raya.{subsystem_module} import x\n")
        assert any(x.rule == "dependency-graph" for x in v), f"persistence -> {subsystem_module} aurait dû être détecté"


def test_real_memory_and_world_state_modules_do_not_cross_import():
    """Vérifie le VRAI code (pas un cas synthétique) : raya/memory/*.py
    n'importe jamais raya.world_state et inversement."""
    import ast as _ast

    for subsystem, forbidden in (("memory", "world_state"), ("world_state", "memory")):
        for path in (arch_lint.RAYA_ROOT / subsystem).rglob("*.py"):
            tree = _ast.parse(path.read_text(encoding="utf-8"))
            for module, _names in arch_lint._raya_imports(tree):
                assert not module.startswith(f"raya.{forbidden}"), f"{path} importe raya.{forbidden}"


def test_real_persistence_module_contains_no_business_imports():
    for path in (arch_lint.RAYA_ROOT / "persistence").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        violations = arch_lint.check_dependency_graph(path, tree, "persistence")
        assert violations == []


# --- Groupe F (Phase 2) : Attention, Scheduler, EventBus, Interfaces ---

def test_attention_importing_harness_detected():
    v = _violations_for_source("attention", "bad.py", "from raya.harness import Harness\n")
    assert any(x.rule == "dependency-graph" for x in v)


def test_attention_importing_models_detected():
    v = _violations_for_source("attention", "bad.py", "from raya.models import route\n")
    assert any(x.rule == "dependency-graph" for x in v)


def test_attention_executing_tools_detected():
    v = _violations_for_source("attention", "bad.py", "from raya.tools import execute\n")
    assert any(x.rule == "dependency-graph" or x.rule == "second-agent-loop" for x in v)


def test_task_registry_cannot_import_models_or_devices():
    for forbidden in ("models", "devices"):
        v = _violations_for_source("tasks", "bad.py", f"from raya.{forbidden} import x\n")
        assert any(x.rule == "dependency-graph" for x in v)


def test_real_scheduler_file_never_imports_provider_directly():
    """'Scheduler direct Ollama access' (consigne §35) — vérifié sur le VRAI
    fichier, pas un cas synthétique."""
    path = arch_lint.RAYA_ROOT / "harness" / "scheduler.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations = arch_lint.check_provider_bypass(path, tree, "harness")
    assert violations == []
    for module, _names in arch_lint._raya_imports(tree):
        assert not module.startswith("raya.models"), "scheduler.py ne doit jamais dépendre de models/"


def test_real_cli_never_imports_tasks_internals_regression():
    """Régression exacte trouvée pendant l'implémentation Phase 2 : le CLI
    importait raya.tasks.priority directement (corrigé via Harness.priority_from_name)."""
    path = arch_lint.RAYA_ROOT / "interfaces" / "cli" / "repl.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations = arch_lint.check_dependency_graph(path, tree, "interfaces")
    assert violations == []
    for module, _names in arch_lint._raya_imports(tree):
        assert not module.startswith("raya.tasks"), "interfaces/cli ne doit jamais importer raya.tasks"


def test_event_bus_module_imports_only_contracts():
    """'EventBus ne contient pas de logique métier' (consigne §34) — vérifié
    par l'absence totale d'import d'un subsystem métier dans event_bus/."""
    for path in (arch_lint.RAYA_ROOT / "event_bus").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module, _names in arch_lint._raya_imports(tree):
            if "<relative>" in module:
                continue  # import intra-package (ex: __init__.py -> .bus), pas un subsystem métier
            assert module.startswith("raya.contracts"), f"{path} importe {module}, hors contracts/"


def test_harness_allowed_to_import_attention_now():
    """Confirme la correction du graphe ALLOWED (Phase 0/1 avait oublié
    'attention' alors que RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.5 l'autorisait déjà)."""
    v = _violations_for_source("harness", "ok.py", "from raya.attention import AttentionEngine\n")
    assert v == []


# --- Phase 3 (consigne §30) : les 4 extensions du lint ---

def test_provider_shaped_vocabulary_detected_outside_models():
    path = arch_lint.RAYA_ROOT / "harness" / "bad.py"
    source = "def f(resp):\n    return resp.stop_reason\n"
    v = arch_lint.check_provider_shaped_leak(path, source, "harness")
    assert any(x.rule == "provider-shaped-leak" for x in v)


def test_provider_shaped_vocabulary_allowed_inside_models_providers():
    path = arch_lint.RAYA_ROOT / "models" / "providers" / "ollama_cloud.py"
    source = "def f(resp):\n    return resp.get('stop_reason')\n"
    v = arch_lint.check_provider_shaped_leak(path, source, "models")
    assert v == []


def test_tools_catalog_importing_safety_detected():
    path = arch_lint.RAYA_ROOT / "tools" / "catalog" / "bad.py"
    tree = ast.parse("from raya.safety import SafetyService\n")
    v = arch_lint.check_tools_catalog_no_safety_import(path, tree, "tools")
    assert any(x.rule == "tool-handler-self-authorizes" for x in v)


def test_tools_non_catalog_importing_safety_not_flagged_by_this_rule():
    """execution.py (tools/, hors catalog/) importe légitimement raya.safety —
    seul un HANDLER de catalog/ ne doit jamais s'auto-autoriser."""
    path = arch_lint.RAYA_ROOT / "tools" / "execution.py"
    tree = ast.parse("from raya.safety import SafetyService\n")
    v = arch_lint.check_tools_catalog_no_safety_import(path, tree, "tools")
    assert v == []


def test_interfaces_private_harness_attribute_access_detected():
    path = arch_lint.RAYA_ROOT / "interfaces" / "cli" / "bad.py"
    source = "def f(harness):\n    return harness._tasks\n"
    v = arch_lint.check_interfaces_no_private_harness_access(path, source, "interfaces")
    assert any(x.rule == "interface-private-harness-access" for x in v)


def test_interfaces_public_harness_api_not_flagged():
    path = arch_lint.RAYA_ROOT / "interfaces" / "cli" / "ok.py"
    source = "def f(harness):\n    return harness.list_tasks()\n"
    v = arch_lint.check_interfaces_no_private_harness_access(path, source, "interfaces")
    assert v == []


def test_legacy_v1_reference_detected():
    path = arch_lint.RAYA_ROOT / "harness" / "bad.py"
    source = "# port depuis core.orchestrator\n"
    v = arch_lint.check_no_legacy_v1_references(path, source)
    assert any(x.rule == "legacy-v1-reference" for x in v)


def test_legacy_v1_reference_absent_not_flagged():
    path = arch_lint.RAYA_ROOT / "harness" / "ok.py"
    source = "# boucle agentique REBUILD, aucun lien avec V1\n"
    v = arch_lint.check_no_legacy_v1_references(path, source)
    assert v == []


# --- Phase 4 (consigne §2, invariant #5) : devices/ n'importe JAMAIS raya.models ---

def test_real_devices_tree_never_imports_models_or_harness_or_cognition():
    """Vérifie le VRAI code Phase 4 (raya/devices/windows/, raya/devices/browser/) —
    invariant #5 ('aucun Device Agent ne possède son propre cerveau') et
    invariant #3 (devices/ n'importe jamais harness/cognition)."""
    for path in (arch_lint.RAYA_ROOT / "devices").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        violations = arch_lint.check_dependency_graph(path, tree, "devices")
        assert violations == [], f"{path}: {violations}"
        for module, _names in arch_lint._raya_imports(tree):
            assert not module.startswith("raya.models"), f"{path} importe raya.models"
            assert not module.startswith("raya.harness"), f"{path} importe raya.harness"
            assert not module.startswith("raya.cognition"), f"{path} importe raya.cognition"
            assert not module.startswith("raya.safety"), f"{path} importe raya.safety (devices/ consulte should_stop via injection, jamais par import)"


def test_real_pc_and_browser_catalog_declare_requires_device():
    """tools/catalog/pc.py et browser.py (Phase 4) délèguent bien à un Device
    Agent nommé (via la constante DEVICE_ID importée du bon sous-module
    devices/) — jamais une exécution locale déguisée en délégation."""
    for filename, expected_import in (("pc.py", "raya.devices.windows"), ("browser.py", "raya.devices.browser")):
        path = arch_lint.RAYA_ROOT / "tools" / "catalog" / filename
        source = path.read_text(encoding="utf-8")
        assert "requires_device=" in source
        assert f"from {expected_import} import DEVICE_ID" in source


def test_real_source_tree_free_of_provider_shaped_leak_and_legacy_references():
    """Vérifie le VRAI code Phase 3 (pas un cas synthétique) pour les 4
    nouvelles règles — déjà couvert par test_current_source_tree_has_zero_violations
    via arch_lint.run(), reformulé ici explicitement par règle pour un signal
    de test ciblé si l'une d'elles régresse seule."""
    for path in arch_lint.RAYA_ROOT.rglob("*.py"):
        subsystem = arch_lint._subsystem_of(path)
        if subsystem is None:
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        assert arch_lint.check_provider_shaped_leak(path, source, subsystem) == []
        assert arch_lint.check_tools_catalog_no_safety_import(path, tree, subsystem) == []
        assert arch_lint.check_interfaces_no_private_harness_access(path, source, subsystem) == []
        assert arch_lint.check_no_legacy_v1_references(path, source) == []


# --- Phase 5 (consigne §42 "ARCHITECTURE PROOF") : Voice n'est pas un second cerveau ---

def test_real_voice_tree_never_imports_cognition_tools_devices_models_directly():
    """Preuve structurelle sur le VRAI code : raya/interfaces/voice/ (et ses
    sous-packages audio/vad/stt/tts) n'importe jamais cognition/tools/
    devices/models — seulement harness (client mince) + contracts/event_bus
    (transversaux, toujours autorisés)."""
    voice_dir = arch_lint.RAYA_ROOT / "interfaces" / "voice"
    for path in voice_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        violations = arch_lint.check_dependency_graph(path, tree, "interfaces")
        assert violations == [], f"{path}: {violations}"
        for module, _names in arch_lint._raya_imports(tree):
            for forbidden in ("cognition", "tools", "devices", "models", "tasks", "safety", "attention"):
                assert not module.startswith(f"raya.{forbidden}"), f"{path} importe raya.{forbidden}"


def test_real_vad_and_stt_submodules_never_import_harness_either():
    """VAD/STT sont des MÉCANISMES purs (consigne §3/§4) — ils n'ont même
    pas besoin de `harness` (seul `channel.py`/`runtime.py`/`factory.py`,
    qui orchestrent le TRANSPORT, en ont besoin)."""
    for sub in ("vad", "stt", "audio"):
        for path in (arch_lint.RAYA_ROOT / "interfaces" / "voice" / sub).rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for module, _names in arch_lint._raya_imports(tree):
                assert not module.startswith("raya.harness"), f"{path} importe raya.harness"


def test_real_voice_package_never_imports_runtime_composition_root():
    """`runtime/` est la racine de composition (autorisée à TOUT importer) —
    l'inverse (voice -> runtime) créerait une dépendance ascendante interdite
    (RAYA_V2_REPOSITORY_STRUCTURE.md §20). Régression réelle trouvée et
    corrigée pendant cette phase (`factory.py` important `RuntimeHandles`
    par erreur, remplacé par un Protocol local — voir rapport §Bugs)."""
    for path in (arch_lint.RAYA_ROOT / "interfaces" / "voice").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for module, _names in arch_lint._raya_imports(tree):
            assert not module.startswith("raya.runtime"), f"{path} importe raya.runtime"


def test_real_tasks_catalog_never_imports_harness_uses_injected_ops_instead():
    """tools/catalog/tasks.py (steering, Phase 5) : dépendance ascendante
    tools->harness interdite — vérifie l'injection `TaskControlOps` réelle."""
    path = arch_lint.RAYA_ROOT / "tools" / "catalog" / "tasks.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations = arch_lint.check_dependency_graph(path, tree, "tools")
    assert violations == []
    for module, _names in arch_lint._raya_imports(tree):
        assert not module.startswith("raya.harness"), f"{path} importe raya.harness"


def test_real_voice_module_contains_no_hardcoded_action_recipes():
    """Garde-fou anti-'if phrase X then action Y' (consigne §40 NO MAGIC
    PHRASES) : aucun fichier de raya/interfaces/voice/ ne doit contenir de
    comparaison directe transcript == 'stop'-style pour le mécanisme STOP —
    seul `request_stop()` (Event -> EventBus) existe pour ça."""
    for path in (arch_lint.RAYA_ROOT / "interfaces" / "voice").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert 'text == "stop"' not in source and "text == 'stop'" not in source
        assert '== "stop"' not in source
