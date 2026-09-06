"""Chantier 13C — Safety Test Isolation / No Real Consequential Actions.

Root cause (voir RAYA_V2_CHANTIER13C_REPORT, §1) : un script de vérification
AD HOC (pas un test pytest committé) a appelé `execute(..., user_confirmed=
True)` contre un `bootstrap()` réel — plaçant un vrai appel téléphonique.
Ce fichier est le test CANONIQUE du comportement Safety pour les Tools
téléphoniques : TOUJOURS via `FakePhoneDeviceAgent` (tests/support/
fake_phone_agent.py), JAMAIS `PhoneLinkDeviceAgent`/`raya.devices.ios.
mechanisms.phone_link` — aucune action externe réelle n'est possible ici,
structurellement, quel que soit l'argument `user_confirmed` passé."""

from __future__ import annotations

import ast
from pathlib import Path

from raya.contracts import PermissionLevel, ToolCall, ToolCallRequester, ToolResultStatus
from raya.event_bus import EventBus
from raya.safety import AuditTrail, SafetyService, StopController
from raya.tools import ToolRegistry, execute
from raya.tools.catalog import register_phone_tools

from tests.support.fake_phone_agent import FakePhoneDeviceAgent


def _setup():
    bus = EventBus()
    registry = ToolRegistry()
    fake_agent = FakePhoneDeviceAgent()
    safety = SafetyService(StopController(bus), AuditTrail())
    register_phone_tools(registry, fake_agent, should_stop=safety.should_stop)
    return registry, safety, fake_agent


def _call(name: str, arguments: dict) -> ToolCall:
    return ToolCall(tool_name=name, arguments=arguments, correlation_id="c1",
                     requested_by=ToolCallRequester(subsystem="harness", session_id="s1"))


# 1. SENSITIVE tool sans confirmation -> aucune exécution.

def test_sensitive_tool_without_confirmation_never_executes():
    registry, safety, fake_agent = _setup()
    assert registry.get("phone.call.number").permission_level == PermissionLevel.SENSITIVE
    result = execute(registry, safety, _call("phone.call.number", {"number": "0467808448"}))
    assert result.status == ToolResultStatus.PERMISSION_DENIED
    assert fake_agent.calls == []  # RIEN n'a atteint l'executor, même fake


# 2. SENSITIVE tool avec confirmation "non résolue" (valeur par défaut,
#    jamais explicitement True) -> aucune exécution.

def test_sensitive_tool_with_unresolved_confirmation_never_executes():
    registry, safety, fake_agent = _setup()
    result = execute(registry, safety, _call("phone.sms.send", {"recipient": "Christopher", "message": "salut"}),
                      user_confirmed=False)
    assert result.status == ToolResultStatus.PERMISSION_DENIED
    assert fake_agent.calls == []


# 3. SENSITIVE tool après confirmation explicite -> exécution du FAKE
#    executor uniquement (jamais un vrai mécanisme).

def test_sensitive_tool_after_explicit_confirmation_executes_fake_only():
    registry, safety, fake_agent = _setup()
    result = execute(registry, safety, _call("phone.call.contact", {"name": "Christopher"}), user_confirmed=True)
    assert result.status == ToolResultStatus.SUCCESS
    assert fake_agent.calls == [("call.dial_contact", {"name": "Christopher"})]
    assert result.output.get("fake") is True  # jamais un ToolResult qui prétendrait un vrai appel


# 4. Le vrai executor téléphonique n'est JAMAIS importé/appelé dans ce fichier
#    — vérification structurelle, pas seulement comportementale.

def test_this_test_file_never_imports_the_real_phone_mechanism():
    source = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith("raya.devices.ios"), (
                f"test de Safety téléphonique important le VRAI device : {node.module}"
            )
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("raya.devices.ios"), (
                    f"test de Safety téléphonique important le VRAI device : {alias.name}"
                )


# 5. SAFE tool conserve son comportement normal (aucune confirmation requise).

def test_safe_tool_executes_without_confirmation():
    registry, safety, fake_agent = _setup()
    assert registry.get("phone.call.end").permission_level == PermissionLevel.SAFE
    result = execute(registry, safety, _call("phone.call.end", {}))
    assert result.status == ToolResultStatus.SUCCESS
    assert fake_agent.calls == [("call.end", {})]


# 6. STOP empêche l'exécution MÊME avec confirmation.

def test_stop_blocks_execution_even_with_confirmation():
    bus = EventBus()
    registry = ToolRegistry()
    fake_agent = FakePhoneDeviceAgent()
    safety = SafetyService(StopController(bus), AuditTrail())
    register_phone_tools(registry, fake_agent, should_stop=safety.should_stop)
    safety.request_stop(source="test")

    result = execute(registry, safety, _call("phone.call.number", {"number": "0467808448"}), user_confirmed=True)
    assert result.status == ToolResultStatus.CANCELLED
    assert fake_agent.calls == []


# 7. Le même ToolCall confirmé une fois ne fait jamais l'économie de Safety
#    pour un appel SUIVANT sans confirmation — aucune "mémorisation" de
#    l'approbation précédente.

def test_prior_confirmation_never_carries_over_to_a_later_unconfirmed_call():
    registry, safety, fake_agent = _setup()
    call = _call("phone.call.number", {"number": "0467808448"})

    first = execute(registry, safety, call, user_confirmed=True)
    assert first.status == ToolResultStatus.SUCCESS

    second = execute(registry, safety, call, user_confirmed=False)
    assert second.status == ToolResultStatus.PERMISSION_DENIED
    assert len(fake_agent.calls) == 1  # toujours une seule exécution réelle (la première, confirmée)


# 8. Une fixture de test ne peut pas accidentellement utiliser le vrai
#    executor pour une action conséquente — vérifié sur le helper partagé
#    tests/support/harness_factory.py (root cause : ce helper wirait le VRAI
#    PhoneLinkDeviceAgent par défaut avant la correction de ce chantier).

def test_shared_harness_factory_never_wires_the_real_phone_device_by_default(tmp_path):
    from tests.support.harness_factory import build_test_harness

    handles, _fake_model = build_test_harness(tmp_path, [])
    try:
        assert "phone_agent" not in handles.devices.all_ids()
        assert [t.name for t in handles.tools.all() if t.name.startswith("phone.")] == []
    finally:
        handles.shutdown()


# 9. Le mécanisme de confirmation générique existant (Harness.confirm_pending,
#    déjà prouvé tool-agnostique par tests/harness/test_confirmation.py)
#    fonctionne pour un Tool téléphonique SANS jamais toucher d'action externe
#    réelle, en le raccordant explicitement au FakePhoneDeviceAgent plutôt
#    qu'au vrai device (jamais bootstrap() nu pour ce genre de test).

def test_phone_tool_integrates_with_generic_confirmation_flow_via_fake_agent():
    registry, safety, fake_agent = _setup()
    denied = execute(registry, safety, _call("phone.sms.send", {"recipient": "Christopher", "message": "salut"}))
    assert denied.status == ToolResultStatus.PERMISSION_DENIED
    assert denied.error.retryable is True  # REQUIRES_CONFIRMATION reste retryable (résolution possible ensuite)
    assert fake_agent.calls == []

    resolved = execute(registry, safety, _call("phone.sms.send", {"recipient": "Christopher", "message": "salut"}),
                        user_confirmed=True)
    assert resolved.status == ToolResultStatus.SUCCESS
    assert fake_agent.calls == [("sms.send", {"recipient": "Christopher", "message": "salut"})]
