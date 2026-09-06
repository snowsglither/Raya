"""Déclarations de Tools délégant au PhoneLinkDeviceAgent (Chantier 13,
Phone Integration MVP) — même pattern que tools/catalog/pc.py : chaque
handler construit une `Command` déjà décidée et l'exécute via le VRAI
Device Agent, c'est ici, et SEULEMENT ici, que `tools/` a accès à `safety`
(`should_stop`) pour le transmettre à `device.execute()` (devices/ ne peut
pas importer raya.safety).

Le modèle ne connaît JAMAIS Phone Link/Bluetooth/UI Automation — seulement
ces capacités structurées (contacts.lookup -> call.dial_contact, etc.),
conformément à la consigne §0/§6 ("Le Core doit demander des CAPABILITIES").

Classification de risque (voir raya/safety/risk.py) :
  phone.call        SENSITIVE — initier un appel a des conséquences réelles (dérange un tiers)
  phone.sms         SENSITIVE — envoie du contenu réel à un tiers (même famille que mail.send)
  phone.answer      SENSITIVE — engager une conversation réelle, décision à conséquence
  phone.control     SAFE      — terminer/refuser un appel déjà en cours (même raisonnement
                                 que tasks.cancel : "arrêter/décliner quelque chose" reste
                                 fondamentalement réversible, jamais une nouvelle conséquence)
  phone.read        SAFE      — lecture seule (état d'appel/connexion, recherche de contact)
"""

from __future__ import annotations

from typing import Callable

from raya.contracts import (
    Command,
    CommandStatus,
    PermissionLevel,
    Tool,
    ToolCall,
    ToolResult,
    ToolResultStatus,
)
from raya.devices import DeviceAgent, ShouldStop
from raya.devices.ios import DEVICE_ID as PHONE_DEVICE_ID

_STATUS_MAP = {
    CommandStatus.SUCCESS: ToolResultStatus.SUCCESS,
    CommandStatus.FAILURE: ToolResultStatus.FAILURE,
    CommandStatus.TIMEOUT: ToolResultStatus.TIMEOUT,
    CommandStatus.CANCELLED: ToolResultStatus.CANCELLED,
}


def _run(agent: DeviceAgent, capability_name: str, should_stop: ShouldStop, call: ToolCall) -> ToolResult:
    command = Command(device_id=PHONE_DEVICE_ID, capability_name=capability_name,
                       arguments=call.arguments, correlation_id=call.correlation_id, timeout_ms=call.timeout_ms)
    result = agent.execute(command, should_stop=should_stop)
    return ToolResult(
        tool_call_id=call.id, status=_STATUS_MAP[result.status], output=result.output,
        evidence=result.evidence, error=result.error,
    )


def _tool(name: str, capability_name: str, description: str, input_schema: dict,
          permission_level: PermissionLevel, tag: str, idempotent: bool = False) -> Tool:
    return Tool(name=name, description=description, capability_tags=[tag], input_schema=input_schema,
                output_schema={"type": "object"}, permission_level=permission_level,
                idempotent=idempotent, requires_device=PHONE_DEVICE_ID)


def register_phone_tools(registry, phone_agent: DeviceAgent, should_stop: ShouldStop) -> None:
    _defs = [
        ("phone.contacts.lookup", "contacts.lookup",
         "Cherche un ou plusieurs contacts par nom dans les contacts RÉELS du téléphone connecté. "
         "Retourne toutes les correspondances (nom, numéro, label) — si plus d'une, demande à "
         "l'utilisateur laquelle il veut avant d'appeler/écrire. N'invente JAMAIS un numéro.",
         {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
         PermissionLevel.SAFE, "phone.read", True),
        ("phone.call.number", "call.dial_number",
         "Appelle un numéro de téléphone EXPLICITE (déjà connu ou fourni par l'utilisateur). "
         "N'utilise PAS ceci pour appeler 'quelqu'un par son nom' — utilise phone.contacts.lookup "
         "puis phone.call.contact à la place.",
         {"type": "object", "properties": {"number": {"type": "string"}}, "required": ["number"]},
         PermissionLevel.SENSITIVE, "phone.call", False),
        ("phone.call.contact", "call.dial_contact",
         "Appelle un contact déjà identifié SANS AMBIGUÏTÉ par phone.contacts.lookup — `name` doit "
         "être EXACTEMENT le nom retourné par phone.contacts.lookup (incluant numéro/label s'il y "
         "figurait). Si plusieurs contacts correspondaient, demande d'abord lequel à l'utilisateur.",
         {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
         PermissionLevel.SENSITIVE, "phone.call", False),
        ("phone.call.end", "call.end",
         "Termine l'appel téléphonique actuellement en cours. Vérifie l'état réel après coup — "
         "ne répond jamais 'appel terminé' sans cette vérification.",
         {"type": "object", "properties": {}}, PermissionLevel.SAFE, "phone.control", False),
        ("phone.call.answer", "call.answer",
         "Répond à un appel entrant EXPLICITEMENT demandé par l'utilisateur (ex: 'réponds à l'appel "
         "de Paul'). N'appelle JAMAIS ceci de ta propre initiative à la simple observation d'un "
         "appel entrant — seulement sur demande explicite.",
         {"type": "object", "properties": {}}, PermissionLevel.SENSITIVE, "phone.answer", False),
        ("phone.call.reject", "call.reject",
         "Refuse/ignore un appel entrant, uniquement sur demande explicite de l'utilisateur.",
         {"type": "object", "properties": {}}, PermissionLevel.SAFE, "phone.control", False),
        ("phone.call.state", "call.state",
         "Lit l'état RÉEL de l'appel en cours (en communication ou non) — jamais une affirmation "
         "sans cette vérification.",
         {"type": "object", "properties": {}}, PermissionLevel.SAFE, "phone.read", True),
        ("phone.sms.send", "sms.send",
         "Envoie un SMS à un destinataire déjà résolu SANS AMBIGUÏTÉ (nom exact retourné par "
         "phone.contacts.lookup, ou numéro explicite). N'envoie JAMAIS si plusieurs contacts "
         "correspondaient sans confirmation préalable de l'utilisateur.",
         {"type": "object", "properties": {"recipient": {"type": "string"}, "message": {"type": "string"}}, "required": ["recipient", "message"]},
         PermissionLevel.SENSITIVE, "phone.sms", False),
        ("phone.connection.state", "connection.state",
         "Vérifie si un téléphone est actuellement connecté et lequel.",
         {"type": "object", "properties": {}}, PermissionLevel.SAFE, "phone.read", True),
    ]
    for name, capability_name, description, schema, level, tag, idem in _defs:
        tool = _tool(name, capability_name, description, schema, level, tag, idem)
        handler: Callable[[ToolCall], ToolResult] = _make_handler(phone_agent, capability_name, should_stop)
        registry.register(tool, handler)


def _make_handler(agent: DeviceAgent, capability_name: str, should_stop: ShouldStop):
    def handler(call: ToolCall) -> ToolResult:
        return _run(agent, capability_name, should_stop, call)

    return handler
