"""Tool `system.time.now` (Chantier 12 §A) — seule source de vérité
temporelle exposée à Cognition. Le modèle DOIT appeler ce Tool avant
d'interpréter une expression temporelle relative ("demain", "dans 3
minutes", "à 22h") plutôt que d'inventer l'heure actuelle — la primitive
réelle (`raya.contracts.clock.now_local`) lit l'horloge OS, jamais la
mémoire/le contexte/une hypothèse du modèle."""

from __future__ import annotations

from raya.contracts import PermissionLevel, Tool, ToolCall, ToolResult, ToolResultStatus, local_time_to_dict, now_local
from raya.contracts.clock import DEFAULT_TIMEZONE


def _make_handler(default_timezone: str):
    def handler(call: ToolCall) -> ToolResult:
        tz_name = (call.arguments.get("timezone") or "").strip() or default_timezone
        current = now_local(tz_name)
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output=local_time_to_dict(current))

    return handler


def register_system_time_tool(registry, default_timezone: str = DEFAULT_TIMEZONE) -> None:
    registry.register(
        Tool(
            name="system.time.now",
            description=(
                "Retourne la date/heure réelle actuelle (horloge système), structurée : date, "
                "heure, jour de semaine, année, fuseau, offset UTC. Fuseau utilisateur par défaut : "
                "Europe/Brussels. À utiliser AVANT d'interpréter une expression temporelle relative "
                "(aujourd'hui, demain, ce soir, dans 3 minutes, à 22h) ou de construire un `run_at` "
                "absolu pour tasks.create — ne jamais inventer l'heure actuelle."
            ),
            capability_tags=["system.read"],
            input_schema={"type": "object", "properties": {"timezone": {"type": "string"}}, "required": []},
            output_schema={"type": "object"},
            permission_level=PermissionLevel.SAFE,
            idempotent=True,
        ),
        _make_handler(default_timezone),
    )
