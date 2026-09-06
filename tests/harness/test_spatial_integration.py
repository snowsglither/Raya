"""Creative/Spatial Agent — bout-en-bout via un vrai Harness (RAYA V2 Phase 8).
Même discipline que test_observation_promotion.py (Phase 7) : le modèle est
scripté, Tools/Safety/World State/Cognition restent 100% réels. Prouve
l'intégration complète : Harness -> Tool Registry -> SceneStore -> World
State -> ContextEngine -> ModelRequest, ET le caractère INTENT-DRIVEN
(aucune scène créée pour une conversation neutre).

Les scénarios à plusieurs étapes utilisent un `ScriptEntry` CALLABLE (lit le
VRAI résultat d'outil du tour précédent, ex: le `scene_id` généré) plutôt
qu'un ID pré-deviné — exactement comme le ferait un vrai modèle."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import (  # noqa: E402
    Channel,
    ContentPart,
    Event,
    FinishReason,
    HarnessRequest,
    HarnessStatus,
    InterfaceInput,
    ModelResponse,
    RequestedToolCall,
)


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


def _last_tool_output(req) -> dict:
    tool_messages = [m for m in req.messages if m.role == "tool"]
    payload = json.loads(tool_messages[-1].content[0].value)
    return payload["output"]


def _req(text: str, session_id: str = "s1") -> HarnessRequest:
    return HarnessRequest(channel=Channel.CLI, session_id=session_id, input=InterfaceInput(text=text))


def _system_text(call) -> str:
    system_messages = [m for m in call.messages if m.role == "system"]
    return "".join(p.value for p in system_messages[0].content if p.type == "text") if system_messages else ""


# --- Intent-driven : aucune scène pour une conversation neutre ---

def test_greeting_never_creates_a_scene_or_touches_world_state(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("Salut ! Comment puis-je t'aider ?")])
    try:
        state = handles.harness.handle_request(_req("Salut"))
        assert state.status == HarnessStatus.COMPLETED
        assert handles.world_state.get_fact("spatial", "active_scene") is None
        assert handles.scene_store.list_scenes() == []
    finally:
        handles.shutdown()


def test_unrelated_question_never_invokes_spatial_tools(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("Il fait environ 18°C aujourd'hui.")])
    try:
        handles.harness.handle_request(_req("Quelle est la température aujourd'hui ?"))
        trace = handles.harness.last_tool_trace("s1")
        assert trace == []
        assert handles.scene_store.list_scenes() == []
    finally:
        handles.shutdown()


# --- Le modèle CRÉE une scène réelle quand la demande le justifie ---

def test_model_driven_scene_creation_end_to_end(tmp_path):
    script = [
        _tool_call_response("scene.create", {"label": "Solar System"}),
        _text_response("Scène créée."),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        state = handles.harness.handle_request(_req("Crée-moi une scène du système solaire"))
        assert state.status == HarnessStatus.COMPLETED
        scenes = handles.scene_store.list_scenes()
        assert len(scenes) == 1
        assert scenes[0].label == "Solar System"
    finally:
        handles.shutdown()


def test_scene_creation_promotes_active_scene_into_world_state(tmp_path):
    script = [_tool_call_response("scene.create", {"label": "X"}), _text_response("fait.")]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        handles.harness.handle_request(_req("crée une scène"))
        fact = handles.world_state.get_fact("spatial", "active_scene")
        assert fact is not None
        assert fact.source == "tool:scene.create"
    finally:
        handles.shutdown()


def test_add_object_promotes_object_count_into_world_state(tmp_path):
    """Scénario réaliste à 2 outils : le modèle voit le VRAI scene_id généré
    par scene.create avant d'appeler scene.add_object (jamais deviné)."""
    def step2(req):
        scene_id = _last_tool_output(req)["scene_id"]
        return _tool_call_response("scene.add_object", {"scene_id": scene_id, "label": "Sun"})

    script = [_tool_call_response("scene.create", {"label": "Test"}), step2, _text_response("Soleil ajouté.")]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        handles.harness.handle_request(_req("crée une scène et ajoute un soleil"))
        fact = handles.world_state.get_fact("spatial", "scene_object_count")
        assert fact is not None
        assert fact.value == 1
    finally:
        handles.shutdown()


