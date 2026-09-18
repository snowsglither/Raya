"""Chantier 20 — tests des handlers interaction.track et interaction.reply
(raya/tools/catalog/interaction.py). Backend InMemory — zéro I/O disque."""

from __future__ import annotations

from raya.contracts import PermissionLevel, ToolCall, ToolCallRequester, ToolResultStatus
from raya.contracts.interaction import ExternalInteractionState
from raya.event_bus import EventBus
from raya.persistence import InMemoryBackend
from raya.safety import AuditTrail, SafetyService, StopController
from raya.tools import ToolRegistry, execute
from raya.tools.catalog.interaction import register_interaction_tools
from raya.world_state import WorldStateStore


def _setup():
    backend = InMemoryBackend()
    bus = EventBus()
    world_state = WorldStateStore(backend, bus)
    safety = SafetyService(StopController(bus), AuditTrail())
    registry = ToolRegistry()
    register_interaction_tools(registry, world_state)
    return registry, safety, world_state


def _call_track(args: dict, session_id: str = "sess_test") -> ToolCall:
    return ToolCall(
        tool_name="interaction.track",
        arguments=args,
        correlation_id="c1",
        requested_by=ToolCallRequester(subsystem="harness", session_id=session_id),
    )


def _call_reply(args: dict, session_id: str = "sess_test") -> ToolCall:
    return ToolCall(
        tool_name="interaction.reply",
        arguments=args,
        correlation_id="c2",
        requested_by=ToolCallRequester(subsystem="harness", session_id=session_id),
    )


# --- Registration ---

def test_interaction_tools_are_registered_and_safe():
    registry, _, _ = _setup()
    assert registry.get("interaction.track") is not None
    assert registry.get("interaction.reply") is not None
    assert registry.get("interaction.track").permission_level == PermissionLevel.SAFE
    assert registry.get("interaction.reply").permission_level == PermissionLevel.SAFE


# --- interaction.track ---

def test_track_creates_world_state_fact_with_interaction_domain():
    registry, safety, world_state = _setup()
    result = execute(registry, safety, _call_track({
        "interlocutor": "mon frère",
        "channel": "whatsapp",
        "outgoing_message": "Est-ce que tu viens ce soir ?",
    }))
    assert result.status == ToolResultStatus.SUCCESS
    inter_id = result.output["interaction_id"]
    fact = world_state.retrieve_fact("interaction", inter_id)
    assert fact is not None
    assert fact.domain == "interaction"


def test_track_returns_awaiting_state():
    registry, safety, _ = _setup()
    result = execute(registry, safety, _call_track({
        "interlocutor": "Marie",
        "outgoing_message": "Tu viens demain ?",
    }))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["state"] == ExternalInteractionState.AWAITING_EXTERNAL_REPLY.value


def test_track_id_starts_with_inter():
    registry, safety, _ = _setup()
    result = execute(registry, safety, _call_track({
        "interlocutor": "A",
        "outgoing_message": "B",
    }))
    assert result.output["interaction_id"].startswith("inter_")


def test_track_missing_required_fields_returns_validation_error():
    registry, safety, _ = _setup()
    # interlocutor missing
    r1 = execute(registry, safety, _call_track({"outgoing_message": "Bonjour"}))
    assert r1.status == ToolResultStatus.FAILURE
    assert r1.error.code == "VALIDATION_ERROR"
    # outgoing_message missing
    r2 = execute(registry, safety, _call_track({"interlocutor": "quelqu'un"}))
    assert r2.status == ToolResultStatus.FAILURE
    assert r2.error.code == "VALIDATION_ERROR"


def test_track_multiple_interactions_are_independent():
    registry, safety, world_state = _setup()
    r1 = execute(registry, safety, _call_track({"interlocutor": "A", "outgoing_message": "msg1"}))
    r2 = execute(registry, safety, _call_track({"interlocutor": "B", "outgoing_message": "msg2"}))
    assert r1.status == r2.status == ToolResultStatus.SUCCESS
    id1, id2 = r1.output["interaction_id"], r2.output["interaction_id"]
    assert id1 != id2
    f1 = world_state.retrieve_fact("interaction", id1)
    f2 = world_state.retrieve_fact("interaction", id2)
    assert f1.value["interlocutor"] == "A"
    assert f2.value["interlocutor"] == "B"


# --- interaction.reply ---

def test_reply_updates_state_to_replied():
    registry, safety, world_state = _setup()
    track_result = execute(registry, safety, _call_track({
        "interlocutor": "Sophie",
        "outgoing_message": "Tu es disponible ?",
    }))
    inter_id = track_result.output["interaction_id"]
    reply_result = execute(registry, safety, _call_reply({
        "interaction_id": inter_id,
        "reply_text": "Oui, je suis là.",
    }))
    assert reply_result.status == ToolResultStatus.SUCCESS
    assert reply_result.output["state"] == ExternalInteractionState.REPLIED.value
    fact = world_state.retrieve_fact("interaction", inter_id)
    assert fact.value["state"] == ExternalInteractionState.REPLIED.value


def test_reply_records_reply_text_in_world_state():
    registry, safety, world_state = _setup()
    track_result = execute(registry, safety, _call_track({
        "interlocutor": "Lucas",
        "outgoing_message": "Tu viens demain ?",
    }))
    inter_id = track_result.output["interaction_id"]
    execute(registry, safety, _call_reply({
        "interaction_id": inter_id,
        "reply_text": "Non, je suis occupé.",
    }))
    fact = world_state.retrieve_fact("interaction", inter_id)
    assert fact.value["reply_text"] == "Non, je suis occupé."


def test_reply_on_unknown_id_returns_not_found():
    registry, safety, _ = _setup()
    result = execute(registry, safety, _call_reply({
        "interaction_id": "inter_does_not_exist",
        "reply_text": "Bonjour",
    }))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "INTERACTION_NOT_FOUND"


def test_reply_missing_interaction_id_returns_failure():
    registry, safety, _ = _setup()
    result = execute(registry, safety, _call_reply({"reply_text": "Oui"}))
    assert result.status == ToolResultStatus.FAILURE
    # Schema validation fires first (interaction_id is required in input_schema)
    assert result.error.code == "VALIDATION_ERROR"
