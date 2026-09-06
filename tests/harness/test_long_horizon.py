"""Long-Horizon Autonomy — moteur réel dans le Harness (RAYA V2 Phase 10).
Système réel de bout en bout (vrai TaskRegistry/TaskScheduler/Safety/Tools/
Cognition, seul le Model Layer est scripté — même discipline Phase 3+).
RENFORCE le pipeline existant : `_run_long_horizon_step` réutilise
`execute_tool`/`verify_tool_result`/`_promote_observations_and_verify`/
`LoopDetector`, jamais une deuxième implémentation."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import (  # noqa: E402
    ContentPart,
    Event,
    FinishReason,
    ModelCapability,
    ModelResponse,
    PermissionLevel,
    RequestedToolCall,
)


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


def _register_planning(handles, *entries) -> None:
    """Chaque entrée est soit une liste de strings (sérialisée en JSON, pour
    un appel `build_plan`), soit une string brute (pour un appel
    `replan_step`, ex: "NONE" ou une phrase alternative). Un SEUL provider
    PLANNING enregistré — jamais deux fakes qui se disputeraient l'ordre de
    résolution de `route()` (le premier épuisé ferait planter le second)."""
    responses = [
        _text_response(str(e).replace("'", '"')) if isinstance(e, list) else _text_response(e)
        for e in entries
    ]
    handles.models.register(FakeScriptedProvider(responses, capabilities=[ModelCapability.PLANNING]))


def _wait_for(predicate, timeout_s: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# --- Plan creation ---

def test_create_long_horizon_task_persists_a_real_plan(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("fait.")])
    try:
        _register_planning(handles, ["une seule étape"])
        task = handles.harness.create_long_horizon_task("objectif simple", channel="cli", session_id="s1")
        assert task.checkpoint is not None
        assert "plan" in task.checkpoint
        assert task.checkpoint["plan"]["steps"][0]["objective"] == "une seule étape"
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED"))
    finally:
        handles.shutdown()


def test_create_long_horizon_task_sets_focus_by_default(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("fait.")])
    try:
        _register_planning(handles, ["étape unique"])
        task = handles.harness.create_long_horizon_task("obj", channel="cli", session_id="s1")
        assert handles.harness.focus.get_focus("s1") == task.id
        # Laisse la tâche (1 seul tick) se terminer avant shutdown() — évite
        # une course bénigne mais bruyante entre la complétion réelle et
        # l'annulation de fin de test (le même log "Transition Task illégale"
        # existe pour n'importe quelle tâche Phase 2 non attendue).
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED"))
    finally:
        handles.shutdown()


# --- Single-step completion (Scenario A, simple case) ---

def test_single_step_task_completes_without_any_tool_call(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("Étape terminée, rien à faire de plus.")])
    try:
        _register_planning(handles, ["réponds simplement"])
        task = handles.harness.create_long_horizon_task("obj trivial", channel="cli", session_id="s1")
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED"))
        final = handles.harness.get_task(task.id)
        assert final.state.value == "COMPLETED"
        assert "terminée" in final.result["summary"]
    finally:
        handles.shutdown()


# --- Multi-step execution with real tool calls (Scenario A, full) ---

def test_multi_step_task_executes_each_step_with_real_tool_calls_and_completes(tmp_path):
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("filesystem.write_file", {"path": "a.txt", "content": "A"}),
        _text_response("Fichier A écrit."),
        _tool_call_response("filesystem.write_file", {"path": "b.txt", "content": "B"}),
        _text_response("Fichier B écrit."),
    ])
    try:
        _register_planning(handles, ["écrire le fichier A", "écrire le fichier B"])
        # filesystem.write_file est classé SAFE par tag ("filesystem",
        # raya/safety/risk.py) — pas de confirmation nécessaire ici.
        task = handles.harness.create_long_horizon_task("créer deux fichiers", channel="cli", session_id="s1")
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED"), timeout_s=5.0)
        final = handles.harness.get_task(task.id)
        assert final.state.value == "COMPLETED"
        assert (handles.config.tool_workspace_dir / "a.txt").read_text(encoding="utf-8") == "A"
        assert (handles.config.tool_workspace_dir / "b.txt").read_text(encoding="utf-8") == "B"
        plan = final.checkpoint["plan"]
        assert plan["steps"][0]["status"] == "COMPLETED"
        assert plan["steps"][1]["status"] == "COMPLETED"
    finally:
        handles.shutdown()


def test_step_evidence_is_shown_to_the_model_right_after_a_successful_tool_call(tmp_path):
    """BUG CORRIGÉ (Chantier 14, constaté EN RÉEL avec telegram.send_message) :
    avant ce fix, `step.evidence` n'était inclus dans le prompt QUE si
    `step.attempts > 0` — mais `step.attempts` n'est incrémenté que sur un
    RecoveryAction.REPLAN (un échec), jamais après un simple SUCCÈS. Un
    outil non observable en World State (rien pour `_is_step_tool_already_
    satisfied` à vérifier, ex: un envoi de message) ne montrait donc JAMAIS
    sa propre preuve de succès au tick suivant — un vrai modèle (contrairement
    à ce fake scripté, qui rejoue une séquence fixe peu importe le prompt)
    rappelait alors le même outil à chaque tick, avec un effet de bord réel
    RENOUVELÉ à chaque fois. Ce test vérifie directement le CONTENU du 2e
    prompt envoyé au modèle, pas seulement l'issue finale."""
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("filesystem.write_file", {"path": "a.txt", "content": "A"}),
        _text_response("Déjà écrit, rien de plus à faire."),
    ])
    try:
        _register_planning(handles, ["écrire le fichier A"])
        task = handles.harness.create_long_horizon_task("écrire un fichier", channel="cli", session_id="s1")
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED"))
        assert handles.harness.get_task(task.id).state.value == "COMPLETED"

        second_call_text = fake.calls[1].messages[-1].content[0].value
        assert "filesystem.write_file" in second_call_text
        assert "already" in second_call_text.lower() or "evidence" in second_call_text.lower()
        assert "do NOT call that tool again" in second_call_text
    finally:
        handles.shutdown()


