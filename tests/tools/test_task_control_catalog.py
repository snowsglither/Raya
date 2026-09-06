"""tools/catalog/tasks.py — steering de tâches (consigne Phase 5 §15/§33) :
pause/resume/cancel/steer délégués aux méthodes déjà publiques du Harness
(jamais un accès direct au Task Actor depuis le canal voix/tools). Safety
gating réel : pause/resume SAFE, cancel/steer SENSITIVE."""

from __future__ import annotations

from raya.contracts import PermissionLevel, TaskOwner, ToolCall, ToolCallRequester, ToolResultStatus
from raya.event_bus import EventBus
from raya.persistence import SqliteBackend
from raya.safety import AuditTrail, SafetyService, StopController
from raya.tasks import TaskRegistry
from raya.tools import ToolRegistry, execute
from raya.tools.catalog import TaskControlOps, register_task_control_tools


def _setup(tmp_path, create=None, wire_list=False):
    bus = EventBus()
    backend = SqliteBackend(tmp_path / "t.sqlite3")
    tasks = TaskRegistry(backend, bus)
    registry = ToolRegistry()
    ops = TaskControlOps(pause=tasks.pause, resume=tasks.resume, cancel=tasks.cancel,
                          checkpoint=tasks.checkpoint, get_task=tasks.get, create=create,
                          list=tasks.list if wire_list else None)
    register_task_control_tools(registry, ops)
    safety = SafetyService(StopController(bus), AuditTrail())
    return registry, safety, tasks, backend


def _call(name: str, arguments: dict) -> ToolCall:
    return ToolCall(tool_name=name, arguments=arguments, correlation_id="c1",
                     requested_by=ToolCallRequester(subsystem="harness", session_id="s1"))


def test_all_task_control_tools_registered():
    import tempfile
    from pathlib import Path

    registry, safety, tasks, backend = _setup(Path(tempfile.mkdtemp()))
    names = {t.name for t in registry.all()}
    assert {"tasks.pause", "tasks.resume", "tasks.cancel", "tasks.steer"} <= names
    backend.close()


def test_pause_and_resume_are_safe_and_execute_for_real(tmp_path):
    registry, safety, tasks, backend = _setup(tmp_path)
    try:
        task = tasks.create("recherche", TaskOwner(channel="voice", session_id="s1"), correlation_id="c1")
        tasks.start(task.id)

        assert registry.get("tasks.pause").permission_level == PermissionLevel.SAFE
        result = execute(registry, safety, _call("tasks.pause", {"task_id": task.id}))
        assert result.status == ToolResultStatus.SUCCESS
        assert result.output["state"] == "PAUSED"

        result2 = execute(registry, safety, _call("tasks.resume", {"task_id": task.id}))
        assert result2.status == ToolResultStatus.SUCCESS
        assert result2.output["state"] == "RUNNING"
    finally:
        backend.close()


def test_cancel_is_safe_like_a_scoped_stop_and_executes_for_real(tmp_path):
    """tasks.cancel est classé SAFE (raya/safety/risk.py) : annuler SA
    PROPRE tâche est l'équivalent d'un STOP scopé — cohérent avec le fait
    que le STOP global lui-même n'est jamais gated par une confirmation."""
    registry, safety, tasks, backend = _setup(tmp_path)
    try:
        assert registry.get("tasks.cancel").permission_level == PermissionLevel.SAFE
        task = tasks.create("recherche", TaskOwner(channel="voice", session_id="s1"), correlation_id="c1")
        result = execute(registry, safety, _call("tasks.cancel", {"task_id": task.id}))
        assert result.status == ToolResultStatus.SUCCESS
        assert tasks.get(task.id).state.value == "CANCELLED"
    finally:
        backend.close()


def test_steer_is_sensitive_and_merges_guidance_into_checkpoint_directly(tmp_path):
    """Test du handler directement (SENSITIVE, comme les patterns déjà
    établis Phase 3/4 pour les capacités mutantes) — prouve le MÉCANISME
    réel : la consigne s'ajoute au checkpoint SANS écraser l'existant."""
    registry, safety, tasks, backend = _setup(tmp_path)
    try:
        task = tasks.create("recherche", TaskOwner(channel="voice", session_id="s1"), correlation_id="c1")
        tasks.start(task.id)
        tasks.checkpoint(task.id, {"step_index": 3})

        handler = registry.handler_for("tasks.steer")
        assert registry.get("tasks.steer").permission_level == PermissionLevel.SENSITIVE
        result = handler(_call("tasks.steer", {"task_id": task.id, "guidance": "concentre-toi sur les sources officielles"}))
        assert result.status == ToolResultStatus.SUCCESS
        reloaded = tasks.get(task.id)
        assert reloaded.checkpoint["steering_guidance"] == "concentre-toi sur les sources officielles"
        assert reloaded.checkpoint["step_index"] == 3  # jamais écrasé
    finally:
        backend.close()