def test_relationship_and_hierarchy_via_real_multi_step_tool_calls(tmp_path):
    """Consigne §19/§36 : Terre parent->Soleil, via de VRAIS appels d'outils
    chaînés (jamais une scène pré-fabriquée hors du pipeline modèle)."""
    def step_sun(req):
        return _tool_call_response("scene.create", {"label": "Solar System"})

    def step_earth(req):
        scene_id = _last_tool_output(req)["scene_id"]
        return _tool_call_response("scene.add_object", {"scene_id": scene_id, "label": "Sun", "object_id": "sun"})

    def step_earth2(req):
        scene_id = _last_tool_output(req)  # {"object_id": "sun", "label": "Sun"} -> pas de scene_id ici
        # Le scene_id vient du TOUT premier résultat (scene.create) — on le
        # relit dans l'historique complet des messages tool.
        tool_messages = [m for m in req.messages if m.role == "tool"]
        first_payload = json.loads(tool_messages[0].content[0].value)
        scene_id = first_payload["output"]["scene_id"]
        return _tool_call_response("scene.add_object", {"scene_id": scene_id, "label": "Earth", "object_id": "earth", "parent": "sun"})

    script = [step_sun, step_earth, step_earth2, _text_response("Système solaire créé avec la Terre en orbite.")]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=6)
    try:
        state = handles.harness.handle_request(_req("crée le soleil et la terre qui l'orbite"))
        assert state.status == HarnessStatus.COMPLETED
        scene = handles.scene_store.list_scenes()[0]
        real_scene = handles.scene_store.get_scene(scene.id)
        assert real_scene.objects["earth"].parent == "sun"
        assert "earth" in real_scene.objects["sun"].children
    finally:
        handles.shutdown()


# --- Vérification post-action réutilise le mécanisme Phase 7 (pas de système parallèle) ---

def test_add_object_to_unknown_scene_fails_never_promoted(tmp_path):
    script = [
        _tool_call_response("scene.add_object", {"scene_id": "ghost", "label": "X"}),
        _text_response("Je n'ai pas pu ajouter cet objet."),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        handles.harness.handle_request(_req("ajoute un objet à une scène inexistante"))
        assert handles.world_state.get_fact("spatial", "scene_object_count") is None
    finally:
        handles.shutdown()


# --- Rendu : scene.render pilote l'UI via le même mécanisme que Phase 6 ---

def test_scene_render_triggers_ui_view_requested_real_event(tmp_path):
    from raya.interfaces.ui import UIEventBridge

    def step2(req):
        scene_id = _last_tool_output(req)["scene_id"]
        return _tool_call_response("scene.render", {"scene_id": scene_id})

    script = [_tool_call_response("scene.create", {"label": "Test"}), step2, _text_response("Voilà la scène.")]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        bridge = UIEventBridge(handles.bus, session_id="s1")
        received = []
        bridge.add_listener(received.append)
        try:
            handles.harness.handle_request(_req("montre-moi cette scène"))
            handles.bus.wait_idle(timeout_s=1.0)
            assert any(n["type"] == "ui.view_requested" and n["payload"]["view"] == "spatial" and n["payload"]["action"] == "show"
                       for n in received)
            scene_id = handles.scene_store.list_scenes()[0].id
            assert handles.harness.mounted_scene_id("s1") == scene_id
        finally:
            bridge.close()
    finally:
        handles.shutdown()


def test_scene_close_unmounts_and_hides_the_view(tmp_path):
    def step2(req):
        scene_id = _last_tool_output(req)["scene_id"]
        return _tool_call_response("scene.render", {"scene_id": scene_id})

    def step3(req):
        scene_id = handles.scene_store.list_scenes()[0].id
        return _tool_call_response("scene.close", {"scene_id": scene_id})

    script = [_tool_call_response("scene.create", {"label": "Test"}), step2, step3, _text_response("Fermé.")]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=6)
    try:
        handles.harness.handle_request(_req("montre puis ferme la scène"))
        assert handles.harness.mounted_scene_id("s1") is None
    finally:
        handles.shutdown()


# --- Context integration : l'observation spatiale atteint le vrai ModelRequest ---

def test_spatial_observation_reaches_the_real_model_request(tmp_path):
    script = [
        _tool_call_response("scene.create", {"label": "Solar System"}), _text_response("Scène créée."),
        _text_response("Oui, une scène existe."),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        handles.harness.handle_request(_req("crée une scène"))
        handles.harness.handle_request(_req("ai-je une scène active ?"))
        text = _system_text(fake.calls[-1])
        assert "active_scene" in text
    finally:
        handles.shutdown()


# --- STOP reste fonctionnel avec le Creative/Spatial Agent en place ---

def test_stop_still_works_before_spatial_tool_call(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_tool_call_response("scene.create", {"label": "X"})])
    try:
        handles.bus.publish(Event(type="interface.stop_requested", source="test", payload={}))
        handles.bus.wait_idle(timeout_s=1.0)
        state = handles.harness.handle_request(_req("crée une scène"))
        assert state.status == HarnessStatus.FAILED
        assert state.error.code == "STOP_ACTIVE"
        assert handles.scene_store.list_scenes() == []
    finally:
        handles.shutdown()


# --- Sécurité : découverte générique, comme n'importe quel autre outil ---

def test_spatial_tools_are_discovered_like_any_other_tool(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("ok")])
    try:
        handles.harness.handle_request(_req("salut"))
        available = fake.calls[0].available_tools or []
        names = {t["name"] for t in available}
        assert "scene.create" in names  # même pipeline générique que tout autre outil
    finally:
        handles.shutdown()