# --- Checkpoint (Scenario A/C) ---

def test_checkpoint_persists_step_result_text_on_completion(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("fini.")])
    try:
        _register_planning(handles, ["étape A"])
        task = handles.harness.create_long_horizon_task("obj", channel="cli", session_id="s1")
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value == "COMPLETED")
        final = handles.harness.get_task(task.id)
        plan = final.checkpoint["plan"]
        assert plan["steps"][0]["status"] == "COMPLETED"
        assert plan["steps"][0]["result"]["text"] == "fini."
        assert plan["current_step_id"] == plan["steps"][0]["id"]
    finally:
        handles.shutdown()


# --- STOP (Scenario B) ---
#
# NOTE sur le choix d'outil : la classification de risque réelle est par
# CAPABILITY TAG (raya/safety/risk.py::classify_risk), pas par le champ
# `Tool.permission_level` déclaré dans tools/catalog/*.py — le tag "demo"
# (demo.always_fail, demo.idempotent_counter) est classé SENSITIVE, donc
# CES outils exigeraient une confirmation avant même d'échouer. Le tag
# "filesystem" est classé SAFE : `filesystem.read_file` sur un chemin
# inexistant échoue réellement, SANS confirmation — c'est l'outil utilisé
# ci-dessous pour tester recovery/replanning/STOP sans le bruit de la
# confirmation (elle-même testée séparément plus bas).

_MISSING_FILE_CALL = _tool_call_response("filesystem.read_file", {"path": "does-not-exist.txt"})


def test_stop_prevents_further_tool_calls_mid_task(tmp_path):
    tick_started = threading.Event()

    def _first_tick(_req):
        tick_started.set()
        return _MISSING_FILE_CALL

    handles, fake = build_test_harness(tmp_path, [_first_tick, _MISSING_FILE_CALL])
    try:
        _register_planning(handles, ["lire un fichier qui n'existe pas"])
        task = handles.harness.create_long_horizon_task("obj", channel="cli", session_id="s1")
        assert tick_started.wait(timeout=3.0), "la première tentative n'a jamais démarré"
        handles.bus.publish(Event(type="interface.stop_requested", source="test", payload={}))
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value in ("CANCELLED", "FAILED"), timeout_s=3.0)
        final = handles.harness.get_task(task.id)
        assert final.state.value == "CANCELLED"
    finally:
        handles.shutdown()


# --- Replanning (Scenario D, with a viable alternative) ---

