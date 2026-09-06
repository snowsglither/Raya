"""Bout-en-bout : le Context assemblé atteint RÉELLEMENT le ModelRequest
envoyé au Model Layer (le bug central de la stabilisation pré-Phase 7).
FakeScriptedProvider capture les VRAIS ModelRequest — on inspecte
`fake.calls[i].messages` directement, jamais une supposition sur ce qui a
dû être envoyé."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import (  # noqa: E402
    Channel,
    ChannelScope,
    Confidence,
    ContentPart,
    FinishReason,
    HarnessRequest,
    InterfaceInput,
    MemoryEntry,
    MemoryLayer,
    MemoryLifecycle,
    MemoryType,
    ModelResponse,
)


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _req(text: str, session_id: str = "s1", channel: Channel = Channel.CLI) -> HarnessRequest:
    return HarnessRequest(channel=channel, session_id=session_id, input=InterfaceInput(text=text))


def _system_text(call) -> str:
    system_messages = [m for m in call.messages if m.role == "system"]
    assert len(system_messages) == 1, "exactement un message système attendu"
    return "".join(p.value for p in system_messages[0].content if p.type == "text")


def test_real_model_request_carries_a_system_message_at_all(tmp_path):
    """LE bug central : avant le correctif, ModelRequest.messages ne
    contenait JAMAIS de message role=system — le Context assemblé était
    entièrement jeté."""
    handles, fake = build_test_harness(tmp_path, [_text_response("bonjour")])
    try:
        handles.harness.handle_request(_req("salut"))
        assert any(m.role == "system" for m in fake.calls[0].messages)
    finally:
        handles.shutdown()


def test_real_model_request_system_message_names_the_assistant(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("bonjour")])
    try:
        handles.harness.handle_request(_req("salut"))
        assert "RAYA" in _system_text(fake.calls[0])
    finally:
        handles.shutdown()


def test_real_model_request_exposes_whatever_describe_active_actually_returns(tmp_path):
    """Le texte système doit refléter EXACTEMENT ce que describe_active()
    retourne pour ce registry réel — jamais un nom générique/halluciné, et
    jamais désynchronisé de la vraie fonction de sélection (peu importe quel
    provider precis gagne dans ce fixture de test, cf. raya.models.describe_active)."""
    from raya.contracts import ModelCapability
    from raya.models import describe_active

    handles, fake = build_test_harness(tmp_path, [_text_response("bonjour")])
    try:
        handles.harness.handle_request(_req("salut"))
        text = _system_text(fake.calls[0])
        descriptor = describe_active(handles.models, ModelCapability.REASONING)
        assert descriptor is not None
        assert descriptor.id in text
        assert descriptor.provider in text
    finally:
        handles.shutdown()


def test_real_model_request_includes_confirmed_personal_memory_when_relevant(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("bonjour")])
    try:
        handles.memory.write(MemoryEntry(
            type=MemoryType.FACT, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.SHARED,
            content="Nom complet : Ruben Lukusa", provenance="profile_migration:identity",
            confidence=Confidence.KNOWN_FACT, lifecycle=MemoryLifecycle.CONFIRMED,
        ))
        handles.harness.handle_request(_req("Qui suis-je ?"))
        assert "Ruben Lukusa" in _system_text(fake.calls[0])
    finally:
        handles.shutdown()


def test_real_model_request_excludes_irrelevant_personal_memory(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("bonjour")])
    try:
        handles.memory.write(MemoryEntry(
            type=MemoryType.FACT, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.SHARED,
            content="GUITARE : joue depuis un an", provenance="profile_migration:centres_d_interet",
            confidence=Confidence.KNOWN_FACT, lifecycle=MemoryLifecycle.CONFIRMED,
        ))
        handles.harness.handle_request(_req("Quel temps fait-il ?"))
        assert "GUITARE" not in _system_text(fake.calls[0])
    finally:
        handles.shutdown()


def test_real_model_request_never_dumps_tool_schemas_twice(tmp_path):
    """Les tool schemas partent déjà via ModelRequest.available_tools —
    jamais dupliqués en texte dans le message système."""
    handles, fake = build_test_harness(tmp_path, [_text_response("bonjour")])
    try:
        handles.harness.handle_request(_req("salut"))
        call = fake.calls[0]
        if call.available_tools:
            tool_name = call.available_tools[0]["name"]
            assert tool_name not in _system_text(call)
    finally:
        handles.shutdown()


def test_channel_isolation_end_to_end_voice_fact_not_in_cli_request(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("un"), _text_response("deux")])
    try:
        handles.memory.write(MemoryEntry(
            type=MemoryType.FACT, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.VOICE,
            content="secret vocal uniquement", provenance="profile_migration:identity",
            confidence=Confidence.KNOWN_FACT, lifecycle=MemoryLifecycle.CONFIRMED,
        ))
        handles.harness.handle_request(_req("salut", channel=Channel.CLI))
        assert "secret vocal" not in _system_text(fake.calls[0])
    finally:
        handles.shutdown()


def test_no_credential_like_content_ever_injected_by_construction(tmp_path):
    """Aucune donnée de type secret/API key n'est jamais écrite comme
    MemoryEntry PERSONAL par le pipeline normal — seul un provenance explicite
    'profile_migration:*' ou une écriture explicite d'interface alimente
    Memory (raya/harness/loop.py::handle_request écrit uniquement le texte
    utilisateur, jamais une variable d'environnement/secret)."""
    handles, fake = build_test_harness(tmp_path, [_text_response("bonjour")])
    try:
        handles.harness.handle_request(_req("mon mot de passe est hunter2"))
        # Le texte utilisateur brut est bien mémorisé (comportement existant,
        # layer=CONVERSATION) mais n'est jamais promu en fait d'identité
        # PERSONAL "toujours inclus" — seul profile_migration:identity l'est.
        text = _system_text(fake.calls[0])
        assert "hunter2" not in text
    finally:
        handles.shutdown()
