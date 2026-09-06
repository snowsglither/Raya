"""View models — état UI-facing (RAYA V2 Phase 6 consigne "VIEW MODEL").

Le frontend ne reconstruit jamais de logique backend et ne reçoit jamais un
objet interne brut (Task/HarnessState/WorldStateFact/...) — uniquement ces
dataclasses, volontairement plus pauvres que les contrats internes (pas de
`owner`/`checkpoint`/`dependencies` bruts, par exemple). `to_dict()`
(raya.contracts) sérialise n'importe laquelle de ces dataclasses en JSON.

Chaque champ ici doit pouvoir être tracé jusqu'à un état RÉEL du Harness —
aucun champ n'est inventé pour "remplir" l'UI (consigne NO FALSE UI CLAIMS).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from raya.contracts import ErrorInfo


@dataclass
class PresenceView:
    state: str  # PresenceLabel.value
    timestamp: str
    active_session: str | None
    active_task_count: int
    needs_attention: bool


@dataclass
class ConversationMessageView:
    role: str  # "user" | "raya"
    text: str
    timestamp: str


@dataclass
class ConversationView:
    session_id: str
    messages: list[ConversationMessageView] = field(default_factory=list)


@dataclass
class TaskControlsView:
    """Honnêteté des capacités (consigne "Do not create fake buttons") :
    reflète EXACTEMENT ce que l'état courant de la tâche permet réellement
    d'appeler sur le Harness (can_transition), jamais une liste statique."""
    can_pause: bool
    can_resume: bool
    can_cancel: bool
    steering_available: bool = False  # tasks.steer n'existe qu'en Tool piloté par le modèle — jamais un bouton direct


@dataclass
class TaskSummaryItemView:
    id: str
    objective: str
    state: str  # TaskState.value
    priority_name: str
    progress_percent: float | None
    updated_at: str
    # AJOUTÉ Phase 10 (consigne §19 : "Cockpit peut afficher au minimum...
    # étape courante") — reflète exactement `Task.progress.current_step`,
    # déjà réel pour le démonstrateur Phase 2 ("step_N") et désormais aussi
    # pour une tâche long-horizon (l'objectif de l'étape en cours).
    current_step: str = ""


@dataclass
class TaskSummaryView:
    tasks: list[TaskSummaryItemView] = field(default_factory=list)


@dataclass
class TaskDetailView:
    id: str
    objective: str
    state: str
    priority_name: str
    progress_step: str
    progress_percent: float | None
    created_at: str
    updated_at: str
    error: ErrorInfo | None
    result: dict | None
    controls: TaskControlsView


@dataclass
class WorldFactView:
    domain: str
    key: str
    value: object
    status: str  # FactStatus.value — "active" | "stale" | "superseded"
    timestamp: str
    is_stale: bool


@dataclass
class WorldSummaryView:
    facts: list[WorldFactView] = field(default_factory=list)


@dataclass
class ToolActivityView:
    """Une entrée RÉELLE de `Harness.last_tool_trace()` — jamais fabriquée.
    C'est la seule source honnête de "ce que RAYA fait sur le PC/le
    navigateur" tant qu'aucun Device Agent n'écrit dans World State."""
    tool_name: str
    status: str
    outcome: str
    evidence: dict | None


@dataclass
class ComputerSummaryView:
    activity: list[ToolActivityView] = field(default_factory=list)
    has_activity: bool = False


@dataclass
class BrowserSummaryView:
    activity: list[ToolActivityView] = field(default_factory=list)
    has_activity: bool = False


@dataclass
class ConfirmationView:
    session_id: str
    tool_name: str
    arguments: dict
    reason: str


@dataclass
class ResultView:
    session_id: str
    response_text: str
    tool_trace: list[ToolActivityView] = field(default_factory=list)


@dataclass
class AttentionItemView:
    decision: str
    source_event_type: str | None
    task_id: str | None
    reasoning: str


@dataclass
class AttentionView:
    decisions: list[AttentionItemView] = field(default_factory=list)


@dataclass
class SpatialSceneView:
    """Phase 8 — jamais une scène fabriquée côté UI : reflète exactement ce
    que `Harness.mounted_scene_id()`/`get_spatial_render_payload()` rapporte
    (`mounted=False` tant qu'aucun `scene.render` réel n'a eu lieu pour cette
    session, consigne NO FALSE UI CLAIMS). `payload` est déjà la forme prête
    pour le renderer (raya.spatial.renderer.threejs_adapter.ThreeJSAdapter) —
    l'UI ne réinterprète jamais Scene/SpatialObject elle-même."""
    mounted: bool
    scene_id: str | None
    payload: dict | None