def test_step_failure_triggers_replanning_and_task_still_completes(tmp_path):
    # 3 appels REASONING attendus : 2 tentatives de l'étape qui échoue
    # (REPLAN puis ESCALATE au 2e échec identique, LoopDetector par défaut),
    # puis 1 appel pour l'étape alternative insérée par le replanning.
    handles, fake = build_test_harness(tmp_path, [
        _MISSING_FILE_CALL, _MISSING_FILE_CALL, _text_response("Étape alternative terminée."),
    ], max_tool_iterations=10)
    try:
        _register_planning(handles, ["lire un fichier qui n'existe pas"], "Utiliser une méthode alternative sans outil.")
        task = handles.harness.create_long_horizon_task("obj", channel="cli", session_id="s1")

        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED"), timeout_s=5.0)
        final = handles.harness.get_task(task.id)
        plan = final.checkpoint["plan"]
        assert plan["steps"][0]["status"] == "SKIPPED"  # contournée par le replanning, pas retentée
        assert plan["steps"][0]["error"] is not None  # trace de l'échec initial conservée (§6)
        assert len(plan["steps"]) == 2
        assert plan["steps"][1]["objective"] == "Utiliser une méthode alternative sans outil."
        assert final.state.value == "COMPLETED"
    finally:
        handles.shutdown()


# --- Escalation without a viable alternative (Scenario D, honest failure) ---

def test_step_failure_without_alternative_fails_the_task_honestly(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_MISSING_FILE_CALL, _MISSING_FILE_CALL])
    try:
        _register_planning(handles, ["lire un fichier qui n'existe pas, sans issue"], "NONE")
        task = handles.harness.create_long_horizon_task("obj", channel="cli", session_id="s1")
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED"))
        final = handles.harness.get_task(task.id)
        assert final.state.value == "FAILED"
        assert final.error is not None
        plan = final.checkpoint["plan"]
        assert plan["steps"][0]["status"] == "FAILED"
        assert len(plan["steps"]) == 1  # aucune étape fabriquée sans base réelle
    finally:
        handles.shutdown()


# --- Confirmation required in background (Chantier 15 : BLOCKED, jamais FAILED) ---

def test_sensitive_tool_in_background_blocks_honestly_never_bypasses_safety(tmp_path):
    """`demo.idempotent_counter` (tag "demo") est classé SENSITIVE par
    `classify_risk` — une tâche de fond n'a pas de canal interactif pour la
    confirmer. Chantier 15 (Axe D) : ceci n'est PAS un échec technique —
    la tâche devient BLOCKED (attend une confirmation externe), jamais
    FAILED (qui signifierait une exécution réellement cassée sans
    alternative), et jamais un contournement silencieux de Safety."""
    handles, fake = build_test_harness(tmp_path, [_tool_call_response("demo.idempotent_counter", {})])
    try:
        _register_planning(handles, ["incrémenter un compteur nécessitant confirmation"], "NONE")
        task = handles.harness.create_long_horizon_task("obj", channel="cli", session_id="s1")
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED", "BLOCKED"))
        final = handles.harness.get_task(task.id)
        assert final.state.value == "BLOCKED"
        assert final.error.code == "CONFIRMATION_REQUIRED_IN_BACKGROUND"
        plan = final.checkpoint["plan"]
        assert plan["steps"][0]["error"]["code"] == "CONFIRMATION_REQUIRED_IN_BACKGROUND"
    finally:
        handles.shutdown()


def test_confirmation_required_in_background_never_attempts_replanning(tmp_path):
    """Chantier 15 (Axe D/F) : contrairement à un échec technique, aucune
    étape alternative ne peut "contourner" une confirmation Safety requise
    — le planner (replan_step) ne doit JAMAIS être consulté pour ce cas
    précis. Le script PLANNING ne contient qu'UNE entrée (le plan initial) ;
    si `replan_step` était appelé, `FakeScriptedProvider` lèverait une
    AssertionError pour script épuisé."""
    handles, fake = build_test_harness(tmp_path, [_tool_call_response("demo.idempotent_counter", {})])
    try:
        _register_planning(handles, ["incrémenter un compteur nécessitant confirmation"])  # une seule entrée
        task = handles.harness.create_long_horizon_task("obj", channel="cli", session_id="s1")
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED", "BLOCKED"))
        assert handles.harness.get_task(task.id).state.value == "BLOCKED"
    finally:
        handles.shutdown()


def test_cancel_task_on_blocked_task_cancels_immediately(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_tool_call_response("demo.idempotent_counter", {})])
    try:
        _register_planning(handles, ["incrémenter un compteur nécessitant confirmation"])
        task = handles.harness.create_long_horizon_task("obj", channel="cli", session_id="s1")
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value == "BLOCKED")
        cancelled = handles.harness.cancel_task(task.id)
        assert cancelled.state.value == "CANCELLED"
    finally:
        handles.shutdown()


