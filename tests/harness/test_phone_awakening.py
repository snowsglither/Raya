"""Chantier 13B (Event-Driven Phone Awareness) — scénario CENTRAL : RAYA
idle (aucune conversation, aucune session, aucune tâche en focus) reçoit un
événement d'activité téléphonique -> EventBus -> Attention -> Harness
réveillé, SANS jamais déclencher phone.answer/phone.call.reject
automatiquement. `enable_perception=False` : le vrai capteur n'est pas
démarré, l'event est publié directement (déterministe, pas de dépendance à
un vrai Phone Link) — le pipeline EventBus/Attention/Harness lui-même reste
100% réel."""

from __future__ import annotations

import threading
import time

from raya.contracts import Event
from raya.persistence import SqliteBackend
from raya.runtime.bootstrap import bootstrap
from raya.runtime.config import load_config


def _handles(tmp_path):
    cfg = load_config()
    cfg.db_path = tmp_path / "phone_awakening.sqlite3"
    cfg.enable_perception = False  # capteur réel non démarré — event publié à la main
    return bootstrap(config=cfg, backend=SqliteBackend(cfg.db_path))


def _phone_activity_event(value: dict) -> Event:
    return Event(type="perception.phone_call_activity", source="perception", payload={
        "domain": "phone", "key": "call_activity", "value": value,
        "source": "perception:phone_link_call_activity", "confidence": "inferred",
    })


def _incoming_call_notification_event(value: dict) -> Event:
    return Event(type="perception.incoming_call_notification", source="perception", payload={
        "domain": "phone", "key": "incoming_call", "value": value,
        "source": "perception:incoming_call_notification", "confidence": "inferred",
    })


def test_idle_runtime_wakes_on_incoming_call_activity(tmp_path):
    """Le scénario principal du chantier : AUCUNE conversation n'a jamais eu
    lieu (aucun HarnessRequest envoyé) — l'event doit quand même réveiller
    Harness, prouvé par la publication de `harness.external_event_noticed`."""
    handles = _handles(tmp_path)
    noticed = threading.Event()
    captured = {}

    def _on_noticed(event: Event) -> None:
        captured["payload"] = event.payload
        noticed.set()

    handles.bus.subscribe("harness.external_event_noticed", _on_noticed, subscriber="test")
    try:
        handles.bus.publish(_phone_activity_event(
            {"in_call": True, "latest_call_log_name": "Christopher", "latest_call_log_time": "10:34"}
        ))
        assert noticed.wait(timeout=2.0), "Harness ne s'est jamais réveillé (idle -> incoming call)"
        assert captured["payload"]["reason"] == "phone_activity"
    finally:
        handles.shutdown()


def test_world_state_receives_the_phone_fact_via_generic_perception_ingestion(tmp_path):
    """Aucun code Harness dédié ne promeut ce fait — le mécanisme générique
    existant (WorldStateStore s'abonne à perception.*) suffit, inchangé."""
    handles = _handles(tmp_path)
    try:
        handles.bus.publish(_phone_activity_event(
            {"in_call": False, "latest_call_log_name": "Glodi", "latest_call_log_time": "09:59"}
        ))
        handles.bus.wait_idle(timeout_s=2.0)
        fact = handles.world_state.get_fact("phone", "call_activity")
        assert fact is not None
        assert fact.value["latest_call_log_name"] == "Glodi"
        from raya.contracts import Confidence

        assert fact.confidence == Confidence.INFERRED
    finally:
        handles.shutdown()


def test_no_phone_tool_is_ever_called_automatically_on_incoming_activity(tmp_path):
    """§5 : jamais d'auto-réponse — vérifié en observant qu'AUCUN ToolResult/
    exécution n'a lieu (rien dans last_tool_trace d'une session inexistante,
    et aucune Task n'est créée) suite à l'event, contrairement à une demande
    explicite de l'utilisateur qui, elle, passerait par handle_request()."""
    handles = _handles(tmp_path)
    try:
        handles.bus.publish(_phone_activity_event(
            {"in_call": True, "latest_call_log_name": "Inconnu", "latest_call_log_time": "11:00"}
        ))
        handles.bus.wait_idle(timeout_s=2.0)
        assert handles.tasks.list() == []  # aucune Task créée automatiquement
    finally:
        handles.shutdown()


