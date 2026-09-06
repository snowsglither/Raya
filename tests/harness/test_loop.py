"""Priorité D — Harness/CLI : démarrage, transmission InterfaceRequest -> Harness."""

from __future__ import annotations

from raya.contracts import Channel, HarnessRequest, HarnessStatus, InterfaceInput
from raya.harness import steer
from raya.runtime.bootstrap import bootstrap


def _make_handles():
    return bootstrap()


def test_handle_request_fails_honestly_with_null_provider_stub():
    """Phase 3 : sans vrai provider configuré, le tour échoue explicitement
    (FAILED + message clair) plutôt que de prétendre avoir répondu — jamais
    d'erreur masquée (consigne Phase 3 §5, §9)."""
    handles = _make_handles()
    try:
        req = HarnessRequest(channel=Channel.CLI, session_id="s1", input=InterfaceInput(text="ouvre Chrome"))
        state = handles.harness.handle_request(req)
        assert state.status == HarnessStatus.FAILED
        assert state.error is not None
        response = handles.harness.response_text("s1")
        assert "erreur modèle" in response
    finally:
        handles.shutdown()


def test_two_turns_same_session_increments_turn():
    handles = _make_handles()
    try:
        req = HarnessRequest(channel=Channel.CLI, session_id="s1", input=InterfaceInput(text="salut"))
        s1 = handles.harness.handle_request(req)
        turn_after_first = s1.current_turn  # HarnessState est le même objet muté en place, capturer avant
        s2 = handles.harness.handle_request(req)
        assert s2.current_turn == turn_after_first + 1
        assert s2.session_id == s1.session_id
    finally:
        handles.shutdown()


def test_conversation_recorded_in_memory():
    handles = _make_handles()
    try:
        req = HarnessRequest(channel=Channel.CLI, session_id="s1", input=InterfaceInput(text="mémorise ceci"))
        handles.harness.handle_request(req)
        from raya.contracts import ChannelScope

        # Phase 11 (contexte continuité) : la réponse de RAYA est désormais
        # AUSSI persistée en mémoire CONVERSATION (avant, seul le message
        # utilisateur l'était) — le nombre de hits n'est donc plus figé à 1,
        # mais le message utilisateur original doit toujours être retrouvable.
        hits = handles.memory.search(query="mémorise", channel_scope=ChannelScope.CHAT)
        assert len(hits) >= 1
        assert any(h.content == "mémorise ceci" for h in hits)
    finally:
        handles.shutdown()


def test_steering_is_honest_not_implemented_stub():
    handles = _make_handles()
    try:
        req = HarnessRequest(channel=Channel.CLI, session_id="s1", input=InterfaceInput(text="x"))
        state = handles.harness.handle_request(req)
        error = steer(state, "annule ce que tu fais")
        assert error.code == "NOT_IMPLEMENTED"
    finally:
        handles.shutdown()