def test_resume_task_from_blocked_resubmits_and_completes(tmp_path):
    """Prouve que `resume_task()` (inchangé, générique à l'état source)
    fonctionne déjà pour BLOCKED sans code dédié — même mécanisme que la
    reprise d'une tâche PAUSED après un crash."""
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("demo.idempotent_counter", {}),
        _text_response("terminé, confirmation obtenue hors bande"),
    ])
    try:
        _register_planning(handles, ["incrémenter un compteur nécessitant confirmation"])
        task = handles.harness.create_long_horizon_task("obj", channel="cli", session_id="s1")
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value == "BLOCKED")

        handles.harness.resume_task(task.id)
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED"))
        assert handles.harness.get_task(task.id).state.value == "COMPLETED"
    finally:
        handles.shutdown()


# --- Idempotence (§12) ---

def test_step_tool_already_satisfied_per_world_state_is_never_replayed(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    try:
        harness = handles.harness
        from raya.contracts import ObservationSpec, Tool, ToolResult, ToolResultStatus
        from raya.contracts import RequestedToolCall as RTC

        # idempotent=True (Chantier 15, Axe B) : lancer une appli déjà
        # ouverte est un no-op — ce test exerce spécifiquement le
        # garde-fou basé sur le World State (ObservationSpec), distinct du
        # garde-fou "idempotent=False -> jamais rejoué" testé ailleurs.
        tool = Tool(
            name="demo.launch", description="d", capability_tags=["demo"],
            input_schema={"type": "object"}, output_schema={"type": "object"},
            permission_level=PermissionLevel.SAFE, idempotent=True,
            observation=(ObservationSpec(domain="demo", key="active", evidence_field="target", expected_argument="target"),),
        )
        handles.tools.register(tool, lambda call: ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output={}))
        harness.set_world_fact("demo", "active", "notepad")

        requested = RTC(tool_name="demo.launch", arguments={"target": "notepad"})
        step_evidence = {"demo.launch": {"status": "success"}}
        assert harness._is_step_tool_already_satisfied(requested, step_evidence) is True
    finally:
        handles.shutdown()


def test_step_tool_not_satisfied_when_world_state_disagrees(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    try:
        harness = handles.harness
        from raya.contracts import ObservationSpec, RequestedToolCall as RTC, Tool, ToolResult, ToolResultStatus

        # idempotent=True : voir le test ci-dessus (même raison).
        tool = Tool(
            name="demo.launch", description="d", capability_tags=["demo"],
            input_schema={"type": "object"}, output_schema={"type": "object"},
            permission_level=PermissionLevel.SAFE, idempotent=True,
            observation=(ObservationSpec(domain="demo", key="active", evidence_field="target", expected_argument="target"),),
        )
        handles.tools.register(tool, lambda call: ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output={}))
        harness.set_world_fact("demo", "active", "calculator")

        requested = RTC(tool_name="demo.launch", arguments={"target": "notepad"})
        step_evidence = {"demo.launch": {"status": "success"}}
        assert harness._is_step_tool_already_satisfied(requested, step_evidence) is False
    finally:
        handles.shutdown()


def test_non_idempotent_tool_already_succeeded_is_never_replayed_structurally(tmp_path):
    """Chantier 15 (Axe B, cœur du fix) : contrairement au garde-fou basé
    sur le World State (ci-dessus, seulement valable pour un Tool
    idempotent avec ObservationSpec), un Tool `idempotent=False` sans
    aucun ObservationSpec (exactement le cas réel de
    `telegram.send_message`, cf. Chantier 14) qui a déjà réussi pour ce
    step est satisfait de façon STRUCTURELLE — jamais dépendant du fait
    que le modèle "coopère" en lisant l'évidence du prompt."""
    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    try:
        harness = handles.harness
        from raya.contracts import PermissionLevel as PL
        from raya.contracts import RequestedToolCall as RTC
        from raya.contracts import Tool

        handles.tools.register(
            Tool(name="demo.notify", description="d", capability_tags=["demo"],
                 input_schema={"type": "object"}, output_schema={"type": "object"},
                 permission_level=PL.SAFE, idempotent=False),  # aucun `observation=` -- comme telegram.send_message
            lambda call: None,  # jamais appelé dans ce test
        )
        requested = RTC(tool_name="demo.notify", arguments={"text": "x"})
        step_evidence = {"demo.notify": {"status": "success"}}
        assert harness._is_step_tool_already_satisfied(requested, step_evidence) is True
    finally:
        handles.shutdown()


