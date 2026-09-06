"""Confirmation Safety réelle (RAYA V2 Phase 6 CONFIRMATION UI).

Avant cette phase, une action SENSITIVE/DESTRUCTIVE échouait silencieusement
avec PERMISSION_DENIED sans jamais pouvoir être reprise — aucune interface ne
pouvait réellement demander/obtenir une confirmation utilisateur. Ces tests
vérifient le chemin complet : Harness pause honnêtement le tour
(AWAITING_USER_INPUT + pending_confirmation + event), puis confirm_pending()
route la décision exclusivement via tools/execution.py -> Safety (jamais
d'auto-approbation UI)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import (  # noqa: E402
    Channel,
    ContentPart,
    Event,
    FinishReason,
    HarnessRequest,
    HarnessStatus,
    InterfaceInput,
    ModelResponse,
    RequestedToolCall,
)


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


def _req(text: str, session_id: str = "s1") -> HarnessRequest:
    return HarnessRequest(channel=Channel.CLI, session_id=session_id, input=InterfaceInput(text=text))


def test_sensitive_tool_call_pauses_turn_awaiting_confirmation(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_tool_call_response("demo.idempotent_counter", {})])
    try:
        state = handles.harness.handle_request(_req("incrémente le compteur"))
        assert state.status == HarnessStatus.AWAITING_USER_INPUT
        assert state.pending_confirmation is not None
        assert state.pending_confirmation["tool_name"] == "demo.idempotent_counter"
        assert "confirmation" in handles.harness.response_text("s1")
        # Le modèle n'a été appelé qu'une fois (la boucle s'arrête, ne recommence pas)
        assert len(fake.calls) == 1
    finally:
        handles.shutdown()


def test_confirmation_required_event_published(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_tool_call_response("demo.idempotent_counter", {})])
    try:
        received = []
        handles.bus.subscribe("harness.confirmation_required", lambda e: received.append(e), subscriber="test")
        handles.harness.handle_request(_req("incrémente"))
        handles.bus.wait_idle(timeout_s=1.0)
        assert len(received) == 1
        assert received[0].payload["tool_name"] == "demo.idempotent_counter"
        assert received[0].payload["session_id"] == "s1"
    finally:
        handles.shutdown()


def test_confirm_pending_approved_actually_executes_the_tool(tmp_path):
    """approved=True doit RÉELLEMENT exécuter l'action (pas juste changer un
    statut) — vérifié par un effet de bord réel (fichier workspace modifié).
    Phase 11 (§4, "plus de ToolResult JSON brut exposé") : la réponse après
    confirmation doit être la phrase naturelle produite par
    `_natural_response_for_tool_result` (une reformulation modèle du
    ToolResult), jamais le JSON structuré lui-même."""
    script = [_tool_call_response("demo.idempotent_counter", {}), _text_response("C'est fait, compteur incrémenté.")]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        handles.harness.handle_request(_req("incrémente"))
        state = handles.harness.confirm_pending("s1", approved=True)
        assert state.status == HarnessStatus.COMPLETED
        assert state.pending_confirmation is None
        response = handles.harness.response_text("s1")
        assert response == "C'est fait, compteur incrémenté."
        assert '"status"' not in response and "tool_call_id" not in response
        state_file = tmp_path / "workspace" / "_demo_counters.json"
        assert state_file.exists()
    finally:
        handles.shutdown()


def test_confirm_pending_denied_never_executes(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_tool_call_response("demo.idempotent_counter", {})])
    try:
        handles.harness.handle_request(_req("incrémente"))
        state = handles.harness.confirm_pending("s1", approved=False)
        assert state.status == HarnessStatus.COMPLETED
        assert state.pending_confirmation is None
        assert "je n'exécute pas" in handles.harness.response_text("s1")
        state_file = tmp_path / "workspace" / "_demo_counters.json"
        assert not state_file.exists()
    finally:
        handles.shutdown()


def test_confirmation_resolved_event_published_on_both_outcomes(tmp_path):
    for approved in (True, False):
        # Le 2e élément n'est consommé que pour approved=True (confirm_pending
        # y fait un appel modèle supplémentaire, Phase 11 §4) ; inoffensif et
        # jamais consommé pour approved=False (FakeScriptedProvider tolère un
        # script non entièrement épuisé).
        script = [_tool_call_response("demo.idempotent_counter", {}), _text_response("fait.")]
        handles, fake = build_test_harness(tmp_path, script, db_name=f"test_{approved}.sqlite3")
        try:
            received = []
            handles.bus.subscribe("harness.confirmation_resolved", lambda e: received.append(e), subscriber="test")
            handles.harness.handle_request(_req("incrémente"))
            handles.harness.confirm_pending("s1", approved=approved)
            handles.bus.wait_idle(timeout_s=1.0)
            assert len(received) == 1
            assert received[0].payload["approved"] == approved
        finally:
            handles.shutdown()


def test_confirm_pending_without_any_pending_raises(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("bonjour")])
    try:
        handles.harness.handle_request(_req("salut"))
        try:
            handles.harness.confirm_pending("s1", approved=True)
            assert False, "devait lever ValueError"
        except ValueError:
            pass
    finally:
        handles.shutdown()


def test_safe_tool_never_requires_confirmation(tmp_path):
    """Non-régression : une action SAFE (ex: filesystem.write_file, tag
    'filesystem') continue de s'exécuter directement, sans jamais passer par
    AWAITING_USER_INPUT."""
    script = [
        _tool_call_response("filesystem.write_file", {"path": "out.txt", "content": "hello"}),
        _text_response("fait."),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        state = handles.harness.handle_request(_req("écris un fichier"))
        assert state.status == HarnessStatus.COMPLETED
        assert state.pending_confirmation is None
    finally:
        handles.shutdown()


def test_session_state_exposes_pending_confirmation_read_only(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_tool_call_response("demo.idempotent_counter", {})])
    try:
        assert handles.harness.session_state("s1") is None
        handles.harness.handle_request(_req("incrémente"))
        state = handles.harness.session_state("s1")
        assert state is not None
        assert state.status == HarnessStatus.AWAITING_USER_INPUT
        assert state.pending_confirmation["tool_name"] == "demo.idempotent_counter"
    finally:
        handles.shutdown()
