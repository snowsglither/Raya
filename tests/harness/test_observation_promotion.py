"""Device Agent -> Tool -> Harness -> World State (RAYA_V2 Phase 7 §4-9) —
bout-en-bout avec un Tool factice porteur d'un `ObservationSpec` réel, jamais
un `if tool_name == ...` dans le Harness (vérifié en §13/architecture).
Le modèle reste scripté (FakeScriptedProvider) ; Tools/Safety/WorldState/
Cognition restent 100% réels, même discipline que test_agentic_loop.py."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import (  # noqa: E402
    Channel,
    Confidence,
    ContentPart,
    ErrorInfo,
    FinishReason,
    HarnessRequest,
    InterfaceInput,
    ModelResponse,
    ObservationSpec,
    PermissionLevel,
    RequestedToolCall,
    Tool,
    ToolResult,
    ToolResultStatus,
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


def _register_fake_launch_tool(handles, *, resulting_window: str, expected_argument: str = "target"):
    spec = ObservationSpec(domain="pc", key="active_window", evidence_field="window",
                            expected_argument=expected_argument, freshness_ttl_s=60)
    handles.tools.register(
        Tool(name="test.app_launch", description="lance une appli factice", capability_tags=["utils"],
             input_schema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
             output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE, observation=(spec,)),
        lambda call: ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
                                 output={"target": call.arguments["target"]}, evidence={"window": resulting_window}),
    )


def test_successful_tool_call_promotes_observation_into_world_state(tmp_path):
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("test.app_launch", {"target": "notepad"}), _text_response("fait."),
    ])
    try:
        _register_fake_launch_tool(handles, resulting_window="Notepad - Untitled")
        handles.harness.handle_request(_req("ouvre notepad"))
        fact = handles.world_state.get_fact("pc", "active_window")
        assert fact is not None
        assert fact.value == "Notepad - Untitled"
        assert fact.source == "tool:test.app_launch"
        assert fact.confidence == Confidence.KNOWN_FACT
    finally:
        handles.shutdown()


def test_observation_never_promoted_on_tool_failure(tmp_path):
    spec = ObservationSpec(domain="pc", key="active_window", evidence_field="window", expected_argument="target")

    def failing_handler(call):
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE, error=ErrorInfo(code="X", message="échec"))

    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("test.app_launch", {"target": "notepad"}), _text_response("échec."),
    ])
    try:
        handles.tools.register(
            Tool(name="test.app_launch", description="d", capability_tags=["utils"],
                 input_schema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
                 output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE, observation=(spec,)),
            failing_handler,
        )
        handles.harness.handle_request(_req("ouvre notepad"))
        assert handles.world_state.get_fact("pc", "active_window") is None
    finally:
        handles.shutdown()


def test_matching_observation_verifies_success_and_completes_turn(tmp_path):
    """target='notepad' attendu, fenêtre observée 'Notepad - Untitled' ->
    correspondance générique (sous-chaîne, insensible à la casse) -> SUCCESS,
    le tour se termine normalement (pas d'escalade)."""
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("test.app_launch", {"target": "notepad"}), _text_response("Notepad est ouvert."),
    ])
    try:
        _register_fake_launch_tool(handles, resulting_window="Notepad - Untitled")
        state = handles.harness.handle_request(_req("ouvre notepad"))
        from raya.contracts import HarnessStatus

        assert state.status == HarnessStatus.COMPLETED
        assert handles.harness.response_text("s1") == "Notepad est ouvert."
    finally:
        handles.shutdown()


def test_mismatched_observation_is_incoherence_triggers_recovery_not_silent_success(tmp_path):
    """LE scénario central de la consigne §8-9 : le modèle demande 'ouvre
    youtube', le Tool réussit SANS exception, mais l'état observé (une AUTRE
    fenêtre/URL) ne correspond pas -> jamais un succès silencieux. Le modèle
    revoit le VRAI résultat divergent au tour suivant (pas son intention)."""
    script = [
        _tool_call_response("test.app_launch", {"target": "youtube"}),
        _text_response("Je n'ai pas réussi à ouvrir YouTube, une autre page s'est affichée."),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        _register_fake_launch_tool(handles, resulting_window="Some Unrelated App")
        handles.harness.handle_request(_req("ouvre youtube"))
        # Le 2e appel modèle a bien reçu le résultat DIVERGENT, jamais "succès".
        second_call = fake.calls[1]
        tool_messages = [m for m in second_call.messages if m.role == "tool"]
        assert len(tool_messages) == 1
        assert '"verification": "FAILURE"' in tool_messages[0].content[0].value
    finally:
        handles.shutdown()


def test_repeated_incoherence_escalates_via_existing_loop_detector(tmp_path):
    """Consigne §9 : une incohérence RÉPÉTÉE (même appel, même divergence)
    doit déclencher l'escalade existante (LoopDetector), jamais un nouvel
    orchestrateur — la même mécanique déjà prouvée pour un échec technique
    (test_agentic_loop.py::test_repeated_identical_tool_failure_escalates)
    fonctionne ici pour une incohérence de CONTENU, pas seulement un statut
    d'erreur brut."""
    script = [_tool_call_response("test.app_launch", {"target": "youtube"}) for _ in range(10)]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=10)
    try:
        _register_fake_launch_tool(handles, resulting_window="Some Unrelated App")
        handles.harness.handle_request(_req("ouvre youtube, encore et encore"))
        response = handles.harness.response_text("s1")
        assert "prétendre avoir terminé" in response
        assert len(fake.calls) < 10  # escalade bien avant d'épuiser les itérations
    finally:
        handles.shutdown()


