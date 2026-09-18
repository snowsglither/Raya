"""Déclarations de Tools pour le SUIVI D'INTERACTIONS EXTERNES (Chantier 20).

Une interaction externe (message envoyé à une tierce personne, texte traduit
à partager, demande avec réponse attendue) n'est PAS terminée dès l'envoi.
`interaction.track` enregistre l'état AWAITING_EXTERNAL_REPLY en World State ;
`interaction.reply` le met à jour quand la réponse arrive.

Le World State est persisté (SQLite) → survie au restart.
Le World State est partagé → cross-interface (Cockpit/Telegram/voix).
Le Context Engine inclut tous les faits actifs → le modèle voit les
interactions en attente dans son contexte sans changement d'assembler.

JAMAIS : ConversationManager, InteractionManager, second orchestrateur."""

from __future__ import annotations

from raya.contracts import (
    Confidence,
    ErrorInfo,
    PermissionLevel,
    Tool,
    ToolCall,
    ToolResult,
    ToolResultStatus,
    WorldStateFact,
    to_dict,
    utc_now_iso,
)
from raya.contracts.interaction import ExternalInteraction, ExternalInteractionState

_INTERACTION_DOMAIN = "interaction"
_INTERACTION_TTL_S = 86_400  # 24h — expiration naturelle


def _session_id(call: ToolCall) -> str:
    return call.requested_by.session_id if call.requested_by else "unknown"


def _make_track_handler(world_state: WorldStateStore):
    def handler(call: ToolCall) -> ToolResult:
        args = call.arguments
        interlocutor = (args.get("interlocutor") or "").strip()
        outgoing_message = (args.get("outgoing_message") or "").strip()
        if not interlocutor:
            return ToolResult(
                tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                error=ErrorInfo(code="MISSING_ARGUMENT", message="interlocutor requis", retryable=False),
            )
        if not outgoing_message:
            return ToolResult(
                tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                error=ErrorInfo(code="MISSING_ARGUMENT", message="outgoing_message requis", retryable=False),
            )
        interaction = ExternalInteraction(
            interlocutor=interlocutor,
            channel=args.get("channel", "") or "unknown",
            outgoing_message=outgoing_message,
            original_request=args.get("original_request", "") or "",
            source_language=args.get("source_language", "") or "",
            target_language=args.get("target_language", "") or "",
            expected_reply=args.get("expected_reply", "") or "",
            owner_session_id=_session_id(call),
        )
        fact = WorldStateFact(
            domain=_INTERACTION_DOMAIN,
            key=interaction.id,
            value=to_dict(interaction),
            source=f"interaction.track:{_session_id(call)}",
            confidence=Confidence.KNOWN_FACT,
            freshness_ttl_s=_INTERACTION_TTL_S,
        )
        world_state.apply_update(fact)
        return ToolResult(
            tool_call_id=call.id,
            status=ToolResultStatus.SUCCESS,
            output={
                "interaction_id": interaction.id,
                "state": interaction.state,
                "interlocutor": interaction.interlocutor,
                "channel": interaction.channel,
            },
        )
    return handler


def _make_reply_handler(world_state: WorldStateStore):
    def handler(call: ToolCall) -> ToolResult:
        args = call.arguments
        interaction_id = (args.get("interaction_id") or "").strip()
        reply_text = (args.get("reply_text") or "").strip()
        if not interaction_id:
            return ToolResult(
                tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                error=ErrorInfo(code="MISSING_ARGUMENT", message="interaction_id requis", retryable=False),
            )
        fact = world_state.retrieve_fact(_INTERACTION_DOMAIN, interaction_id)
        if fact is None:
            return ToolResult(
                tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                error=ErrorInfo(
                    code="INTERACTION_NOT_FOUND",
                    message=f"Interaction {interaction_id!r} introuvable",
                    retryable=False,
                ),
            )
        value = dict(fact.value) if isinstance(fact.value, dict) else {}
        value["state"] = ExternalInteractionState.REPLIED.value
        value["reply_text"] = reply_text
        value["last_activity"] = utc_now_iso()
        world_state.apply_update(WorldStateFact(
            domain=fact.domain,
            key=fact.key,
            value=value,
            source=f"interaction.reply:{_session_id(call)}",
            confidence=Confidence.KNOWN_FACT,
            freshness_ttl_s=fact.freshness_ttl_s,
        ))
        return ToolResult(
            tool_call_id=call.id,
            status=ToolResultStatus.SUCCESS,
            output={
                "interaction_id": interaction_id,
                "state": ExternalInteractionState.REPLIED.value,
                "interlocutor": value.get("interlocutor", ""),
                "reply_text": reply_text,
            },
        )
    return handler


