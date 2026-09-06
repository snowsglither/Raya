"""Déclarations de Tools pour l'ADAPTIVE UI (RAYA V2 Phase 6, consigne
ADAPTIVE UI : "The UI must respond to actual user intent through the existing
Harness. The UI must NOT interpret arbitrary business commands itself.").

Sans ce Tool, la seule façon pour le Cockpit de savoir QUAND révéler une vue
contextuelle (Tasks/World/Browser/Computer) serait de faire du pattern
matching côté frontend sur le texte de la réponse — exactement ce que la
consigne interdit. Ici, c'est la cognition (le modèle, via la boucle
agentique normale) qui décide d'ouvrir/fermer une vue, EXACTEMENT comme
n'importe quelle autre capacité (Tool Discovery -> Safety -> exécution) —
l'UI ne fait qu'écouter l'event résultant (raya/interfaces/ui/events.py).

Effet de bord = publication d'un Event sur l'EventBus, jamais une mutation de
World State/Task (une vue ouverte/fermée n'est pas un fait sur le monde) —
risque SAFE (aucune conséquence physique, purement présentationnel)."""

from __future__ import annotations

from typing import Callable

from raya.contracts import ErrorInfo, Event, PermissionLevel, Tool, ToolCall, ToolResult, ToolResultStatus
from raya.event_bus import EventBus

_VALID_VIEWS = ("conversation", "tasks", "world", "browser", "computer", "attention", "spatial")


def _make_view_handler(bus: EventBus, action: str):
    def handler(call: ToolCall) -> ToolResult:
        view = call.arguments.get("view")
        if view != "all" and view not in _VALID_VIEWS:
            return ToolResult(
                tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                error=ErrorInfo(code="INVALID_VIEW", message=f"vue inconnue : {view!r} (attendu: {_VALID_VIEWS} ou 'all')", retryable=False),
            )
        session_id = call.requested_by.session_id
        bus.publish(Event(
            type="ui.view_requested", source="tools", correlation_id=call.correlation_id,
            payload={"session_id": session_id, "view": view, "action": action},
        ))
        return ToolResult(
            tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
            output={"view": view, "action": action}, evidence={"session_id": session_id},
        )

    return handler


def register_ui_view_tools(registry, bus: EventBus) -> None:
    registry.register(
        Tool(
            name="ui.show_view",
            description=(
                "Révèle une vue contextuelle du Cockpit (tasks, world, browser, computer, "
                "attention, conversation) quand l'utilisateur demande explicitement de voir "
                "quelque chose (ex: 'montre-moi mes tâches', 'montre ce que tu fais dans le "
                "navigateur'). N'affecte jamais l'état réel du système — purement présentationnel."
            ),
            capability_tags=["ui.presentation"],
            input_schema={"type": "object", "properties": {"view": {"type": "string", "enum": list(_VALID_VIEWS)}}, "required": ["view"]},
            output_schema={"type": "object", "properties": {"view": {"type": "string"}, "action": {"type": "string"}}},
            permission_level=PermissionLevel.SAFE, idempotent=True, requires_device=None,
        ),
        _make_view_handler(bus, "show"),
    )
    registry.register(
        Tool(
            name="ui.hide_view",
            description=(
                "Referme une vue contextuelle du Cockpit déjà ouverte (ou 'all' pour revenir à "
                "l'état minimal) quand l'utilisateur demande explicitement de la cacher/fermer."
            ),
            capability_tags=["ui.presentation"],
            input_schema={"type": "object", "properties": {"view": {"type": "string", "enum": [*_VALID_VIEWS, "all"]}}, "required": ["view"]},
            output_schema={"type": "object", "properties": {"view": {"type": "string"}, "action": {"type": "string"}}},
            permission_level=PermissionLevel.SAFE, idempotent=True, requires_device=None,
        ),
        _make_view_handler(bus, "hide"),
    )
