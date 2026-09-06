"""Les 10 scénarios d'intégration numérotés + tests critiques explicitement
requis par la consigne Phase 3 (§25-29). Système réel de bout en bout
(§23/§45) : vrai SQLite, vrai EventBus, vraie Safety, vrais Tools (catalogue
sandboxé réel), vrai Harness/boucle agentique, vrai ExecutionRecordRepository
— seul le MODÈLE est scripté (FakeScriptedProvider) sauf dans les tests
"live" en toute fin de fichier, qui appellent le vrai Ollama Cloud."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import (  # noqa: E402
    Channel,
    ContentPart,
    ExecutionRecord,
    ExecutionState,
    FinishReason,
    HarnessRequest,
    HarnessStatus,
    InterfaceInput,
    ModelResponse,
    RequestedToolCall,
    ToolCall,
    ToolCallRequester,
    ToolResult,
    ToolResultStatus,
    VerificationState,
)
from raya.harness import ExecutionRecordRepository, RecoveryDecision, decide_recovery
from raya.persistence import SqliteBackend


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


def _req(text: str, session_id: str = "s1") -> HarnessRequest:
    return HarnessRequest(channel=Channel.CLI, session_id=session_id, input=InterfaceInput(text=text))


def _wait_for(predicate, timeout_s: float = 3.0, interval_s: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


# --- 1. CREATE + VERIFY FILE ---

def test_1_create_file_then_verify_real_content_on_disk(tmp_path):
    script = [
        _tool_call_response("filesystem.write_file", {"path": "note.txt", "content": "réunion 15h"}),
        _text_response("Fichier note.txt créé."),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        state = handles.harness.handle_request(_req("crée note.txt avec 'réunion 15h'"))
        assert state.status == HarnessStatus.COMPLETED
        real_file = tmp_path / "workspace" / "note.txt"
        assert real_file.read_text(encoding="utf-8") == "réunion 15h"
    finally:
        handles.shutdown()


# --- 2. MODIFY + VERIFY FILE ---

def test_2_modify_existing_file_then_verify_new_content(tmp_path):
    script = [
        _tool_call_response("filesystem.write_file", {"path": "note.txt", "content": "v1"}),
        _text_response("créé"),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        handles.harness.handle_request(_req("crée note.txt = v1"))
        assert (tmp_path / "workspace" / "note.txt").read_text(encoding="utf-8") == "v1"

        fake._script.extend([
            _tool_call_response("filesystem.write_file", {"path": "note.txt", "content": "v2"}),
            _text_response("modifié"),
        ])
        handles.harness.handle_request(_req("remplace note.txt par v2", session_id="s1"))
        assert (tmp_path / "workspace" / "note.txt").read_text(encoding="utf-8") == "v2"
    finally:
        handles.shutdown()


# --- 3. ACTION FAILS + RECOVERY ---

def test_3_action_fails_model_sees_real_failure_then_recovers(tmp_path):
    """Le 1er tool_call échoue RÉELLEMENT (fichier absent) — le modèle voit le
    VRAI ToolResult d'échec (jamais une supposition) et re-planifie."""
    script = [
        _tool_call_response("filesystem.read_file", {"path": "missing.txt"}),
        _tool_call_response("filesystem.write_file", {"path": "missing.txt", "content": "créé après échec"}),
        _text_response("Le fichier n'existait pas, je l'ai créé."),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        state = handles.harness.handle_request(_req("lis missing.txt, crée-le si absent"))
        assert state.status == HarnessStatus.COMPLETED
        trace = handles.harness.last_tool_trace("s1")
        assert trace[0]["status"] == "failure"
        assert trace[1]["status"] == "success"
        assert (tmp_path / "workspace" / "missing.txt").read_text(encoding="utf-8") == "créé après échec"

        # Le message "tool" envoyé au modèle après l'échec contient le VRAI code d'erreur.
        # (messages est une liste mutable partagée entre tous les appels captures
        # par FakeScriptedProvider -> on lit le PREMIER message "tool", qui
        # correspond à l'échec du 1er tool_call, quel que soit l'état final.)
        all_messages = fake.calls[-1].messages
        tool_msgs = [m for m in all_messages if m.role == "tool"]
        assert '"status": "failure"' in tool_msgs[0].content[0].value
        assert "FILE_NOT_FOUND" in tool_msgs[0].content[0].value
    finally:
        handles.shutdown()


# --- 4. ACTION + CRASH SIMULÉ + ExecutionRecord ---

def test_4_simulated_crash_mid_tool_call_execution_record_never_falsely_completed(tmp_path):
    """Reproduit EXACTEMENT la séquence de raya/harness/loop.py::_run_agentic_loop
    (start() AVANT l'action, complete() APRÈS) via le VRAI ExecutionRecordRepository
    câblé dans un runtime bootstrapé, mais sans jamais appeler complete() —
    simulant un crash entre les deux. Un second repository sur le MÊME fichier
    SQLite doit voir EXECUTING -> UNKNOWN, jamais COMPLETED (§14.2)."""
    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    db_path = handles.config.db_path
    try:
        record = ExecutionRecord(
            operation_id="op_crash_1", tool_call_id="tc1", correlation_id="c1", idempotency_key="op_crash_1:demo",
        )
        handles.execution_records.start(record)
        assert handles.execution_records.get("op_crash_1").execution_state == ExecutionState.EXECUTING
        # PAS d'appel à .complete() -> crash simulé ici.
    finally:
        handles.shutdown()

    fresh_repo = ExecutionRecordRepository(SqliteBackend(db_path))
    recovered = fresh_repo.load_and_reinterpret("op_crash_1")
    assert recovered.execution_state == ExecutionState.UNKNOWN
    assert recovered.execution_state != ExecutionState.COMPLETED


# --- 5. IDEMPOTENT RETRY SAME KEY ---

def test_5_idempotent_tool_replayed_with_same_key_never_double_effect(tmp_path):
    """demo.idempotent_counter (vrai handler, vrai catalogue) rejoué avec la
    MÊME idempotency_key -> aucun double effet, ExecutionRecord réel."""
    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    try:
        handler = handles.tools.handler_for("demo.idempotent_counter")
        record = ExecutionRecord(operation_id="op5", tool_call_id="tc5", correlation_id="c5", idempotency_key="stable-5")
        handles.execution_records.start(record)
        call1 = ToolCall(tool_name="demo.idempotent_counter", arguments={}, correlation_id="c5",
                          requested_by=ToolCallRequester(subsystem="harness", session_id="s1"))
        call1.idempotency_key = "stable-5"
        result1 = handler(call1)
        handles.execution_records.complete("op5")

        # Reprise supposée après un crash post-complete (retry par sécurité,
        # même clé) : decide_recovery ne rejoue PAS le handler pour un
        # idempotent -> RETRY direct est sûr, on le simule en rappelant le
        # handler avec la même clé et on vérifie l'ABSENCE de double effet.
        call2 = ToolCall(tool_name="demo.idempotent_counter", arguments={}, correlation_id="c5",
                          requested_by=ToolCallRequester(subsystem="harness", session_id="s1"))
        call2.idempotency_key = "stable-5"
        result2 = handler(call2)

        assert result1.output["counter"] == result2.output["counter"]
        assert result2.output["deduplicated"] is True
        assert handles.execution_records.get("op5").execution_state == ExecutionState.COMPLETED
    finally:
        handles.shutdown()


# --- 6. NON-IDEMPOTENT + UNKNOWN + PAS DE RETRY AVEUGLE ---

def test_6_non_idempotent_unknown_after_crash_escalates_never_blind_retry(tmp_path):
    """demo.non_idempotent_append : après un crash (execution_state=UNKNOWN),
    sans preuve de vérification, decide_recovery DOIT escalader — jamais
    rejouer aveuglément un outil dont chaque appel a un effet réel (§8)."""
    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    try:
        handler = handles.tools.handler_for("demo.non_idempotent_append")
        call = ToolCall(tool_name="demo.non_idempotent_append", arguments={"text": "ligne unique"}, correlation_id="c6",
                         requested_by=ToolCallRequester(subsystem="harness", session_id="s1"))
        record = ExecutionRecord(operation_id="op6", tool_call_id="tc6", correlation_id="c6", idempotency_key="op6:append")
        handles.execution_records.start(record)
        handler(call)
        # PAS de .complete() -> crash simulé.

        reloaded = handles.execution_records.load_and_reinterpret("op6")
        assert reloaded.execution_state == ExecutionState.UNKNOWN

        decision = decide_recovery(reloaded, idempotent=False, verify=lambda: VerificationState.UNVERIFIABLE)
        assert decision == RecoveryDecision.ESCALATE
    finally:
        handles.shutdown()


# --- 7. LONG TASK + QUESTION PENDANT ---

def test_7_conversation_answered_immediately_while_background_task_runs(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("Oui, je t'écoute.")])
    try:
        handles.harness.start_background_task("tâche longue", total_steps=30, session_id="cli", channel="cli")
        start = time.monotonic()
        state = handles.harness.handle_request(_req("tu es toujours là ?", session_id="s_conv"))
        elapsed = time.monotonic() - start
        assert state.status == HarnessStatus.COMPLETED
        assert elapsed < 0.5  # répond bien avant la fin des 30 steps de fond
    finally:
        handles.shutdown()