def register_interaction_tools(registry, world_state) -> None:
    registry.register(
        Tool(
            name="interaction.track",
            description=(
                "Enregistre une interaction externe CONTINUE — à appeler après avoir envoyé "
                "un message, partagé une traduction, ou effectué toute action pour laquelle "
                "une RÉPONSE D'UN TIERS EST ATTENDUE. L'envoi n'est PAS l'objectif accompli : "
                "interaction.track crée un état AWAITING_EXTERNAL_REPLY visible dans le contexte "
                "futur, permettant de résoudre automatiquement les pronoms ('il', 'elle', 'ils') "
                "quand la réponse arrivera sans redemander 'de qui parles-tu ?'. "
                "Ne pas appeler pour des actions purement locales (écrire un fichier, ouvrir "
                "une app) — uniquement quand la complétion dépend d'une entité externe."
            ),
            capability_tags=["interaction.track"],
            input_schema={
                "type": "object",
                "properties": {
                    "interlocutor": {
                        "type": "string",
                        "description": "Qui reçoit ou va répondre (ex: 'mon frère', 'Marie', 'le service client')",
                    },
                    "channel": {
                        "type": "string",
                        "description": "Canal utilisé (ex: 'google_translate', 'whatsapp', 'telegram', 'email', 'sms')",
                    },
                    "outgoing_message": {
                        "type": "string",
                        "description": "Message envoyé / texte transmis",
                    },
                    "original_request": {
                        "type": "string",
                        "description": "Demande originale de l'utilisateur",
                    },
                    "source_language": {
                        "type": "string",
                        "description": "Langue source si traduction",
                    },
                    "target_language": {
                        "type": "string",
                        "description": "Langue cible si traduction",
                    },
                    "expected_reply": {
                        "type": "string",
                        "description": "Nature de la réponse attendue (ex: 'oui/non', 'disponibilité', 'confirmation')",
                    },
                },
                "required": ["interlocutor", "outgoing_message"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "interaction_id": {"type": "string"},
                    "state": {"type": "string"},
                    "interlocutor": {"type": "string"},
                    "channel": {"type": "string"},
                },
            },
            permission_level=PermissionLevel.SAFE,
            idempotent=False,
            requires_device=None,
        ),
        _make_track_handler(world_state),
    )
    registry.register(
        Tool(
            name="interaction.reply",
            description=(
                "Enregistre la réponse reçue pour une interaction externe "
                "(AWAITING_EXTERNAL_REPLY → REPLIED). À appeler quand l'utilisateur rapporte "
                "une réponse d'une personne ou d'un service externe concernant une interaction "
                "précédemment trackée. L'interaction_id est fourni par interaction.track et visible "
                "dans le contexte (Pending external interaction). Si plusieurs interactions sont "
                "en attente et que la référence est ambiguë, demander laquelle est concernée "
                "avant d'appeler — jamais deviner arbitrairement."
            ),
            capability_tags=["interaction.reply"],
            input_schema={
                "type": "object",
                "properties": {
                    "interaction_id": {
                        "type": "string",
                        "description": "ID de l'interaction (fourni par interaction.track)",
                    },
                    "reply_text": {
                        "type": "string",
                        "description": "Texte de la réponse reçue",
                    },
                },
                "required": ["interaction_id", "reply_text"],
            },
            output_schema={
                "type": "object",
                "properties": {
                    "interaction_id": {"type": "string"},
                    "state": {"type": "string"},
                    "interlocutor": {"type": "string"},
                    "reply_text": {"type": "string"},
                },
            },
            permission_level=PermissionLevel.SAFE,
            idempotent=True,
            requires_device=None,
        ),
        _make_reply_handler(world_state),
    )
