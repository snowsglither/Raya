"""UIChannel — client mince du Harness pour le Cockpit (RAYA V2 Phase 6).

Même contrat que `raya/interfaces/voice/channel.py::VoiceChannel` : traduit
une intention utilisateur en `HarnessRequest`, appelle `harness.handle_request()`
(jamais cognition/tools/devices/models/tasks/safety/attention directement —
invariant vérifié par tests/architecture/test_ui_architecture_proof.py), lit
l'état via l'API PUBLIQUE du Harness uniquement (jamais `harness._...`).

STOP suit le même chemin que partout ailleurs (consigne §18) : publication
d'un `Event(type="interface.stop_requested")`, jamais un appel direct à
`safety.request_stop()`.

Réutilise volontairement `raya.interfaces.voice.presence.PresenceTracker` —
la présence est un concept transversal aux canaux (déjà partagé entre
sessions voix), pas une notion propre à la voix ; dupliquer la classe aurait
introduit deux sources de vérité pour le même état (consigne "ONE SOURCE OF
TRUTH"). Les deux packages restent dans le même subsystem `interfaces`
(RAYA_V2_REPOSITORY_STRUCTURE.md §20), cet import est donc autorisé par le lint.
"""

from __future__ import annotations

import threading

from raya.contracts import Channel, Event, HarnessRequest, InterfaceInput, TaskState, can_transition, utc_now_iso
from raya.event_bus import EventBus
from raya.harness import Harness
from raya.interfaces.voice.presence import PresenceTracker

from .viewmodels import (
    AttentionItemView,
    AttentionView,
    BrowserSummaryView,
    ComputerSummaryView,
    ConfirmationView,
    ConversationMessageView,
    ConversationView,
    PresenceView,
    ResultView,
    SpatialSceneView,
    TaskControlsView,
    TaskDetailView,
    TaskSummaryItemView,
    TaskSummaryView,
    ToolActivityView,
    WorldFactView,
    WorldSummaryView,
)

_MAX_CONVERSATION_HISTORY = 200

# Préfixes de tool_name (raya/tools/catalog/{pc,browser}.py) utilisés pour
# router les entrées de last_tool_trace() vers la vue Computer ou Browser.
_COMPUTER_TOOL_PREFIXES = ("pc.", "window.", "application.", "keyboard.", "mouse.", "ui.", "screen.", "process.")
_BROWSER_TOOL_PREFIXES = ("browser.",)


