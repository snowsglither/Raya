"""Catalogue de démonstration Phase 3 (consigne §0, §25-27) — outils RÉELS,
déterministes, sandboxés dans un répertoire workspace dédié (jamais un chemin
arbitraire du système réel). Ce ne sont PAS des mocks : ils lisent/écrivent
réellement des fichiers, réussissent ou échouent réellement.

- `filesystem.write_file` / `filesystem.read_file` : preuve d'exécution réelle
  (création/lecture de fichier vérifiable après coup).
- `demo.always_fail` : chemin d'échec déterministe pour tester recovery.
- `demo.idempotent_counter` : idempotent=True — un retry avec la même
  idempotency_key ne double jamais l'effet (déduplication explicite).
- `demo.non_idempotent_append` : idempotent=False — chaque appel ajoute
  réellement une ligne, sert à prouver qu'un retry aveugle NE DOIT PAS
  se produire après un état UNKNOWN (RAYA_V2_TECHNICAL_ARCHITECTURE.md §14.3).
"""

from __future__ import annotations

import json
from pathlib import Path

from raya.contracts import ErrorInfo, PermissionLevel, Tool, ToolCall, ToolResult, ToolResultStatus
from raya.tools.registry import ToolRegistry


def _resolve_safe_path(workspace_root: Path, rel_path: str) -> Path:
    workspace_resolved = workspace_root.resolve()
    candidate = (workspace_resolved / rel_path).resolve()
    try:
        candidate.relative_to(workspace_resolved)
    except ValueError:
        raise ValueError(f"chemin hors du workspace sandboxé : {rel_path!r}") from None
    return candidate


def _make_write_file(workspace_root: Path):
    def handler(call: ToolCall) -> ToolResult:
        try:
            path = _resolve_safe_path(workspace_root, call.arguments["path"])
        except (KeyError, ValueError) as exc:
            return ToolResult(
                tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                error=ErrorInfo(code="INVALID_ARGUMENT", message=str(exc)),
            )
        content = call.arguments.get("content", "")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return ToolResult(
            tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
            output={"path": str(path.relative_to(workspace_root.resolve()))},
            evidence={"bytes_written": len(content.encode("utf-8")), "path_exists": path.exists()},
        )

    return handler


def _make_read_file(workspace_root: Path):
    def handler(call: ToolCall) -> ToolResult:
        try:
            path = _resolve_safe_path(workspace_root, call.arguments["path"])
        except (KeyError, ValueError) as exc:
            return ToolResult(
                tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                error=ErrorInfo(code="INVALID_ARGUMENT", message=str(exc)),
            )
        if not path.exists():
            return ToolResult(
                tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                error=ErrorInfo(code="FILE_NOT_FOUND", message=f"{call.arguments['path']!r} n'existe pas"),
            )
        content = path.read_text(encoding="utf-8")
        return ToolResult(
            tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
            output={"content": content}, evidence={"bytes_read": len(content.encode("utf-8"))},
        )

    return handler


def _always_fail(call: ToolCall) -> ToolResult:
    return ToolResult(
        tool_call_id=call.id, status=ToolResultStatus.FAILURE,
        error=ErrorInfo(code="DEMO_DETERMINISTIC_FAILURE", message="échec déterministe (outil de démonstration)", retryable=False),
    )


def _make_idempotent_counter(workspace_root: Path):
    """Déduplique explicitement par idempotency_key : rejouer la MÊME clé ne
    ré-incrémente jamais — c'est la preuve concrète du contrat `idempotent=True`."""

    def handler(call: ToolCall) -> ToolResult:
        state_path = workspace_root.resolve() / "_demo_counters.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
        applied_keys = state.setdefault("applied_idempotency_keys", {})

        if call.idempotency_key in applied_keys:
            return ToolResult(
                tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
                output={"counter": applied_keys[call.idempotency_key], "deduplicated": True},
                evidence={"idempotency_key": call.idempotency_key},
            )

        counter = state.get("counter", 0) + 1
        state["counter"] = counter
        applied_keys[call.idempotency_key] = counter
        state_path.write_text(json.dumps(state), encoding="utf-8")
        return ToolResult(
            tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
            output={"counter": counter, "deduplicated": False},
            evidence={"idempotency_key": call.idempotency_key},
        )

    return handler


def _make_non_idempotent_append(workspace_root: Path):
    def handler(call: ToolCall) -> ToolResult:
        log_path = workspace_root.resolve() / "_demo_append_log.txt"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        text = call.arguments.get("text", "")
        with log_path.open("a", encoding="utf-8") as f:
            f.write(text + "\n")
        line_count = sum(1 for _ in log_path.open(encoding="utf-8"))
        return ToolResult(
            tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
            output={"appended": text}, evidence={"line_count_after": line_count},
        )

    return handler


def register_demo_tools(registry: ToolRegistry, workspace_root: Path) -> None:
    workspace_root.mkdir(parents=True, exist_ok=True)

    registry.register(
        Tool(
            name="filesystem.write_file", description="Écrit (crée/remplace) un fichier texte dans le workspace.",
            capability_tags=["filesystem"],
            input_schema={"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]},
            output_schema={"type": "object", "properties": {"path": {"type": "string"}}},
            permission_level=PermissionLevel.SENSITIVE, idempotent=True, requires_device=None,
        ),
        _make_write_file(workspace_root),
    )
    registry.register(
        Tool(
            name="filesystem.read_file", description="Lit le contenu d'un fichier texte du workspace.",
            capability_tags=["filesystem"],
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
            output_schema={"type": "object", "properties": {"content": {"type": "string"}}},
            permission_level=PermissionLevel.SAFE, idempotent=True, requires_device=None,
        ),
        _make_read_file(workspace_root),
    )
    registry.register(
        Tool(
            name="demo.always_fail", description="Échoue toujours de façon déterministe (test de recovery).",
            capability_tags=["demo"], input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {}},
            permission_level=PermissionLevel.SAFE, idempotent=False, requires_device=None, retryable=False,
        ),
        _always_fail,
    )
    registry.register(
        Tool(
            name="demo.idempotent_counter", description="Incrémente un compteur — un retry avec la même clé ne double jamais l'effet.",
            capability_tags=["demo"], input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object", "properties": {"counter": {"type": "integer"}}},
            permission_level=PermissionLevel.SENSITIVE, idempotent=True, requires_device=None,
        ),
        _make_idempotent_counter(workspace_root),
    )
    registry.register(
        Tool(
            name="demo.non_idempotent_append", description="Ajoute une ligne à un journal — CHAQUE appel a un effet, jamais dédupliqué.",
            capability_tags=["demo"],
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            output_schema={"type": "object", "properties": {"appended": {"type": "string"}}},
            permission_level=PermissionLevel.SENSITIVE, idempotent=False, requires_device=None,
        ),
        _make_non_idempotent_append(workspace_root),
    )
