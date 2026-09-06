"""La vraie boucle agentique (consigne Phase 3 §0-12) : PLAN -> ACTION ->
OBSERVE -> VERIFY -> CONTINUE/REPLAN/ESCALATE. Le modèle est scripté
(FakeScriptedProvider) pour la déterminisme du contrôle de flux ; Tools/
Safety/ExecutionRecord/Persistence restent 100% réels (consigne §23/§45)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import (  # noqa: E402
    Channel,
    ContentPart,
    Confidence,
    FinishReason,
    HarnessRequest,
    HarnessStatus,
    InterfaceInput,
    Message,
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


def test_simple_response_no_tools(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("Bonjour !")])
    try:
        state = handles.harness.handle_request(_req("salut"))
        assert state.status == HarnessStatus.COMPLETED
        assert handles.harness.response_text("s1") == "Bonjour !"
        assert len(fake.calls) == 1
    finally:
        handles.shutdown()


def test_one_real_tool_call_then_final_response(tmp_path):
    """Le tool_call est RÉELLEMENT exécuté (vrai fichier créé) avant que le
    modèle ne reçoive le VRAI résultat pour formuler sa réponse finale."""
    script = [
        _tool_call_response("filesystem.write_file", {"path": "out.txt", "content": "hello"}),
        _text_response("Fichier créé avec succès."),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        state = handles.harness.handle_request(_req("crée un fichier"))
        assert state.status == HarnessStatus.COMPLETED
        assert len(fake.calls) == 2

        real_file = tmp_path / "workspace" / "out.txt"
        assert real_file.exists()
        assert real_file.read_text(encoding="utf-8") == "hello"

        trace = handles.harness.last_tool_trace("s1")
        assert len(trace) == 1
        assert trace[0]["tool_name"] == "filesystem.write_file"
        assert trace[0]["outcome"] == "SUCCESS"

        # Le DEUXIÈME appel modèle a bien reçu le VRAI résultat en message "tool".
        second_call = fake.calls[1]
        tool_messages = [m for m in second_call.messages if m.role == "tool"]
        assert len(tool_messages) == 1
        assert '"status": "success"' in tool_messages[0].content[0].value
    finally:
        handles.shutdown()


def test_execution_record_wired_to_real_tool_call(tmp_path):
    script = [
        _tool_call_response("filesystem.write_file", {"path": "rec.txt", "content": "x"}),
        _text_response("ok"),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        handles.harness.handle_request(_req("écris un fichier"))
        trace = handles.harness.last_tool_trace("s1")
        operation_ids = list(handles.execution_records._backend.query("execution_records"))
        assert len(operation_ids) == 1
        assert operation_ids[0]["execution_state"] == "COMPLETED"
    finally:
        handles.shutdown()


def test_model_error_produces_failed_state_not_fake_success(tmp_path):
    error_response = ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.ERROR,
        error=__import__("raya.contracts", fromlist=["ErrorInfo"]).ErrorInfo(code="TEST_ERROR", message="panne simulée"),
    )
    handles, fake = build_test_harness(tmp_path, [error_response])
    try:
        state = handles.harness.handle_request(_req("salut"))
        assert state.status == HarnessStatus.FAILED
        assert state.error.code == "TEST_ERROR"
        assert "TEST_ERROR" in handles.harness.response_text("s1")
    finally:
        handles.shutdown()


def test_max_iterations_exceeded_gives_honest_message_not_fake_completion(tmp_path):
    """Le modèle demande toujours un nouvel appel d'outil (jamais de réponse
    finale) -> la boucle DOIT s'arrêter et le dire, jamais prétendre avoir terminé."""
    infinite_tool_calls = [_tool_call_response("filesystem.read_file", {"path": "out.txt"}) for _ in range(10)]
    handles, fake = build_test_harness(tmp_path, infinite_tool_calls, max_tool_iterations=3)
    try:
        state = handles.harness.handle_request(_req("boucle"))
        response = handles.harness.response_text("s1")
        # Passe "Targeted Execution Repair" (§10) : le message d'arrêt est
        # désormais construit par `_explain_blocked_turn` (honnête, jamais
        # une fausse complétion) — le repli générique se termine toujours
        # par cette phrase quand aucun texte modèle exploitable n'est produit.
        assert "prétendre avoir terminé" in response
        assert len(fake.calls) <= 4  # <= 3 itérations + 1 appel d'explication
    finally:
        handles.shutdown()