def test_no_expected_argument_means_no_content_verification_only_promotion(tmp_path):
    """Un ObservationSpec sans expected_argument (ex: un futur capteur
    purement informatif) promeut quand même l'observation, sans jamais
    dégrader un SUCCESS d'exécution en FAILURE/UNKNOWN de contenu."""
    spec = ObservationSpec(domain="pc", key="active_window", evidence_field="window")  # pas d'expected_argument
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("test.app_launch", {"target": "peu importe"}), _text_response("fait."),
    ])
    try:
        handles.tools.register(
            Tool(name="test.app_launch", description="d", capability_tags=["utils"],
                 input_schema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
                 output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE, observation=(spec,)),
            lambda call: ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output={}, evidence={"window": "Anything"}),
        )
        from raya.contracts import HarnessStatus

        state = handles.harness.handle_request(_req("fais quelque chose"))
        assert state.status == HarnessStatus.COMPLETED
        assert handles.world_state.get_fact("pc", "active_window").value == "Anything"
    finally:
        handles.shutdown()


def test_tool_without_observation_spec_never_touches_world_state(tmp_path):
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("filesystem.write_file", {"path": "a.txt", "content": "x"}), _text_response("fait."),
    ])
    try:
        handles.harness.handle_request(_req("écris un fichier"))
        assert handles.world_state.all() == []
    finally:
        handles.shutdown()


# --- Chantier 12 §C (Living Environment Awareness) : key_from_argument ---

def test_key_from_argument_indexes_fact_by_the_resolved_name(tmp_path):
    """Domaine à faits MULTIPLES (ex: 'filesystem', un fait par dossier connu)
    — contrairement à pc.active_window (un seul fait possible), la clé réelle
    vient de l'argument du ToolCall, jamais d'une valeur fixe du spec."""
    spec = ObservationSpec(domain="filesystem", key="unused", evidence_field="path",
                            key_from_argument="name", confidence=Confidence.INFERRED)
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("test.open_folder", {"path": "C:/Users/x/Projets", "name": "Projets"}),
        _text_response("ouvert."),
    ])
    try:
        handles.tools.register(
            Tool(name="test.open_folder", description="d", capability_tags=["utils"],
                 input_schema={"type": "object", "properties": {"path": {"type": "string"}, "name": {"type": "string"}}, "required": ["path", "name"]},
                 output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE, observation=(spec,)),
            lambda call: ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
                                     output={"path": call.arguments["path"]}),
        )
        handles.harness.handle_request(_req("ouvre le dossier Projets"))
        fact = handles.world_state.get_fact("filesystem", "projets")  # normalisé en minuscules
        assert fact is not None
        assert fact.value == "C:/Users/x/Projets"
        assert fact.confidence == Confidence.INFERRED
        # Le spec.key littéral ("unused") ne doit JAMAIS être utilisé quand
        # key_from_argument est renseigné.
        assert handles.world_state.get_fact("filesystem", "unused") is None
    finally:
        handles.shutdown()


def test_key_from_argument_skips_promotion_when_argument_missing(tmp_path):
    """Un ToolResult SUCCESS sans l'argument attendu ne doit jamais produire
    un fait à clé vide/invalide — rien d'exploitable, rien de promu."""
    spec = ObservationSpec(domain="filesystem", key="unused", evidence_field="path", key_from_argument="name")
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("test.open_folder_no_name", {"path": "C:/x"}), _text_response("fait."),
    ])
    try:
        handles.tools.register(
            Tool(name="test.open_folder_no_name", description="d", capability_tags=["utils"],
                 input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                 output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE, observation=(spec,)),
            lambda call: ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output={"path": call.arguments["path"]}),
        )
        handles.harness.handle_request(_req("fais quelque chose"))
        assert handles.world_state.all() == []
    finally:
        handles.shutdown()


def test_observation_carries_freshness_ttl_from_spec(tmp_path):
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("test.app_launch", {"target": "notepad"}), _text_response("fait."),
    ])
    try:
        _register_fake_launch_tool(handles, resulting_window="Notepad")
        handles.harness.handle_request(_req("ouvre notepad"))
        fact = handles.world_state.get_fact("pc", "active_window")
        assert fact.freshness_ttl_s == 60  # défini par l'ObservationSpec du test helper
    finally:
        handles.shutdown()
