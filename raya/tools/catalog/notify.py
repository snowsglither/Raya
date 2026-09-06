"""Déclarations de Tools pour l'ENVOI PROACTIF DE MESSAGE (RAYA V2 Phase 11,
addendum "Telegram outbound capability") : capacité GÉNÉRIQUE invocable
depuis N'IMPORTE QUELLE interface (Cockpit, voix, Telegram lui-même) quand
l'utilisateur demande explicitement "envoie-moi ça sur Telegram" — jamais un
bypass codé directement dans `interfaces/telegram/` (interdit explicitement
par la consigne), jamais un envoi automatique sans demande explicite ou
notification déjà prévue par le Task System (§28/§19, inchangé).

Injection de dépendance étroite (`NotifyOps`, même pattern que
`TaskControlOps` dans tasks.py) : `tools/` ne peut pas importer
`raya.interfaces.telegram` (subsystem strictement au-dessus dans le graphe de
dépendance, RAYA_V2_REPOSITORY_STRUCTURE.md §20) — un unique callable
`send_telegram` est construit et injecté par
`raya/runtime/entrypoints/web.py::_maybe_start_telegram()`, SEULEMENT quand
Telegram est réellement démarré (le Tool n'est enregistré dans AUCUN autre
cas — pas de faux espoir si Telegram est BLOCKED/NOT_CONFIGURED, cf.
RAYA_V2_ARCHITECTURAL_INVARIANTS "never fake state")."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from raya.contracts import ErrorInfo, PermissionLevel, Tool, ToolCall, ToolResult, ToolResultStatus


@dataclass
class NotifyOps:
    # Callable[[texte], succès] — résout lui-même le destinataire (dernier
    # chat_id connu, cf. `TelegramChannel.send_proactive`) ; retourne False
    # (jamais une exception) si aucun chat n'est encore connu.
    send_telegram: Callable[[str], bool]


def _make_telegram_handler(ops: NotifyOps):
    def handler(call: ToolCall) -> ToolResult:
        text = (call.arguments.get("text") or "").strip()
        if not text:
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                               error=ErrorInfo(code="MISSING_ARGUMENT", message="text requis", retryable=False))
        try:
            sent = ops.send_telegram(text)
        except Exception as exc:  # jamais planter tools/execution.py pour une panne réseau/API
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                               error=ErrorInfo(code="TELEGRAM_SEND_FAILED", message=str(exc), retryable=True))
        if not sent:
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                               error=ErrorInfo(code="NO_KNOWN_TELEGRAM_CHAT",
                                                message=(
                                                    "aucun chat Telegram identifié pour l'instant (ni message récent, "
                                                    "ni propriétaire unique déterminable) — l'utilisateur doit "
                                                    "envoyer un message une fois au bot Telegram depuis son "
                                                    "téléphone pour que RAYA sache où répondre, puis redemander"
                                                ),
                                                retryable=False))
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output={"sent": True})

    return handler


def register_notify_tools(registry, ops: NotifyOps) -> None:
    registry.register(
        Tool(
            name="telegram.send_message",
            description=(
                "Envoie un message texte au propriétaire via Telegram, quand il le demande "
                "explicitement (ex: 'envoie-moi ça sur Telegram', 'préviens-moi par Telegram "
                "quand...'). Utilisable depuis N'IMPORTE QUELLE interface (Cockpit, voix, "
                "Telegram lui-même) — pas seulement depuis une conversation Telegram en cours. "
                "N'envoie JAMAIS de message de sa propre initiative sans demande explicite."
            ),
            capability_tags=["notify.telegram"],
            input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
            output_schema={"type": "object", "properties": {"sent": {"type": "boolean"}}},
            permission_level=PermissionLevel.SAFE, idempotent=False, requires_device=None,
        ),
        _make_telegram_handler(ops),
    )
