"""SceneStore — moteur headless du Spatial Scene Model (RAYA V2 Phase 8).

"Un seul point d'écriture par type de donnée" (RAYA_V2_ARCHITECTURAL_INVARIANTS.md
#13) : une seule `SceneStore` par process, injectée dans `Harness` (lecture,
vues UI) et dans `tools/catalog/spatial.py` (écriture, seul chemin de
mutation légitime — jamais le Harness ni l'UI ne mutent une scène
directement, RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.2 même principe que
`WorldStateStore`).

En mémoire uniquement cette phase (pas de `PersistenceBackend`) — une scène
est un artefact de travail créé/consulté/fermé dans la durée d'une session,
pas une donnée durable au sens de Memory (consigne §13 "ne pas confondre
Spatial Scene et Memory"). Documenté comme limitation (voir rapport §21) :
un redémarrage perd les scènes en cours, exactement comme World State
("pas de connaissance durable, pas d'historique long", §1.2).
"""

from __future__ import annotations

import threading

from raya.contracts import Geometry, Material, Scene, SpatialError, SpatialObject, Transform, Vec3, slugify_object_id, utc_now_iso, validate_object_id


def _vec3(data: dict | None, default: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> Vec3:
    data = data or {}
    return Vec3(
        x=float(data.get("x", default[0])), y=float(data.get("y", default[1])), z=float(data.get("z", default[2])),
    )


class SceneStore:
    def __init__(self) -> None:
        self._scenes: dict[str, Scene] = {}
        self._mounted: dict[str, str] = {}  # session_id -> scene_id actuellement "monté" (rendu)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Scenes
    # ------------------------------------------------------------------

    def create_scene(self, label: str = "Scene") -> Scene:
        scene = Scene(label=label or "Scene")
        with self._lock:
            self._scenes[scene.id] = scene
        return scene

    def get_scene(self, scene_id: str) -> Scene | None:
        with self._lock:
            return self._scenes.get(scene_id)

    def list_scenes(self) -> list[Scene]:
        with self._lock:
            return list(self._scenes.values())

    def delete_scene(self, scene_id: str) -> bool:
        with self._lock:
            existed = self._scenes.pop(scene_id, None) is not None
            for session_id, mounted_id in list(self._mounted.items()):
                if mounted_id == scene_id:
                    del self._mounted[session_id]
        return existed

    def _require_scene(self, scene_id: str) -> Scene:
        scene = self._scenes.get(scene_id)
        if scene is None:
            raise SpatialError(f"Scène inconnue : {scene_id!r}")
        return scene

    # ------------------------------------------------------------------
    # Objects — mutations explicites, jamais un accès direct à Scene.objects
    # depuis l'extérieur de ce module (consigne §11 "modifications de scène
    # explicites et vérifiables").
    # ------------------------------------------------------------------

    def add_object(
        self, scene_id: str, label: str, *, object_id: str | None = None, object_type: str = "object",
        parent: str | None = None, position: dict | None = None, rotation: dict | None = None,
        scale: dict | None = None, geometry: dict | None = None, material: dict | None = None,
        relationships: dict[str, str] | None = None,
    ) -> SpatialObject:
        with self._lock:
            scene = self._require_scene(scene_id)
            oid = object_id or slugify_object_id(label, existing=set(scene.objects.keys()))
            validate_object_id(oid)
            if oid in scene.objects:
                raise SpatialError(f"Un objet {oid!r} existe déjà dans la scène {scene_id!r}")
            if parent is not None and parent not in scene.objects:
                raise SpatialError(f"Parent inconnu : {parent!r}")

            obj = SpatialObject(
                id=oid, label=label, object_type=object_type, parent=parent,
                transform=Transform(
                    position=_vec3(position), rotation=_vec3(rotation), scale=_vec3(scale, default=(1.0, 1.0, 1.0)),
                ),
                geometry=Geometry(kind=geometry["kind"], params=geometry.get("params", {})) if geometry else None,
                material=Material(**material) if material else None,
                relationships=dict(relationships or {}),
            )
            scene.objects[oid] = obj
            if parent is not None:
                scene.objects[parent].children.append(oid)
            else:
                scene.root_ids.append(oid)
            scene.updated_at = utc_now_iso()

            # Vérification immédiate (consigne §11 "add object -> verify
            # object exists") — déterministe ici (mutation en mémoire),
            # jamais présumée depuis l'absence d'exception seule.
            if oid not in scene.objects:
                raise SpatialError(f"Échec de vérification : {oid!r} absent après insertion")
            return obj

    def update_object(
        self, scene_id: str, object_id: str, *, label: str | None = None,
        position: dict | None = None, rotation: dict | None = None, scale: dict | None = None,
        relationships: dict[str, str] | None = None,
    ) -> SpatialObject:
        with self._lock:
            scene = self._require_scene(scene_id)
            obj = scene.objects.get(object_id)
            if obj is None:
                raise SpatialError(f"Objet inconnu : {object_id!r}")
            if label is not None:
                obj.label = label
            if position is not None:
                obj.transform.position = _vec3(position, default=(obj.transform.position.x, obj.transform.position.y, obj.transform.position.z))
            if rotation is not None:
                obj.transform.rotation = _vec3(rotation, default=(obj.transform.rotation.x, obj.transform.rotation.y, obj.transform.rotation.z))
            if scale is not None:
                obj.transform.scale = _vec3(scale, default=(obj.transform.scale.x, obj.transform.scale.y, obj.transform.scale.z))
            if relationships is not None:
                obj.relationships.update(relationships)
            scene.updated_at = utc_now_iso()
            return obj

    def remove_object(self, scene_id: str, object_id: str) -> None:
        with self._lock:
            scene = self._require_scene(scene_id)
            obj = scene.objects.get(object_id)
            if obj is None:
                raise SpatialError(f"Objet inconnu : {object_id!r}")
            # Retire récursivement les descendants — jamais un enfant orphelin.
            to_remove = [object_id] + self._descendants(scene, object_id)
            for oid in to_remove:
                scene.objects.pop(oid, None)
            if obj.parent is not None and obj.parent in scene.objects:
                scene.objects[obj.parent].children = [c for c in scene.objects[obj.parent].children if c != object_id]
            scene.root_ids = [r for r in scene.root_ids if r != object_id]
            scene.updated_at = utc_now_iso()

    def _descendants(self, scene: Scene, object_id: str) -> list[str]:
        out: list[str] = []
        stack = list(scene.objects[object_id].children) if object_id in scene.objects else []
        while stack:
            oid = stack.pop()
            if oid in scene.objects:
                out.append(oid)
                stack.extend(scene.objects[oid].children)
        return out

    def list_objects(self, scene_id: str) -> list[SpatialObject]:
        with self._lock:
            scene = self._require_scene(scene_id)
            return list(scene.objects.values())

    def describe_scene(self, scene_id: str) -> dict:
        """Résumé STRUCTURÉ — jamais la scène complète dupliquée ailleurs
        (consigne §12 "pas de dump massif du World State" : ce résumé, pas
        la scène entière, est ce qui a vocation à être promu en World State)."""
        with self._lock:
            scene = self._require_scene(scene_id)
            return {
                "scene_id": scene.id,
                "label": scene.label,
                "object_count": len(scene.objects),
                "root_ids": list(scene.root_ids),
                "objects": [
                    {"id": o.id, "label": o.label, "object_type": o.object_type, "parent": o.parent}
                    for o in scene.objects.values()
                ],
            }

    # ------------------------------------------------------------------
    # Renderer lifecycle (consigne §18) — bookkeeping seul : le VRAI
    # renderer (Three.js) vit côté client, ce module ne fait que suivre
    # "quelle scène est actuellement montée pour quelle session", jamais un
    # état de renderer global permanent.
    # ------------------------------------------------------------------

    def mount(self, session_id: str, scene_id: str) -> None:
        with self._lock:
            self._require_scene(scene_id)
            self._mounted[session_id] = scene_id

    def unmount(self, session_id: str) -> None:
        with self._lock:
            self._mounted.pop(session_id, None)

    def mounted_scene_id(self, session_id: str) -> str | None:
        with self._lock:
            return self._mounted.get(session_id)
