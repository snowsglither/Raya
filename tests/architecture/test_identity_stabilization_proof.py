"""Preuve architecturale — stabilisation pré-Phase 7 Identity/Memory/Context.

Vérifie que les correctifs (context_engine.assemble()/render_system_prompt(),
Harness, scripts/ingest_profile.py) respectent les invariants existants :
aucun import V1, aucune réponse figée à une question d'identité, aucun
deuxième Memory Manager / Context Engine, context_engine ne route jamais
lui-même vers un modèle (jamais raya.models)."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import arch_lint  # noqa: E402

_NEW_FILES = [
    arch_lint.RAYA_ROOT / "context_engine" / "assembler.py",
    arch_lint.RAYA_ROOT / "context_engine" / "render.py",
    arch_lint.RAYA_ROOT / "models" / "router.py",
    arch_lint.RAYA_ROOT / "harness" / "loop.py",
]
_HARDCODED_IDENTITY_PATTERNS = (
    "Ruben Lukusa",  # une donnée personnelle ne doit jamais être un littéral dans le code RAYA
    'if text == "qui suis-je',
    "if text ==",  # aucune branche conditionnée sur le texte exact d'une question d'identité
)


def test_context_engine_never_imports_models_never_routes_itself():
    """context_engine ne doit jamais choisir/appeler un modèle lui-même —
    runtime_identity lui est INJECTÉ par Harness (seul le Harness importe
    raya.models, RAYA_V2_REPOSITORY_STRUCTURE.md §20 inchangé)."""
    for path in (arch_lint.RAYA_ROOT / "context_engine").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        violations = arch_lint.check_dependency_graph(path, tree, "context_engine")
        assert violations == [], f"{path}: {violations}"
        for module, _names in arch_lint._raya_imports(tree):
            assert not module.startswith("raya.models"), f"{path} importe raya.models"


def test_no_v1_references_in_new_or_modified_files():
    for path in _NEW_FILES:
        source = path.read_text(encoding="utf-8")
        violations = arch_lint.check_no_legacy_v1_references(path, source)
        assert violations == [], f"{path}: {violations}"


def test_no_hardcoded_ruben_or_identity_phrase_matching_in_engine_code():
    """Le nom 'RAYA' (branding) peut apparaître comme constante — jamais
    'Ruben Lukusa' (donnée personnelle) ni un pattern-matching sur le texte
    exact d'une question d'identité, dans le CODE MOTEUR (pas les scripts de
    migration, dont le rôle même est de lire — jamais d'écrire en dur — cette
    donnée depuis le fichier source)."""
    engine_files = [
        arch_lint.RAYA_ROOT / "context_engine" / "assembler.py",
        arch_lint.RAYA_ROOT / "context_engine" / "render.py",
        arch_lint.RAYA_ROOT / "harness" / "loop.py",
        arch_lint.RAYA_ROOT / "models" / "router.py",
    ]
    for path in engine_files:
        source = path.read_text(encoding="utf-8")
        assert "Ruben Lukusa" not in source, f"{path} contient une donnée personnelle en dur"
        assert 'text == "qui suis-je' not in source.lower()


def test_ingest_profile_script_never_hardcodes_personal_data_only_parses_source():
    """scripts/ingest_profile.py doit lire le contenu depuis un fichier passé
    en paramètre — jamais un contenu personnel recopié en dur dans le .py."""
    source = (Path(__file__).resolve().parents[2] / "scripts" / "ingest_profile.py").read_text(encoding="utf-8")
    assert "Ruben Lukusa" not in source
    assert "Lukusa" not in source
    assert "read_text(" in source  # lit bien un fichier, ne fabrique pas le contenu


def test_no_second_memory_manager_or_context_engine_introduced():
    """Un seul MemoryStore, un seul assemble() — aucune nouvelle classe
    '*MemoryManager'/'*ContextEngine' introduite par cette stabilisation."""
    for path in arch_lint.RAYA_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "class MemoryManager" not in source
        assert "class ContextEngine" not in source
        assert "class SecondHarness" not in source


def test_render_system_prompt_never_bypasses_context_sections():
    """render_system_prompt() ne doit lire QUE context.sections — jamais un
    accès direct à memory/world_state (qui casserait le filtrage déjà fait
    par assemble(), et romprait l'isolation de canal déjà appliquée)."""
    path = arch_lint.RAYA_ROOT / "context_engine" / "render.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for module, _names in arch_lint._raya_imports(tree):
        assert not module.startswith("raya.memory"), f"{path} importe raya.memory directement"
        assert not module.startswith("raya.world_state"), f"{path} importe raya.world_state directement"


def test_full_dependency_lint_still_zero_violations():
    """Non-régression globale : la stabilisation ne doit introduire AUCUNE
    violation architecturale nulle part dans l'arbre."""
    assert arch_lint.run() == []