def test_repeated_identical_tool_failure_escalates_not_infinite_retry(tmp_path):
    """demo.always_fail échouerait indéfiniment si rejoué aveuglément —
    LoopDetector doit escalader avant la fin des max_tool_iterations."""
    script = [_tool_call_response("filesystem.read_file", {"path": "never_exists.txt"}) for _ in range(10)]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=10)
    try:
        state = handles.harness.handle_request(_req("lis un fichier qui n'existe pas, encore et encore"))
        response = handles.harness.response_text("s1")
        assert "prétendre avoir terminé" in response
        # escalade dès la 2e tentative identique -> beaucoup moins que 10 appels modèle
        assert len(fake.calls) < 10
    finally:
        handles.shutdown()


def test_stop_active_before_loop_starts_aborts_honestly(tmp_path):
    from raya.contracts import Event

    handles, fake = build_test_harness(tmp_path, [_text_response("ne devrait jamais être atteint")])
    try:
        handles.bus.publish(Event(type="interface.stop_requested", source="interfaces.cli", payload={}))
        handles.bus.wait_idle(timeout_s=1.0)
        state = handles.harness.handle_request(_req("salut"))
        assert state.status == HarnessStatus.FAILED
        assert state.error.code == "STOP_ACTIVE"
        assert len(fake.calls) == 0  # le modèle n'a même pas été appelé
    finally:
        handles.shutdown()


def test_stop_during_tool_loop_interrupts(tmp_path):
    """STOP levé de façon SYNCHRONE par le handler du tool exécuté en 1ère
    itération (jamais via l'EventBus asynchrone, pour ne pas dépendre d'une
    course avec la livraison des events) doit empêcher le 2e appel modèle —
    checkpoint_or_abort est revérifié en tête de boucle avant chaque nouvel
    appel modèle (invariant #6, raya/harness/cancellation.py)."""
    from raya.contracts import PermissionLevel, Tool, ToolResult, ToolResultStatus

    script = [
        _tool_call_response("test.stop_trigger", {}),
        _text_response("ne devrait jamais être renvoyé à l'utilisateur"),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        def handler(call):
            handles.safety.request_stop("test")
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output={}, evidence={"stopped": True})

        handles.tools.register(
            Tool(name="test.stop_trigger", description="déclenche STOP (test)", capability_tags=["utils"],
                 input_schema={}, output_schema={}, permission_level=PermissionLevel.SAFE),
            handler,
        )

        state = handles.harness.handle_request(_req("écris puis continue"))
        assert state.status == HarnessStatus.FAILED
        assert state.error.code == "STOP_ACTIVE"
        assert len(fake.calls) == 1  # le 2e appel modèle n'a jamais eu lieu
    finally:
        handles.shutdown()


# --- TEST CRITIQUE (consigne §26) : NO CLAIM WITHOUT EVIDENCE ---

def test_critical_model_claims_success_without_tool_call_never_becomes_world_state_fact(tmp_path):
    """Le modèle AFFIRME avoir créé un fichier dans son texte, SANS jamais
    appeler filesystem.write_file. RAYA ne doit contenir AUCUNE preuve
    structurée (World State/Task/ExecutionRecord) de cette action — le texte
    du modèle n'est jamais une source de vérité pour l'état du système."""
    fake_claim = _text_response("J'ai créé le fichier secret.txt avec succès. ✅")
    handles, fake = build_test_harness(tmp_path, [fake_claim])
    try:
        state = handles.harness.handle_request(_req("crée secret.txt"))
        assert state.status == HarnessStatus.COMPLETED  # le tour se termine (réponse du modèle relayée telle quelle)

        # Aucune preuve structurée de l'action prétendue :
        assert handles.harness.last_tool_trace("s1") == []  # aucun ToolCall n'a eu lieu
        assert not (tmp_path / "workspace" / "secret.txt").exists()  # le fichier n'existe PAS réellement
        assert handles.world_state.retrieve_fact("filesystem", "secret.txt") is None
        assert len(handles.execution_records._backend.query("execution_records")) == 0
    finally:
        handles.shutdown()


def test_conversation_never_writes_world_state_from_model_text(tmp_path):
    """Généralisation du test critique : AUCUN tour de conversation, quel que
    soit son texte, ne fait jamais d'écriture World State — seules les
    méthodes explicites du Harness (set_world_fact, appelées par une
    interface ou un ToolResult vérifié) le font."""
    handles, fake = build_test_harness(tmp_path, [_text_response("Le monde est en paix, tout est terminé, succès total.")])
    try:
        handles.harness.handle_request(_req("raconte-moi n'importe quoi"))
        assert handles.world_state.all() == []
    finally:
        handles.shutdown()
