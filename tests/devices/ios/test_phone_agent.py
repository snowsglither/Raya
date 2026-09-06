"""PhoneLinkDeviceAgent (Chantier 13, Phone Integration MVP) — dispatch réel
vers mechanisms/phone_link.py, monkeypatché ici pour un test déterministe
(pas de vrai Phone Link) ; la validation RÉELLE contre Phone Link est
documentée dans le rapport (appels/SMS réels effectués en conditions
contrôlées, avec consentement explicite)."""

from __future__ import annotations

from raya.contracts import Command, CommandStatus, DeviceStatus, DeviceType
from raya.devices.ios import DEVICE_ID, PhoneLinkDeviceAgent
from raya.devices.ios import agent as agent_module


def _command(capability_name: str, arguments: dict | None = None) -> Command:
    return Command(device_id=DEVICE_ID, capability_name=capability_name, arguments=arguments or {}, correlation_id="c1")


def test_lists_all_documented_capabilities():
    agent = PhoneLinkDeviceAgent()
    names = {c.name for c in agent.list_capabilities()}
    assert names == {
        "call.dial_number", "call.dial_contact", "call.end", "call.answer",
        "call.reject", "call.state", "contacts.lookup", "sms.send", "connection.state",
    }


def test_health_reflects_real_connection_state(monkeypatch):
    agent = PhoneLinkDeviceAgent()
    monkeypatch.setattr(agent_module.phone_link, "connection_state",
                         lambda: {"status": "ok", "connected": True})
    assert agent.health().status == DeviceStatus.ONLINE

    monkeypatch.setattr(agent_module.phone_link, "connection_state",
                         lambda: {"status": "ok", "connected": False, "reason": "pairing_onboarding_screen"})
    health = agent.health()
    assert health.status == DeviceStatus.OFFLINE
    assert health.detail == "pairing_onboarding_screen"


def test_unknown_capability_fails_honestly():
    agent = PhoneLinkDeviceAgent()
    result = agent.execute(_command("call.teleport"))
    assert result.status == CommandStatus.FAILURE
    assert result.error.code == "UNKNOWN_CAPABILITY"


def test_should_stop_cancels_before_execution():
    agent = PhoneLinkDeviceAgent()
    result = agent.execute(_command("call.dial_number", {"number": "0467808448"}), should_stop=lambda: True)
    assert result.status == CommandStatus.CANCELLED


def test_dial_number_success_maps_to_result(monkeypatch):
    agent = PhoneLinkDeviceAgent()
    monkeypatch.setattr(agent_module.phone_link, "dial_number",
                         lambda number: {"status": "ok", "number": number, "call_state": {"in_call": True}})
    result = agent.execute(_command("call.dial_number", {"number": "0467808448"}))
    assert result.status == CommandStatus.SUCCESS
    assert result.output["number"] == "0467808448"


def test_permission_not_granted_is_a_distinct_failure_code(monkeypatch):
    """La distinction 'permission côté iPhone non accordée' vs un échec
    technique générique — observée en réel Chantier 13 (ErrorTakeoverPanel)."""
    agent = PhoneLinkDeviceAgent()
    monkeypatch.setattr(agent_module.phone_link, "send_sms",
                         lambda recipient, message: {"status": "blocked", "reason": "permission_not_granted_on_phone"})
    result = agent.execute(_command("sms.send", {"recipient": "Christopher", "message": "test"}))
    assert result.status == CommandStatus.FAILURE
    assert result.error.code == "PERMISSION_NOT_GRANTED_ON_PHONE"


def test_answer_call_fails_honestly_when_mechanism_reports_unconfirmed(monkeypatch):
    """phone.answer n'a jamais été confirmé sur un appel entrant réel
    (voir rapport) — le mécanisme échoue honnêtement, jamais un faux succès."""
    agent = PhoneLinkDeviceAgent()
    monkeypatch.setattr(agent_module.phone_link, "answer_call",
                         lambda: {"status": "error", "reason": "aucun bouton de réponse reconnu (BLOCKED — non confirmé en réel)"})
    result = agent.execute(_command("call.answer"))
    assert result.status == CommandStatus.FAILURE
    assert result.error.code == "ANSWER_FAILED"


def test_device_exception_never_crashes_execute(monkeypatch):
    agent = PhoneLinkDeviceAgent()

    def boom(number):
        raise RuntimeError("uia timeout")

    monkeypatch.setattr(agent_module.phone_link, "dial_number", boom)
    result = agent.execute(_command("call.dial_number", {"number": "123"}))
    assert result.status == CommandStatus.FAILURE
    assert result.error.code == "DEVICE_EXCEPTION"


def test_device_id_matches_registry_convention():
    assert DEVICE_ID == "phone_agent"
