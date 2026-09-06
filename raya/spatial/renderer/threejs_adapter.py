"""ThreeJSAdapter (RAYA V2 Phase 8) — SEUL endroit du Core connaissant les
conventions Three.js (noms de géométries, forme attendue par la scène JS du
Cockpit). `raya/contracts/spatial.py` reste volontairement ignorant de ces
conventions (consigne §6 "le modèle de scène ne doit pas importer Three.js").

Aucune dépendance à une bibliothèque Three.js Python (il n'en existe pas de
toute façon) — ce module ne fait QUE façonner un dict JSON, envoyé tel quel
au vrai Three.js qui tourne côté navigateur (raya/interfaces/ui/static/)."""

from __future__ import annotations

from raya.contracts import Scene, SpatialObject

from .base import RendererAdapter

# Mapping GÉNÉRIQUE, jamais fermé arbitrairement : un `Geometry.kind` non
# reconnu est transmis tel quel — c'est au renderer JS de décider d'un
# repli honnête (ex: un cube générique), jamais un crash Python.
_KNOWN_KINDS = frozenset({"box", "sphere", "cylinder", "cone", "torus", "plane", "group"})


def _vec3_array(v) -> list[float]:
    return [v.x, v.y, v.z]


def _object_payload(obj: SpatialObject) -> dict:
    return {
        "id": obj.id,
        "label": obj.label,
        "type": obj.object_type,
        "parent": obj.parent,
        "children": list(obj.children),
        "position": _vec3_array(obj.transform.position),
        "rotation": _vec3_array(obj.transform.rotation),
        "scale": _vec3_array(obj.transform.scale),
        "geometry": (
            {"kind": obj.geometry.kind if obj.geometry.kind in _KNOWN_KINDS else "box",
             "params": dict(obj.geometry.params)}
            if obj.geometry else None
        ),
        "material": {"color": obj.material.color, "opacity": obj.material.opacity} if obj.material else None,
        "relationships": dict(obj.relationships),
    }


class ThreeJSAdapter(RendererAdapter):
    def to_payload(self, scene: Scene) -> dict:
        return {
            "scene_id": scene.id,
            "label": scene.label,
            "root_ids": list(scene.root_ids),
            "objects": [_object_payload(o) for o in scene.objects.values()],
            "camera": dict(scene.camera),
        }
