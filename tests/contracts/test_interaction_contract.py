"""Chantier 20 — ExternalInteraction contract (raya/contracts/interaction.py).
Tests de contrat pur — zéro dépendance sur le runtime ou les tools."""

from __future__ import annotations

from raya.contracts import (
    ACTIVE_INTERACTION_STATES,
    ExternalInteraction,
    ExternalInteractionState,
    from_dict,
    to_dict,
)


def test_external_interaction_state_values():
    """Tous les états attendus sont présents et corrects."""
    assert ExternalInteractionState.AWAITING_EXTERNAL_REPLY.value == "AWAITING_EXTERNAL_REPLY"
    assert ExternalInteractionState.REPLIED.value == "REPLIED"
    assert ExternalInteractionState.COMPLETED.value == "COMPLETED"
    assert ExternalInteractionState.CANCELLED.value == "CANCELLED"


def test_active_interaction_states_contains_only_awaiting():
    """Seul AWAITING_EXTERNAL_REPLY est un état actif — REPLIED/COMPLETED/CANCELLED ne le sont pas."""
    assert ExternalInteractionState.AWAITING_EXTERNAL_REPLY in ACTIVE_INTERACTION_STATES
    assert ExternalInteractionState.REPLIED not in ACTIVE_INTERACTION_STATES
    assert ExternalInteractionState.COMPLETED not in ACTIVE_INTERACTION_STATES
    assert ExternalInteractionState.CANCELLED not in ACTIVE_INTERACTION_STATES


def test_external_interaction_default_state_is_awaiting():
    """Un ExternalInteraction nouvellement créé est en AWAITING_EXTERNAL_REPLY."""
    i = ExternalInteraction(interlocutor="mon frère", channel="google_translate", outgoing_message="Est-ce que tu viens ?")
    assert i.state == ExternalInteractionState.AWAITING_EXTERNAL_REPLY.value


def test_external_interaction_id_auto_generated():
    """L'ID est généré automatiquement et préfixé 'inter_'."""
    i = ExternalInteraction(interlocutor="A", channel="B", outgoing_message="C")
    assert i.id.startswith("inter_")
    i2 = ExternalInteraction(interlocutor="A", channel="B", outgoing_message="C")
    assert i.id != i2.id


def test_external_interaction_to_dict_from_dict_roundtrip():
    """to_dict / from_dict préserve tous les champs."""
    i = ExternalInteraction(
        interlocutor="Marie",
        channel="telegram",
        outgoing_message="Tu viens demain ?",
        original_request="demande à Marie",
        expected_reply="oui/non",
        owner_session_id="sess_1",
    )
    d = to_dict(i)
    assert d["interlocutor"] == "Marie"
    assert d["channel"] == "telegram"
    assert d["state"] == "AWAITING_EXTERNAL_REPLY"
    reconstructed = from_dict(ExternalInteraction, d)
    assert reconstructed.interlocutor == i.interlocutor
    assert reconstructed.channel == i.channel
    assert reconstructed.outgoing_message == i.outgoing_message
    assert reconstructed.expected_reply == i.expected_reply
    assert reconstructed.id == i.id