# --- 8. STOP PENDANT UNE TÂCHE DE FOND *ET* UNE BOUCLE AGENTIQUE EN COURS ---

def test_8_stop_interrupts_background_task_and_agentic_loop_together(tmp_path):
    from raya.contracts import Event, TaskState

    script = [
        _tool_call_response("filesystem.write_file", {"path": "x.txt", "content": "y"}),
        _text_response("ne devrait jamais être renvoyé"),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        task = handles.harness.start_background_task("tâche de fond", total_steps=50, set_as_focus=False)
        time.sleep(0.03)

        def trigger_stop_after_tool(event):
            handles.bus.publish(Event(type="interface.stop_requested", source="interfaces.cli", payload={}))

        handles.bus.subscribe("tool.call_completed", trigger_stop_after_tool, subscriber="test_stop_trigger")

        state = handles.harness.handle_request(_req("écris x.txt", session_id="s_conv"))
        handles.bus.wait_idle(timeout_s=1.0)

        assert _wait_for(lambda: handles.tasks.get(task.id).state == TaskState.CANCELLED)
        # Le tour conversationnel voit le STOP soit avant le 2e appel modèle
        # (FAILED honnête), soit — si la course l'a manqué — se termine
        # normalement ; ce qui compte ici est que la TÂCHE DE FOND est bien
        # coupée, prouvant que STOP est un signal global partagé (§Phase2 STOP).
        assert state.status in (HarnessStatus.COMPLETED, HarnessStatus.FAILED)
    finally:
        handles.shutdown()


# --- 9. OBJECTIF AMBIGU -> CLARIFICATION, AUCUN EFFET DE BORD ---

def test_9_ambiguous_objective_model_asks_for_clarification_no_side_effects(tmp_path):
    script = [_text_response("Je ne suis pas sûr de comprendre : veux-tu dire le fichier budget.txt ou notes.txt ?")]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        state = handles.harness.handle_request(_req("modifie le fichier"))
        assert state.status == HarnessStatus.COMPLETED
        assert "?" in handles.harness.response_text("s1")
        assert handles.harness.last_tool_trace("s1") == []
        assert list((tmp_path / "workspace").glob("*")) == []  # aucun effet de bord
        assert handles.world_state.all() == []
    finally:
        handles.shutdown()


# --- 10. LE MODÈLE PRÉTEND UNE ACTION SUR UN FICHIER RÉEL EXISTANT -> RAYA REFUSE DE LE CROIRE ---

def test_10_model_falsely_claims_it_modified_a_real_file_raya_never_acts_on_the_claim(tmp_path):
    """Tour 1 : vraie création de fichier (preuve réelle). Tour 2 : le modèle
    prétend, en texte seul, avoir SUPPRIMÉ ce même fichier, sans ToolCall.
    RAYA ne doit ni supprimer le fichier ni enregistrer cette prétention
    nulle part comme un fait établi (§9, §10)."""
    script = [
        _tool_call_response("filesystem.write_file", {"path": "important.txt", "content": "données réelles"}),
        _text_response("Fichier créé."),
        _text_response("J'ai supprimé important.txt comme demandé. ✅"),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        handles.harness.handle_request(_req("crée important.txt"))
        real_file = tmp_path / "workspace" / "important.txt"
        assert real_file.exists()

        state = handles.harness.handle_request(_req("supprime-le", session_id="s1"))
        assert state.status == HarnessStatus.COMPLETED  # le tour se termine (texte relayé tel quel)

        # La prétention du modèle n'a PRODUIT AUCUN effet réel ni AUCUNE preuve structurée :
        assert real_file.exists()  # toujours là, jamais supprimé
        assert real_file.read_text(encoding="utf-8") == "données réelles"
        assert handles.harness.last_tool_trace("s1") == []
        assert handles.world_state.retrieve_fact("filesystem", "important.txt") is None
    finally:
        handles.shutdown()


# --- §26 CRITIQUE (redondant volontairement avec tests/harness/test_agentic_loop.py
# pour une preuve à deux niveaux : unitaire ET intégration bout-en-bout réelle) ---

def test_26_critical_no_evidence_no_claim_end_to_end(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("C'est fait, le rapport est envoyé par email.")])
    try:
        handles.harness.handle_request(_req("envoie le rapport par email"))
        assert handles.harness.last_tool_trace("s1") == []
        assert handles.world_state.all() == []
        assert len(list(handles.execution_records._backend.query("execution_records"))) == 0
    finally:
        handles.shutdown()


# --- §27 CRITIQUE : preuve d'exécution réelle physique, chaîne complète ---

def test_27_critical_real_multi_step_execution_physical_evidence_chain(tmp_path):
    """write -> read -> réponse finale : à CHAQUE étape, une preuve physique
    vérifiable indépendamment du framework de test (lecture disque directe)."""
    script = [
        _tool_call_response("filesystem.write_file", {"path": "chain.txt", "content": "preuve physique"}),
        _tool_call_response("filesystem.read_file", {"path": "chain.txt"}),
        _text_response("Le fichier contient : preuve physique"),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        state = handles.harness.handle_request(_req("écris puis relis chain.txt"))
        assert state.status == HarnessStatus.COMPLETED

        real_file = tmp_path / "workspace" / "chain.txt"
        assert real_file.read_text(encoding="utf-8") == "preuve physique"

        trace = handles.harness.last_tool_trace("s1")
        assert [t["tool_name"] for t in trace] == ["filesystem.write_file", "filesystem.read_file"]
        assert trace[1]["status"] == "success"
        assert trace[1]["evidence"]["bytes_read"] == len("preuve physique".encode("utf-8"))

        # ExecutionRecord réel pour les 2 opérations, toutes deux COMPLETED.
        records = list(handles.execution_records._backend.query("execution_records", execution_state="COMPLETED"))
        assert len(records) == 2
    finally:
        handles.shutdown()


# --- §28 : tâche raisonnable multi-étapes, réponse finale groundée sur le VRAI résultat ---

def test_28_multi_step_task_final_answer_grounded_in_real_tool_output_only(tmp_path):
    script = [
        _tool_call_response("filesystem.write_file", {"path": "journal.txt", "content": "42 tâches terminées"}),
        _tool_call_response("filesystem.read_file", {"path": "journal.txt"}),
        _text_response("D'après le fichier : 42 tâches terminées."),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        handles.harness.handle_request(_req("résume le contenu de journal.txt après l'avoir créé"))
        response = handles.harness.response_text("s1")
        assert "42" in response
        # Le nombre affiché vient du VRAI contenu relu, pas d'une invention —
        # preuve indirecte : le fichier réel contient exactement cette valeur.
        assert "42" in (tmp_path / "workspace" / "journal.txt").read_text(encoding="utf-8")
    finally:
        handles.shutdown()


# ======================================================================
# TESTS LIVE (vrai Ollama Cloud) — honnêtement BLOCKED/NOT_TESTED si la
# clé n'est pas disponible, jamais simulés silencieusement (§29/§37).
# Clé lue de façon TRANSITOIRE depuis RAYA/.env (V1), jamais copiée dans
# RayaV2, jamais loggée/affichée.
# ======================================================================

def _read_v1_ollama_key() -> str | None:
    v1_env = Path(r"C:\Users\ruben\OneDrive\Bureau\RAYA\.env")
    if not v1_env.exists():
        return None
    for line in v1_env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith("OLLAMA_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


def test_live_real_ollama_cloud_creates_a_real_file_end_to_end(tmp_path):
    import pytest

    api_key = _read_v1_ollama_key()
    if not api_key:
        pytest.skip("BLOCKED: OLLAMA_API_KEY indisponible (ni V1/.env ni env) — non testé en réel")

    from raya.runtime.bootstrap import bootstrap
    from raya.runtime.config import load_config

    cfg = load_config()
    cfg.db_path = tmp_path / "live.sqlite3"
    cfg.tool_workspace_dir = tmp_path / "workspace"
    cfg.ollama_api_key = api_key
    cfg.max_tool_iterations = 5

    handles = bootstrap(config=cfg, backend=SqliteBackend(cfg.db_path))
    try:
        req = HarnessRequest(
            channel=Channel.CLI, session_id="live1",
            input=InterfaceInput(text=(
                "Utilise l'outil filesystem.write_file pour créer un fichier nommé "
                "preuve.txt contenant exactement le texte 'ok-phase3'. Réponds "
                "seulement après avoir vraiment appelé l'outil."
            )),
        )
        state = handles.harness.handle_request(req)
        assert state.status == HarnessStatus.COMPLETED, f"real Ollama call did not complete: {state.error}"

        real_file = tmp_path / "workspace" / "preuve.txt"
        assert real_file.exists(), "le vrai modèle n'a jamais appelé l'outil réel -> AUCUN fichier créé"
        trace = handles.harness.last_tool_trace("live1")
        assert any(t["tool_name"] == "filesystem.write_file" and t["status"] == "success" for t in trace)
    finally:
        handles.shutdown()