def test_pause_unknown_task_fails_honestly(tmp_path):
    registry, safety, tasks, backend = _setup(tmp_path)
    try:
        result = execute(registry, safety, _call("tasks.pause", {"task_id": "task_inconnue_xyz"}))
        # PAUSE est SAFE -> atteint réellement le handler -> échoue honnêtement
        assert result.status == ToolResultStatus.FAILURE
        assert result.error.code == "TASK_PAUSE_FAILED"
    finally:
        backend.close()


def test_steer_missing_task_reports_not_found(tmp_path):
    registry, safety, tasks, backend = _setup(tmp_path)
    try:
        handler = registry.handler_for("tasks.steer")
        result = handler(_call("tasks.steer", {"task_id": "inconnue", "guidance": "x"}))
        assert result.status == ToolResultStatus.FAILURE
        assert result.error.code == "TASK_NOT_FOUND"
    finally:
        backend.close()


def test_missing_task_id_argument_fails_before_touching_any_task(tmp_path):
    """Le schéma déclare task_id requis -> validate_call() (tools/execution.py)
    le rejette AVANT même d'atteindre le handler (défense en profondeur —
    la vérification MISSING_TASK_ID du handler lui-même reste un filet pour
    un appel direct hors pipeline, cf. test_steer_missing_task_reports_not_found)."""
    registry, safety, tasks, backend = _setup(tmp_path)
    try:
        result = execute(registry, safety, _call("tasks.pause", {}))
        assert result.status == ToolResultStatus.FAILURE
        assert result.error.code == "VALIDATION_ERROR"
    finally:
        backend.close()


def test_risk_classification_matches_tasks_control_vs_modify_tags():
    from raya.safety.risk import classify_risk

    assert classify_risk(["tasks.control"]) == PermissionLevel.SAFE
    assert classify_risk(["tasks.modify"]) == PermissionLevel.SENSITIVE


# --- tasks.create (RAYA V2 Phase 10, consigne §13) ---

def test_tasks_create_is_not_registered_when_ops_create_is_none(tmp_path):
    """Rétrocompatibilité (consigne §2) : un appelant Phase 5-9 qui construit
    `TaskControlOps` sans `create` (ex: un test existant) ne voit jamais
    apparaître `tasks.create` — jamais un Tool à moitié câblé."""
    registry, safety, tasks, backend = _setup(tmp_path, create=None)
    try:
        assert registry.get("tasks.create") is None
    finally:
        backend.close()


def test_tasks_create_is_registered_and_safe_when_wired(tmp_path):
    from raya.safety.risk import classify_risk

    registry, safety, tasks, backend = _setup(tmp_path, create=lambda obj, ch, sid, not_before=None: tasks.create(obj, TaskOwner(channel=ch, session_id=sid), correlation_id="c1", not_before=not_before))
    try:
        tool = registry.get("tasks.create")
        assert tool is not None
        assert tool.permission_level == PermissionLevel.SAFE
        assert classify_risk(tool.capability_tags) == PermissionLevel.SAFE
    finally:
        backend.close()


def test_tasks_create_handler_uses_requester_channel_and_session(tmp_path):
    """Consigne §13 : jamais un `TelegramTaskCreator`/`UITaskCreator` — le
    handler dérive TOUJOURS channel/session_id de `call.requested_by`,
    identique quel que soit le canal d'origine."""
    captured = {}

    def _create(objective, channel, session_id, not_before=None):
        captured["objective"] = objective
        captured["channel"] = channel
        captured["session_id"] = session_id
        return tasks.create(objective, TaskOwner(channel=channel, session_id=session_id), correlation_id="c1", not_before=not_before)

    registry, safety, tasks, backend = _setup(tmp_path, create=_create)
    try:
        call = ToolCall(
            tool_name="tasks.create", arguments={"objective": "rédiger un rapport"}, correlation_id="c1",
            requested_by=ToolCallRequester(subsystem="harness", session_id="telegram:555", channel="mobile"),
        )
        result = execute(registry, safety, call)
        assert result.status == ToolResultStatus.SUCCESS
        assert captured == {"objective": "rédiger un rapport", "channel": "mobile", "session_id": "telegram:555"}
    finally:
        backend.close()


