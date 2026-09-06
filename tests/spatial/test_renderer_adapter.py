"""ThreeJSAdapter (RAYA V2 Phase 8 consigne §7-8) — frontière Data/Rendering :
transforme une `Scene` en payload JSON pour Three.js, ne stocke rien, ne
mute rien, connaît les seules conventions Three.js du système."""

from __future__ import annotations

from raya.contracts import Geometry, Material, Scene, SpatialObject, Transform, Vec3
from raya.spatial import SceneStore, ThreeJSAdapter
from raya.spatial.renderer.base import RendererAdapter


def test_threejs_adapter_is_a_renderer_adapter():
    assert isinstance(ThreeJSAdapter(), RendererAdapter)


def test_to_payload_flattens_vec3_into_arrays():
    scene = Scene(label="Test")
    scene.objects["cube"] = SpatialObject(
        id="cube", label="Cube", transform=Transform(position=Vec3(1, 2, 3)),
    )
    scene.root_ids.append("cube")
    payload = ThreeJSAdapter().to_payload(scene)
    obj = payload["objects"][0]
    assert obj["position"] == [1.0, 2.0, 3.0]
    assert obj["rotation"] == [0.0, 0.0, 0.0]
    assert obj["scale"] == [1.0, 1.0, 1.0]


def test_to_payload_includes_known_geometry_kind_unchanged():
    scene = Scene()
    scene.objects["ball"] = SpatialObject(id="ball", label="Ball", geometry=Geometry(kind="sphere", params={"radius": 1.0}))
    payload = ThreeJSAdapter().to_payload(scene)
    assert payload["objects"][0]["geometry"] == {"kind": "sphere", "params": {"radius": 1.0}}


def test_to_payload_falls_back_honestly_on_unknown_geometry_kind():
    """Jamais un crash sur un kind non reconnu — repli explicite sur 'box'."""
    scene = Scene()
    scene.objects["thing"] = SpatialObject(id="thing", label="Thing", geometry=Geometry(kind="dodecahedron", params={}))
    payload = ThreeJSAdapter().to_payload(scene)
    assert payload["objects"][0]["geometry"]["kind"] == "box"


def test_to_payload_object_without_geometry_or_material_is_none():
    scene = Scene()
    scene.objects["empty"] = SpatialObject(id="empty", label="Empty")
    payload = ThreeJSAdapter().to_payload(scene)
    assert payload["objects"][0]["geometry"] is None
    assert payload["objects"][0]["material"] is None


def test_to_payload_includes_relationships_and_hierarchy():
    scene = Scene()
    scene.objects["sun"] = SpatialObject(id="sun", label="Sun", children=["earth"])
    scene.objects["earth"] = SpatialObject(id="earth", label="Earth", parent="sun", relationships={"orbits": "sun"})
    scene.root_ids.append("sun")
    payload = ThreeJSAdapter().to_payload(scene)
    earth_payload = next(o for o in payload["objects"] if o["id"] == "earth")
    assert earth_payload["parent"] == "sun"
    assert earth_payload["relationships"] == {"orbits": "sun"}
    sun_payload = next(o for o in payload["objects"] if o["id"] == "sun")
    assert sun_payload["children"] == ["earth"]


def test_to_payload_never_mutates_the_source_scene():
    store = SceneStore()
    scene = store.create_scene()
    store.add_object(scene.id, "Cube", object_id="cube")
    before = store.describe_scene(scene.id)
    ThreeJSAdapter().to_payload(scene)
    after = store.describe_scene(scene.id)
    assert before == after