def test_non_idempotent_tool_never_called_twice_even_when_model_insists(tmp_path):
    """Preuve end-to-end : même si le modèle rappelle le même Tool non
    idempotent au tick suivant (au lieu de conclure "terminé" comme le lui
    demande l'instruction de prompt), le garde-fou STRUCTUREL empêche un
    second appel réel. Sans le fix Chantier 15, ce test échouerait avec
    calls['n'] == 2 (exactement le scénario réel qui a spammé Telegram)."""
    from raya.contracts import PermissionLevel as PL
    from raya.contracts import Tool, ToolResult, ToolResultStatus

    calls = {"n": 0}

    def _send(call):
        calls["n"] += 1
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output={"sent": True})

    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("demo.send_once", {"text": "hello"}),
        _tool_call_response("demo.send_once", {"text": "hello"}),  # le modèle "insiste" -- ne doit jamais réussir 2x
        _text_response("terminé."),
    ])
    try:
        handles.tools.register(
            # capability_tags=["utils"] (jamais "demo", classé SENSITIVE par
            # défaut dans raya/safety/risk.py — exigerait une confirmation,
            # hors sujet de CE test) -- SAFE, exécute sans confirmation.
            Tool(name="demo.send_once", description="d", capability_tags=["utils"],
                 input_schema={"type": "object"}, output_schema={"type": "object"},
                 permission_level=PL.SAFE, idempotent=False),
            _send,
        )
        _register_planning(handles, ["envoyer un message une seule fois"])
        task = handles.harness.create_long_horizon_task("obj", channel="cli", session_id="s1")
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED"))
        assert handles.harness.get_task(task.id).state.value == "COMPLETED"
        assert calls["n"] == 1  # jamais 2, peu importe ce que le modèle redemande
    finally:
        handles.shutdown()


# --- Multi-tick continuity (Chantier 15, Axe G) ---

def test_multi_tick_prompt_includes_prior_completed_step_result(tmp_path):
    """Le step 2 doit voir ce que le step 1 a réellement accompli — pas
    seulement son propre objectif texte (sans quoi le modèle n'a aucun
    moyen structuré de savoir ce qui a déjà été fait avant lui)."""
    handles, fake = build_test_harness(tmp_path, [
        _text_response("Fichier A créé avec succès."),
        _text_response("Deuxième étape terminée."),
    ])
    try:
        _register_planning(handles, ["créer le fichier A", "écrire dans le fichier A"])
        task = handles.harness.create_long_horizon_task("obj", channel="cli", session_id="s1")
        assert _wait_for(lambda: handles.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED"))
        assert handles.harness.get_task(task.id).state.value == "COMPLETED"

        second_call_text = fake.calls[1].messages[-1].content[0].value
        assert "Already completed earlier steps" in second_call_text
        assert "créer le fichier A" in second_call_text
        assert "Fichier A créé avec succès." in second_call_text
    finally:
        handles.shutdown()


# --- Recovery duplication window (Chantier 15, Axe C/I) ---

def test_step_evidence_is_checkpointed_immediately_not_only_at_end_of_step(tmp_path):
    """Une évidence de succès pour le PREMIER tool call d'un step doit
    survivre dans le checkpoint DURABLE même si un DEUXIÈME tool call,
    dans la MÊME réponse modèle, ne revient jamais (simule un crash en
    plein milieu de la boucle, avant le checkpoint de fin de fonction) —
    sans quoi un crash à cet instant perdrait la preuve d'une action non
    idempotente déjà réellement effectuée (fenêtre de duplication réelle
    constatée à l'inspection, cf. l'incident Telegram du Chantier 14)."""
    from raya.contracts import PermissionLevel as PL
    from raya.contracts import RequestedToolCall as RTC
    from raya.contracts import Tool, ToolResult, ToolResultStatus

    second_tool_started = threading.Event()

    def _hang_forever(_call):
        second_tool_started.set()
        time.sleep(3600)  # ne revient jamais : simule un crash en plein 2e tool call

    def _two_calls(_req):
        return ModelResponse(
            request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
            tool_calls_requested=[
                RTC(tool_name="demo.send_once", arguments={"text": "A"}),
                RTC(tool_name="demo.hang", arguments={}),
            ],
        )

    handles, fake = build_test_harness(tmp_path, [_two_calls])
    try:
        handles.tools.register(
            # capability_tags=["utils"], jamais "demo" (SENSITIVE par
            # défaut) — hors sujet de ce test, qui porte sur la
            # persistance de l'évidence, pas sur Safety.
            Tool(name="demo.send_once", description="d", capability_tags=["utils"],
                 input_schema={"type": "object"}, output_schema={"type": "object"},
                 permission_level=PL.SAFE, idempotent=False),
            lambda call: ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output={"sent": True}),
        )
        handles.tools.register(
            Tool(name="demo.hang", description="d", capability_tags=["utils"],
                 input_schema={"type": "object"}, output_schema={"type": "object"}, permission_level=PL.SAFE),
            _hang_forever,
        )
        _register_planning(handles, ["envoyer puis faire une action lente"])
        task = handles.harness.create_long_horizon_task("obj", channel="cli", session_id="s1")
        assert second_tool_started.wait(timeout=3.0), "le deuxième tool call n'a jamais démarré"

        # "Gelé" ici, comme en plein crash -- ce qui compte est ce qui est
        # DURABLEMENT persisté à cet instant précis, jamais un état en
        # mémoire seulement.
        persisted = handles.harness.get_task(task.id)
        evidence = persisted.checkpoint["plan"]["steps"][0]["evidence"] or {}
        assert "demo.send_once" in evidence
        assert evidence["demo.send_once"]["status"] == "success"
    finally:
        handles.shutdown()