class UIChannel:
    def __init__(self, harness: Harness, bus: EventBus, session_id: str = "ui-default") -> None:
        self._harness = harness
        self._bus = bus
        self.session_id = session_id
        self.presence = PresenceTracker(session_id=session_id)
        self._history_lock = threading.Lock()
        self._history: list[ConversationMessageView] = []

        bus.subscribe("task.*", self.presence.on_event, subscriber=f"ui.presence.{session_id}")
        bus.subscribe("attention.decision_made", self.presence.on_event, subscriber=f"ui.presence.{session_id}")
        bus.subscribe("harness.confirmation_required", self.presence.on_event, subscriber=f"ui.presence.{session_id}")
        bus.subscribe("harness.confirmation_resolved", self.presence.on_event, subscriber=f"ui.presence.{session_id}")

    @property
    def bus(self) -> EventBus:
        return self._bus

    # ------------------------------------------------------------------
    # Conversation -> Harness (SEUL point d'entrée cognitif)
    # ------------------------------------------------------------------

    def send_message(self, text: str, *, channel: Channel = Channel.WEB) -> ConversationView:
        self._append_message("user", text)
        self._bus.publish(Event(
            type="interface.request_received", source="interfaces.ui",
            payload={"channel": channel.value, "session_id": self.session_id},
        ))

        request = HarnessRequest(channel=channel, session_id=self.session_id, input=InterfaceInput(text=text))
        # `handle_request` est synchrone et RÉELLEMENT en vol pendant cet
        # appel — `set_processing` reflète un fait connu, pas une supposition
        # (consigne §PRESENCE : jamais un état que le backend ne rapporte pas).
        self.presence.set_processing(True)
        try:
            self._harness.handle_request(request)
        finally:
            self.presence.set_processing(False)

        response_text = self._harness.response_text(self.session_id)
        self._append_message("raya", response_text)
        return self.conversation_view()

    def _append_message(self, role: str, text: str) -> None:
        with self._history_lock:
            self._history.append(ConversationMessageView(role=role, text=text, timestamp=utc_now_iso()))
            if len(self._history) > _MAX_CONVERSATION_HISTORY:
                self._history = self._history[-_MAX_CONVERSATION_HISTORY:]

    def conversation_view(self) -> ConversationView:
        with self._history_lock:
            return ConversationView(session_id=self.session_id, messages=list(self._history))

    # ------------------------------------------------------------------
    # STOP global — Event -> EventBus -> Safety (jamais d'appel direct, §18)
    # ------------------------------------------------------------------

    def request_stop(self) -> None:
        self._bus.publish(Event(type="interface.stop_requested", source="interfaces.ui",
                                 payload={"session_id": self.session_id}))

    # ------------------------------------------------------------------
    # Présence
    # ------------------------------------------------------------------

    def presence_view(self) -> PresenceView:
        snap = self.presence.snapshot()
        return PresenceView(
            state=snap.state.value, timestamp=snap.timestamp, active_session=snap.active_session,
            active_task_count=len(snap.active_tasks), needs_attention=snap.needs_attention,
        )

    # ------------------------------------------------------------------
    # Tasks — API publique du Harness uniquement (jamais raya.tasks)
    # ------------------------------------------------------------------

    def task_summary_view(self) -> TaskSummaryView:
        items = [
            TaskSummaryItemView(
                id=t.id, objective=t.objective, state=t.state.value,
                priority_name=self._harness.priority_to_name(t.priority),
                progress_percent=t.progress.percent, updated_at=t.updated_at,
                current_step=t.progress.current_step,
            )
            for t in self._harness.list_tasks()
        ]
        return TaskSummaryView(tasks=items)

    def task_detail_view(self, task_id: str) -> TaskDetailView | None:
        task = self._harness.get_task(task_id)
        if task is None:
            return None
        controls = TaskControlsView(
            can_pause=can_transition(task.state, TaskState.PAUSED),
            can_resume=can_transition(task.state, TaskState.RUNNING),
            can_cancel=can_transition(task.state, TaskState.CANCELLED),
        )
        return TaskDetailView(
            id=task.id, objective=task.objective, state=task.state.value,
            priority_name=self._harness.priority_to_name(task.priority),
            progress_step=task.progress.current_step, progress_percent=task.progress.percent,
            created_at=task.created_at, updated_at=task.updated_at,
            error=task.error, result=task.result, controls=controls,
        )

    def pause_task(self, task_id: str):
        return self._harness.pause_task(task_id)

    def resume_task(self, task_id: str):
        return self._harness.resume_task(task_id)

    def cancel_task(self, task_id: str):
        return self._harness.cancel_task(task_id)

    # ------------------------------------------------------------------
    # World — à la demande uniquement (jamais poussé en continu)
    # ------------------------------------------------------------------

    def world_view(self, domains: tuple[str, ...] = ()) -> WorldSummaryView:
        facts = [
            WorldFactView(
                domain=f.domain, key=f.key, value=f.value, status=f.status.value,
                timestamp=f.timestamp, is_stale=f.status.value == "stale",
            )
            for f in self._harness.list_world_facts(domains)
        ]
        return WorldSummaryView(facts=facts)

    # ------------------------------------------------------------------
    # Computer / Browser — dérivés de last_tool_trace() (la SEULE source
    # honnête aujourd'hui : aucun Device Agent n'écrit encore dans World
    # State, cf. RAYA_V2_PHASE6_IMPLEMENTATION_REPORT.md §Known Limitations).
    # Jamais de miroir live du bureau/de l'onglet — uniquement les VRAIS
    # ToolResult déjà exécutés pendant le dernier tour de cette session.
    # ------------------------------------------------------------------

    def _tool_activity(self, prefixes: tuple[str, ...]) -> list[ToolActivityView]:
        trace = self._harness.last_tool_trace(self.session_id)
        return [
            ToolActivityView(tool_name=e["tool_name"], status=e["status"], outcome=e["outcome"], evidence=e.get("evidence"))
            for e in trace if e["tool_name"].startswith(prefixes)
        ]

    def computer_view(self) -> ComputerSummaryView:
        activity = self._tool_activity(_COMPUTER_TOOL_PREFIXES)
        return ComputerSummaryView(activity=activity, has_activity=bool(activity))

    def browser_view(self) -> BrowserSummaryView:
        activity = self._tool_activity(_BROWSER_TOOL_PREFIXES)
        return BrowserSummaryView(activity=activity, has_activity=bool(activity))

    # ------------------------------------------------------------------
    # Result — dernière réponse + trace complète de ce tour
    # ------------------------------------------------------------------

    def result_view(self) -> ResultView:
        trace = self._harness.last_tool_trace(self.session_id)
        return ResultView(
            session_id=self.session_id,
            response_text=self._harness.response_text(self.session_id),
            tool_trace=[
                ToolActivityView(tool_name=e["tool_name"], status=e["status"], outcome=e["outcome"], evidence=e.get("evidence"))
                for e in trace
            ],
        )

    # ------------------------------------------------------------------
    # Confirmation — Safety reste seule autorité (consigne CONFIRMATION UI)
    # ------------------------------------------------------------------

    def confirmation_view(self) -> ConfirmationView | None:
        state = self._harness.session_state(self.session_id)
        if state is None or state.pending_confirmation is None:
            return None
        pending = state.pending_confirmation
        return ConfirmationView(
            session_id=self.session_id, tool_name=pending["tool_name"],
            arguments=pending["arguments"], reason=pending["reason"],
        )

    def confirm(self, approved: bool) -> ConversationView:
        """L'UI ne décide JAMAIS elle-même qu'une action est sûre — elle ne
        fait que relayer la décision humaine à `Harness.confirm_pending()`,
        qui repasse par tools/execution.py -> Safety avant tout effet réel."""
        self._harness.confirm_pending(self.session_id, approved=approved)
        response_text = self._harness.response_text(self.session_id)
        self._append_message("raya", response_text)
        return self.conversation_view()

    # ------------------------------------------------------------------
    # Attention — observabilité en lecture seule
    # ------------------------------------------------------------------

    def attention_view(self, limit: int = 10) -> AttentionView:
        decisions = self._harness.recent_attention_decisions(limit)
        items = [
            AttentionItemView(
                decision=d.get("decision", ""), source_event_type=d.get("source_event_type"),
                task_id=d.get("task_id"), reasoning=d.get("reasoning", ""),
            )
            for d in decisions
        ]
        return AttentionView(decisions=items)

    # ------------------------------------------------------------------
    # Spatial — lecture seule, à la demande (Phase 8). Jamais un mount/état
    # inventé côté UI : reflète exactement ce que `scene.render`/`scene.close`
    # ont réellement fait pour CETTE session (Harness.mounted_scene_id()).
    # ------------------------------------------------------------------

    def spatial_view(self) -> SpatialSceneView:
        scene_id = self._harness.mounted_scene_id(self.session_id)
        if scene_id is None:
            return SpatialSceneView(mounted=False, scene_id=None, payload=None)
        payload = self._harness.get_spatial_render_payload(scene_id)
        if payload is None:  # la scène a été supprimée entre le mount et cette lecture
            return SpatialSceneView(mounted=False, scene_id=None, payload=None)
        return SpatialSceneView(mounted=True, scene_id=scene_id, payload=payload)
