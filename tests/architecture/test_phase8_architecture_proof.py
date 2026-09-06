"""Preuve architecturale — Creative/Spatial Agent (RAYA V2 Phase 8).

Prouve la chaîne imposée par la consigne §4 :
    Interface -> Harness -> Cognition/Task/Context -> Tool Registry ->
    Creative/Spatial capability -> Environment
et le retour :
    Spatial/Perception observation -> World State -> ContextEngine -> Harness -> Model
Jamais : spatial -> Model direct, spatial -> Memory direct, un deuxième
orchestrateur/Harness/scheduler, Three.js comme dépendance obligatoire du Core."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import arch_lint  # noqa: E402

_SPATIAL_DIR = arch_lint.RAYA_ROOT / "spatial"


def _py_files(path: Path) -> list[Path]:
    return list(path.rglob("*.py"))


def test_spatial_never_imports_models_memory_harness_interfaces_tools_devices():
    """Consigne §29 : "spatial n'importe pas directement Model", "spatial
    n'écrit pas directement dans Memory" — étendu à tout ce qui ferait de
    spatial un deuxième cerveau/orchestrateur."""
    for path in _py_files(_SPATIAL_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        violations = arch_lint.check_dependency_graph(path, tree, "spatial")
        assert violations == [], f"{path}: {violations}"
        for module, _names in arch_lint._raya_imports(tree):
            for forbidden in ("models", "memory", "harness", "interfaces", "tools", "devices", "attention", "cognition", "runtime"):
                assert not module.startswith(f"raya.{forbidden}"), f"{path} importe raya.{forbidden}"


def test_spatial_model_never_imports_or_calls_threejs_api():
    """Consigne §6 : "Le modèle de scène ne doit pas importer Three.js" —
    vérifié au niveau CODE (import, appel d'API `THREE.*`), pas au niveau
    prose : la documentation du fichier peut légitimement EXPLIQUER que le
    renderer Three.js vit côté client sans que ce soit un couplage réel."""
    path = arch_lint.RAYA_ROOT / "contracts" / "spatial.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for module, _names in arch_lint._raya_imports(tree):
        assert "three" not in module.lower()
    source = path.read_text(encoding="utf-8")
    assert "THREE." not in source  # namespace JS THREE.xxx — jamais un appel d'API réel ici


def test_only_renderer_adapter_contains_threejs_api_surface():
    """Consigne §7 : la frontière Data/Rendering est un seul fichier — seul
    `threejs_adapter.py` a le droit de coder les conventions Three.js
    (noms de géométrie `_KNOWN_KINDS`), jamais `store.py` ni les tools."""
    for path in _py_files(_SPATIAL_DIR):
        if path.name == "threejs_adapter.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert "THREE." not in source, f"{path} appelle l'API Three.js"
        assert "_KNOWN_KINDS" not in source, f"{path} connaît les conventions de géométrie Three.js"


def test_tools_catalog_spatial_never_imports_harness():
    """tools/ reste structurellement en dessous de harness/ — jamais une
    dépendance ascendante (RAYA_V2_REPOSITORY_STRUCTURE.md §20)."""
    path = arch_lint.RAYA_ROOT / "tools" / "catalog" / "spatial.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for module, _names in arch_lint._raya_imports(tree):
        assert not module.startswith("raya.harness"), f"{path} importe raya.harness"


def test_spatial_tools_never_self_authorize_no_safety_import():
    """Même règle que tout tools/catalog/*.py (consigne §23 'aucune capacité
    spatiale ne doit contourner Safety') — la vérification reste exclusive à
    tools/execution.py."""
    path = arch_lint.RAYA_ROOT / "tools" / "catalog" / "spatial.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations = arch_lint.check_tools_catalog_no_safety_import(path, tree, "tools")
    assert violations == []


def test_harness_spatial_exposure_is_read_only():
    """Consigne §4 : Harness expose spatial en LECTURE seule à l'UI — jamais
    de méthode Harness qui mute une scène (create/add_object/etc. restent
    exclusivement dans tools/catalog/spatial.py, jamais un `Harness.create_scene()`)."""
    source = (arch_lint.RAYA_ROOT / "harness" / "loop.py").read_text(encoding="utf-8")
    forbidden_methods = ("def create_scene(", "def add_object(", "def update_object(", "def remove_object(")
    for method in forbidden_methods:
        assert method not in source, f"Harness expose une mutation directe : {method}"


def test_no_second_orchestrator_scheduler_or_harness_introduced_by_spatial():
    for path in _py_files(_SPATIAL_DIR) + [arch_lint.RAYA_ROOT / "tools" / "catalog" / "spatial.py"]:
        source = path.read_text(encoding="utf-8")
        assert "class TaskScheduler" not in source
        assert "class Harness" not in source
        assert "class SceneScheduler" not in source


def test_no_second_agent_loop_in_spatial():
    for path in _py_files(_SPATIAL_DIR):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        violations = arch_lint.check_second_agent_loop(path, tree, "spatial")
        assert violations == []


def test_ui_cockpit_static_files_do_not_load_threejs_unconditionally():
    """Consigne §3 : "Ne transforme pas RAYA en... scène Three.js chargée par
    défaut" — vérifié textuellement : l'index.html du Cockpit ne charge
    JAMAIS le script Three.js de façon inconditionnelle (uniquement à la
    demande, via app.js, quand la vue spatiale s'ouvre réellement)."""
    index_html = (arch_lint.RAYA_ROOT / "interfaces" / "ui" / "static" / "index.html").read_text(encoding="utf-8")
    assert "three" not in index_html.lower(), "index.html référence Three.js de façon statique/inconditionnelle"


def test_ui_cockpit_has_a_spatial_panel_that_loads_threejs_lazily_in_app_js():
    """Consigne §17/§18 : la vue spatiale existe (un panneau + canvas dans le
    Cockpit) mais le renderer n'est chargé QUE depuis app.js, jamais depuis
    index.html — complète le test précédent au lieu de le remplacer."""
    ui_static = arch_lint.RAYA_ROOT / "interfaces" / "ui" / "static"
    index_html = (ui_static / "index.html").read_text(encoding="utf-8")
    assert 'id="panel-spatial"' in index_html
    assert "<canvas" in index_html
    app_js = (ui_static / "app.js").read_text(encoding="utf-8").lower()
    assert "three" in app_js, "app.js doit être le SEUL endroit qui charge le renderer Three.js"


def test_full_arch_lint_zero_violations():
    assert arch_lint.run() == []
