"""Harness — LE seul runtime agentique (RAYA_V2_TECHNICAL_ARCHITECTURE.md §3, §8).

Phase 3 : boucle agentique RÉELLE (consigne Phase 3 §0) — Cognition/Model
Layer/Tool System/Safety/ExecutionRecord orchestrés ici pour de vrai,
REBUILD complet (aucune classe/decision-loop copiée de core/orchestrator.py
V1, voir RAYA_V2_PHASE3_IMPLEMENTATION_REPORT.md pour les preuves). Règle
NON-NÉGOCIABLE (consigne §9) : RAYA n'affirme jamais qu'une action a eu lieu
sans ToolResult réel — World State/Task ne sont JAMAIS mis à jour depuis le
texte du modèle, uniquement depuis des ToolResult vérifiés.

Phase 2 (inchangé) : plusieurs Task Actors concurrents via TaskScheduler,
AttentionEngine, réaction aux `attention.decision_made`.

Invariant absolu inchangé : ce fichier + scheduler.py restent le SEUL endroit
définissant la boucle/l'orchestration agentique (RAYA_V2_TECHNICAL_ARCHITECTURE.md
§3.3) — aucune autre boucle nulle part (vérifié par le lint architectural).
"""

from __future__ import annotations

import json
import threading
import time
import uuid

from raya.attention import AttentionEngine, FocusTracker
from raya.cognition import (
    ActiveTaskContext,
    CapabilitySelectionRequest,
    Intent,
    LoopDetector,
    ObjectiveRelationProposal,
    ObjectiveRelationRequest,
    RecoveryAction,
    RecentlyCompletedContext,
    TraceSummary,
    VerificationOutcome,
    build_plan,
    classify_objective_relation,
    combine_outcomes,
    derive_intent,
    detect_no_progress,
    detect_repeating_cycle,
    replan_step,
    select_capabilities,
    verify_observation_against_intent,
    verify_tool_result,
)
from raya.context_engine import assemble, render_system_prompt
from raya.contracts import (
    Capability,
    ChannelScope,
    Confidence,
    ContentPart,
    Device,
    DeviceType,
    ErrorInfo,
    Event,
    ExecutionRecord,
    HarnessRequest,
    HarnessState,
    HarnessStatus,
    Message,
    MemoryEntry,
    MemoryLayer,
    MemoryLifecycle,
    MemoryType,
    NotificationChannel,
    FinishReason,
    ModelCapability,
    ModelRequest,
    Plan,
    PlanStep,
    RequestedToolCall,
    SpatialError,
    StepState,
    Task,
    TaskEvent,
    TaskEventPayload,
    TaskOwner,
    TaskState,
    ToolCall,
    ToolCallRequester,
    ToolResultStatus,
    WorldStateFact,
    from_dict,
    new_id,
    next_runnable_step,
    plan_is_complete,
    plan_is_stuck,
    to_dict,
)
from raya.devices import DeviceRegistry
from raya.event_bus import BackpressurePolicy, EventBus
from raya.memory import MemoryStore
from raya.models import ModelRegistry, describe_active, route as model_route
from raya.observability import log
from raya.safety import SafetyService
from raya.spatial import SceneStore
from raya.spatial.renderer.threejs_adapter import ThreeJSAdapter
from raya.tasks import TaskRegistry
from raya.tasks import priority as prio
from raya.tools import ToolRegistry, discover as discover_tools, execute as execute_tool
from raya.world_state import WorldStateStore

from .cancellation import checkpoint_or_abort
from .execution_records import ExecutionRecordRepository
from .scheduler import TaskScheduler
from .session import SessionStore

_CHANNEL_TO_SCOPE = {
    "voice": ChannelScope.VOICE,
    "web": ChannelScope.CHAT,
    "cli": ChannelScope.CHAT,
    "desktop": ChannelScope.CHAT,
    "mobile": ChannelScope.IOS,
    "api": ChannelScope.CHAT,
}

_SIMULATED_TASK_STEPS = 5
_SIMULATED_STEP_DELAY_S = 0.05
_CHECKPOINT_EVERY_N_STEPS = 2  # throttlé — pas d'écriture SQLite à chaque step (§19/§46)
_ATTENTION_LOG_MAXLEN = 50
# Chantier 16 (Contextualisation) : mêmes états exclus que tasks.list
# (Chantier 14, tools/catalog/tasks.py) — jamais l'historique terminal
# complet dans le contexte d'une conversation normale.
_TERMINAL_TASK_STATES_FOR_CONTEXT = frozenset({TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED})

# RC1 — Conversational Objective Working State constants
_CONV_OBJ_KIND = "conversational_objective"
_TASK_EXPIRATION_UNTOUCHED_TURNS = 10
_RECENTLY_COMPLETED_OBJECTIVE_WINDOW_TURNS = 3
_RELEVANT_INFO_CAP = 20

# RC2 — Pre-model capability selection constants
# Always included regardless of selector output — covers time queries, task
# management, and other baseline needs without requiring a Cognition call.
BASELINE_TAGS: frozenset[str] = frozenset({
    "system.read",
    "tasks.control",
    "tasks.read",
})


