"""Tool `preferences.set_channel` (Chantier 12 §E) — persiste une préférence
de CANAL DE NOTIFICATION (Telegram/Email/Phone) explicitement confirmée par
l'utilisateur ("envoie-moi toujours ça par mail"), via la Memory structurée
existante (`MemoryType.PREFERENCE`) — jamais un second système de
préférences, jamais un fichier de config à part.

Injection étroite (`PreferenceOps`, même pattern que `TaskControlOps`/
`NotifyOps`) : `tools/` ne peut pas importer `raya.harness`
(RAYA_V2_REPOSITORY_STRUCTURE.md §20) — bootstrap.py construit `PreferenceOps`
à partir des méthodes publiques du Harness et l'injecte ici."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from raya.contracts import ErrorInfo, NotificationChannel, PermissionLevel, Tool, ToolCall, ToolResult, ToolResultStatus


@dataclass
class PreferenceOps:
    # (channel_for, channel, session_id) -> MemoryEntry (résolu par
    # Harness.set_channel_preference, jamais un accès Memory direct ici).
    set_channel: Callable[[str, str, str], object]


def _make_set_channel_handler(ops: PreferenceOps):
    def handler(call: ToolCall) -> ToolResult:
        channel_for = (call.arguments.get("channel_for") or "").strip().lower()
        channel = (call.arguments.get("channel") or "").strip().lower()
        if not channel_for or not channel:
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                               error=ErrorInfo(code="MISSING_ARGUMENT", message="channel_for et channel requis", retryable=False))
        try:
            NotificationChannel(channel)
        except ValueError:
            valid = ", ".join(c.value for c in NotificationChannel)
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                               error=ErrorInfo(code="UNKNOWN_CHANNEL", message=f"canal inconnu {channel!r} (valides: {valid})", retryable=False))
        session_id = call.requested_by.session_id
        entry = ops.set_channel(channel_for, channel, session_id)
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
                           output={"channel_for": channel_for, "channel": channel, "preference_id": entry.id})

    return handler


def register_preference_tools(registry, ops: PreferenceOps) -> None:
    registry.register(
        Tool(
            name="preferences.set_channel",
            description=(
                "Enregistre une préférence DURABLE de canal de notification pour un type de "
                "demande (ex: channel_for='message' -> channel='email'), quand l'utilisateur "
                "confirme explicitement qu'il veut TOUJOURS ce canal pour ce type de demande "
                "(ex: 'envoie-moi toujours ça par mail'). N'appelle ceci QUE sur confirmation "
                "explicite — jamais après une seule demande ponctuelle."
            ),
            capability_tags=["preferences.write"],
            input_schema={
                "type": "object",
                "properties": {
                    "channel_for": {"type": "string", "description": "type de demande, ex: 'message'"},
                    "channel": {"type": "string", "enum": [c.value for c in NotificationChannel]},
                },
                "required": ["channel_for", "channel"],
            },
            output_schema={"type": "object"},
            permission_level=PermissionLevel.SAFE, idempotent=False,
        ),
        _make_set_channel_handler(ops),
    )
