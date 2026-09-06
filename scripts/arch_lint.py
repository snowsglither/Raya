#!/usr/bin/env python3
"""Lint architectural RAYA V2 (consigne Phase 0 §9).

Vérifie automatiquement le graphe de dépendance de
RAYA_V2_REPOSITORY_STRUCTURE.md §20 (corrigé par
RAYA_V2_ARCHITECTURE_FINAL_REVIEW.md §9 pour l'exception
context_engine -> tools.discovery).

Détecte au minimum :
  - interface -> subsystem interdit
  - device -> model
  - device -> harness/cognition
  - models -> task/session/harness (concepts, pas seulement le module)
  - attention -> tools/models/devices/harness
  - perception -> models
  - context_engine -> écriture memory/world_state (heuristique d'appel)
  - context_engine -> tools.execution (l'exception discovery-only est autorisée)
  - deuxième boucle agentique (heuristique : models+tools/devices combinés hors harness/)
  - appel provider hors models/providers (bypass du Model Layer)
  - secrets hardcodés (heuristique)

Phase 3 (consigne §30) :
  - contrat provider-shaped (Anthropic-like) qui fuit hors models/providers/
  - tools/catalog/* import raya.safety (un handler ne s'auto-autorise jamais)
  - interfaces/* accède à un attribut privé du Harness (harness._...)
  - références textuelles à l'ancien decision loop V1 (garde-fou anti-copier-coller)

Usage : python scripts/arch_lint.py
Sortie : liste de violations lisible CI, exit code 1 si une violation BLOQUANT existe.
"""

from __future__ import annotations

import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path

RAYA_ROOT = Path(__file__).resolve().parents[1] / "raya"

# ---------------------------------------------------------------------------
# Graphe de dépendance autorisé (RAYA_V2_REPOSITORY_STRUCTURE.md §20)
# clé = subsystem (dossier top-level sous raya/), valeur = subsystems raya.*
# qu'il a le droit d'importer (en plus de "contracts" et "event_bus", toujours
# autorisés partout, et de son propre package).
# ---------------------------------------------------------------------------
ALWAYS_ALLOWED = {"contracts", "event_bus"}

ALLOWED: dict[str, set[str]] = {
    "runtime": {"*"},  # composition root : câble tout
    "interfaces": {"harness", "observability"},
    "harness": {
        "attention",  # AJOUTÉ Phase 2 — déjà documenté RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.5,
                      # oubli de la table ALLOWED Phase 0/1 corrigé ici (pas un changement d'architecture)
        "cognition",
        "tasks",
        "context_engine",
        "memory",
        "world_state",
        "tools",
        "safety",
        "models",
        "persistence",
        "observability",
        "spatial",  # AJOUTÉ Phase 8 — lecture seule pour l'exposition UI (Harness.get_scene()), même principe que world_state/memory
        "devices",  # AJOUTÉ Phase 9 — Device Registry (RAYA_V2_ARCHITECTURAL_BLUEPRINT.md §19), lecture/enregistrement pour les interfaces (ex: /status Telegram), jamais un Command/execute() direct
    },
    "cognition": {"models", "context_engine", "memory", "observability"},
    "context_engine": {"memory", "world_state", "tasks", "tools", "observability"},
    "memory": {"persistence", "observability"},
    "tasks": {"persistence", "observability"},
    "tools": {"devices", "safety", "models", "spatial", "observability"},  # spatial AJOUTÉ Phase 8 (tools/catalog/spatial.py)
    "safety": {"persistence", "observability"},
    "devices": {"observability"},
    "world_state": {"persistence", "observability"},
    "perception": {"world_state", "observability"},
    "spatial": {"observability"},  # AJOUTÉ Phase 8 — feuille, comme perception (consigne §29)
    "attention": {"world_state", "tasks", "observability"},
    "models": {"observability"},
    "persistence": {"observability"},
    "observability": {"persistence"},
}

FORBIDDEN_CONTRACT_SYMBOLS_IN_MODELS = {"Task", "TaskEvent", "TaskState", "HarnessState", "HarnessRequest"}

SECOND_LOOP_EXEMPT = {"harness", "runtime"}
PROVIDER_IMPORT_ALLOWED_FROM = {"models", "runtime"}

SECRET_NAME_RE = re.compile(r"(key|secret|token|password)", re.IGNORECASE)
SECRET_VALUE_RE = re.compile(r"^[A-Za-z0-9_\-]{20,}$")


@dataclass
class Violation:
    file: str
    rule: str
    message: str
    severity: str = "BLOQUANT"

    def __str__(self) -> str:
        return f"[{self.severity}] {self.file}: ({self.rule}) {self.message}"


def _subsystem_of(path: Path) -> str | None:
    try:
        rel = path.relative_to(RAYA_ROOT)
    except ValueError:
        return None
    parts = rel.parts
    if not parts:
        return None
    if parts[0] == "__init__.py" or parts[0] == "_base.py":
        return None
    return parts[0] if not parts[0].endswith(".py") else parts[0][:-3]