def test_tasks_create_defaults_channel_to_cli_when_requester_channel_is_empty(tmp_path):
    """Rétrocompatibilité : un `ToolCallRequester` construit avant la Phase
    10 (sans `channel`) ne doit jamais faire planter `tasks.create`."""
    captured = {}

    def _create(objective, channel, session_id, not_before=None):
        captured["channel"] = channel
        return tasks.create(objective, TaskOwner(channel=channel, session_id=session_id), correlation_id="c1", not_before=not_before)

    registry, safety, tasks, backend = _setup(tmp_path, create=_create)
    try:
        call = ToolCall(
            tool_name="tasks.create", arguments={"objective": "obj"}, correlation_id="c1",
            requested_by=ToolCallRequester(subsystem="harness", session_id="s1"),  # pas de channel=
        )
        execute(registry, safety, call)
        assert captured["channel"] == "cli"
    finally:
        backend.close()


def test_tasks_create_missing_objective_fails_honestly(tmp_path):
    registry, safety, tasks, backend = _setup(tmp_path, create=lambda o, c, s, not_before=None: None)
    try:
        result = execute(registry, safety, _call("tasks.create", {}))
        assert result.status == ToolResultStatus.FAILURE
    finally:
        backend.close()


# --- tasks.create scheduling (Chantier 12 §B) ---

def test_tasks_create_with_delay_seconds_resolves_not_before(tmp_path):
    captured = {}

    def _create(objective, channel, session_id, not_before=None):
        captured["not_before"] = not_before
        return tasks.create(objective, TaskOwner(channel=channel, session_id=session_id), correlation_id="c1", not_before=not_before)

    registry, safety, tasks, backend = _setup(tmp_path, create=_create)
    try:
        result = execute(registry, safety, _call("tasks.create", {"objective": "rappel", "delay_seconds": 60}))
        assert result.status == ToolResultStatus.SUCCESS
        assert captured["not_before"] is not None
        assert result.output["not_before"] == captured["not_before"]
        assert tasks.get(result.output["task_id"]).not_before == captured["not_before"]
    finally:
        backend.close()


def test_tasks_create_without_schedule_args_has_no_not_before(tmp_path):
    captured = {}

    def _create(objective, channel, session_id, not_before=None):
        captured["not_before"] = not_before
        return tasks.create(objective, TaskOwner(channel=channel, session_id=session_id), correlation_id="c1", not_before=not_before)

    registry, safety, tasks, backend = _setup(tmp_path, create=_create)
    try:
        result = execute(registry, safety, _call("tasks.create", {"objective": "immédiat"}))
        assert result.status == ToolResultStatus.SUCCESS
        assert captured["not_before"] is None
    finally:
        backend.close()


def test_tasks_create_rejects_run_at_without_timezone_offset(tmp_path):
    registry, safety, tasks, backend = _setup(tmp_path, create=lambda o, c, s, not_before=None: None)
    try:
        result = execute(registry, safety, _call("tasks.create", {"objective": "obj", "run_at": "2026-01-01T10:00:00"}))
        assert result.status == ToolResultStatus.FAILURE
        assert result.error.code == "INVALID_SCHEDULE"
    finally:
        backend.close()


def test_tasks_create_rejects_both_delay_seconds_and_run_at(tmp_path):
    registry, safety, tasks, backend = _setup(tmp_path, create=lambda o, c, s, not_before=None: None)
    try:
        result = execute(registry, safety, _call(
            "tasks.create", {"objective": "obj", "delay_seconds": 5, "run_at": "2026-01-01T10:00:00+01:00"},
        ))
        assert result.status == ToolResultStatus.FAILURE
        assert result.error.code == "INVALID_SCHEDULE"
    finally:
        backend.close()


# --- tasks.list (Chantier 14 §16, "Task Identity") ---

def test_tasks_list_is_not_registered_when_ops_list_is_none(tmp_path):
    """Rétrocompatibilité (même pattern que tasks.create) : un appelant qui
    construit TaskControlOps sans `list` ne voit jamais apparaître tasks.list."""
    registry, safety, tasks, backend = _setup(tmp_path)
    try:
        assert registry.get("tasks.list") is None
    finally:
        backend.close()


def test_tasks_list_is_registered_and_safe_when_wired(tmp_path):
    from raya.safety.risk import classify_risk

    registry, safety, tasks, backend = _setup(tmp_path, wire_list=True)
    try:
        tool = registry.get("tasks.list")
        assert tool is not None
        assert tool.permission_level == PermissionLevel.SAFE
        assert classify_risk(tool.capability_tags) == PermissionLevel.SAFE
        assert tool.capability_tags == ["tasks.read"]  # ".read" -> Intent.INFORMATION (cognition/intent.py)
    finally:
        backend.close()