# --- Resume dispatch (checkpoint kind detection) ---

def test_resume_task_dispatches_long_horizon_step_when_checkpoint_has_a_plan(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("continué et terminé.")])
    try:
        harness = handles.harness
        task = harness.create_task("obj", channel="cli", session_id="s1")
        from raya.contracts import Plan, PlanStep, to_dict
        plan = Plan(steps=[PlanStep(id="s1", objective="obj")], current_step_id="s1")
        harness._tasks.checkpoint(task.id, {"plan": to_dict(plan)})
        harness._tasks.start(task.id)
        harness.pause_task(task.id)

        harness.resume_task(task.id)
        assert _wait_for(lambda: harness.get_task(task.id).state.value in ("COMPLETED", "FAILED"))
        assert harness.get_task(task.id).state.value == "COMPLETED"
    finally:
        handles.shutdown()


def test_resume_task_still_dispatches_demo_step_fn_without_a_plan(tmp_path):
    """Non-régression Phase 2 : une tâche démonstrateur (sans plan) reprend
    toujours sur le compteur simulé, jamais confondue avec une tâche
    long-horizon (consigne §2 : ne rien casser de l'existant)."""
    handles, fake = build_test_harness(tmp_path, [])
    try:
        harness = handles.harness
        task = harness.start_background_task("démo", channel="cli", session_id="s1", total_steps=2)
        assert _wait_for(lambda: harness.get_task(task.id).state.value == "RUNNING")
        harness.pause_task(task.id)
        harness.resume_task(task.id)
        assert _wait_for(lambda: harness.get_task(task.id).state.value == "COMPLETED")
    finally:
        handles.shutdown()


# --- Restart / crash recovery (Scenario C) ---