def _iter_py_files():
    for path in RAYA_ROOT.rglob("*.py"):
        yield path


def _raya_imports(tree: ast.AST) -> list[tuple[str, list[str]]]:
    """Retourne [(module_path, [noms importés])] pour tout import 'raya.*' ou 'from .. import'."""
    results: list[tuple[str, list[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("raya."):
                    results.append((alias.name, []))
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.startswith("raya."):
                results.append((node.module, [a.name for a in node.names]))
            elif node.level and node.module:
                # import relatif (from . import x / from .x import y) — résolu
                # approximativement, suffisant pour notre usage (packages plats)
                results.append((f"raya.<relative>.{node.module}", [a.name for a in node.names]))
    return results


def check_dependency_graph(path: Path, tree: ast.AST, subsystem: str) -> list[Violation]:
    violations: list[Violation] = []
    allowed = ALLOWED.get(subsystem, set())
    if "*" in allowed:
        return violations

    for module, names in _raya_imports(tree):
        if "<relative>" in module:
            continue
        parts = module.split(".")
        if len(parts) < 2:
            continue
        imported_subsystem = parts[1]
        if imported_subsystem == subsystem:
            continue
        if imported_subsystem in ALWAYS_ALLOWED:
            continue

        if imported_subsystem == "tools" and subsystem == "context_engine":
            # Exception verrouillée : lecture seule via discovery, jamais execution.
            if "execute" in names or (len(parts) >= 3 and parts[2] == "execution"):
                violations.append(
                    Violation(
                        str(path),
                        "context_engine->tools.execution",
                        "context_engine ne peut lire que tools.discovery (schémas), "
                        "jamais exécuter un outil (RAYA_V2_REPOSITORY_STRUCTURE.md §20).",
                    )
                )
            continue  # discover()/ToolRegistry en lecture = autorisé

        if imported_subsystem not in allowed:
            violations.append(
                Violation(
                    str(path),
                    "dependency-graph",
                    f"{subsystem}/ importe raya.{imported_subsystem} — dépendance non "
                    f"autorisée (RAYA_V2_REPOSITORY_STRUCTURE.md §20). Autorisé pour "
                    f"{subsystem}/: {sorted(allowed) or '(rien au-delà de contracts/event_bus)'}",
                )
            )
    return violations


def check_models_no_task_harness_concepts(path: Path, tree: ast.AST, subsystem: str) -> list[Violation]:
    if subsystem != "models":
        return []
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("raya.contracts"):
            for alias in node.names:
                if alias.name in FORBIDDEN_CONTRACT_SYMBOLS_IN_MODELS:
                    violations.append(
                        Violation(
                            str(path),
                            "models-no-task-harness",
                            f"models/ importe le concept {alias.name!r} — models/ ne doit connaître "
                            f"aucun concept Task/Session/Harness (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.11, invariant #10).",
                        )
                    )
    return violations


def check_context_engine_no_write(path: Path, source: str, subsystem: str) -> list[Violation]:
    if subsystem != "context_engine":
        return []
    violations = []
    if re.search(r"\.write\(|\.apply_update\(", source):
        violations.append(
            Violation(
                str(path),
                "context_engine-no-write",
                "context_engine/ appelle une méthode d'écriture (.write()/.apply_update()) — "
                "Context Engine sélectionne, ne persiste jamais (invariant #9).",
            )
        )
    return violations


def check_provider_bypass(path: Path, tree: ast.AST, subsystem: str) -> list[Violation]:
    if subsystem in PROVIDER_IMPORT_ALLOWED_FROM or subsystem == "providers":
        return []
    violations = []
    for module, _names in _raya_imports(tree):
        if module.startswith("raya.models.providers"):
            violations.append(
                Violation(
                    str(path),
                    "provider-bypass",
                    f"{subsystem}/ importe raya.models.providers directement — tout appel provider "
                    f"doit passer par models/router.py (invariant #2).",
                )
            )
    return violations


def check_second_agent_loop(path: Path, tree: ast.AST, subsystem: str) -> list[Violation]:
    if subsystem in SECOND_LOOP_EXEMPT:
        return []
    imported = {m.split(".")[1] for m, _ in _raya_imports(tree) if "<relative>" not in m and len(m.split(".")) > 1}
    if "models" in imported and ("tools" in imported or "devices" in imported):
        return [
            Violation(
                str(path),
                "second-agent-loop",
                f"{subsystem}/ importe à la fois raya.models et raya.tools/devices — signature "
                f"d'une boucle agentique parallèle. harness/loop.py est le SEUL endroit autorisé "
                f"à combiner les deux (invariant #1, RAYA_V2_TECHNICAL_ARCHITECTURE.md §3.3).",
            )
        ]
    return []


def check_hardcoded_secrets(path: Path, tree: ast.AST) -> list[Violation]:
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                literal = value.value
                for target in node.targets:
                    name = getattr(target, "id", None) or getattr(target, "attr", None)
                    if name and SECRET_NAME_RE.search(name) and SECRET_VALUE_RE.match(literal):
                        violations.append(
                            Violation(
                                str(path),
                                "hardcoded-secret",
                                f"variable {name!r} assignée à une chaîne littérale qui ressemble "
                                f"à un secret — les secrets ne doivent jamais être dans le code.",
                            )
                        )
    return violations


# Vocabulaire spécifique à un provider tiers — ne doit JAMAIS apparaître en
# dehors de raya/models/providers/ (RAYA_V2_TECHNICAL_ARCHITECTURE.md §4.5,
# consigne Phase 3 §4 : "Le cœur ne doit pas connaître les détails spécifiques
# d'Ollama", a fortiori d'un tout autre fournisseur type Anthropic/OpenAI).
_PROVIDER_SHAPED_VOCABULARY = ("stop_reason", "SimpleNamespace", "type=\"tool_use\"", "type='tool_use'")

_LEGACY_V1_REFERENCES = ("core.orchestrator", "core.llm", "modules.brain.router", "modules.pc_control.auto_agent")


def check_provider_shaped_leak(path: Path, source: str, subsystem: str) -> list[Violation]:
    if subsystem in ("models", "contracts"):
        # providers/ traduit légitimement depuis/vers un format tiers en interne ;
        # contracts/model.py documente par son nom le vocabulaire interdit
        # (RAYA_V2_CONTRACTS.md §12) — ce n'est pas une fuite, c'est la règle elle-même.
        return []
    violations = []
    for term in _PROVIDER_SHAPED_VOCABULARY:
        if term in source:
            violations.append(
                Violation(
                    str(path), "provider-shaped-leak",
                    f"vocabulaire spécifique à un SDK provider ({term!r}) trouvé hors de models/ — "
                    f"le contrat natif RAYA (ModelRequest/ModelResponse/FinishReason) ne doit jamais fuiter.",
                )
            )
    return violations


def check_tools_catalog_no_safety_import(path: Path, tree: ast.AST, subsystem: str) -> list[Violation]:
    if subsystem != "tools" or "catalog" not in path.parts:
        return []
    violations = []
    for module, _names in _raya_imports(tree):
        if module.startswith("raya.safety"):
            violations.append(
                Violation(
                    str(path), "tool-handler-self-authorizes",
                    "un handler de tools/catalog/ importe raya.safety directement — un Tool ne doit "
                    "jamais s'auto-autoriser, la vérification se fait exclusivement dans tools/execution.py.",
                )
            )
    return violations


def check_interfaces_no_private_harness_access(path: Path, source: str, subsystem: str) -> list[Violation]:
    if subsystem != "interfaces":
        return []
    violations = []
    if re.search(r"\bharness\._[A-Za-z_]", source):
        violations.append(
            Violation(
                str(path), "interface-private-harness-access",
                "accès à un attribut privé du Harness (harness._...) depuis une interface — "
                "une interface n'utilise que l'API publique du Harness.",
            )
        )
    return violations


def check_no_legacy_v1_references(path: Path, source: str) -> list[Violation]:
    violations = []
    for ref in _LEGACY_V1_REFERENCES:
        if ref in source:
            violations.append(
                Violation(
                    str(path), "legacy-v1-reference",
                    f"référence textuelle à un composant V1 legacy ({ref!r}) — garde-fou anti-copier-coller "
                    f"(consigne Phase 3 §1, §34).",
                )
            )
    return violations


def run() -> list[Violation]:
    violations: list[Violation] = []
    for path in _iter_py_files():
        subsystem = _subsystem_of(path)
        if subsystem is None:
            continue
        source = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(path))
        except SyntaxError as exc:
            violations.append(Violation(str(path), "syntax-error", str(exc)))
            continue

        violations += check_dependency_graph(path, tree, subsystem)
        violations += check_models_no_task_harness_concepts(path, tree, subsystem)
        violations += check_context_engine_no_write(path, source, subsystem)
        violations += check_provider_bypass(path, tree, subsystem)
        violations += check_second_agent_loop(path, tree, subsystem)
        violations += check_hardcoded_secrets(path, tree)
        violations += check_provider_shaped_leak(path, source, subsystem)
        violations += check_tools_catalog_no_safety_import(path, tree, subsystem)
        violations += check_interfaces_no_private_harness_access(path, source, subsystem)
        violations += check_no_legacy_v1_references(path, source)

    return violations


def main() -> int:
    violations = run()
    if not violations:
        print("ARCH LINT: PASS — aucune violation détectée.")
        return 0
    print(f"ARCH LINT: {len(violations)} violation(s) détectée(s) :\n")
    for v in violations:
        print(f"  {v}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
