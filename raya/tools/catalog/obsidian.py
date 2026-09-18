"""Outils Obsidian — lecture seule du vault (MVP).

Architecture :
- Lecture directe des fichiers .md du vault (pas de DB parallèle).
- SAFE : lecture pure, aucune écriture automatique.
- Obsidian vault = source documentaire humaine, jamais une seconde
  source de vérité opérationnelle (ça reste SQLite).
- Vault path : RAYA_DATA_DIR/vault/ ou RAYA_OBSIDIAN_VAULT_DIR.
- Pas d'écriture automatique — le modèle ne peut pas écrire dans
  Obsidian sans une confirmation explicite (pas encore implémenté).

Pas de ObsidianManager : deux fonctions simples, path injecté.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from raya.contracts import (
    ErrorInfo,
    PermissionLevel,
    Tool,
    ToolCall,
    ToolResult,
    ToolResultStatus,
)
from raya.tools.registry import ToolRegistry

_VAULT_DIR: Path | None = None


def _make_list_notes_handler(vault_dir: Path):
    def handler(call: ToolCall) -> ToolResult:
        folder_arg = (call.arguments or {}).get("folder", "")
        base = vault_dir / folder_arg if folder_arg else vault_dir
        if not base.exists():
            return ToolResult(
                tool_call_id=call.id,
                status=ToolResultStatus.FAILURE,
                error=ErrorInfo(code="VAULT_FOLDER_NOT_FOUND", message=f"Dossier Obsidian introuvable : {folder_arg!r}"),
            )
        notes = []
        for root, dirs, files in os.walk(base):
            # Ignore hidden directories (.obsidian, .trash, etc.)
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for f in files:
                if f.endswith(".md"):
                    rel = Path(root, f).relative_to(vault_dir)
                    notes.append(str(rel).replace("\\", "/"))
        notes.sort()
        return ToolResult(
            tool_call_id=call.id,
            status=ToolResultStatus.SUCCESS,
            output={"vault": str(vault_dir), "folder": folder_arg or "", "notes": notes, "count": len(notes)},
        )
    return handler


def _make_read_note_handler(vault_dir: Path):
    def handler(call: ToolCall) -> ToolResult:
        note_path_arg = (call.arguments or {}).get("path", "").strip()
        if not note_path_arg:
            return ToolResult(
                tool_call_id=call.id,
                status=ToolResultStatus.FAILURE,
                error=ErrorInfo(code="MISSING_PATH", message="Le paramètre 'path' est requis."),
            )
        # Normalize: add .md if missing
        if not note_path_arg.endswith(".md"):
            note_path_arg = note_path_arg + ".md"
        note_path = (vault_dir / note_path_arg).resolve()
        # Security: ensure path is within vault (no directory traversal)
        try:
            note_path.relative_to(vault_dir.resolve())
        except ValueError:
            return ToolResult(
                tool_call_id=call.id,
                status=ToolResultStatus.FAILURE,
                error=ErrorInfo(code="VAULT_PATH_OUTSIDE", message="Le chemin demandé est en dehors du vault."),
            )
        if not note_path.exists():
            return ToolResult(
                tool_call_id=call.id,
                status=ToolResultStatus.FAILURE,
                error=ErrorInfo(code="NOTE_NOT_FOUND", message=f"Note introuvable : {note_path_arg!r}"),
            )
        try:
            content = note_path.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult(
                tool_call_id=call.id,
                status=ToolResultStatus.FAILURE,
                error=ErrorInfo(code="READ_ERROR", message=str(exc)),
            )
        return ToolResult(
            tool_call_id=call.id,
            status=ToolResultStatus.SUCCESS,
            output={"path": note_path_arg, "content": content, "size_chars": len(content)},
        )
    return handler


def register_obsidian_tools(
    registry: ToolRegistry,
    vault_dir: Path | None,
    should_stop: Callable[[], bool],
) -> None:
    """Enregistre les outils Obsidian si vault_dir existe.

    Best-effort : si vault_dir est None ou n'existe pas, aucun outil
    n'est enregistré (dégradation honnête).
    """
    if vault_dir is None or not vault_dir.exists():
        return

    list_tool = Tool(
        name="obsidian.list_notes",
        description=(
            "Liste les notes Obsidian disponibles dans le vault (ou un sous-dossier). "
            "Retourne les chemins relatifs de tous les fichiers .md."
        ),
        capability_tags=["obsidian.read"],
        input_schema={
            "type": "object",
            "properties": {"folder": {"type": "string", "description": "Sous-dossier à lister (ex: 'Projets'). Vide = tout le vault."}},
        },
        output_schema={"type": "object"},
        permission_level=PermissionLevel.SAFE,
        idempotent=True,
    )
    registry.register(list_tool, _make_list_notes_handler(vault_dir))

    read_tool = Tool(
        name="obsidian.read_note",
        description=(
            "Lit le contenu d'une note Obsidian par son chemin relatif dans le vault "
            "(ex: 'Projets/RAYA.md'). Retourne le texte Markdown brut."
        ),
        capability_tags=["obsidian.read"],
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Chemin relatif de la note dans le vault (ex: 'Journal/2026-09-01.md')."}},
            "required": ["path"],
        },
        output_schema={"type": "object"},
        permission_level=PermissionLevel.SAFE,
        idempotent=True,
    )
    registry.register(read_tool, _make_read_note_handler(vault_dir))
