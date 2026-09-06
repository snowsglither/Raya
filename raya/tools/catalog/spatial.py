"""Déclarations de Tools pour le Creative/Spatial Agent (RAYA V2 Phase 8).

Même pattern que `tools/catalog/demo.py` (pas de Device Agent — exécution
locale directe) plutôt que `pc.py`/`browser.py` (délégation à un Device) :
une scène spatiale n'a pas de "device" physique à commander, c'est une
mutation de données en mémoire via `SceneStore` (injecté, jamais importé
depuis `raya.harness` — `tools/` reste structurellement en dessous de
`harness/`, RAYA_V2_REPOSITORY_STRUCTURE.md §20).

`scene.render`/`scene.close` publient `ui.view_requested` EXACTEMENT comme
`tools/catalog/ui_views.py` (même EventBus, même contrat, même liste
`_VALID_VIEWS` partagée) — c'est la SEULE façon dont une vue Cockpit
s'ouvre/se ferme (consigne Phase 8 §17 : "Le modèle doit pouvoir demander
explicitement une vue spatiale... NE PAS utiliser du JavaScript pour
analyser les phrases utilisateur")."""

from __future__ import annotations

import json
from pathlib import Path

from raya.contracts import ErrorInfo, Event, ObservationSpec, PermissionLevel, SpatialError, Tool, ToolCall, ToolResult, ToolResultStatus, to_dict
from raya.event_bus import EventBus
from raya.spatial import SceneStore

_SCENE_OBJECT_COUNT_OBSERVATION = (
    ObservationSpec(domain="spatial", key="scene_object_count", evidence_field="object_count"),
)
_ACTIVE_SCENE_OBSERVATION = (
    ObservationSpec(domain="spatial", key="active_scene", evidence_field="scene_id"),
)
_RENDERER_STATE_OBSERVATION = (
    ObservationSpec(domain="spatial", key="renderer_state", evidence_field="state", freshness_ttl_s=300),
)


def _fail(call: ToolCall, code: str, message: str, retryable: bool = False) -> ToolResult:
    return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                       error=ErrorInfo(code=code, message=message, retryable=retryable))


def _resolve_safe_path(workspace_root: Path, rel_path: str) -> Path:
    workspace_resolved = workspace_root.resolve()
    candidate = (workspace_resolved / rel_path).resolve()
    try:
        candidate.relative_to(workspace_resolved)
    except ValueError:
        raise ValueError(f"chemin hors du workspace sandboxé : {rel_path!r}") from None
    return candidate


def _tool(name: str, description: str, input_schema: dict, tag: str,
          idempotent: bool = False, observation=()) -> Tool:
    return Tool(name=name, description=description, capability_tags=[tag], input_schema=input_schema,
                output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE,
                idempotent=idempotent, observation=observation)


def register_spatial_tools(registry, scene_store: SceneStore, bus: EventBus, workspace_root: Path) -> None:
    registry.register(
        _tool("scene.create", "Crée une nouvelle scène spatiale vide.",
              {"type": "object", "properties": {"label": {"type": "string"}}},
              "spatial.create", idempotent=False, observation=_ACTIVE_SCENE_OBSERVATION),
        _make_create(scene_store),
    )
    registry.register(
        _tool("scene.add_object",
              "Ajoute un objet (sémantique libre : planète, serveur, cube...) à une scène, "
              "optionnellement rattaché à un parent (hiérarchie) ou lié par une relation nommée.",
              {"type": "object", "properties": {
                  "scene_id": {"type": "string"}, "label": {"type": "string"}, "object_type": {"type": "string"},
                  "parent": {"type": "string"},
                  "position": {"type": "object"}, "rotation": {"type": "object"}, "scale": {"type": "object"},
                  "geometry": {"type": "object"}, "material": {"type": "object"},
                  "relationships": {"type": "object"},
              }, "required": ["scene_id", "label"]},
              "spatial.modify", observation=_SCENE_OBJECT_COUNT_OBSERVATION),
        _make_add_object(scene_store),
    )
    registry.register(
        _tool("scene.update_object", "Modifie le label/transform/relations d'un objet existant.",
              {"type": "object", "properties": {
                  "scene_id": {"type": "string"}, "object_id": {"type": "string"}, "label": {"type": "string"},
                  "position": {"type": "object"}, "rotation": {"type": "object"}, "scale": {"type": "object"},
                  "relationships": {"type": "object"},
              }, "required": ["scene_id", "object_id"]},
              "spatial.modify"),
        _make_update_object(scene_store),
    )
    registry.register(
        _tool("scene.remove_object", "Retire un objet (et ses descendants) d'une scène.",
              {"type": "object", "properties": {"scene_id": {"type": "string"}, "object_id": {"type": "string"}},
               "required": ["scene_id", "object_id"]},
              "spatial.modify", observation=_SCENE_OBJECT_COUNT_OBSERVATION),
        _make_remove_object(scene_store),
    )
    registry.register(
        _tool("scene.describe", "Décrit une scène : nombre d'objets, hiérarchie, types — jamais la scène complète.",
              {"type": "object", "properties": {"scene_id": {"type": "string"}}, "required": ["scene_id"]},
              "spatial.read", idempotent=True),
        _make_describe(scene_store),
    )
    registry.register(
        _tool("scene.render", "Affiche une scène dans la vue spatiale du Cockpit.",
              {"type": "object", "properties": {"scene_id": {"type": "string"}}, "required": ["scene_id"]},
              "spatial.render", observation=_RENDERER_STATE_OBSERVATION),
        _make_render(scene_store, bus),
    )
    registry.register(
        _tool("scene.close", "Ferme la vue spatiale du Cockpit et démonte le renderer proprement.",
              {"type": "object", "properties": {"scene_id": {"type": "string"}}, "required": ["scene_id"]},
              "spatial.render", observation=_RENDERER_STATE_OBSERVATION),
        _make_close(scene_store, bus),
    )
    registry.register(
        _tool("scene.export", "Exporte une scène en JSON (et optionnellement dans un fichier du workspace).",
              {"type": "object", "properties": {"scene_id": {"type": "string"}, "filename": {"type": "string"}},
               "required": ["scene_id"]},
              "spatial.export", idempotent=True),
        _make_export(scene_store, workspace_root),
    )


