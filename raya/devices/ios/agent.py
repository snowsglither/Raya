"""PhoneLinkDeviceAgent — implémente `DeviceAgent` (Chantier 13, Phone
Integration MVP). Pilote un iPhone connecté via Microsoft Phone Link, JAMAIS
via une API iOS privée ou un contournement Apple — le mécanisme réel est
l'UI Automation générique déjà établie pour le Windows Device Agent
(raya/devices/windows/mechanisms/), appliquée à la fenêtre
PhoneExperienceHost.exe. Phone Link reste, du point de vue du Core, une
application Windows ordinaire parmi d'autres.

`DeviceType.IOS` (contracts/device.py, réservé Phase 0 — jamais utilisé
avant cette phase) est enfin câblé ici : précisément le slot que
RAYA_V2_REPOSITORY_STRUCTURE.md §14 prévoyait ("devices/ios/... squelette").
Nommé `ios/` (et non `phone/` générique) car le mécanisme découvert est
SPÉCIFIQUEMENT celui de l'intégration iPhone de Phone Link (Bluetooth pour
les appels, permissions contacts/SMS séparées) — Android via Phone Link
expose une intégration structurellement différente (SMS/contacts natifs
sans dépendance Bluetooth) qui mériterait son propre Device Agent le jour
où elle est réellement implémentée, jamais une supposition anticipée ici.

Dispatche une `Command` déjà décidée vers le mécanisme approprié — AUCUNE
décision "quoi faire" ici, uniquement "comment le faire" (même principe que
`devices/windows/agent.py`). N'importe jamais `raya.models`/`raya.safety`/
`raya.harness`/`raya.cognition` (invariant #5, vérifié par le lint
architectural)."""

from __future__ import annotations

from raya.contracts import Capability, Command, CommandStatus, DeviceStatus, ErrorInfo, Health, Result

from ..base import DeviceAgent, ShouldStop, _never_stop
from .mechanisms import phone_link

DEVICE_ID = "phone_agent"

_CAPABILITIES = [
    Capability(name="call.dial_number", input_schema={"type": "object", "properties": {"number": {"type": "string"}}, "required": ["number"]}, mechanism_hint="phone_link_uia_dial"),
    Capability(name="call.dial_contact", input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}, mechanism_hint="phone_link_uia_dial"),
    Capability(name="call.end", input_schema={"type": "object", "properties": {}}, mechanism_hint="phone_link_uia_end_call"),
    Capability(name="call.answer", input_schema={"type": "object", "properties": {}}, mechanism_hint="phone_link_uia_answer_call"),
    Capability(name="call.reject", input_schema={"type": "object", "properties": {}}, mechanism_hint="phone_link_uia_reject_call"),
    Capability(name="call.state", input_schema={"type": "object", "properties": {}}, mechanism_hint="phone_link_uia_read"),
    Capability(name="contacts.lookup", input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, mechanism_hint="phone_link_uia_contact_search"),
    Capability(name="sms.send", input_schema={"type": "object", "properties": {"recipient": {"type": "string"}, "message": {"type": "string"}}, "required": ["recipient", "message"]}, mechanism_hint="phone_link_uia_compose"),
    Capability(name="connection.state", input_schema={"type": "object", "properties": {}}, mechanism_hint="phone_link_uia_read"),
]


class PhoneLinkDeviceAgent(DeviceAgent):
    def list_capabilities(self) -> list[Capability]:
        return list(_CAPABILITIES)

    def health(self) -> Health:
        state = phone_link.connection_state()
        if state.get("status") != "ok":
            return Health(device_id=DEVICE_ID, status=DeviceStatus.DEGRADED, detail=state.get("error"))
        if not state.get("connected"):
            return Health(device_id=DEVICE_ID, status=DeviceStatus.OFFLINE, detail=state.get("reason"))
        return Health(device_id=DEVICE_ID, status=DeviceStatus.ONLINE)

    def execute(self, command: Command, should_stop: ShouldStop = _never_stop) -> Result:
        if should_stop():
            return Result(command_id=command.id, status=CommandStatus.CANCELLED,
                           error=ErrorInfo(code="STOP_ACTIVE", message="Interrompu par STOP avant exécution", retryable=False))

        handler = _DISPATCH.get(command.capability_name)
        if handler is None:
            return Result(command_id=command.id, status=CommandStatus.FAILURE,
                           error=ErrorInfo(code="UNKNOWN_CAPABILITY", message=f"Capacité inconnue: {command.capability_name!r}", retryable=False))
        try:
            return handler(command)
        except Exception as exc:
            return Result(command_id=command.id, status=CommandStatus.FAILURE,
                           error=ErrorInfo(code="DEVICE_EXCEPTION", message=str(exc), retryable=True),
                           mechanism_used=command.capability_name)


