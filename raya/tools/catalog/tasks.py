"""Déclarations de Tools pour le STEERING de tâches (consigne Phase 5 §15) :
tasks.pause/resume/cancel/steer. Le voice layer (ou tout autre canal) ne
modifie JAMAIS directement un Task Actor — la transcription finale passe par
le pipeline normal (Harness -> Cognition -> Tool Discovery -> Safety -> ce
Tool), exactement comme n'importe quelle autre capacité.

Injection de dépendance étroite (`TaskControlOps`, 4 callables) plutôt
qu'un import `raya.harness` : `tools/` est structurellement EN DESSOUS de
`harness/` dans le graphe de dépendance
(RAYA_V2_REPOSITORY_STRUCTURE.md §20 — "un subsystem ne dépend que des
subsystems strictement en dessous de lui... toute communication ascendante
passe par un Event, pas par un appel direct"). Importer `raya.harness` ici
créerait une dépendance ascendante interdite. Le PRÉCÉDENT déjà établi
(Phase 3/4, `should_stop: Callable[[], bool]` injecté dans les Device Agents
plutôt qu'un import `raya.safety`) est repris à l'identique : bootstrap.py
(composition root, autorisé à tout importer) construit `TaskControlOps` à
partir des méthodes déjà publiques du Harness et l'injecte ici — aucun
import ascendant, mais un appel synchrone réel (nécessaire pour un
`ToolResult` honnête, preuve immédiate du succès/échec, pas un événement
fire-and-forget dont on ne connaîtrait jamais l'issue)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from raya.contracts import ErrorInfo, PermissionLevel, Tool, ToolCall, ToolResult, ToolResultStatus, resolve_not_before


@dataclass
class TaskControlOps:
    pause: Callable[[str], object]
    resume: Callable[[str], object]
    cancel: Callable[[str], object]
    checkpoint: Callable[[str, dict], object]
    get_task: Callable[[str], object | None]
    # AJOUTÉ Phase 10 (consigne §13, "natural language task creation" doit
    # être centralisé, jamais par interface) — lié à
    # `Harness.create_long_horizon_task` par bootstrap.py, même injection
    # étroite que les 4 callables ci-dessus (jamais un import raya.harness ici).
    # 4e argument (Chantier 12 §B, additif) : `not_before` ISO8601 UTC ou
    # `None` — résolu ICI depuis delay_seconds/run_at, jamais par Harness.
    create: Callable[[str, str, str, str | None], object] | None = None
    # AJOUTÉ Chantier 14 (Natural Language Tasks §16, "Task Identity") : lié
    # à `Harness.list_tasks` — nécessaire pour que le modèle retrouve le
    # task_id réel d'une référence utilisateur ("mon rappel", "celui que je
    # viens de créer") AVANT tasks.cancel, plutôt que de deviner un id.
    # Optionnel comme `create` ci-dessus, même raison de rétrocompatibilité.
    list: Callable[[], list] | None = None


def _task_output(task) -> dict:
    return {"task_id": task.id, "state": task.state.value, "objective": task.objective}


def _make_simple_handler(op: Callable[[str], object], op_name: str):
    def handler(call: ToolCall) -> ToolResult:
        task_id = call.arguments.get("task_id")
        if not task_id:
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                               error=ErrorInfo(code="MISSING_TASK_ID", message="task_id requis", retryable=False))
        try:
            task = op(task_id)
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output=_task_output(task))
        except (KeyError, ValueError) as exc:
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                               error=ErrorInfo(code=f"TASK_{op_name.upper()}_FAILED", message=str(exc), retryable=False))

    return handler


def _make_steer_handler(ops: TaskControlOps):
    def handler(call: ToolCall) -> ToolResult:
        task_id = call.arguments.get("task_id")
        guidance = call.arguments.get("guidance", "")
        if not task_id or not guidance:
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                               error=ErrorInfo(code="MISSING_ARGUMENT", message="task_id et guidance requis", retryable=False))
        task = ops.get_task(task_id)
        if task is None:
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                               error=ErrorInfo(code="TASK_NOT_FOUND", message=f"tâche inconnue: {task_id!r}", retryable=False))
        merged = dict(task.checkpoint or {})
        merged["steering_guidance"] = guidance
        try:
            updated = ops.checkpoint(task_id, merged)
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
                               output={**_task_output(updated), "steering_guidance": guidance})
        except (KeyError, ValueError) as exc:
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                               error=ErrorInfo(code="TASK_STEER_FAILED", message=str(exc), retryable=False))

    return handler


def _make_create_handler(ops: TaskControlOps):
    def handler(call: ToolCall) -> ToolResult:
        objective = (call.arguments.get("objective") or "").strip()
        if not objective:
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                               error=ErrorInfo(code="MISSING_ARGUMENT", message="objective requis", retryable=False))
        # `call.requested_by.channel`/`.session_id` (Phase 10 : `channel`
        # ajouté à ToolCallRequester) — jamais un `TelegramTaskCreator`/
        # `UITaskCreator` par interface, UNE seule Task creation ici,
        # peu importe quel canal a déclenché l'appel (consigne §13).
        channel = call.requested_by.channel or "cli"
        session_id = call.requested_by.session_id
        # Chantier 12 §B (Persistent Scheduling), additif : `delay_seconds`/
        # `run_at` sont mutuellement exclusifs et résolus ICI (jamais devinés
        # par Harness) — `resolve_not_before` rejette un `run_at` sans fuseau
        # explicite (jamais un fuseau inventé, voir contracts/clock.py).
        delay_seconds = call.arguments.get("delay_seconds")
        run_at = call.arguments.get("run_at")
        try:
            not_before = resolve_not_before(delay_seconds=delay_seconds, run_at=run_at)
        except ValueError as exc:
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                               error=ErrorInfo(code="INVALID_SCHEDULE", message=str(exc), retryable=False))
        try:
            task = ops.create(objective, channel, session_id, not_before=not_before)
        except Exception as exc:  # jamais planter tools/execution.py pour une erreur de planification
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                               error=ErrorInfo(code="TASK_CREATE_FAILED", message=str(exc), retryable=False))
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
                           output={**_task_output(task), "not_before": not_before})

    return handler


# États terminaux — exclus par défaut de tasks.list (Chantier 14) : l'usage
# principal est de retrouver un rappel/une tâche ENCORE actionnable (pour
# tasks.cancel/tasks.create de remplacement), jamais de faire défiler tout
# l'historique. Un filtre `state` explicite reste disponible pour le reste.
_TERMINAL_STATE_VALUES = {"COMPLETED", "FAILED", "CANCELLED"}


def _task_list_item(task) -> dict:
    return {
        "task_id": task.id, "objective": task.objective, "state": task.state.value,
        "not_before": task.not_before, "created_at": task.created_at,
    }


def _make_list_handler(ops: TaskControlOps):
    def handler(call: ToolCall) -> ToolResult:
        state_filter = call.arguments.get("state")
        tasks = ops.list()
        if state_filter:
            tasks = [t for t in tasks if t.state.value == state_filter]
        else:
            tasks = [t for t in tasks if t.state.value not in _TERMINAL_STATE_VALUES]
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
                           output={"tasks": [_task_list_item(t) for t in tasks]})

    return handler


def register_task_control_tools(registry, ops: TaskControlOps) -> None:
    registry.register(
        Tool(name="tasks.pause", description="Met une tâche de fond en pause.",
             capability_tags=["tasks.control"], input_schema={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
             output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE, idempotent=True),
        _make_simple_handler(ops.pause, "pause"),
    )
    registry.register(
        Tool(name="tasks.resume", description="Reprend une tâche en pause.",
             capability_tags=["tasks.control"], input_schema={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
             output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE, idempotent=True),
        _make_simple_handler(ops.resume, "resume"),
    )
    registry.register(
        Tool(name="tasks.cancel", description="Annule une tâche de fond (équivalent d'un STOP scopé à cette tâche).",
             capability_tags=["tasks.control"], input_schema={"type": "object", "properties": {"task_id": {"type": "string"}}, "required": ["task_id"]},
             output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE, idempotent=True),
        _make_simple_handler(ops.cancel, "cancel"),
    )
    registry.register(
        Tool(name="tasks.steer", description="Modifie la consigne d'une tâche en cours (ex: 'concentre-toi sur les sources officielles').",
             capability_tags=["tasks.modify"],
             input_schema={"type": "object", "properties": {"task_id": {"type": "string"}, "guidance": {"type": "string"}}, "required": ["task_id", "guidance"]},
             output_schema={"type": "object"}, permission_level=PermissionLevel.SENSITIVE, idempotent=False),
        _make_steer_handler(ops),
    )
    if ops.create is not None:
        registry.register(
            Tool(name="tasks.create",
                 description=(
                     "Crée une tâche de fond à plusieurs étapes pour un objectif qui prend du "
                     "temps ou nécessite plusieurs actions (recherche, analyse, rapport...), OU "
                     "programme une action/un rappel pour PLUS TARD (Chantier 12 §B). La conversation "
                     "continue immédiatement ; l'utilisateur sera notifié quand la tâche sera "
                     "terminée. N'utilise PAS ceci pour une question ou une action simple réalisable "
                     "dans ce tour SANS délai demandé. Pour un délai/une échéance future, passe "
                     "OBLIGATOIREMENT `delay_seconds` (délai relatif, ex: 'dans 5 secondes' -> 5) ou "
                     "`run_at` (horodatage ISO8601 ABSOLU avec fuseau explicite, ex: 'demain à 9h' — "
                     "lis l'heure/le fuseau courants via system.time.now avant de le construire) — "
                     "sans l'un de ces deux arguments, la tâche s'exécute IMMÉDIATEMENT, jamais au "
                     "moment demandé."
                 ),
                 capability_tags=["tasks.control"],
                 input_schema={
                     "type": "object",
                     "properties": {
                         "objective": {"type": "string"},
                         "delay_seconds": {"type": "number", "description": "délai relatif en secondes avant exécution, ex: 300 pour 'dans 5 minutes'. Mutuellement exclusif avec run_at."},
                         "run_at": {"type": "string", "description": "horodatage ISO8601 ABSOLU avec fuseau explicite (ex: '2026-09-06T22:00:00+02:00'), jamais une heure locale sans offset. Mutuellement exclusif avec delay_seconds."},
                     },
                     "required": ["objective"],
                 },
                 output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE, idempotent=False),
            _make_create_handler(ops),
        )
    if ops.list is not None:
        registry.register(
            Tool(name="tasks.list",
                 description=(
                     "Liste les tâches de fond/rappels programmés. Sans argument, ne retourne QUE "
                     "les tâches encore actionnables (PENDING/RUNNING/PAUSED) — jamais celles déjà "
                     "terminées/échouées/annulées. Utilise ceci AVANT tasks.cancel, ou avant de créer "
                     "un nouveau rappel, pour retrouver le task_id réel derrière une référence de "
                     "l'utilisateur comme 'mon rappel' ou 'celui que je viens de créer' — ne devine "
                     "JAMAIS un task_id. Si plusieurs tâches actives correspondent à ce que "
                     "l'utilisateur décrit, demande-lui de préciser plutôt que d'en choisir une au "
                     "hasard. Pour changer l'heure/le délai d'un rappel existant, il n'y a pas de "
                     "modification en place : annule l'ancien (tasks.cancel) puis crée le nouveau "
                     "(tasks.create) avec le bon délai."
                 ),
                 capability_tags=["tasks.read"],
                 input_schema={
                     "type": "object",
                     "properties": {
                         "state": {
                             "type": "string",
                             "enum": ["PENDING", "RUNNING", "PAUSED", "COMPLETED", "FAILED", "CANCELLED"],
                             "description": "Filtre optionnel par état exact. Omis = seulement les tâches encore actionnables.",
                         },
                     },
                 },
                 output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE, idempotent=True),
            _make_list_handler(ops),
        )