def test_restart_recovers_a_running_long_horizon_task_and_continues_from_checkpoint(tmp_path):
    from raya.persistence import SqliteBackend
    from raya.runtime.bootstrap import bootstrap
    from raya.runtime.config import load_config

    db_path = tmp_path / "restart.sqlite3"

    def _cfg():
        cfg = load_config()
        cfg.db_path = db_path
        cfg.tool_workspace_dir = tmp_path / "ws"
        cfg.ollama_api_key = None
        cfg.model_pool = []
        cfg.enable_windows_device = False
        cfg.enable_browser_device = False
        cfg.enable_perception = False
        cfg.enable_telegram = False
        return cfg

    step2_call_started = threading.Event()

    def _blocking_step2_call(_req):
        step2_call_started.set()
        time.sleep(3600)  # ne revient jamais : simule un crash EN PLEIN tick

    handles1 = bootstrap(config=_cfg(), backend=SqliteBackend(db_path))
    handles1.models.register(FakeScriptedProvider(
        [_text_response('["premiere etape", "deuxieme etape"]')], capabilities=[ModelCapability.PLANNING],
    ))
    handles1.models.register(FakeScriptedProvider([_text_response("premiere etape terminee"), _blocking_step2_call]))

    task = handles1.harness.create_long_horizon_task("objectif deux etapes", channel="cli", session_id="s1")

    assert step2_call_started.wait(timeout=3.0), "l'étape 2 n'a jamais démarré"
    frozen = handles1.harness.get_task(task.id)
    assert frozen.state.value == "RUNNING"
    assert frozen.checkpoint["plan"]["steps"][0]["status"] == "COMPLETED"
    assert frozen.checkpoint["plan"]["steps"][1]["status"] == "RUNNING"
    # Process 1 abandonné tel quel (pas de shutdown propre) — RUNNING reste
    # persisté en base exactement comme après un vrai crash.

    # bootstrap() appelle déjà harness.recover() en interne (comme au vrai
    # démarrage, RAYA_V2_MIGRATION_PLAN.md §11.3) — pas de second appel
    # manuel nécessaire, la tâche est déjà PAUSED à cet instant.
    handles2 = bootstrap(config=_cfg(), backend=SqliteBackend(db_path))
    try:
        assert handles2.harness.get_task(task.id).state.value == "PAUSED"

        handles2.models.register(FakeScriptedProvider([_text_response("deuxieme etape terminee")]))
        handles2.harness.resume_task(task.id)

        assert _wait_for(lambda: handles2.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED"), timeout_s=5.0)
        final = handles2.harness.get_task(task.id)
        assert final.state.value == "COMPLETED"
        plan = final.checkpoint["plan"]
        assert plan["steps"][0]["status"] == "COMPLETED"  # jamais refaite
        assert plan["steps"][1]["status"] == "COMPLETED"
    finally:
        handles2.shutdown()


def test_restart_never_replays_a_non_idempotent_tool_that_already_succeeded(tmp_path):
    """Chantier 15 (Axe I, cœur du scénario réel Chantier 14) : un Tool non
    idempotent (ex: un envoi) qui a RÉELLEMENT réussi avant un crash ne
    doit jamais être rappelé après un redémarrage, même si l'étape n'était
    pas encore marquée COMPLETED au moment du crash. Combine les deux
    fixes de ce chantier : le checkpoint incrémental (Axe C/I) garantit
    que l'évidence survit au "crash", et le garde-fou structurel
    idempotent=False (Axe B) garantit qu'elle n'est jamais rejouée après
    la reprise, peu importe ce que dit le modèle."""
    from raya.contracts import PermissionLevel as PL
    from raya.contracts import Tool, ToolResult, ToolResultStatus
    from raya.persistence import SqliteBackend
    from raya.runtime.bootstrap import bootstrap
    from raya.runtime.config import load_config

    db_path = tmp_path / "restart_no_replay.sqlite3"
    calls = {"n": 0}

    def _send(call):
        calls["n"] += 1
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output={"sent": True})

    def _register_send_once_tool(handles):
        handles.tools.register(
            Tool(name="demo.send_once", description="d", capability_tags=["utils"],
                 input_schema={"type": "object"}, output_schema={"type": "object"},
                 permission_level=PL.SAFE, idempotent=False),
            _send,
        )

    def _cfg():
        cfg = load_config()
        cfg.db_path = db_path
        cfg.tool_workspace_dir = tmp_path / "ws"
        cfg.ollama_api_key = None
        cfg.model_pool = []
        cfg.enable_windows_device = False
        cfg.enable_browser_device = False
        cfg.enable_perception = False
        cfg.enable_telegram = False
        return cfg

    next_call_blocks = threading.Event()

    def _blocking_next_call(_req):
        next_call_blocks.set()
        time.sleep(3600)  # ne revient jamais : simule un crash juste après le succès du tool

    handles1 = bootstrap(config=_cfg(), backend=SqliteBackend(db_path))
    _register_send_once_tool(handles1)
    handles1.models.register(FakeScriptedProvider(
        [_text_response('["envoyer le message une fois"]')], capabilities=[ModelCapability.PLANNING],
    ))
    handles1.models.register(FakeScriptedProvider([
        _tool_call_response("demo.send_once", {"text": "hello"}), _blocking_next_call,
    ]))

    task = handles1.harness.create_long_horizon_task("obj", channel="cli", session_id="s1")
    assert next_call_blocks.wait(timeout=3.0), "le tick suivant n'a jamais démarré"
    assert calls["n"] == 1
    # "Crash" ici -- process 1 abandonné sans shutdown propre, comme le test
    # de restart existant ci-dessus.

    handles2 = bootstrap(config=_cfg(), backend=SqliteBackend(db_path))
    _register_send_once_tool(handles2)
    try:
        assert handles2.harness.get_task(task.id).state.value == "PAUSED"
        # Le modèle "insiste" pour rappeler le même outil au lieu de
        # conclure -- ne doit jamais réussir une 2e fois.
        handles2.models.register(FakeScriptedProvider([
            _tool_call_response("demo.send_once", {"text": "hello"}), _text_response("terminé."),
        ]))
        handles2.harness.resume_task(task.id)
        assert _wait_for(lambda: handles2.harness.get_task(task.id).state.value in ("COMPLETED", "FAILED"), timeout_s=5.0)
        assert handles2.harness.get_task(task.id).state.value == "COMPLETED"
        assert calls["n"] == 1  # jamais rappelé pour de vrai après le redémarrage
    finally:
        handles2.shutdown()