def test_tasks_list_defaults_to_active_tasks_only(tmp_path):
    """Sans argument, un rappel déjà terminé ne doit jamais réapparaître —
    évite que le modèle confonde un rappel passé avec un rappel encore actif."""
    registry, safety, tasks, backend = _setup(tmp_path, wire_list=True)
    try:
        owner = TaskOwner(channel="cli", session_id="s1")
        pending = tasks.create("rappel actif", owner, correlation_id="c1")
        done = tasks.create("rappel déjà fini", owner, correlation_id="c2")
        tasks.start(done.id)
        tasks.complete(done.id, {"ok": True})

        result = execute(registry, safety, _call("tasks.list", {}))
        assert result.status == ToolResultStatus.SUCCESS
        ids = {t["task_id"] for t in result.output["tasks"]}
        assert pending.id in ids
        assert done.id not in ids
    finally:
        backend.close()


def test_tasks_list_explicit_state_filter_can_reach_terminal_tasks(tmp_path):
    registry, safety, tasks, backend = _setup(tmp_path, wire_list=True)
    try:
        owner = TaskOwner(channel="cli", session_id="s1")
        done = tasks.create("rappel déjà fini", owner, correlation_id="c1")
        tasks.start(done.id)
        tasks.complete(done.id, {"ok": True})

        result = execute(registry, safety, _call("tasks.list", {"state": "COMPLETED"}))
        assert result.status == ToolResultStatus.SUCCESS
        ids = {t["task_id"] for t in result.output["tasks"]}
        assert done.id in ids
    finally:
        backend.close()


def test_tasks_list_output_shape_includes_not_before_and_objective(tmp_path):
    registry, safety, tasks, backend = _setup(tmp_path, wire_list=True)
    try:
        owner = TaskOwner(channel="telegram", session_id="s1")
        task = tasks.create("sortir le poulet", owner, correlation_id="c1", not_before="2026-09-06T22:00:00.000Z")

        result = execute(registry, safety, _call("tasks.list", {}))
        assert result.status == ToolResultStatus.SUCCESS
        [item] = [t for t in result.output["tasks"] if t["task_id"] == task.id]
        assert item["objective"] == "sortir le poulet"
        assert item["state"] == "PENDING"
        assert item["not_before"] == "2026-09-06T22:00:00.000Z"
        assert "created_at" in item
    finally:
        backend.close()


def test_tasks_list_empty_when_no_tasks_exist(tmp_path):
    registry, safety, tasks, backend = _setup(tmp_path, wire_list=True)
    try:
        result = execute(registry, safety, _call("tasks.list", {}))
        assert result.status == ToolResultStatus.SUCCESS
        assert result.output["tasks"] == []
    finally:
        backend.close()


def test_risk_classification_includes_tasks_read():
    from raya.safety.risk import classify_risk

    assert classify_risk(["tasks.read"]) == PermissionLevel.SAFE


def test_tasks_list_is_not_scoped_to_the_calling_session(tmp_path):
    """§21 (contexte conversationnel) : un rappel créé depuis un canal
    (ex: CLI) doit rester retrouvable pour annulation depuis un AUTRE canal
    (ex: Telegram) — la Task reste la source de vérité, jamais liée à une
    session de conversation particulière pour sa visibilité."""
    registry, safety, tasks, backend = _setup(tmp_path, wire_list=True)
    try:
        created_from_cli = tasks.create("sortir le poulet", TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")

        call_from_telegram = ToolCall(
            tool_name="tasks.list", arguments={}, correlation_id="c2",
            requested_by=ToolCallRequester(subsystem="harness", session_id="telegram:999", channel="telegram"),
        )
        result = execute(registry, safety, call_from_telegram)
        assert result.status == ToolResultStatus.SUCCESS
        assert created_from_cli.id in {t["task_id"] for t in result.output["tasks"]}
    finally:
        backend.close()


def test_tasks_list_state_filter_with_no_match_returns_empty_not_an_error(tmp_path):
    registry, safety, tasks, backend = _setup(tmp_path, wire_list=True)
    try:
        tasks.create("rappel", TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
        result = execute(registry, safety, _call("tasks.list", {"state": "FAILED"}))
        assert result.status == ToolResultStatus.SUCCESS
        assert result.output["tasks"] == []
    finally:
        backend.close()
