"""tools/catalog/phone.py (Chantier 13) — délégation réelle au
PhoneLinkDeviceAgent (registration, classification de risque, forme du
dispatch réel vers mechanisms/phone_link.py, monkeypatché ici pour rester
déterministe). Chantier 13C : la vérification du comportement Safety
SENSITIVE->confirmation elle-même (le point le plus sensible, puisqu'une
confirmation mal testée peut réellement exécuter l'action) vit désormais
exclusivement dans tests/tools/test_phone_safety_isolation.py, contre
`FakePhoneDeviceAgent` — structurellement incapable de toucher le vrai
mécanisme, contrairement au monkeypatch utilisé ici. Ne pas réintroduire de
test de confirmation contre le VRAI PhoneLinkDeviceAgent dans ce fichier."""

from __future__ import annotations

from raya.contracts import PermissionLevel, ToolCall, ToolCallRequester, ToolResultStatus
from raya.devices.ios import PhoneLinkDeviceAgent
from raya.devices.ios import agent as agent_module
from raya.event_bus import EventBus
from raya.safety import AuditTrail, SafetyService, StopController
from raya.safety.risk import classify_risk
from raya.tools import ToolRegistry, execute
from raya.tools.catalog import register_phone_tools


def _setup():
    bus = EventBus()
    registry = ToolRegistry()
    agent = PhoneLinkDeviceAgent()
    safety = SafetyService(StopController(bus), AuditTrail())
    register_phone_tools(registry, agent, should_stop=safety.should_stop)
    return registry, safety, agent


def _call(name: str, arguments: dict) -> ToolCall:
    return ToolCall(tool_name=name, arguments=arguments, correlation_id="c1",
                     requested_by=ToolCallRequester(subsystem="harness", session_id="s1"))


def test_all_phone_tools_registered_with_device_requirement():
    registry, safety, agent = _setup()
    names = {t.name for t in registry.all() if t.name.startswith("phone.")}
    assert names == {
        "phone.contacts.lookup", "phone.call.number", "phone.call.contact", "phone.call.end",
        "phone.call.answer", "phone.call.reject", "phone.call.state", "phone.sms.send", "phone.connection.state",
    }
    for t in registry.all():
        if t.name.startswith("phone."):
            assert t.requires_device == "phone_agent"


def test_risk_classification_matches_documented_levels():
    assert classify_risk(["phone.call"]) == PermissionLevel.SENSITIVE
    assert classify_risk(["phone.sms"]) == PermissionLevel.SENSITIVE
    assert classify_risk(["phone.answer"]) == PermissionLevel.SENSITIVE
    assert classify_risk(["phone.control"]) == PermissionLevel.SAFE
    assert classify_risk(["phone.read"]) == PermissionLevel.SAFE


def test_contacts_lookup_is_safe_and_executes_for_real(monkeypatch):
    registry, safety, agent = _setup()
    monkeypatch.setattr(agent_module.phone_link, "lookup_contact",
                         lambda query: {"status": "ok", "query": query, "matches": ["Christopher. 0484590083. Domicile."], "count": 1})
    assert registry.get("phone.contacts.lookup").permission_level == PermissionLevel.SAFE
    result = execute(registry, safety, _call("phone.contacts.lookup", {"query": "Christopher"}))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["count"] == 1


def test_call_end_is_safe_like_a_scoped_stop(monkeypatch):
    """Même raisonnement que tasks.cancel : terminer un appel déjà en cours
    est fondamentalement un arrêt, jamais une nouvelle conséquence."""
    registry, safety, agent = _setup()
    monkeypatch.setattr(agent_module.phone_link, "end_call", lambda: {"status": "ok", "call_state": {"in_call": False}})
    assert registry.get("phone.call.end").permission_level == PermissionLevel.SAFE
    result = execute(registry, safety, _call("phone.call.end", {}))
    assert result.status == ToolResultStatus.SUCCESS