def _make_create(scene_store: SceneStore):
    def handler(call: ToolCall) -> ToolResult:
        label = call.arguments.get("label") or "Scene"
        scene = scene_store.create_scene(label=label)
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
                           output={"scene_id": scene.id, "label": scene.label},
                           evidence={"scene_id": scene.id})

    return handler


def _make_add_object(scene_store: SceneStore):
    def handler(call: ToolCall) -> ToolResult:
        args = call.arguments
        try:
            obj = scene_store.add_object(
                args["scene_id"], args["label"], object_type=args.get("object_type", "object"),
                parent=args.get("parent"), position=args.get("position"), rotation=args.get("rotation"),
                scale=args.get("scale"), geometry=args.get("geometry"), material=args.get("material"),
                relationships=args.get("relationships"),
            )
        except SpatialError as exc:
            return _fail(call, "SPATIAL_ERROR", str(exc))
        count = len(scene_store.list_objects(args["scene_id"]))
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
                           output={"object_id": obj.id, "label": obj.label},
                           evidence={"object_id": obj.id, "object_count": count})

    return handler


def _make_update_object(scene_store: SceneStore):
    def handler(call: ToolCall) -> ToolResult:
        args = call.arguments
        try:
            obj = scene_store.update_object(
                args["scene_id"], args["object_id"], label=args.get("label"),
                position=args.get("position"), rotation=args.get("rotation"), scale=args.get("scale"),
                relationships=args.get("relationships"),
            )
        except SpatialError as exc:
            return _fail(call, "SPATIAL_ERROR", str(exc))
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
                           output={"object_id": obj.id, "label": obj.label},
                           evidence={"object_id": obj.id})

    return handler


def _make_remove_object(scene_store: SceneStore):
    def handler(call: ToolCall) -> ToolResult:
        args = call.arguments
        try:
            scene_store.remove_object(args["scene_id"], args["object_id"])
        except SpatialError as exc:
            return _fail(call, "SPATIAL_ERROR", str(exc))
        count = len(scene_store.list_objects(args["scene_id"]))
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
                           output={"removed": args["object_id"]}, evidence={"object_count": count})

    return handler


def _make_describe(scene_store: SceneStore):
    def handler(call: ToolCall) -> ToolResult:
        try:
            summary = scene_store.describe_scene(call.arguments["scene_id"])
        except SpatialError as exc:
            return _fail(call, "SPATIAL_ERROR", str(exc), retryable=False)
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output=summary, evidence={"object_count": summary["object_count"]})

    return handler


def _make_render(scene_store: SceneStore, bus: EventBus):
    def handler(call: ToolCall) -> ToolResult:
        scene_id = call.arguments["scene_id"]
        session_id = call.requested_by.session_id
        try:
            scene_store.mount(session_id, scene_id)
        except SpatialError as exc:
            return _fail(call, "SPATIAL_ERROR", str(exc))
        bus.publish(Event(
            type="ui.view_requested", source="tools", correlation_id=call.correlation_id,
            payload={"session_id": session_id, "view": "spatial", "action": "show"},
        ))
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
                           output={"scene_id": scene_id, "mounted": True}, evidence={"state": "mounted"})

    return handler


def _make_close(scene_store: SceneStore, bus: EventBus):
    def handler(call: ToolCall) -> ToolResult:
        scene_id = call.arguments["scene_id"]
        session_id = call.requested_by.session_id
        scene_store.unmount(session_id)
        bus.publish(Event(
            type="ui.view_requested", source="tools", correlation_id=call.correlation_id,
            payload={"session_id": session_id, "view": "spatial", "action": "hide"},
        ))
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
                           output={"scene_id": scene_id, "mounted": False}, evidence={"state": "unmounted"})

    return handler


def _make_export(scene_store: SceneStore, workspace_root: Path):
    def handler(call: ToolCall) -> ToolResult:
        scene = scene_store.get_scene(call.arguments["scene_id"])
        if scene is None:
            return _fail(call, "SPATIAL_ERROR", f"Scène inconnue : {call.arguments['scene_id']!r}")
        payload = to_dict(scene)
        filename = call.arguments.get("filename")
        if not filename:
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
                               output={"scene": payload}, evidence={"exported": False})
        try:
            path = _resolve_safe_path(workspace_root, filename)
        except ValueError as exc:
            return _fail(call, "INVALID_ARGUMENT", str(exc))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
                           output={"path": str(path.relative_to(workspace_root.resolve()))},
                           evidence={"exported": True, "path_exists": path.exists()})

    return handler
