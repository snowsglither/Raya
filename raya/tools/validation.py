"""Validation de schéma (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.10).

Phase 0 : validation JSON-Schema-lite (uniquement "required"), suffisante
pour prouver le mécanisme sans dépendance externe. Une vraie librairie
JSON Schema pourra remplacer ceci en Phase 3 sans changer le contrat Tool.
"""

from __future__ import annotations

from raya.contracts import Tool, ToolCall


class ValidationError(ValueError):
    pass


def validate_call(tool: Tool, call: ToolCall) -> None:
    required = tool.input_schema.get("required", [])
    missing = [k for k in required if k not in call.arguments]
    if missing:
        raise ValidationError(
            f"ToolCall pour {tool.name!r} : arguments requis manquants {missing}"
        )