def test_repeated_identical_activity_wakes_harness_only_once(tmp_path):
    handles = _handles(tmp_path)
    count = {"n": 0}

    def _on_noticed(event: Event) -> None:
        count["n"] += 1

    handles.bus.subscribe("harness.external_event_noticed", _on_noticed, subscriber="test")
    try:
        activity = {"in_call": True, "latest_call_log_name": "Christopher", "latest_call_log_time": "10:34"}
        handles.bus.publish(_phone_activity_event(activity))
        handles.bus.publish(_phone_activity_event(dict(activity)))  # même contenu, publié une 2e fois
        handles.bus.wait_idle(timeout_s=2.0)
        time.sleep(0.2)
        assert count["n"] == 1  # déduplication Attention — jamais un 2e réveil pour le même appel
    finally:
        handles.shutdown()


# --- Chantier 13G (révisé) : perception.incoming_call_notification ---
# PERCEPTION ≠ INTERRUPTION ≠ RÉACTION — un appel entrant réel (contenu
# structuré fiable de la bannière Windows) NE réveille PAS Harness par
# défaut : seule une justification contextuelle explicite (non construite
# ici, NOT_IMPLEMENTED, cf. rapport 13G) pourrait un jour le justifier.

def test_idle_runtime_does_not_wake_on_real_incoming_call_notification_by_default(tmp_path):
    """Contraste volontaire avec `test_idle_runtime_wakes_on_incoming_call_activity`
    (13B/13D, mécanisme séparé et hors scope de cette révision) : le signal
    FIABLE du Chantier 13G (contenu structuré de la bannière Windows réelle)
    est bien perçu (voir test World State ci-dessous) mais ne déclenche
    JAMAIS `harness.external_event_noticed` par défaut."""
    handles = _handles(tmp_path)
    noticed = threading.Event()
    handles.bus.subscribe("harness.external_event_noticed", lambda e: noticed.set(), subscriber="test")
    try:
        handles.bus.publish(_incoming_call_notification_event(
            {"call_state": "incoming", "caller": "Papa", "toast_view_type": "PriorityToastView",
             "source_text": "via Mobile connecté", "sender_category": "Appels"}
        ))
        handles.bus.wait_idle(timeout_s=2.0)
        assert not noticed.is_set(), "Harness ne doit JAMAIS se réveiller par défaut pour un simple appel entrant (13G révisé)"
    finally:
        handles.shutdown()


def test_other_call_state_notification_never_wakes_harness(tmp_path):
    """Une notification 'other' (ex: appel manqué après-coup) ne doit
    jamais réveiller Harness comme si un appel sonnait."""
    handles = _handles(tmp_path)
    noticed = threading.Event()
    handles.bus.subscribe("harness.external_event_noticed", lambda e: noticed.set(), subscriber="test")
    try:
        handles.bus.publish(_incoming_call_notification_event(
            {"call_state": "other", "caller": "Téléphone", "toast_view_type": "NormalToastView",
             "source_text": "via Mobile connecté", "sender_category": "Téléphone"}
        ))
        handles.bus.wait_idle(timeout_s=2.0)
        assert not noticed.is_set()
    finally:
        handles.shutdown()


def test_incoming_call_notification_promotes_correct_world_state_fact(tmp_path):
    handles = _handles(tmp_path)
    try:
        handles.bus.publish(_incoming_call_notification_event(
            {"call_state": "incoming", "caller": "Papa", "toast_view_type": "PriorityToastView",
             "source_text": "via Mobile connecté", "sender_category": "Appels"}
        ))
        handles.bus.wait_idle(timeout_s=2.0)
        fact = handles.world_state.get_fact("phone", "incoming_call")
        assert fact is not None
        assert fact.value["caller"] == "Papa"
        assert fact.value["call_state"] == "incoming"
        from raya.contracts import Confidence

        assert fact.confidence == Confidence.INFERRED
    finally:
        handles.shutdown()


def test_no_phone_tool_ever_called_from_incoming_call_notification(tmp_path):
    """§12/§13 : aucune action téléphonique (phone.answer/reject/end/call/
    sms.send) ne doit jamais être déclenchée par ce chemin — vérifié en
    observant qu'aucune Task n'est créée automatiquement."""
    handles = _handles(tmp_path)
    try:
        handles.bus.publish(_incoming_call_notification_event(
            {"call_state": "incoming", "caller": "Papa", "toast_view_type": "PriorityToastView",
             "source_text": "via Mobile connecté", "sender_category": "Appels"}
        ))
        handles.bus.wait_idle(timeout_s=2.0)
        assert handles.tasks.list() == []
    finally:
        handles.shutdown()