class Harness:
    def __init__(
        self,
        *,
        world_state: WorldStateStore,
        memory: MemoryStore,
        tasks: TaskRegistry,
        safety: SafetyService,
        model_registry: ModelRegistry,
        bus: EventBus,
        tools_registry: ToolRegistry | None = None,
        execution_records: ExecutionRecordRepository | None = None,
        scene_store: SceneStore | None = None,
        devices: DeviceRegistry | None = None,
        context_budget_tokens: int = 4096,
        max_concurrent_tasks: int = 2,
        max_tool_iterations: int = 4,
    ) -> None:
        self._world_state = world_state
        self._memory = memory
        self._tasks = tasks
        self._safety = safety
        self._model_registry = model_registry
        self._bus = bus
        self._tools_registry = tools_registry or ToolRegistry()
        # Phase 8 : lecture seule pour l'exposition UI (get_scene/describe_scene/
        # get_spatial_render_payload) — la SEULE écriture légitime reste
        # tools/catalog/spatial.py, jamais le Harness lui-même (RAYA_V2_
        # TECHNICAL_ARCHITECTURE.md §1.2, même principe que world_state).
        self._scene_store = scene_store or SceneStore()
        # Phase 9 : lecture/enregistrement pour les interfaces (Device Registry,
        # RAYA_V2_ARCHITECTURAL_BLUEPRINT.md §19) — même principe que
        # scene_store/world_state, un seul point d'écriture réel
        # (raya/devices/registry.py), le Harness ne fait que le exposer.
        self._devices = devices or DeviceRegistry()
        self._execution_records = execution_records
        self._context_budget_tokens = context_budget_tokens
        self._max_tool_iterations = max_tool_iterations
        self._sessions = SessionStore()
        self._last_response_text: dict[str, str] = {}
        self._last_context: dict[str, object] = {}
        self._last_tool_trace: dict[str, list[dict]] = {}
        self._loop_detector = LoopDetector()

        self.focus = FocusTracker()
        self._scheduler = TaskScheduler(tasks, safety, max_concurrent_tasks=max_concurrent_tasks)
        self.attention = AttentionEngine(world_state, tasks, self.focus, bus)

        self._attention_log: list[dict] = []
        self._attention_lock = threading.Lock()
        # Abonné critique : une décision d'attention perdue silencieusement
        # (ex: un INTERRUPT jamais traité) est dangereux — jamais drop_oldest.
        bus.subscribe(
            "attention.decision_made", self._on_attention_decision, subscriber="harness.attention",
            backpressure_policy=BackpressurePolicy.BLOCK_PUBLISHER_WITH_TIMEOUT,
        )

    # ------------------------------------------------------------------
    # Boucle agentique nominale (RAYA_V2_TECHNICAL_ARCHITECTURE.md §3.1)
    # ------------------------------------------------------------------

    def handle_request(self, request: HarnessRequest) -> HarnessState:
        state = self._sessions.get_or_create(request.session_id, request.correlation_id, request.channel)
        state.current_turn += 1
        state.status = HarnessStatus.ASSEMBLING_CONTEXT

        self._bus.publish(
            Event(type="harness.turn_started", source="harness", correlation_id=request.correlation_id,
                  payload={"session_id": request.session_id, "turn": state.current_turn})
        )

        if checkpoint_or_abort(state, self._safety):
            self._emit_turn_failed(state, request.correlation_id)
            return state

        channel_scope = _CHANNEL_TO_SCOPE.get(request.channel.value, ChannelScope.CHAT)

        # Identité runtime RÉELLE (stabilisation pré-Phase 7 §3) — jamais
        # inventée : describe_active() reflète exactement ce que model_route()
        # sélectionnerait pour ce tour, ou None si aucun provider n'est
        # disponible (le modèle ne doit jamais halluciner son propre runtime).
        runtime_identity = self.active_model_identity()

        # RC1 : récupère ou crée l'objectif conversationnel de cette session
        # AVANT l'assemblage du contexte — le Working State (relevant_information,
        # next_checkpoint) doit être visible dans le contexte du tour courant.
        conv_task = self._get_or_resume_conv_objective(
            request.session_id, request.input.text or "", state.current_turn
        )

        # Conversation != task execution (§7, §10) : ce tour n'attend jamais
        # une Task de fond et ne la modifie jamais, quelle que soit la
        # décision d'Attention sur cet événement (toujours PROCESS_NOW pour
        # une requête directe — voir AttentionEvaluator._decide_interface_request).
        # Chantier 16 (Contextualisation, Task context) : les tâches actives
        # (rappels programmés, téléchargements en fond...) sont désormais
        # visibles même pendant une conversation NORMALE, pas seulement
        # depuis l'intérieur du step d'une tâche de fond en cours (§7 de la
        # consigne : "arrête ça" doit pouvoir référer à une tâche active).
        # Mêmes états exclus que tasks.list (Chantier 14) — jamais l'historique
        # terminal complet, jamais un dump inconditionnel (consigne §22).
        # RC1 : les conv_obj d'AUTRES sessions sont exclues — chaque session
        # ne voit que son propre Working State, jamais celui d'une autre.
        active_tasks = tuple(
            t for t in self._tasks.list()
            if t.state not in _TERMINAL_TASK_STATES_FOR_CONTEXT
            and not (
                isinstance(t.checkpoint, dict)
                and t.checkpoint.get("kind") == _CONV_OBJ_KIND
                and (t.owner is None or t.owner.session_id != request.session_id)
            )
        )

        # RC1 : tâches conversationnelles récemment complétées pour la
        # fenêtre de contexte « récemment terminé » (3 tours).
        recently_completed_conv = self._get_recently_completed_conv(
            request.session_id, state.current_turn
        )

        context = assemble(
            session_id=request.session_id,
            channel_scope=channel_scope,
            world_state=self._world_state,
            memory=self._memory,
            budget_tokens=self._context_budget_tokens,
            query_text=request.input.text or "",
            active_tasks=active_tasks,
            runtime_identity=runtime_identity,
            recently_completed_conv_tasks=recently_completed_conv,
            current_turn=state.current_turn,
        )
        self._last_context[request.session_id] = context

        if request.input.text:
            self._memory.write(
                MemoryEntry(
                    type=MemoryType.FACT,
                    layer=MemoryLayer.CONVERSATION,
                    channel_scope=channel_scope,
                    content=request.input.text,
                    provenance=f"interface:{request.channel.value}",
                    lifecycle=MemoryLifecycle.CONFIRMED,
                )
            )

        if checkpoint_or_abort(state, self._safety):
            self._emit_turn_failed(state, request.correlation_id)
            return state

        # RC2 : sélection pre-loop des capability tags — READ-ONLY sur RC1 state.
        # conv_task et recently_completed_conv sont lus ici, jamais modifiés.
        selected_tags = self._select_capability_tags(
            request, conv_task, recently_completed_conv
        )

        response_text = self._run_agentic_loop(request, state, context, selected_tags)
        self._last_response_text[request.session_id] = response_text
        self._remember_assistant_turn(request.session_id, channel_scope, response_text)

        # RC1 : post-loop — promotion d'evidence + Cognition + checkpoint/pause
        # du Working State. Appelé APRÈS _remember_assistant_turn car Cognition
        # lit le texte du tour (jamais avant que la réponse ne soit stabilisée).
        self._finalize_conversational_objective(request, state, conv_task)

        # AWAITING_USER_INPUT (confirmation Safety en attente, voir
        # confirm_pending() ci-dessous) est un arrêt volontaire du tour —
        # jamais réécrit en COMPLETED tant que l'utilisateur n'a pas tranché.
        if state.status not in (HarnessStatus.FAILED, HarnessStatus.AWAITING_USER_INPUT):
            state.status = HarnessStatus.COMPLETED
        state.checkpoint()

        self._bus.publish(
            Event(type="harness.turn_completed", source="harness", correlation_id=request.correlation_id,
                  payload={"session_id": request.session_id, "turn": state.current_turn})
        )
        return state

    def _emit_turn_failed(self, state: HarnessState, correlation_id: str) -> None:
        self._bus.publish(
            Event(type="harness.turn_failed", source="harness", correlation_id=correlation_id,
                  payload={"session_id": state.session_id, "error": state.error.code if state.error else None})
        )

    def response_text(self, session_id: str) -> str:
        return self._last_response_text.get(session_id, "")

    def _remember_assistant_turn(self, session_id: str, channel_scope: ChannelScope, text: str) -> None:
        """Fix Phase 11 (context continuity) : avant cette phase, SEUL le
        message utilisateur était écrit en mémoire CONVERSATION (voir
        `handle_request`, quelques lignes plus haut) — le modèle ne voyait
        donc JAMAIS ses propres réponses passées au tour suivant, rendant
        impossible toute résolution de référent portant sur sa propre
        réponse ("Oui lance-la" après "je peux lancer la calculatrice").
        Provenance suffixée `:assistant` — voir
        `context_engine/assembler.py::_entry_role`, jamais un second champ
        de contrat ni un second système de mémoire."""
        if not text:
            return
        self._memory.write(
            MemoryEntry(
                type=MemoryType.FACT,
                layer=MemoryLayer.CONVERSATION,
                channel_scope=channel_scope,
                content=text,
                provenance=f"harness:{session_id}:assistant",
                lifecycle=MemoryLifecycle.CONFIRMED,
            )
        )

    def last_context(self, session_id: str):
        return self._last_context.get(session_id)

    # ------------------------------------------------------------------
    # RC1 — Conversational Objective Working State
    # Harness SEUL écrit dans TaskRegistry. Cognition PROPOSE (post-loop
    # uniquement). REPLACE ne se produit jamais sur erreur de Cognition.
    # ------------------------------------------------------------------

    def _get_or_resume_conv_objective(
        self, session_id: str, user_text: str, current_turn: int
    ) -> "Task | None":
        for t in self._tasks.list():
            if (
                t.state == TaskState.PAUSED
                and isinstance(t.checkpoint, dict)
                and t.checkpoint.get("kind") == _CONV_OBJ_KIND
                and t.owner is not None
                and t.owner.session_id == session_id
            ):
                if t.checkpoint.get("turns_active", 0) >= _TASK_EXPIRATION_UNTOUCHED_TURNS:
                    self._tasks.cancel(t.id)
                    break
                self._tasks.resume(t.id)
                return self._tasks.get(t.id)
        return self._create_conv_objective(session_id, user_text)

    def _create_conv_objective(self, session_id: str, user_text: str) -> "Task":
        task = self._tasks.create(
            objective=user_text[:120],
            owner=TaskOwner(channel="conversation", session_id=session_id),
            correlation_id=new_id("conv"),
        )
        task = self._tasks.start(task.id)
        self._tasks.checkpoint(task.id, {
            "kind": _CONV_OBJ_KIND,
            "objective": user_text[:120],
            "relevant_information": [],
            "last_domain_of_activity": None,
            "turns_active": 0,
            "completed_at_turn": None,
            "result_summary": None,
            "next_checkpoint": None,
        })
        return self._tasks.get(task.id)

    def _get_recently_completed_conv(
        self, session_id: str, current_turn: int
    ) -> "tuple[Task, ...]":
        window = _RECENTLY_COMPLETED_OBJECTIVE_WINDOW_TURNS
        result = []
        for t in self._tasks.list():
            if (
                t.state == TaskState.COMPLETED
                and isinstance(t.checkpoint, dict)
                and t.checkpoint.get("kind") == _CONV_OBJ_KIND
                and t.owner is not None
                and t.owner.session_id == session_id
            ):
                completed_at = t.checkpoint.get("completed_at_turn")
                if completed_at is not None and (current_turn - completed_at) <= window:
                    result.append(t)
        return tuple(result)

    def _finalize_conversational_objective(
        self, request: "HarnessRequest", state: "HarnessState", conv_task: "Task | None"
    ) -> None:
        """Post-loop: promote evidence, call Cognition if needed, checkpoint+pause."""
        if conv_task is None:
            return
        session_id = request.session_id
        current_turn = state.current_turn
        trace = self._last_tool_trace.get(session_id, [])
        ckpt = conv_task.checkpoint or {}

        relevant_info = list(ckpt.get("relevant_information", []))
        last_domain = ckpt.get("last_domain_of_activity")
        for entry in trace:
            if entry.get("status") == "success":
                evidence = entry.get("evidence") or {}
                if evidence:
                    relevant_info.append({
                        "turn": current_turn,
                        "source": entry.get("tool_name", "unknown"),
                        "content": json.dumps(
                            evidence, ensure_ascii=False, default=str
                        )[:200],
                    })
                tool_name = entry.get("tool_name", "")
                if "." in tool_name:
                    last_domain = tool_name.split(".")[0]
        if len(relevant_info) > _RELEVANT_INFO_CAP:
            relevant_info = relevant_info[-_RELEVANT_INFO_CAP:]

        turns_active = ckpt.get("turns_active", 0) + 1
        objective = ckpt.get("objective", request.input.text or "")
        next_checkpoint = ckpt.get("next_checkpoint")

        if turns_active > 1:
            proposal = self._classify_conv_objective_relation(
                request, state, conv_task, trace
            )
            if proposal.relation == "REPLACE" and proposal.proposed_new_objective:
                self._tasks.checkpoint(conv_task.id, {
                    "kind": _CONV_OBJ_KIND,
                    "objective": objective,
                    "relevant_information": relevant_info,
                    "last_domain_of_activity": last_domain,
                    "turns_active": turns_active,
                    "completed_at_turn": current_turn,
                    "result_summary": "replaced by new objective",
                    "next_checkpoint": next_checkpoint,
                })
                self._tasks.complete(conv_task.id, {"summary": "replaced"})
                new_task = self._create_conv_objective(
                    session_id, proposal.proposed_new_objective
                )
                if proposal.proposed_next_checkpoint:
                    new_ckpt = dict(new_task.checkpoint)
                    new_ckpt["next_checkpoint"] = proposal.proposed_next_checkpoint
                    self._tasks.checkpoint(new_task.id, new_ckpt)
                self._tasks.pause(new_task.id)
                return
            if proposal.relation == "CORRECT" and proposal.proposed_new_objective:
                objective = proposal.proposed_new_objective[:120]
            if proposal.proposed_next_checkpoint:
                next_checkpoint = proposal.proposed_next_checkpoint

        self._tasks.checkpoint(conv_task.id, {
            "kind": _CONV_OBJ_KIND,
            "objective": objective,
            "relevant_information": relevant_info,
            "last_domain_of_activity": last_domain,
            "turns_active": turns_active,
            "completed_at_turn": None,
            "result_summary": None,
            "next_checkpoint": next_checkpoint,
        })
        self._tasks.pause(conv_task.id)

    def _classify_conv_objective_relation(
        self,
        request: "HarnessRequest",
        state: "HarnessState",
        conv_task: "Task",
        trace: list[dict],
    ) -> ObjectiveRelationProposal:
        ckpt = conv_task.checkpoint or {}
        rel_info = ckpt.get("relevant_information", [])
        summary_entries = rel_info[-5:] if len(rel_info) > 5 else rel_info
        summary = "; ".join(
            e.get("content", "") for e in summary_entries if e.get("content")
        )

        active_ctx = ActiveTaskContext(
            objective=ckpt.get("objective", ""),
            relevant_information_summary=summary[:500],
            last_domain_of_activity=ckpt.get("last_domain_of_activity"),
            turns_since_created=ckpt.get("turns_active", 0),
        )

        domains = list({
            t.get("tool_name", "").split(".")[0]
            for t in trace if "." in t.get("tool_name", "")
        })
        tools = list({t.get("tool_name", "") for t in trace if t.get("tool_name")})
        action_or_read = "INFO"
        for entry in trace:
            tool_def = self._tools_registry.get(entry.get("tool_name", ""))
            if tool_def is None:
                continue
            if any("interact" in tag for tag in tool_def.capability_tags):
                action_or_read = "ACTION"
                break
            if action_or_read == "INFO" and any(
                "read" in tag or "browser" in tag for tag in tool_def.capability_tags
            ):
                action_or_read = "READ"

        trace_summary = TraceSummary(
            domains_touched=domains,
            tools_called=tools,
            action_or_read=action_or_read,
        )

        recently_completed_ctx = None
        recently_completed = self._get_recently_completed_conv(
            request.session_id, state.current_turn
        )
        if recently_completed:
            rc = recently_completed[0]
            rc_ckpt = rc.checkpoint or {}
            completed_at = rc_ckpt.get("completed_at_turn", state.current_turn)
            recently_completed_ctx = RecentlyCompletedContext(
                objective=rc_ckpt.get("objective", rc.objective),
                result_summary=(rc_ckpt.get("result_summary") or "")[:200],
                turns_since_completed=state.current_turn - completed_at,
            )

        req = ObjectiveRelationRequest(
            user_text=request.input.text or "",
            active_task=active_ctx,
            trace_summary=trace_summary,
            recently_completed_objective=recently_completed_ctx,
        )
        return classify_objective_relation(req, self._model_registry, request.correlation_id)

    def last_tool_trace(self, session_id: str) -> list[dict]:
        """Observabilité (consigne §32) : objectif -> tool discovery -> tool
        call -> execution -> result -> verification, reconstructible sans
        chain-of-thought brut — juste la séquence structurée réellement exécutée."""
        return list(self._last_tool_trace.get(session_id, []))

    def last_turn_intent(self, session_id: str) -> Intent:
        """Chantier 12 §D (Response vs Action) : dérivé du VRAI comportement
        observé pendant le dernier tour (les Tools réellement appelés, via
        leurs capability_tags) — jamais un pré-classifieur de texte qui
        déciderait avant/à la place du modèle. INFORMATION si aucun tool
        n'a été appelé ou si tous les tools appelés sont des lectures pures."""
        tags: list[str] = []
        for entry in self._last_tool_trace.get(session_id, []):
            tool = self._tools_registry.get(entry.get("tool_name", ""))
            if tool is not None:
                tags.extend(tool.capability_tags)
        return derive_intent(tags)

    # ------------------------------------------------------------------
    # Boucle agentique RÉELLE (consigne Phase 3 §0-12) : PLAN -> ACTION ->
    # OBSERVE -> VERIFY -> CONTINUE/REPLAN/ESCALATE. Bornée, jamais infinie.
    # ------------------------------------------------------------------

    def _discover_tool_schemas(self, selected_tags: list[str] | None = None) -> list[dict]:
        """Le modèle ne reçoit PAS forcément tous les outils (consigne §7) —
        discovery sur les capability_tags sélectionnés par RC2 (pre-loop),
        ou sur l'union complète si aucune sélection fournie (fallback = comportement
        courant). Jamais un routage texte codé en dur par mot-clé (consigne §22)."""
        tags = selected_tags if selected_tags is not None else self._tools_registry.all_capability_tags()
        if not tags:
            return []
        return [to_dict(t) for t in discover_tools(self._tools_registry, tags)]

    def _select_capability_tags(
        self,
        request: "HarnessRequest",
        conv_task: "Task | None",
        recently_completed_conv: "tuple[Task, ...]",
    ) -> list[str]:
        """RC2 pre-loop: select capability tags via one Cognition classification call.

        Returns a list of tags to pass to _discover_tool_schemas().
        All failure paths fall back to all_capability_tags() (current behavior).
        NEVER modifies conv_task or any RC1 state — read-only.
        """
        all_tags = self._tools_registry.all_capability_tags()

        # Gate: if the entire registry is already just baseline, skip selector.
        non_baseline = set(all_tags) - BASELINE_TAGS
        if not non_baseline:
            return list(BASELINE_TAGS & set(all_tags))

        # Extract RC1 Working State — read-only
        objective_text: str | None = None
        last_domain: str | None = None
        next_checkpoint_domain: str | None = None
        if conv_task is not None:
            ckpt = conv_task.checkpoint or {}
            objective_text = ckpt.get("objective") or None
            last_domain = ckpt.get("last_domain_of_activity") or None
            next_cp = ckpt.get("next_checkpoint")
            if isinstance(next_cp, dict):
                next_checkpoint_domain = next_cp.get("domain") or None

        recently_completed_objective: str | None = None
        if recently_completed_conv:
            latest = max(
                recently_completed_conv,
                key=lambda t: (t.checkpoint or {}).get("completed_at_turn", 0),
            )
            recently_completed_objective = (latest.checkpoint or {}).get("objective") or None

        sel_request = CapabilitySelectionRequest(
            user_text=request.input.text or "",
            available_tags=all_tags,
            objective_text=objective_text,
            last_domain=last_domain,
            next_checkpoint_domain=next_checkpoint_domain,
            recently_completed_objective=recently_completed_objective,
        )

        try:
            proposal = select_capabilities(
                sel_request, self._model_registry, request.correlation_id
            )
        except Exception:
            proposal = None

        # Confidence policy: low → full fallback (same as None)
        if proposal is None or proposal.confidence == "low":
            return all_tags

        # Merge BASELINE + proposal, intersect with registered to drop hallucinated tags
        selected = (BASELINE_TAGS | set(proposal.selected_tags)) & set(all_tags)
        if not selected:
            return all_tags

        return sorted(selected)

    def _promote_observations_and_verify(self, requested, tool_result, outcome: VerificationOutcome) -> VerificationOutcome:
        """Phase 7 §5-9 : Device Agent -> Tool -> Harness -> World State,
        JAMAIS Device Agent -> World State ni Device Agent -> Model direct
        (consigne §4). Entièrement piloté par `Tool.observation` — déclaré
        UNE FOIS par capacité dans `tools/catalog/{pc,browser}.py`, jamais un
        `if requested.tool_name == ...` ici (consigne §13). Un ToolResult qui
        n'est pas SUCCESS ne produit jamais d'observation (rien à observer
        d'une action qui a échoué à s'exécuter)."""
        if tool_result.status != ToolResultStatus.SUCCESS:
            return outcome
        tool_def = self._tools_registry.get(requested.tool_name)
        if tool_def is None:
            return outcome
        for spec in tool_def.observation:
            value = (tool_result.evidence or {}).get(spec.evidence_field)
            if value is None:
                value = (tool_result.output or {}).get(spec.evidence_field)
            if value is None:
                continue  # rien d'exploitable pour ce spec sur ce résultat précis
            key = spec.key
            if spec.key_from_argument is not None:
                # Chantier 12 §C : domaine à faits multiples (ex: un dossier
                # découvert parmi d'autres) — la clé vient de ce que le
                # modèle a réellement demandé, jamais d'une valeur fixe.
                arg_value = requested.arguments.get(spec.key_from_argument)
                if not arg_value:
                    continue
                key = str(arg_value).strip().lower()
            self._world_state.apply_update(WorldStateFact(
                domain=spec.domain, key=key, value=value,
                source=f"tool:{requested.tool_name}", confidence=spec.confidence,
                freshness_ttl_s=spec.freshness_ttl_s,
            ))
            if spec.expected_argument is not None:
                expected = requested.arguments.get(spec.expected_argument)
                content_outcome = verify_observation_against_intent(expected, value)
                outcome = combine_outcomes(outcome, content_outcome)
        return outcome

    @staticmethod
    def _summarize_tool_result(tool_name: str, tool_result, outcome: VerificationOutcome) -> str:
        """Résumé STRUCTURÉ et générique (jamais une phrase par outil codée
        en dur, consigne §22) — construit uniquement à partir du ToolResult
        réel, jamais d'une affirmation du modèle (consigne §9)."""
        output = tool_result.output
        if tool_name == "browser.read_page":
            output = Harness._compact_read_page_output(output)
        payload = {
            "tool": tool_name,
            "status": tool_result.status.value,
            "verification": outcome.value,
            "output": output,
            "evidence": tool_result.evidence,
            "error": to_dict(tool_result.error) if tool_result.error else None,
        }
        return json.dumps(payload, ensure_ascii=False, default=str)

    @staticmethod
    def _compact_old_dom_messages(messages: "list[Message]") -> None:
        """Quand un nouveau résultat browser.read_page arrive, compacte les
        précédents en résumés {url, title, buttons_count, links_count}.
        Préserve le plus récent intégralement. In-place, jamais de suppression."""
        found_recent = False
        for msg in reversed(messages):
            if msg.role != "tool" or not msg.content:
                continue
            part = msg.content[0]
            if not isinstance(part, ContentPart) or part.type != "text":
                continue
            try:
                data = json.loads(part.value)
            except (json.JSONDecodeError, ValueError):
                continue
            if data.get("tool") != "browser.read_page":
                continue
            if not found_recent:
                found_recent = True
                continue
            output = data.get("output")
            if not isinstance(output, dict) or output.get("_compacted"):
                continue
            data["output"] = {
                "url": output.get("url"),
                "title": output.get("title"),
                "buttons_count": len(output.get("buttons") or []),
                "links_count": len(output.get("links") or []),
                "_compacted": True,
            }
            msg.content[0] = ContentPart(type="text", value=json.dumps(data, ensure_ascii=False, default=str))

    @staticmethod
    def _compact_read_page_output(output) -> "dict | str | None":
        """Borne structurellement les sorties browser.read_page (20 boutons, 25 liens).
        Préserve url/title/cookie_banner/inputs intégralement. Repli sûr si JSON invalide."""
        _BUTTONS_CAP = 20
        _LINKS_CAP = 25
        if isinstance(output, dict):
            data = output
        elif isinstance(output, str):
            try:
                data = json.loads(output)
            except (json.JSONDecodeError, ValueError):
                return output
        else:
            return output
        if not isinstance(data, dict):
            return output
        buttons = data.get("buttons") or []
        links = data.get("links") or []
        result: dict = {
            "url": data.get("url"),
            "title": data.get("title"),
            "cookie_banner": data.get("cookie_banner"),
            "inputs": data.get("inputs") or [],
            "buttons": buttons[:_BUTTONS_CAP],
            "links": links[:_LINKS_CAP],
        }
        if len(buttons) > _BUTTONS_CAP:
            result["buttons_capped"] = len(buttons) - _BUTTONS_CAP
        if len(links) > _LINKS_CAP:
            result["links_capped"] = len(links) - _LINKS_CAP
        return result

    def _explain_blocked_turn(self, objective_text: str, trace: list[dict], reason_hint: str,
                               correlation_id: str) -> str:
        """Passe 'Targeted Execution Repair' (Partie 10) : remplace les
        messages d'échec génériques codés en dur ("Je n'arrive pas à faire
        progresser 'browser.navigate'...") par une explication structurée
        (objectif / réussi / tenté / blocage / prochaine action) construite
        UNIQUEMENT à partir de la vraie trace d'exécution — jamais une
        invention. Même pattern architectural que
        `_natural_response_for_tool_result` (Phase 11 §4) : UN appel modèle
        borné supplémentaire, repli honnête générique si aucun modèle n'est
        disponible — un seul endroit qui produit ce texte, utilisé par tous
        les points d'escalade de la boucle agentique."""
        trace_summary = json.dumps([
            {"tool": t.get("tool_name"), "status": t.get("status"), "outcome": t.get("outcome")}
            for t in trace
        ], ensure_ascii=False, default=str)
        messages = [
            Message(role="system", content=[ContentPart(type="text", value=(
                "The agent got stuck trying to complete the user's request. Using ONLY the "
                "objective and the real tool-call trace given below (never invent anything "
                "beyond them), explain to the user, in their language, in a few short honest "
                "sentences: (1) what has been achieved so far, (2) what was attempted, (3) what "
                "is currently blocking progress, (4) what the user could do next, if anything "
                "(e.g. an action only they can take, like choosing a profile or logging in). If "
                "the exact cause isn't clear from the trace, say so rather than guessing. Never "
                "mention internal tool names, JSON, or expose raw internal fields."
            ))]),
            Message(role="user", content=[ContentPart(type="text", value=(
                f"Objective: {objective_text}\nWhy the agent stopped: {reason_hint}\n"
                f"Tool call trace (tool/status/verification): {trace_summary}"
            ))]),
        ]
        response = model_route(self._model_registry, ModelRequest(
            capability=ModelCapability.REASONING, messages=messages, correlation_id=correlation_id,
        ))
        if response.finish_reason != FinishReason.ERROR:
            text = "".join(p.value for p in response.content if p.type == "text").strip()
            if text:
                return text
        # Repli honnête GÉNÉRIQUE (jamais une invention) si aucun modèle
        # n'est disponible — même discipline que les autres replis Harness.
        attempted = ", ".join(sorted({t.get("tool_name", "?") for t in trace})) or "aucune action"
        return f"{reason_hint} J'ai tenté : {attempted}. Je préfère le dire plutôt que prétendre avoir terminé."

    @staticmethod
    def _build_finalization_trace(trace: list[dict], max_entries: int = 8) -> list[dict]:
        """Trace bornée pour _finalize_turn : 4 dernières actions + actions
        plus anciennes portant de l'evidence. Évite de passer la trace brute entière."""
        if not trace:
            return []
        if len(trace) <= max_entries:
            return list(trace)
        recent_count = min(4, max_entries, len(trace))
        recent = trace[-recent_count:]
        recent_start = len(trace) - recent_count
        slots_left = max_entries - recent_count
        if slots_left > 0:
            older_with_evidence = [t for t in trace[:recent_start] if t.get("evidence")]
            selected_older = older_with_evidence[-slots_left:]
        else:
            selected_older = []
        return selected_older + recent

    def _finalize_turn(self, objective_text: str, trace: list[dict], correlation_id: str) -> str:
        """Budget d'itérations d'outils épuisé. BUDGET EXHAUSTED ≠ TASK FAILED.
        Distinct de _explain_blocked_turn (réservé au vrai blocage LoopDetector).
        Passe available_tools=None pour prévenir structurellement un 13e appel d'outil."""
        finalization_trace = self._build_finalization_trace(trace)
        trace_summary = json.dumps([
            {"tool": t.get("tool_name"), "status": t.get("status"),
             "outcome": t.get("outcome"), "evidence": t.get("evidence")}
            for t in finalization_trace
        ], ensure_ascii=False, default=str)
        browser_facts = self._world_state.retrieve_relevant(("browser",))
        ws_relevant = {f.key: f.value for f in browser_facts
                       if f.key in ("current_url", "last_clicked_target")}
        user_content = (
            f"User request: {objective_text}\n"
            f"Last tool results and evidence:\n{trace_summary}"
        )
        if ws_relevant:
            user_content += f"\nCurrent observed state: {json.dumps(ws_relevant, ensure_ascii=False, default=str)}"
        messages = [
            Message(role="system", content=[ContentPart(type="text", value=(
                "You have used your full action budget working on the user's request. "
                "Using ONLY the tool results and evidence shown below, report to the user "
                "HONESTLY and in their language: what was actually accomplished. "
                "If the evidence confirms the objective was completed, say so clearly. "
                "If the evidence is incomplete or absent, acknowledge the uncertainty honestly. "
                "Do NOT claim success without evidence. "
                "Do NOT declare failure simply because the budget was exhausted. "
                "Do NOT mention tools, JSON, traces, budgets, or any technical detail."
            ))]),
            Message(role="user", content=[ContentPart(type="text", value=user_content)]),
        ]
        response = model_route(self._model_registry, ModelRequest(
            capability=ModelCapability.REASONING,
            messages=messages,
            correlation_id=correlation_id,
            available_tools=None,
            context_budget_tokens=self._context_budget_tokens,
        ))
        if response.finish_reason != FinishReason.ERROR:
            text = "".join(p.value for p in response.content if p.type == "text").strip()
            if text:
                return text
        # Repli honnête si aucun modèle disponible
        last_evidence = next((t.get("evidence") for t in reversed(trace) if t.get("evidence")), None)
        if last_evidence:
            return (
                "J'ai atteint la limite de mes actions. "
                f"Dernière observation : {json.dumps(last_evidence, ensure_ascii=False, default=str)}"
            )
        attempted = ", ".join(sorted({t.get("tool_name", "?") for t in trace})) or "aucune action"
        return (
            f"J'ai atteint la limite de mes actions ({len(trace)} tentée(s) : {attempted}) "
            "sans confirmation de résultat."
        )

    def _run_agentic_loop(
        self,
        request: HarnessRequest,
        state: HarnessState,
        context,
        selected_tags: list[str] | None = None,
    ) -> str:
        context_budget_tokens = context.budget_tokens
        # BUG CORRIGÉ (stabilisation pré-Phase 7) : le Context assemblé par
        # context_engine.assemble() (identité RAYA, runtime/modèle actif,
        # mémoire personnelle, historique) était calculé puis intégralement
        # ignoré ici — seul le texte brut du tour courant partait au modèle.
        # render_system_prompt() le sérialise en un message système réel,
        # effectivement envoyé (voir raya/models/providers/ollama_cloud.py::
        # _messages_to_ollama(), qui transmet déjà n'importe quel role tel quel).
        messages: list[Message] = []
        system_prompt = render_system_prompt(context)
        if system_prompt:
            messages.append(Message(role="system", content=[ContentPart(type="text", value=system_prompt)]))
        messages.append(Message(role="user", content=[ContentPart(type="text", value=request.input.text or "")]))
        # RC2: use pre-selected tags when provided; None = full discovery (current behavior)
        available_tools = self._discover_tool_schemas(selected_tags)
        trace: list[dict] = []
        loop_key = f"{request.session_id}:{state.current_turn}"
        # Anti-boucle par ÉTAT (Phase 4 §10) — complémentaire à LoopDetector
        # (échecs identiques répétés) : une séquence d'actions qui RÉUSSISSENT
        # chacune mais ne fait jamais progresser l'état (ex: navigue A->B->A->B)
        # n'est jamais détectée par LoopDetector seul. Générique : n'importe
        # quel ToolResult.evidence exposant "url" alimente cet historique
        # (aujourd'hui : les outils browser.*).
        state_history: list[str] = []
        # Passe "Targeted Execution Repair" (Partie 6) : `LoopDetector` ne
        # détecte que des échecs IDENTIQUES (même tool_name+arguments exacts)
        # répétés — un modèle qui retente le MÊME outil avec des arguments
        # DIFFÉRENTS à chaque fois (ex: deviner une URL différente à chaque
        # browser.navigate) lui échappe totalement, tout comme au détecteur
        # de cycle d'état ci-dessus (qui exige un état identique ou un motif
        # A/B répété — jamais atteint si chaque URL devinée est nouvelle).
        # Compteur générique complémentaire : jamais un hard-stop, juste un
        # rappel injecté au modèle pour changer de stratégie AVANT que la
        # détection de boucle ne devienne la seule protection restante.
        _NUDGE_THRESHOLD = 3
        consecutive_tool_name: str | None = None
        consecutive_failures = 0

        for _iteration in range(self._max_tool_iterations):
            if checkpoint_or_abort(state, self._safety):
                self._emit_turn_failed(state, request.correlation_id)
                self._last_tool_trace[request.session_id] = trace
                return "[interrompu par STOP]"

            state.status = HarnessStatus.AWAITING_MODEL
            model_request = ModelRequest(
                capability=ModelCapability.REASONING,
                messages=messages,
                correlation_id=request.correlation_id,
                available_tools=available_tools or None,
                context_budget_tokens=context_budget_tokens,
            )
            model_response = model_route(self._model_registry, model_request)

            if model_response.finish_reason == FinishReason.ERROR:
                state.status = HarnessStatus.FAILED
                state.error = model_response.error
                self._emit_turn_failed(state, request.correlation_id)
                self._last_tool_trace[request.session_id] = trace
                return (
                    f"Je ne peux pas répondre — erreur modèle ({model_response.error.code}) : "
                    f"{model_response.error.message}"
                )

            if not model_response.tool_calls_requested:
                self._last_tool_trace[request.session_id] = trace
                return "".join(p.value for p in model_response.content if p.type == "text")

            state.status = HarnessStatus.EXECUTING_TOOL
            # Passe "Targeted Execution Repair" : IDs pré-générés AVANT le
            # message assistant, pour que `tool_calls[i].id` et le
            # `tool_call_id` de la réponse `role=tool` correspondante soient
            # IDENTIQUES à `ToolCall.id` réellement exécuté ci-dessous —
            # jamais deux identifiants divergents pour le même appel.
            call_ids = [new_id("tc") for _ in model_response.tool_calls_requested]
            messages.append(Message(
                role="assistant",
                content=[ContentPart(type="text", value="")],
                tool_calls=[
                    {"id": call_id, "name": r.tool_name, "arguments": r.arguments}
                    for call_id, r in zip(call_ids, model_response.tool_calls_requested)
                ],
            ))

            escalation_text: str | None = None
            for requested, call_id in zip(model_response.tool_calls_requested, call_ids):
                if checkpoint_or_abort(state, self._safety):
                    self._emit_turn_failed(state, request.correlation_id)
                    self._last_tool_trace[request.session_id] = trace
                    return "[interrompu par STOP pendant l'exécution d'un outil]"

                operation_id = new_id("op")
                idempotency_key = f"{operation_id}:{requested.tool_name}"
                tool_call = ToolCall(
                    id=call_id, tool_name=requested.tool_name, arguments=requested.arguments,
                    correlation_id=request.correlation_id,
                    requested_by=ToolCallRequester(subsystem="harness", session_id=request.session_id, channel=request.channel.value),
                    operation_id=operation_id, idempotency_key=idempotency_key,
                )

                # ExecutionRecord durablement EXECUTING AVANT l'appel réel
                # (RAYA_V2_TECHNICAL_ARCHITECTURE.md §14.2) — un crash entre
                # ici et la complétion ne sera JAMAIS réinterprété COMPLETED.
                if self._execution_records is not None:
                    self._execution_records.start(ExecutionRecord(
                        operation_id=operation_id, tool_call_id=tool_call.id, correlation_id=request.correlation_id,
                        idempotency_key=idempotency_key,
                    ))

                tool_result = execute_tool(self._tools_registry, self._safety, tool_call, bus=self._bus)

                if (
                    tool_result.status == ToolResultStatus.PERMISSION_DENIED
                    and tool_result.error is not None
                    and tool_result.error.retryable
                ):
                    # Safety exige une confirmation explicite (consigne Phase 6
                    # CONFIRMATION UI) — le tour s'arrête ICI, honnêtement,
                    # plutôt que de continuer à boucler sur un appel refusé.
                    # Reprise possible via confirm_pending() une fois la
                    # décision utilisateur connue (jamais d'auto-approbation).
                    if self._execution_records is not None:
                        self._execution_records.complete(operation_id)
                    state.status = HarnessStatus.AWAITING_USER_INPUT
                    state.pending_confirmation = {
                        "tool_name": requested.tool_name,
                        "arguments": requested.arguments,
                        "correlation_id": request.correlation_id,
                        "reason": tool_result.error.message,
                    }
                    self._bus.publish(Event(
                        type="harness.confirmation_required", source="harness", correlation_id=request.correlation_id,
                        payload={"session_id": request.session_id, "tool_name": requested.tool_name,
                                 "arguments": requested.arguments, "reason": tool_result.error.message},
                    ))
                    trace.append({
                        "tool_name": requested.tool_name, "arguments": requested.arguments,
                        "status": tool_result.status.value, "outcome": "awaiting_confirmation",
                        "evidence": tool_result.evidence,
                    })
                    self._last_tool_trace[request.session_id] = trace
                    return (
                        f"Cette action nécessite ta confirmation avant que je continue : "
                        f"{requested.tool_name} ({tool_result.error.message})."
                    )

                if self._execution_records is not None:
                    # L'ATTEMPT est terminé (pas de crash) — que le résultat
                    # métier soit succès ou échec propre, execution_state
                    # passe à COMPLETED ; c'est verification_state qui
                    # capture l'issue réelle (RAYA_V2_TECHNICAL_ARCHITECTURE.md §14.4).
                    self._execution_records.complete(operation_id)

                state.status = HarnessStatus.VERIFYING
                outcome = verify_tool_result(tool_result)
                outcome = self._promote_observations_and_verify(requested, tool_result, outcome)
                recovery_action = self._loop_detector.record(loop_key, requested.tool_name, requested.arguments, outcome)

                trace.append({
                    "tool_name": requested.tool_name, "arguments": requested.arguments,
                    "status": tool_result.status.value, "outcome": outcome.value, "evidence": tool_result.evidence,
                })
                # Observabilité déjà assurée par tool.call_requested/completed/failed
                # (publiés par raya/tools/execution.py) — pas de doublon d'event ici.

                messages.append(Message(
                    role="tool", tool_call_id=call_id,
                    content=[ContentPart(type="text", value=self._summarize_tool_result(requested.tool_name, tool_result, outcome))],
                ))

                if requested.tool_name == "browser.read_page":
                    self._compact_old_dom_messages(messages)

                if recovery_action == RecoveryAction.ESCALATE:
                    escalation_text = self._explain_blocked_turn(
                        request.input.text or "", trace,
                        f"J'ai arrêté après plusieurs tentatives identiques de {requested.tool_name!r} sans succès.",
                        request.correlation_id,
                    )
                    break

                # Nudge générique anti-répétition (voir note ci-dessus) : le
                # même tool_name rappelé sans succès plusieurs fois de suite,
                # même avec des arguments différents à chaque fois.
                if requested.tool_name == consecutive_tool_name and outcome != VerificationOutcome.SUCCESS:
                    consecutive_failures += 1
                else:
                    consecutive_tool_name = requested.tool_name
                    consecutive_failures = 0 if outcome == VerificationOutcome.SUCCESS else 1
                if consecutive_failures and consecutive_failures % _NUDGE_THRESHOLD == 0:
                    messages.append(Message(role="system", content=[ContentPart(type="text", value=(
                        f"You've called {requested.tool_name!r} {consecutive_failures} times in a row without "
                        "reaching a new, confirmed state. Repeating the same approach with minor variations is "
                        "unlikely to work — consider a different strategy (e.g. using search instead of guessing "
                        "a direct target, re-reading the page/screen, or a different element), or tell the user "
                        "clearly what is blocking you if this genuinely needs their input."
                    ))]))

                state_signal = (tool_result.evidence or {}).get("url")
                if state_signal:
                    state_history.append(state_signal)
                    if detect_repeating_cycle(state_history) or detect_no_progress(state_history):
                        escalation_text = self._explain_blocked_turn(
                            request.input.text or "", trace,
                            f"Je tourne en rond entre les mêmes états sans nouvelle information "
                            f"({', '.join(state_history[-4:])}).",
                            request.correlation_id,
                        )
                        break

            self._last_tool_trace[request.session_id] = trace
            if escalation_text is not None:
                return escalation_text
            # Pas d'escalade : on reboucle, le modèle voit les VRAIS résultats
            # d'outils (jamais ses propres affirmations) au tour suivant.

        self._last_tool_trace[request.session_id] = trace
        return self._finalize_turn(
            request.input.text or "", trace, request.correlation_id,
        )

    # ------------------------------------------------------------------
    # Attention — Harness réagit aux décisions, Attention ne décide jamais
    # COMMENT (consigne Phase 2 §3, §22, §40)
    # ------------------------------------------------------------------

    def _on_attention_decision(self, event: Event) -> None:
        payload = event.payload if isinstance(event.payload, dict) else {}
        with self._attention_lock:
            self._attention_log.append(payload)
            if len(self._attention_log) > _ATTENTION_LOG_MAXLEN:
                self._attention_log.pop(0)

        if payload.get("decision") != "INTERRUPT":
            return

        # Chantier 13B (Event-Driven Phone Awareness) : un événement
        # téléphonique n'est JAMAIS rattaché à une session de conversation
        # (pas de target_session_id, contrairement à un Task en focus) —
        # Harness doit pouvoir "se réveiller" même à l'idle complet (aucune
        # conversation en cours), ce que le chemin ci-dessous ne fait pas
        # (il suppose toujours un focus de tâche existant). Traité en premier
        # et retourne toujours : jamais mélangé avec la logique de pause de
        # tâche ci-dessous, sémantiquement différente.
        # Chantier 13G (révisé) : "perception.incoming_call_notification"
        # n'atteint plus jamais ce point — Attention décide désormais
        # IGNORE par défaut pour ce type d'event (PERCEPTION ≠
        # INTERRUPTION ≠ RÉACTION, voir attention/evaluator.py). Seul
        # "perception.phone_call_activity" (13D, mécanisme plus ancien,
        # hors scope de la révision 13G) peut encore produire un INTERRUPT
        # ici.
        if payload.get("source_event_type") == "perception.phone_call_activity":
            self._on_phone_activity_interrupt(event, payload)
            return

        # §40 : Attention ne fait JAMAIS l'interruption elle-même — c'est ici,
        # dans le Harness, que la conséquence concrète est décidée et appliquée.
        session_id = payload.get("target_session_id")
        source_task_id = payload.get("task_id")
        if not session_id:
            return
        focus_task_id = self.focus.get_focus(session_id)
        if not focus_task_id or focus_task_id == source_task_id:
            return  # rien à suspendre (pas de focus, ou l'événement concerne déjà le focus)
        try:
            focused_task = self._tasks.get(focus_task_id)
            if focused_task is not None and focused_task.state == TaskState.RUNNING:
                self.pause_task(focus_task_id)
                log("info", "attention INTERRUPT -> focus task paused",
                    focus_task_id=focus_task_id, source_task_id=source_task_id)
        except (KeyError, ValueError):
            pass

    def _on_phone_activity_interrupt(self, event: Event, payload: dict) -> None:
        """Chantier 13B : rend l'éveil de RAYA OBSERVABLE (un event distinct,
        jamais juste une ligne de log silencieuse) — le scénario central du
        chantier ('idle -> appel entrant -> événement -> Attention -> Harness
        réveillé') se prouve par la présence de CET event, pas par une
        absence d'erreur. Le fait lui-même est déjà promu en World State par
        le mécanisme générique existant (WorldStateStore s'abonne à
        perception.*, inchangé) au moment où ce handler s'exécute — jamais
        dupliqué ici.

        AUCUNE action téléphonique n'est déclenchée ici, jamais (§5 : pas
        d'auto-réponse) — seulement la prise de conscience. `phone.answer`/
        `phone.call.reject` restent des Tools que SEUL le modèle peut choisir
        d'appeler, en réaction à une demande EXPLICITE de l'utilisateur dans
        un tour de conversation ultérieur, jamais depuis ce chemin."""
        self._bus.publish(Event(
            type="harness.external_event_noticed", source="harness", correlation_id=event.correlation_id,
            payload={"reason": "phone_activity", "reasoning": payload.get("reasoning", "")},
        ))
        log("info", "harness woken by phone activity (no auto-answer)", reasoning=payload.get("reasoning"))

    def recent_attention_decisions(self, limit: int = 10) -> list[dict]:
        with self._attention_lock:
            return list(self._attention_log[-limit:])

    # ------------------------------------------------------------------
    # Identité runtime / statut STOP — lecture seule pour les interfaces
    # (Phase 9, ex: /status Telegram) — jamais une valeur inventée, toujours
    # ce que le Router/Safety décideraient réellement pour ce tour.
    # ------------------------------------------------------------------

    def active_model_identity(self) -> dict:
        active_descriptor = describe_active(self._model_registry, ModelCapability.REASONING)
        return {
            "provider": active_descriptor.provider if active_descriptor else None,
            "model": active_descriptor.id if active_descriptor else None,
        }

    def is_stop_active(self) -> bool:
        return self._safety.should_stop()

    # ------------------------------------------------------------------
    # Device Registry — lecture/enregistrement pour les interfaces (Phase 9,
    # RAYA_V2_ARCHITECTURAL_BLUEPRINT.md §19). `raya.devices` reste le SEUL
    # point d'écriture réel (DeviceRegistry) — le Harness ne fait que le
    # relayer, jamais raya.interfaces.* n'importe raya.devices directement
    # (RAYA_V2_REPOSITORY_STRUCTURE.md §20 : interfaces -> harness UNIQUEMENT).
    # ------------------------------------------------------------------

    def register_device_info(
        self, device_id: str, device_type: DeviceType, *,
        platform: str | None = None, capabilities: tuple[Capability, ...] = (), metadata: dict | None = None,
    ) -> dict:
        device = self._devices.register_info(
            device_id, device_type, platform=platform, capabilities=capabilities, metadata=metadata,
        )
        return to_dict(device)

    def touch_device(self, device_id: str, *, metadata: dict | None = None) -> None:
        self._devices.touch(device_id, metadata=metadata)

    def mark_device_offline(self, device_id: str) -> None:
        self._devices.mark_offline(device_id)

    def describe_device(self, device_id: str) -> dict | None:
        device = self._devices.describe(device_id)
        return to_dict(device) if device is not None else None

    def list_devices(self) -> list[dict]:
        return [to_dict(d) for d in self._devices.list_devices()]

    # ------------------------------------------------------------------
    # Spatial — lecture seule pour les interfaces (Phase 8). La SEULE
    # écriture légitime reste tools/catalog/spatial.py (piloté par le
    # modèle) — jamais directement depuis une interface (RAYA_V2_TECHNICAL_
    # ARCHITECTURE.md §1.14, même principe que world_state/memory).
    # ------------------------------------------------------------------

    def describe_scene(self, scene_id: str) -> dict | None:
        try:
            return self._scene_store.describe_scene(scene_id)
        except SpatialError:
            return None

    def get_spatial_render_payload(self, scene_id: str) -> dict | None:
        scene = self._scene_store.get_scene(scene_id)
        if scene is None:
            return None
        return ThreeJSAdapter().to_payload(scene)

    def mounted_scene_id(self, session_id: str) -> str | None:
        return self._scene_store.mounted_scene_id(session_id)

    def list_scenes(self) -> list[dict]:
        return [{"scene_id": s.id, "label": s.label, "object_count": len(s.objects)} for s in self._scene_store.list_scenes()]

    # ------------------------------------------------------------------
    # Memory — actions explicites demandées par une interface (pas le modèle)
    # ------------------------------------------------------------------

    def create_memory(self, text: str, channel: str, memory_type: MemoryType = MemoryType.FACT) -> MemoryEntry:
        channel_scope = _CHANNEL_TO_SCOPE.get(channel, ChannelScope.CHAT)
        entry = MemoryEntry(
            type=memory_type,
            layer=MemoryLayer.PERSONAL,
            channel_scope=channel_scope,
            content=text,
            provenance=f"interface:{channel}:explicit",
            confidence=Confidence.KNOWN_FACT,
            lifecycle=MemoryLifecycle.CONFIRMED,
        )
        return self._memory.write(entry)

    def list_memory(self, channel: str, query: str = "") -> list[MemoryEntry]:
        channel_scope = _CHANNEL_TO_SCOPE.get(channel, ChannelScope.CHAT)
        return self._memory.search(query=query, channel_scope=channel_scope)

    # ------------------------------------------------------------------
    # Channel preferences (Chantier 12 §E) — Memory structurée existante
    # (MemoryType.PREFERENCE), jamais un second système de préférences.
    # `NotificationChannel` (canal d'ENVOI) reste distinct de `ChannelScope`
    # (INTERFACE d'origine de la conversation) — ne jamais confondre les deux.
    # ------------------------------------------------------------------

    def set_channel_preference(self, channel_for: str, channel: str, session_id: str,
                                channel_scope: ChannelScope = ChannelScope.SHARED) -> MemoryEntry:
        """Persiste une préférence de canal explicitement CONFIRMÉE par
        l'utilisateur (ex: 'envoie-moi toujours ça par mail') — jamais
        appelée pour une simple demande ponctuelle (c'est au Tool appelant,
        `preferences.set_channel`, de ne s'en servir que sur confirmation)."""
        NotificationChannel(channel)  # lève ValueError si canal inconnu — jamais une préférence invalide persistée
        entry = MemoryEntry(
            type=MemoryType.PREFERENCE,
            layer=MemoryLayer.PERSONAL,
            channel_scope=channel_scope,
            content={"channel_for": channel_for, "channel": channel},
            provenance=f"tool:preferences.set_channel:{session_id}",
            confidence=Confidence.KNOWN_FACT,
            lifecycle=MemoryLifecycle.CONFIRMED,
        )
        return self._memory.write(entry)

    def get_channel_preference(self, channel_for: str, channel_scope: ChannelScope = ChannelScope.SHARED) -> str | None:
        """Dernière préférence CONFIRMÉE pour ce type de demande, ou `None`
        si aucune n'a jamais été enregistrée — jamais un défaut deviné ici,
        c'est à l'appelant (Cognition, via SYSTEM_RULES) d'appliquer le
        défaut produit explicite (ex: 'message' -> Telegram) en son absence."""
        entries = self._memory.search(
            query="", channel_scope=channel_scope,
            type_filter=MemoryType.PREFERENCE, lifecycle_filter=MemoryLifecycle.CONFIRMED,
        )
        for entry in entries:
            content = entry.content or {}
            if isinstance(content, dict) and content.get("channel_for") == channel_for:
                return content.get("channel")
        return None

    # ------------------------------------------------------------------
    # World State — actions explicites
    # ------------------------------------------------------------------

    def set_world_fact(self, domain: str, key: str, value: object, source: str = "interface:explicit",
                        freshness_ttl_s: int | None = None) -> WorldStateFact:
        fact = WorldStateFact(
            domain=domain, key=key, value=value, source=source,
            confidence=Confidence.KNOWN_FACT, freshness_ttl_s=freshness_ttl_s,
        )
        return self._world_state.apply_update(fact)

    def get_world_fact(self, domain: str, key: str) -> WorldStateFact | None:
        return self._world_state.retrieve_fact(domain, key)

    def list_world_facts(self, domains: tuple[str, ...] = ()) -> list[WorldStateFact]:
        """Faits actifs/stale pertinents (jamais superseded) — exposé pour les
        interfaces (World View à la demande, consigne Phase 6) qui n'ont pas
        le droit d'importer raya.world_state directement."""
        return self._world_state.retrieve_relevant(domains)

    # ------------------------------------------------------------------
    # Session — lecture seule pour les interfaces (statut, confirmation en
    # attente, erreur) : jamais harness._sessions directement (lint interdit
    # l'accès privé, consigne Phase 6 CONFIRMATION UI).
    # ------------------------------------------------------------------

    def session_state(self, session_id: str) -> HarnessState | None:
        return self._sessions.get(session_id)

    def _natural_response_for_tool_result(self, tool_name: str, tool_result, outcome: VerificationOutcome,
                                           correlation_id: str) -> str:
        """Produit une réponse EN LANGAGE NATUREL à partir d'un `ToolResult`
        déjà exécuté (RAYA_V2 Phase 11 §4/§11 : "les interfaces ne doivent
        pas avoir chacune leur propre formatter de ToolResult"). UN SEUL
        endroit architectural pour ça — ici, dans le Harness — jamais
        Telegram/Cockpit/Voice ne reformattent un ToolResult elles-mêmes.
        Le JSON structuré (`_summarize_tool_result`) reste utilisé ailleurs
        pour l'observabilité/le modèle (jamais supprimé) — seulement jamais
        montré tel quel à l'utilisateur."""
        summary_payload = self._summarize_tool_result(tool_name, tool_result, outcome)
        messages = [
            Message(role="system", content=[ContentPart(type="text", value=(
                "You just executed one action on the user's behalf. Reply with a short, "
                "natural sentence describing the outcome to the user, in the same language "
                "they were using. Never mention tool names, JSON, or internal fields. If it "
                "failed, say so honestly and briefly explain what is blocking you."
            ))]),
            Message(role="user", content=[ContentPart(type="text", value=summary_payload)]),
        ]
        response = model_route(self._model_registry, ModelRequest(
            capability=ModelCapability.REASONING, messages=messages, correlation_id=correlation_id,
        ))
        if response.finish_reason != FinishReason.ERROR:
            text = "".join(p.value for p in response.content if p.type == "text").strip()
            if text:
                return text
        # Repli honnête GÉNÉRIQUE (jamais par nom d'outil) si aucun modèle
        # n'est disponible — même discipline que le repli de _run_agentic_loop.
        if outcome == VerificationOutcome.SUCCESS:
            return "C'est fait."
        detail = tool_result.error.message if tool_result.error else "une erreur inattendue"
        return f"Je n'ai pas réussi : {detail}"

    def confirm_pending(self, session_id: str, approved: bool) -> HarnessState:
        """Résout une confirmation Safety en attente (RAYA_V2 Phase 6 CONFIRMATION
        UI) — SEUL chemin de reprise après un `harness.confirmation_required`.
        `approved=True` réexécute EXACTEMENT l'appel d'outil refusé, avec
        `user_confirmed=True` transmis à tools/execution.py -> Safety (jamais
        l'UI ne décide elle-même qu'une action est sûre, consigne §CONFIRMATION
        UI : "The UI must never decide that an operation is safe.")."""
        state = self._sessions.get(session_id)
        if state is None or state.pending_confirmation is None:
            raise ValueError(f"Aucune confirmation en attente pour la session {session_id!r}")

        pending = state.pending_confirmation
        state.pending_confirmation = None
        correlation_id = pending.get("correlation_id") or new_id("corr")
        channel_scope = _CHANNEL_TO_SCOPE.get(state.channel.value, ChannelScope.CHAT)

        if not approved:
            state.status = HarnessStatus.COMPLETED
            self._last_response_text[session_id] = "D'accord, je n'exécute pas cette action."
            self._remember_assistant_turn(session_id, channel_scope, self._last_response_text[session_id])
            self._bus.publish(Event(
                type="harness.confirmation_resolved", source="harness", correlation_id=correlation_id,
                payload={"session_id": session_id, "approved": False, "tool_name": pending["tool_name"]},
            ))
            state.checkpoint()
            return state

        operation_id = new_id("op")
        tool_call = ToolCall(
            tool_name=pending["tool_name"], arguments=pending["arguments"], correlation_id=correlation_id,
            requested_by=ToolCallRequester(subsystem="harness", session_id=session_id),
            operation_id=operation_id, idempotency_key=f"{operation_id}:{pending['tool_name']}",
        )
        if self._execution_records is not None:
            self._execution_records.start(ExecutionRecord(
                operation_id=operation_id, tool_call_id=tool_call.id, correlation_id=correlation_id,
                idempotency_key=tool_call.idempotency_key,
            ))
        tool_result = execute_tool(self._tools_registry, self._safety, tool_call, bus=self._bus, user_confirmed=True)
        if self._execution_records is not None:
            self._execution_records.complete(operation_id)

        outcome = verify_tool_result(tool_result)
        # Fix Phase 11 (§Root Causes) : confirm_pending() ne réutilisait PAS
        # la promotion d'observation Phase 7 — une action confirmée n'écrivait
        # donc jamais dans World State, contrairement au même appel effectué
        # dans la boucle conversationnelle normale (§_run_agentic_loop).
        requested = RequestedToolCall(tool_name=pending["tool_name"], arguments=pending["arguments"])
        outcome = self._promote_observations_and_verify(requested, tool_result, outcome)
        state.status = HarnessStatus.COMPLETED
        # Fix Phase 11 (§4 : "les ToolResults JSON ne doivent plus polluer les
        # réponses utilisateur") : jamais `_summarize_tool_result` (JSON brut)
        # directement comme réponse — toujours reformulé en langage naturel,
        # au même endroit architectural pour toutes les interfaces.
        self._last_response_text[session_id] = self._natural_response_for_tool_result(
            pending["tool_name"], tool_result, outcome, correlation_id,
        )
        self._remember_assistant_turn(session_id, channel_scope, self._last_response_text[session_id])
        self._bus.publish(Event(
            type="harness.confirmation_resolved", source="harness", correlation_id=correlation_id,
            payload={"session_id": session_id, "approved": True, "tool_name": pending["tool_name"],
                     "status": tool_result.status.value},
        ))
        state.checkpoint()
        return state

    # ------------------------------------------------------------------
    # Tasks — persistance + concurrence réelle via TaskScheduler
    # ------------------------------------------------------------------

    @staticmethod
    def priority_from_name(name: str) -> int:
        """Passthrough exposé pour les interfaces (RAYA_V2_REPOSITORY_STRUCTURE.md
        §20 : interfaces -> harness UNIQUEMENT, jamais raya.tasks directement)."""
        return prio.from_name(name)

    @staticmethod
    def priority_to_name(value: int) -> str:
        return prio.to_name(value)

    def create_task(self, objective: str, channel: str, session_id: str, priority: int = prio.NORMAL,
                     not_before: str | None = None) -> Task:
        return self._tasks.create(
            objective=objective,
            owner=TaskOwner(channel=channel, session_id=session_id),
            correlation_id=str(uuid.uuid4()),
            priority=priority,
            not_before=not_before,
        )

    def list_tasks(self) -> list[Task]:
        return self._tasks.list()

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def pause_task(self, task_id: str) -> Task:
        # §17 : le scheduler ne ré-enfilera pas une tâche PAUSED — pas d'action
        # supplémentaire nécessaire ici au-delà de la transition d'état.
        return self._tasks.pause(task_id)

    def _step_fn_for_task(self, task: Task):
        """Déduit le TYPE de step_fn depuis le checkpoint PERSISTANT (jamais
        un nouveau champ Task, consigne §2 "ne pas dupliquer") : un
        checkpoint contenant "plan" est une tâche long-horizon (Phase 10,
        reprend au step courant du plan) ; sinon c'est le démonstrateur
        historique (Phase 2, reprend au compteur). Partagé par `resume_task`
        (reprise après pause/crash) et `recover` (Chantier 12 §B, re-soumission
        d'une tâche programmée jamais démarrée avant le crash)."""
        if isinstance(task.checkpoint, dict) and "plan" in task.checkpoint:
            return self._run_long_horizon_step
        return self._make_simulated_step_fn(_SIMULATED_TASK_STEPS)

    def resume_task(self, task_id: str) -> Task:
        task = self._tasks.resume(task_id)
        if self._scheduler.is_managed(task_id):
            self._scheduler.resume(task_id, task.priority)  # §18 : reprend au scheduler, pas depuis zéro
        else:
            # Tâche récupérée après un crash : son step_fn (en mémoire) a été
            # perdu avec le process précédent — on le ré-enregistre.
            step_fn = self._step_fn_for_task(task)
            self._scheduler.submit(task_id, step_fn, priority=task.priority)
        return task

    def cancel_task(self, task_id: str) -> Task:
        """PENDING/PAUSED/BLOCKED : rien d'actif dessus -> finalisation
        immédiate sûre (Chantier 15 : une tâche BLOCKED, comme PAUSED,
        n'est plus suivie par le scheduler tant qu'elle n'est pas
        explicitement reprise — même chemin d'annulation).
        RUNNING (potentiellement en plein step) : signal coopératif, le
        scheduler finalise au prochain point de contrôle (consigne §15)."""
        task = self._tasks.get(task_id)
        if task is None:
            raise KeyError(f"Task inconnu : {task_id!r}")
        if task.state in (TaskState.PENDING, TaskState.PAUSED, TaskState.BLOCKED):
            return self._tasks.cancel(task_id)
        self._tasks.request_cancellation(task_id)
        self._scheduler.wake()
        return self._tasks.get(task_id)

    def checkpoint_task(self, task_id: str, checkpoint: dict) -> Task:
        return self._tasks.checkpoint(task_id, checkpoint)

    def start_background_task(self, objective: str, channel: str = "cli", session_id: str = "cli",
                               priority: int = prio.NORMAL, total_steps: int = _SIMULATED_TASK_STEPS,
                               set_as_focus: bool = True) -> Task:
        """Démonstrateur (consigne Phase 2 §20) des mécaniques checkpoint/
        pause/resume/cancel/STOP/priorité/concurrence — PAS un vrai Device
        Agent, PAS une boucle agentique (§28) : chaque appel exécute UNE
        unité de travail déterministe et retourne, le TaskScheduler orchestre
        le reste. Devient le focus de la session par défaut (§6)."""
        task = self.create_task(objective, channel, session_id, priority=priority)
        if set_as_focus:
            self.focus.set_focus(session_id, task.id)
        step_fn = self._make_simulated_step_fn(total_steps)
        self._scheduler.submit(task.id, step_fn, priority=priority)
        return task

    def simulate_task_failure(self, task_id: str, message: str = "échec simulé (démo/tests)") -> Task:
        """Hook de démonstration/test explicite (pas un mécanisme de
        production) pour déclencher de façon déterministe le chemin
        task.failed -> Attention, sans dépendre d'un aléa (§40/§50)."""
        return self._tasks.fail(task_id, ErrorInfo(code="SIMULATED_FAILURE", message=message))

    def _make_simulated_step_fn(self, total_steps: int):
        # Compteur RAPIDE en mémoire (une closure = une tâche, jamais partagé).
        # BUG évité ici : dériver step_index du checkpoint PERSISTÉ à chaque
        # appel ne fonctionne pas tant que le checkpoint est throttlé (§19) —
        # la valeur persistée ne bouge pas entre deux écritures, donc chaque
        # step recalculerait "checkpoint+1" = LA MÊME valeur indéfiniment.
        # Le compteur en mémoire est la source de vérité en fonctionnement
        # normal ; il n'est (ré)initialisé depuis le checkpoint PERSISTANT
        # qu'une seule fois, au premier appel — ce qui reste correct après un
        # resume() suivant une vraie reprise post-crash (nouvelle closure,
        # nouveau compteur, réamorcé depuis le dernier état durable, §18).
        counter: dict[str, int] = {}

        def step(task_id: str) -> bool:
            task = self._tasks.get(task_id)
            if task is None:
                return True
            if task_id not in counter:
                counter[task_id] = int((task.checkpoint or {}).get("step_index", 0))
            time.sleep(_SIMULATED_STEP_DELAY_S)
            counter[task_id] += 1
            step_index = counter[task_id]
            percent = step_index / total_steps * 100
            self._tasks.report_progress(task_id, f"step_{step_index}", percent)  # bon marché, EventBus only
            if step_index % _CHECKPOINT_EVERY_N_STEPS == 0 or step_index >= total_steps:
                self._tasks.checkpoint(task_id, {"step_index": step_index, "percent": percent})  # écriture SQLite
            if step_index >= total_steps:
                self._tasks.complete(task_id, {"summary": f"{total_steps} steps simulés terminés"})
                self.attention.evaluator.forget_task(task_id)
                counter.pop(task_id, None)
                return True
            return False

        return step

    # ------------------------------------------------------------------
    # Long-Horizon Autonomy (Phase 10) — RENFORCE le pipeline existant,
    # n'en crée pas un second : le "step_fn" ci-dessous est un runnable
    # COOPÉRATIF ordinaire pour TaskScheduler (consigne §7 scheduler.py :
    # "exécute EXACTEMENT une unité de travail et retourne"), exactement au
    # même titre que `_make_simulated_step_fn`. Ce qui change, c'est que
    # cette unité de travail est un VRAI tour Cognition/Model/Tools —
    # réutilisant explicitement `execute_tool`/`verify_tool_result`/
    # `_promote_observations_and_verify`/`LoopDetector`, jamais une
    # deuxième implémentation de ces mécanismes (consigne §5/§10/§22).
    # ------------------------------------------------------------------

    def create_long_horizon_task(self, objective: str, channel: str, session_id: str,
                                  priority: int = prio.NORMAL, set_as_focus: bool = True,
                                  not_before: str | None = None) -> Task:
        """Point d'entrée UNIQUE et CENTRAL pour "intention en langage naturel
        -> Task de fond" (consigne §13) : appelé depuis un Tool
        (`tasks.create`, tools/catalog/tasks.py) que N'IMPORTE QUEL canal
        peut invoquer via le modèle — jamais un `TelegramTaskCreator`/
        `UITaskCreator` par interface. Le Cognition Layer produit un plan
        (`build_plan`), le Harness l'exécute — jamais l'inverse.

        `not_before` (Chantier 12 §B, additif) : ISO8601 UTC résolu par
        l'appelant (`tools/catalog/tasks.py`, jamais deviné ici) — le plan
        est construit IMMÉDIATEMENT (le compte à rebours ne retarde que
        l'EXÉCUTION du premier step, jamais la planification elle-même).

        Chantier 14, additif : les mêmes schémas de Tools que l'exécution
        (`_discover_tool_schemas()`) sont transmis à `build_plan()` — sans
        ça, un rappel "envoie-moi ça sur Telegram" pouvait être planifié
        comme une procédure manuelle de contrôle du téléphone au lieu d'un
        unique appel direct de `telegram.send_message` (voir docstring de
        `build_plan`)."""
        task = self.create_task(objective, channel, session_id, priority=priority, not_before=not_before)
        if set_as_focus:
            self.focus.set_focus(session_id, task.id)
        plan = build_plan(objective, self._model_registry, correlation_id=task.correlation_id,
                           available_tools=self._discover_tool_schemas())
        task = self._tasks.checkpoint(task.id, {"plan": to_dict(plan)})
        self._scheduler.submit(task.id, self._run_long_horizon_step, priority=priority, not_before=not_before)
        return task

    def _current_or_next_step(self, plan: Plan) -> PlanStep | None:
        """Reprend l'étape EN COURS si elle n'a pas atteint un état terminal
        (continuité entre deux ticks du scheduler, consigne §4 : "où en
        était la tâche ?") — sinon avance vers la prochaine étape exécutable."""
        if plan.current_step_id:
            current = next((s for s in plan.steps if s.id == plan.current_step_id), None)
            if current is not None and current.status in (StepState.PENDING, StepState.RUNNING):
                return current
        return next_runnable_step(plan)

    @staticmethod
    def _plan_percent(plan: Plan) -> float:
        if not plan.steps:
            return 0.0
        done = sum(1 for s in plan.steps if s.status in (StepState.COMPLETED, StepState.SKIPPED))
        return done / len(plan.steps) * 100

    def _finalize_long_horizon_task(self, task: Task, plan: Plan) -> None:
        summaries = [s.result.get("text", "") for s in plan.steps if s.result]
        summary = " ; ".join(t for t in summaries if t) or "toutes les étapes prévues sont terminées"
        self._tasks.checkpoint(task.id, {"plan": to_dict(plan)})
        self._tasks.complete(task.id, {"summary": summary})
        self.attention.evaluator.forget_task(task.id)

    def _advance_plan(self, task: Task, plan: Plan) -> bool:
        if plan_is_complete(plan):
            self._finalize_long_horizon_task(task, plan)
            return True
        self._tasks.checkpoint(task.id, {"plan": to_dict(plan)})
        return False

    def _handle_step_setback(self, task: Task, plan: Plan, step: PlanStep, loop_key: str,
                              recovery_action: RecoveryAction) -> bool:
        """Recovery/Replanning (consigne §5/§6) : un échec isolé (REPLAN)
        retente la MÊME étape au tick suivant ; une escalade (ESCALATE, même
        `LoopDetector` que la boucle conversationnelle) déclenche UNE
        tentative de replanning — jamais un échec caché, jamais un retry
        aveugle indéfini."""
        if recovery_action != RecoveryAction.ESCALATE:
            self._tasks.checkpoint(task.id, {"plan": to_dict(plan)})
            return False

        step.status = StepState.FAILED
        alternative = replan_step(task.objective, step, step.evidence, self._model_registry, task.correlation_id)
        if alternative is not None:
            # SKIPPED plutôt que FAILED : cette étape ne bloque plus le plan
            # (une alternative la remplace) — son échec/preuve reste dans
            # step.error/step.evidence pour la traçabilité (consigne §6),
            # mais plan_is_complete() ne doit jamais rester bloqué sur une
            # étape volontairement contournée par le replanning.
            step.status = StepState.SKIPPED
            alternative.dependencies = list(step.dependencies)
            plan.steps.append(alternative)
            self._loop_detector.forget(loop_key)
            self._bus.publish(TaskEvent(
                type="task.replanned", source="harness", correlation_id=task.correlation_id,
                payload=TaskEventPayload(task_id=task.id, new_state=task.state.value, previous_state=task.state.value,
                                          detail={"failed_step_id": step.id, "alternative_step_id": alternative.id}),
            ))
            self._tasks.checkpoint(task.id, {"plan": to_dict(plan)})
            return False

        self._tasks.checkpoint(task.id, {"plan": to_dict(plan)})
        if plan_is_stuck(plan):
            error = step.error if isinstance(step.error, ErrorInfo) else ErrorInfo(
                code="LONG_HORIZON_STEP_FAILED", message=f"Étape {step.objective!r} a échoué sans alternative viable.",
            )
            self._tasks.fail(task.id, error)
            self.attention.evaluator.forget_task(task.id)
            return True
        return False  # d'autres étapes indépendantes du plan peuvent encore progresser

    def _is_step_tool_already_satisfied(self, requested, step_evidence: dict) -> bool:
        """Idempotence / restart safety (consigne §12 ; renforcé Chantier 15
        Axe B) : avant de rejouer un appel d'outil déjà tenté pour cette
        étape, vérifie si l'intention est DÉJÀ satisfaite.

        Deux garde-fous, jamais confondus :
        1. `Tool.idempotent is False` + succès déjà enregistré dans
           `step_evidence` -> satisfait INCONDITIONNELLEMENT, sans dépendre
           du World State. Constaté en réel (Chantier 14) :
           `telegram.send_message` n'a aucun `ObservationSpec` (rien à
           vérifier en World State pour "un message a-t-il déjà été
           envoyé"), donc le garde-fou #2 seul ne protège jamais une action
           non idempotente réussie — le modèle pouvait continuer à la
           rappeler malgré l'évidence montrée dans le prompt (garde-fou
           purement textuel/coopératif). Ici, la protection est
           STRUCTURELLE : un step ne représente qu'UN SEUL objectif
           atomique (consigne de planification), donc un même Tool non
           idempotent qui a déjà réussi UNE fois pour ce step n'a plus
           jamais besoin d'être rappelé POUR CE STEP.
        2. Sinon (Tool idempotent, ou statut inconnu) : réutilise le même
           `ObservationSpec`/`verify_observation_against_intent` que la
           vérification post-action Phase 7, jamais une deuxième
           heuristique."""
        prior = step_evidence.get(requested.tool_name)
        if not prior or prior.get("status") != ToolResultStatus.SUCCESS.value:
            return False
        tool_def = self._tools_registry.get(requested.tool_name)
        if tool_def is None:
            return False
        if not tool_def.idempotent:
            return True
        for spec in tool_def.observation:
            if spec.expected_argument is None:
                continue
            expected = requested.arguments.get(spec.expected_argument)
            fact = self._world_state.retrieve_fact(spec.domain, spec.key)
            if fact is not None and verify_observation_against_intent(expected, fact.value) == VerificationOutcome.SUCCESS:
                return True
        return False

    def _run_long_horizon_step(self, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None:
            return True
        plan = from_dict(Plan, (task.checkpoint or {}).get("plan"))
        if plan is None:
            self._tasks.fail(task_id, ErrorInfo(code="LONG_HORIZON_NO_PLAN", message="Aucun plan trouvé pour cette tâche.", retryable=False))
            return True

        if plan_is_complete(plan):
            self._finalize_long_horizon_task(task, plan)
            return True

        step = self._current_or_next_step(plan)
        if step is None:
            self._tasks.fail(task_id, ErrorInfo(code="LONG_HORIZON_STUCK", message="Aucune étape exécutable — le plan est bloqué.", retryable=False))
            self.attention.evaluator.forget_task(task_id)
            return True

        step.status = StepState.RUNNING
        plan.current_step_id = step.id
        self._tasks.report_progress(task_id, step.objective, self._plan_percent(plan))
        # Checkpoint AVANT l'appel modèle (potentiellement lent/bloquant) —
        # consigne §4 : un crash pendant l'appel doit laisser une trace
        # honnête de "quelle étape était active", jamais un état stale d'avant
        # le début de cette tentative.
        self._tasks.checkpoint(task_id, {"plan": to_dict(plan)})

        # Contexte BORNÉ à cette étape (consigne §9) : objectif global + étape
        # courante + World State/Memory pertinents — jamais tout l'historique
        # de la tâche. Réutilise `context_engine.assemble()` sans modification
        # (le paramètre `task=` existe depuis la Phase 1).
        channel_scope = _CHANNEL_TO_SCOPE.get(task.owner.channel, ChannelScope.CHAT)
        context = assemble(
            session_id=task.owner.session_id, channel_scope=channel_scope,
            world_state=self._world_state, memory=self._memory,
            budget_tokens=self._context_budget_tokens, task=task, query_text=step.objective,
            tools_registry=self._tools_registry, capability_tags=tuple(self._tools_registry.all_capability_tags()),
            runtime_identity=self.active_model_identity(),
        )
        system_prompt = render_system_prompt(context)
        user_lines = [f"Global task objective: {task.objective}", f"Current step to work on: {step.objective}"]
        # Chantier 15 (Axe G, multi-tick continuity) : sans ceci, seule
        # l'évidence du step COURANT était visible (fix Chantier 14) —
        # un step 2 qui dépend du résultat du step 1 (ex: "step 1 : créer
        # le fichier X" -> "step 2 : écrire dedans") n'avait aucun accès
        # structuré à ce que le step précédent avait réellement accompli,
        # seulement son propre objectif texte. Résumé court, jamais
        # l'historique complet de conversation (budget borné, consigne §9).
        completed_steps = [s for s in plan.steps if s.status == StepState.COMPLETED and s.id != step.id]
        if completed_steps:
            summary = "; ".join(
                f"{s.objective!r} -> {(s.result or {}).get('text') or 'done'}" for s in completed_steps
            )
            user_lines.append(f"Already completed earlier steps in this task: {summary}")
        # BUG CORRIGÉ (Chantier 14, constaté en réel) : cette ligne était
        # gated par `step.attempts > 0`, mais `step.attempts` n'est
        # incrémenté que sur RecoveryAction.REPLAN (un échec) — jamais après
        # un appel d'outil qui RÉUSSIT sans que le modèle n'ait encore dit
        # "terminé". Un step à un seul outil non observable en World State
        # (ex: telegram.send_message — `_is_step_tool_already_satisfied`
        # ne peut rien vérifier sans ObservationSpec) ne voyait donc JAMAIS
        # la preuve de son propre succès au tick suivant, et rappelait le
        # même outil indéfiniment — un vrai message Telegram renvoyé à
        # chaque tick. `step.evidence` (rempli dès le premier appel, succès
        # ou non) est la condition correcte, pas `step.attempts`.
        if step.evidence:
            user_lines.append(f"Evidence from tool calls already made for this step: {step.evidence}")
            user_lines.append(
                "Check that evidence FIRST: if it already shows this step's objective was "
                "successfully accomplished, do NOT call that tool again — reply with a short "
                "summary and call NO tool. Only call a tool again if the evidence shows it "
                "actually failed or the step genuinely needs another distinct action."
            )
        user_lines.append(
            "Work ONLY on this step. Call tools if needed. Once this step is "
            "actually done, reply with a short summary and call NO tool."
        )
        messages: list[Message] = []
        if system_prompt:
            messages.append(Message(role="system", content=[ContentPart(type="text", value=system_prompt)]))
        messages.append(Message(role="user", content=[ContentPart(type="text", value="\n".join(user_lines))]))

        available_tools = self._discover_tool_schemas()
        loop_key = f"task:{task_id}:{step.id}"
        model_response = model_route(self._model_registry, ModelRequest(
            capability=ModelCapability.REASONING, messages=messages, correlation_id=task.correlation_id,
            available_tools=available_tools or None, context_budget_tokens=self._context_budget_tokens,
        ))

        if model_response.finish_reason == FinishReason.ERROR:
            step.attempts += 1
            step.error = model_response.error
            recovery_action = self._loop_detector.record(loop_key, "model_call", {}, VerificationOutcome.FAILURE)
            return self._handle_step_setback(task, plan, step, loop_key, recovery_action)

        if not model_response.tool_calls_requested:
            step.status = StepState.COMPLETED
            step.result = {"text": "".join(p.value for p in model_response.content if p.type == "text")}
            self._loop_detector.forget(loop_key)
            return self._advance_plan(task, plan)

        step_evidence = dict(step.evidence or {})
        for requested in model_response.tool_calls_requested:
            if self._safety.should_stop():
                break  # le prochain tick verra should_stop() AVANT même d'appeler ce step_fn (scheduler._process_one_step)

            if self._is_step_tool_already_satisfied(requested, step_evidence):
                continue  # §12 : déjà satisfait selon World State — jamais rejoué aveuglément

            operation_id = new_id("op")
            idempotency_key = f"{operation_id}:{requested.tool_name}"
            tool_call = ToolCall(
                tool_name=requested.tool_name, arguments=requested.arguments, correlation_id=task.correlation_id,
                requested_by=ToolCallRequester(subsystem="harness", session_id=task.owner.session_id, channel=task.owner.channel),
                operation_id=operation_id, idempotency_key=idempotency_key,
            )
            if self._execution_records is not None:
                self._execution_records.start(ExecutionRecord(
                    operation_id=operation_id, tool_call_id=tool_call.id, correlation_id=task.correlation_id,
                    idempotency_key=idempotency_key, task_id=task_id, step_id=step.id,
                ))

            tool_result = execute_tool(self._tools_registry, self._safety, tool_call, bus=self._bus)

            if (tool_result.status == ToolResultStatus.PERMISSION_DENIED and tool_result.error is not None
                    and tool_result.error.retryable):
                # Limitation assumée et documentée (consigne §14/§22, jamais un
                # bypass Safety) : une tâche de fond n'a pas de canal
                # interactif pour résoudre une confirmation — traité comme un
                # BLOCAGE honnête de la tâche, jamais un contournement
                # silencieux. Chantier 15 (Axe D/H) : ceci n'est PAS un échec
                # technique — aucune étape alternative ne peut "contourner"
                # une confirmation Safety requise, donc jamais de replanning
                # ici (contrairement à un STEP_VERIFICATION_MISMATCH plus
                # bas) — directement BLOCKED, distinct de FAILED.
                if self._execution_records is not None:
                    self._execution_records.complete(operation_id)
                step_evidence[requested.tool_name] = {"status": "confirmation_required", "detail": tool_result.error.message}
                step.evidence = step_evidence
                step.attempts += 1
                step.error = ErrorInfo(code="CONFIRMATION_REQUIRED_IN_BACKGROUND", message=tool_result.error.message, retryable=False)
                self._loop_detector.record(loop_key, requested.tool_name, requested.arguments, VerificationOutcome.FAILURE)
                self._tasks.checkpoint(task_id, {"plan": to_dict(plan)})
                self._tasks.block(task_id, step.error)
                return True

            if self._execution_records is not None:
                self._execution_records.complete(operation_id)

            outcome = verify_tool_result(tool_result)
            outcome = self._promote_observations_and_verify(requested, tool_result, outcome)
            recovery_action = self._loop_detector.record(loop_key, requested.tool_name, requested.arguments, outcome)

            step_evidence[requested.tool_name] = {
                "status": tool_result.status.value, "outcome": outcome.value,
                "evidence": tool_result.evidence, "output": tool_result.output,
            }
            step.evidence = step_evidence
            # Chantier 15 (Axe C/I, recovery duplication) : persiste
            # l'évidence de CE tool call immédiatement, pas seulement à la
            # toute fin de la fonction — un crash entre un vrai succès non
            # idempotent (ex: telegram.send_message) et la fin de cette
            # boucle laisserait sinon le checkpoint durable sans cette
            # preuve, et une reprise après redémarrage rappellerait l'outil
            # pour de vrai (fenêtre de duplication constatée à l'inspection).
            self._tasks.checkpoint(task_id, {"plan": to_dict(plan)})

            if recovery_action == RecoveryAction.ESCALATE:
                step.error = tool_result.error or ErrorInfo(
                    code="STEP_VERIFICATION_MISMATCH",
                    message=f"{requested.tool_name}: résultat non vérifié conforme à l'intention.",
                )
                return self._handle_step_setback(task, plan, step, loop_key, RecoveryAction.ESCALATE)

            if recovery_action == RecoveryAction.REPLAN:
                step.attempts += 1

        self._tasks.checkpoint(task_id, {"plan": to_dict(plan)})
        return False

    # ------------------------------------------------------------------
    # Shutdown (bug Phase 1 corrigé — ordre garanti : scheduler avant backend)
    # ------------------------------------------------------------------

    def shutdown(self, timeout_s: float = 2.0) -> None:
        self._scheduler.shutdown(timeout_s=timeout_s)

    # ------------------------------------------------------------------
    # Recovery au démarrage (RAYA_V2_MIGRATION_PLAN.md §11.3, consigne §31)
    # ------------------------------------------------------------------

    def recover(self) -> list[Task]:
        recovered = self._tasks.recover_after_restart()
        if recovered:
            log("info", f"{len(recovered)} task(s) RUNNING au crash -> PAUSED au redémarrage",
                task_ids=[t.id for t in recovered])

        # Chantier 12 §B : une tâche PROGRAMMÉE (not_before défini) encore
        # PENDING au redémarrage n'a JAMAIS pu être exécutée — son entrée
        # dans le scheduler EN MÉMOIRE du process précédent a disparu avec
        # lui, `recover_after_restart()` ci-dessus ne traite que RUNNING->
        # PAUSED. Sans ce re-soumission, une tâche programmée pendant que le
        # process est arrêté serait silencieusement perdue pour toujours
        # (PENDING en base, jamais rappelée par personne). Re-soumission
        # sûre et non-dupliquée : ces tâches n'ont, par construction, jamais
        # démarré (PENDING, pas RUNNING) — resoumettre est donc leur PREMIÈRE
        # exécution, pas une ré-exécution. `is_managed()` est trivialement
        # faux ici (scheduler tout juste reconstruit), gardé par prudence
        # pour rester idempotent si `recover()` était un jour appelé deux fois.
        rescheduled: list[Task] = []
        for task in self._tasks.list(state=TaskState.PENDING.value):
            if not task.not_before or self._scheduler.is_managed(task.id):
                continue
            step_fn = self._step_fn_for_task(task)
            self._scheduler.submit(task.id, step_fn, priority=task.priority, not_before=task.not_before)
            rescheduled.append(task)
        if rescheduled:
            log("info", f"{len(rescheduled)} scheduled task(s) re-soumise(s) au scheduler après redémarrage",
                task_ids=[t.id for t in rescheduled])
        # Phase 10 (consigne §12/§21 scénario C) : un ExecutionRecord retrouvé
        # EXECUTING après un crash devient UNKNOWN — jamais réinterprété
        # COMPLETED (optimisme dangereux) ni NOT_STARTED (double exécution).
        # Mécanisme déjà écrit Phase 1 (`decide_recovery`) mais jamais
        # invoqué au démarrage avant cette phase — gap comblé ici, sans
        # nouveau mécanisme (RAYA_V2_TECHNICAL_ARCHITECTURE.md §14.2).
        if self._execution_records is not None:
            reinterpreted = self._execution_records.recover_all_unknown()
            if reinterpreted:
                log("info", f"{len(reinterpreted)} execution record(s) EXECUTING au crash -> UNKNOWN au redémarrage",
                    operation_ids=[r.operation_id for r in reinterpreted])
        return recovered
