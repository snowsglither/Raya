"""SceneStore (RAYA V2 Phase 8) — moteur headless : création, mutations
explicites, hiérarchie parent/enfant, relations nommées, cycle de vie du
renderer (mount/unmount). Aucun Three.js, aucune UI, aucun modèle ici."""

from __future__ import annotations

import pytest

from raya.contracts import SpatialError
from raya.spatial import SceneStore


@pytest.fixture
def store() -> SceneStore:
    return SceneStore()


def test_create_scene_returns_real_scene_with_stable_id(store):
    scene = store.create_scene(label="Solar System")
    assert scene.label == "Solar System"
    assert store.get_scene(scene.id) is scene


def test_get_unknown_scene_returns_none(store):
    assert store.get_scene("does-not-exist") is None


def test_list_scenes_reflects_all_created(store):
    store.create_scene("A")
    store.create_scene("B")
    assert len(store.list_scenes()) == 2


def test_add_object_creates_root_object_by_default(store):
    scene = store.create_scene()
    obj = store.add_object(scene.id, "Sun", object_type="star")
    assert obj.id == "sun"
    assert obj.parent is None
    assert scene.root_ids == ["sun"]


def test_add_object_with_explicit_parent_updates_children():
    store = SceneStore()
    scene = store.create_scene()
    store.add_object(scene.id, "Sun", object_id="sun")
    earth = store.add_object(scene.id, "Earth", object_id="earth", parent="sun", object_type="planet")
    assert earth.parent == "sun"
    assert "earth" in scene.objects["sun"].children
    assert "earth" not in scene.root_ids


def test_add_object_rejects_unknown_parent(store):
    scene = store.create_scene()
    with pytest.raises(SpatialError):
        store.add_object(scene.id, "Moon", parent="does-not-exist")


def test_add_object_rejects_duplicate_id(store):
    scene = store.create_scene()
    store.add_object(scene.id, "Cube", object_id="cube")
    with pytest.raises(SpatialError):
        store.add_object(scene.id, "Another Cube", object_id="cube")


def test_add_object_auto_generates_unique_id_from_label(store):
    scene = store.create_scene()
    a = store.add_object(scene.id, "Server")
    b = store.add_object(scene.id, "Server")
    assert a.id != b.id
    assert a.id == "server"
    assert b.id == "server_2"


def test_add_object_to_unknown_scene_raises(store):
    with pytest.raises(SpatialError):
        store.add_object("no-such-scene", "X")


def test_add_object_position_geometry_material_relationships_stored(store):
    scene = store.create_scene()
    obj = store.add_object(
        scene.id, "Earth", object_id="earth", object_type="planet",
        position={"x": 10.0}, geometry={"kind": "sphere", "params": {"radius": 2.0}},
        material={"color": "#0000ff"}, relationships={"orbits": "sun"},
    )
    assert obj.transform.position.x == 10.0
    assert obj.geometry.kind == "sphere"
    assert obj.geometry.params == {"radius": 2.0}
    assert obj.material.color == "#0000ff"
    assert obj.relationships == {"orbits": "sun"}


def test_update_object_transform(store):
    scene = store.create_scene()
    store.add_object(scene.id, "Cube", object_id="cube")
    updated = store.update_object(scene.id, "cube", position={"x": 5.0, "y": 1.0})
    assert updated.transform.position.x == 5.0
    assert updated.transform.position.y == 1.0


def test_update_object_label_and_relationships(store):
    scene = store.create_scene()
    store.add_object(scene.id, "Switch", object_id="switch01")
    store.add_object(scene.id, "Server", object_id="server01")
    updated = store.update_object(scene.id, "switch01", label="Core Switch", relationships={"connected_to": "server01"})
    assert updated.label == "Core Switch"
    assert updated.relationships == {"connected_to": "server01"}


def test_update_unknown_object_raises(store):
    scene = store.create_scene()
    with pytest.raises(SpatialError):
        store.update_object(scene.id, "ghost", label="x")


def test_remove_object_and_its_descendants(store):
    scene = store.create_scene()
    store.add_object(scene.id, "Sun", object_id="sun")
    store.add_object(scene.id, "Earth", object_id="earth", parent="sun")
    store.add_object(scene.id, "Moon", object_id="moon", parent="earth")
    store.remove_object(scene.id, "earth")
    assert "earth" not in scene.objects
    assert "moon" not in scene.objects  # descendant supprimé aussi
    assert "sun" in scene.objects
    assert "earth" not in scene.objects["sun"].children


def test_remove_root_object_updates_root_ids(store):
    scene = store.create_scene()
    store.add_object(scene.id, "Cube", object_id="cube")
    store.remove_object(scene.id, "cube")
    assert scene.root_ids == []


def test_remove_unknown_object_raises(store):
    scene = store.create_scene()
    with pytest.raises(SpatialError):
        store.remove_object(scene.id, "ghost")


def test_list_objects(store):
    scene = store.create_scene()
    store.add_object(scene.id, "A", object_id="a")
    store.add_object(scene.id, "B", object_id="b")
    ids = {o.id for o in store.list_objects(scene.id)}
    assert ids == {"a", "b"}


def test_describe_scene_returns_structured_summary_not_full_scene(store):
    scene = store.create_scene(label="Network")
    store.add_object(scene.id, "Switch", object_id="switch01", object_type="switch")
    store.add_object(scene.id, "Server", object_id="server01", object_type="server", parent="switch01")
    summary = store.describe_scene(scene.id)
    assert summary["scene_id"] == scene.id
    assert summary["object_count"] == 2
    assert summary["root_ids"] == ["switch01"]
    assert {o["id"] for o in summary["objects"]} == {"switch01", "server01"}
    # jamais le transform/geometry/material complet dans le résumé — juste
    # assez pour raisonner, pas un dump (consigne §12).
    assert "transform" not in summary["objects"][0]


def test_delete_scene(store):
    scene = store.create_scene()
    assert store.delete_scene(scene.id) is True
    assert store.get_scene(scene.id) is None
    assert store.delete_scene(scene.id) is False  # déjà supprimée


def test_mount_unmount_lifecycle(store):
    scene = store.create_scene()
    assert store.mounted_scene_id("session-1") is None
    store.mount("session-1", scene.id)
    assert store.mounted_scene_id("session-1") == scene.id
    store.unmount("session-1")
    assert store.mounted_scene_id("session-1") is None


def test_mount_unknown_scene_raises(store):
    with pytest.raises(SpatialError):
        store.mount("session-1", "does-not-exist")


def test_deleting_mounted_scene_clears_mount_state(store):
    scene = store.create_scene()
    store.mount("session-1", scene.id)
    store.delete_scene(scene.id)
    assert store.mounted_scene_id("session-1") is None


def test_two_sessions_can_mount_different_scenes_independently(store):
    scene_a = store.create_scene()
    scene_b = store.create_scene()
    store.mount("session-1", scene_a.id)
    store.mount("session-2", scene_b.id)
    assert store.mounted_scene_id("session-1") == scene_a.id
    assert store.mounted_scene_id("session-2") == scene_b.id