# --- Natural language -> Task, centralisé (§13) ---

def test_tasks_create_tool_spawns_a_real_long_horizon_task_from_conversation(tmp_path):
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("tasks.create", {"objective": "rédiger un rapport"}),
        _text_response("D'accord, je m'en occupe en fond."),
    ])
    try:
        _register_planning(handles, ["rédiger le rapport"])
        from raya.contracts import Channel, HarnessRequest, InterfaceInput

        state = handles.harness.handle_request(HarnessRequest(
            channel=Channel.WEB, session_id="web-1", input=InterfaceInput(text="fais-moi un rapport"),
        ))
        assert state.status.value == "COMPLETED"
        tasks = handles.harness.list_tasks()
        assert len(tasks) == 1
        assert tasks[0].objective == "rédiger un rapport"
        assert tasks[0].owner.channel == "web"  # jamais un TelegramTaskCreator/UITaskCreator distinct
        assert tasks[0].owner.session_id == "web-1"
    finally:
        handles.shutdown()


def test_tasks_create_tool_is_never_used_for_a_greeting(tmp_path):
    """Consigne §13/§29 : aucune logique centrale ne force une Task pour une
    conversation neutre — c'est le modèle qui décide, jamais un pré-filtrage."""
    handles, fake = build_test_harness(tmp_path, [_text_response("Salut !")])
    try:
        from raya.contracts import Channel, HarnessRequest, InterfaceInput
        handles.harness.handle_request(HarnessRequest(channel=Channel.CLI, session_id="s1", input=InterfaceInput(text="salut")))
        assert handles.harness.list_tasks() == []
    finally:
        handles.shutdown()


# --- Conversation coexists with a long task (Scenario E) ---

def test_conversation_gets_an_immediate_response_while_a_long_task_runs(tmp_path):
    """Deux appels REASONING concurrents (le step de la tâche ET la
    conversation) partagent le même Model Registry — `route()` n'étant pas
    scopé par appelant, on force un ordre déterministe (jamais une course) :
    le PREMIER script entry est consommé par le step de la tâche (bloqué le
    temps que le test le vérifie), le SECOND par la conversation."""
    task_step_started = threading.Event()

    def _blocking_step(_req):
        task_step_started.set()
        time.sleep(1.0)
        return _text_response("étape terminée (non observé par ce test)")

    handles, fake = build_test_harness(tmp_path, [_blocking_step, _text_response("Je vais bien, merci !")])
    try:
        _register_planning(handles, ["étape longue"])
        task = handles.harness.create_long_horizon_task("tâche longue", channel="cli", session_id="s1")
        assert task_step_started.wait(timeout=3.0), "l'étape de la tâche n'a jamais démarré"

        from raya.contracts import Channel, HarnessRequest, InterfaceInput
        start = time.monotonic()
        state = handles.harness.handle_request(HarnessRequest(
            channel=Channel.CLI, session_id="s2", input=InterfaceInput(text="ça va ?"),
        ))
        elapsed = time.monotonic() - start
        assert state.status.value == "COMPLETED"
        assert handles.harness.response_text("s2") == "Je vais bien, merci !"
        assert elapsed < 1.0  # jamais bloqué par la tâche de fond, qui dort encore
        assert handles.harness.get_task(task.id).state.value == "RUNNING"  # toujours en cours, pas perdue
    finally:
        handles.shutdown()