def _ok(command: Command, output: dict, mechanism: str, evidence: dict | None = None) -> Result:
    return Result(command_id=command.id, status=CommandStatus.SUCCESS, output=output, evidence=evidence, mechanism_used=mechanism)


def _from_mechanism_result(command: Command, r: dict, mechanism: str, error_code: str) -> Result:
    """Traduit le dict {status, ...} des fonctions `mechanisms/phone_link.py`
    en `Result` — `status='blocked'` (permission non accordée côté iPhone)
    est distingué d'un `FAILURE` ordinaire via son propre code d'erreur,
    jamais confondu avec un échec technique transitoire (retryable=False
    dans les deux cas : ni l'un ni l'autre ne se résout en réessayant)."""
    if r.get("status") == "ok":
        return _ok(command, r, mechanism)
    if r.get("status") == "blocked":
        return Result(command_id=command.id, status=CommandStatus.FAILURE,
                       error=ErrorInfo(code="PERMISSION_NOT_GRANTED_ON_PHONE", message=r.get("reason", ""), retryable=False),
                       mechanism_used=mechanism)
    return Result(command_id=command.id, status=CommandStatus.FAILURE,
                   error=ErrorInfo(code=error_code, message=str(r.get("reason") or r.get("detail") or r), retryable=True),
                   mechanism_used=mechanism)


def _call_dial_number(command: Command) -> Result:
    r = phone_link.dial_number(command.arguments["number"])
    return _from_mechanism_result(command, r, "phone_link_uia_dial", "CALL_FAILED")


def _call_dial_contact(command: Command) -> Result:
    r = phone_link.dial_contact_suggestion(command.arguments["name"])
    return _from_mechanism_result(command, r, "phone_link_uia_dial", "CALL_FAILED")


def _call_end(command: Command) -> Result:
    r = phone_link.end_call()
    return _from_mechanism_result(command, r, "phone_link_uia_end_call", "END_CALL_FAILED")


def _call_answer(command: Command) -> Result:
    r = phone_link.answer_call()
    return _from_mechanism_result(command, r, "phone_link_uia_answer_call", "ANSWER_FAILED")


def _call_reject(command: Command) -> Result:
    r = phone_link.reject_call()
    return _from_mechanism_result(command, r, "phone_link_uia_reject_call", "REJECT_FAILED")


def _call_state(command: Command) -> Result:
    r = phone_link.call_state()
    return _from_mechanism_result(command, r, "phone_link_uia_read", "CALL_STATE_READ_FAILED")


def _contacts_lookup(command: Command) -> Result:
    r = phone_link.lookup_contact(command.arguments["query"])
    return _from_mechanism_result(command, r, "phone_link_uia_contact_search", "CONTACTS_LOOKUP_FAILED")


def _sms_send(command: Command) -> Result:
    r = phone_link.send_sms(command.arguments["recipient"], command.arguments["message"])
    return _from_mechanism_result(command, r, "phone_link_uia_compose", "SMS_SEND_FAILED")


def _connection_state(command: Command) -> Result:
    r = phone_link.connection_state()
    return _from_mechanism_result(command, r, "phone_link_uia_read", "CONNECTION_STATE_READ_FAILED")


_DISPATCH = {
    "call.dial_number": _call_dial_number,
    "call.dial_contact": _call_dial_contact,
    "call.end": _call_end,
    "call.answer": _call_answer,
    "call.reject": _call_reject,
    "call.state": _call_state,
    "contacts.lookup": _contacts_lookup,
    "sms.send": _sms_send,
    "connection.state": _connection_state,
}
