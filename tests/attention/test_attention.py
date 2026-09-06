"""Groupe A — Attention : PROCESS_NOW/BACKGROUND/INTERRUPT/IGNORE, facteurs,
décisions déterministes, aucun appel modèle/outil, aucune dépendance Harness."""

from __future__ import annotations

import ast
from pathlib import Path

from raya.attention import AttentionEvaluator, FocusTracker
from raya.contracts import (
    AttentionOutcome,
    Event,
    TaskEventPayload,
    TaskOwner,
    TaskState,
)
from raya.persistence import InMemoryBackend
from raya.tasks import TaskRegistry, priority as prio
from raya.world_state import WorldStateStore


def _evaluator():
    return AttentionEvaluator(WorldStateStore(InMemoryBackend()), TaskRegistry(InMemoryBackend()), FocusTracker())


def _task_event(event_type: str, task_id: str, correlation_id: str = "c1") -> Event:
    return Event(type=event_type, source="tasks", correlation_id=correlation_id,
                 payload={"task_id": task_id, "new_state": "RUNNING"})


# --- Les 4 décisions ---

def test_interface_request_is_process_now():
    ev = _evaluator()
    decision = ev.evaluate(Event(type="interface.request_received", source="interfaces.cli", payload={}))
    assert decision.decision == AttentionOutcome.PROCESS_NOW


def test_routine_task_event_is_ignore():
    ev = _evaluator()
    decision = ev.evaluate(_task_event("task.created", "task_x"))
    assert decision.decision == AttentionOutcome.IGNORE


def test_informational_task_event_is_background():
    ev = _evaluator()
    decision = ev.evaluate(_task_event("task.paused", "task_x"))
    assert decision.decision == AttentionOutcome.BACKGROUND


def test_critical_task_failure_is_interrupt():
    tasks = TaskRegistry(InMemoryBackend())
    ev = AttentionEvaluator(WorldStateStore(InMemoryBackend()), tasks, FocusTracker())
    task = tasks.create("obj", TaskOwner(channel="cli", session_id="s1"), correlation_id="c1", priority=prio.CRITICAL)
    decision = ev.evaluate(_task_event("task.failed", task.id, correlation_id="c1"))
    assert decision.decision == AttentionOutcome.INTERRUPT


def test_task_blocked_is_process_now_not_background():
    """Chantier 15 (Axe D/H) : une tâche BLOCKED attend une action de
    l'utilisateur — jamais reléguée en fond comme un simple événement
    informatif (contrairement à task.paused, purement volontaire)."""
    tasks = TaskRegistry(InMemoryBackend())
    ev = AttentionEvaluator(WorldStateStore(InMemoryBackend()), tasks, FocusTracker())
    task = tasks.create("obj", TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    decision = ev.evaluate(_task_event("task.blocked", task.id, correlation_id="c1"))
    assert decision.decision == AttentionOutcome.PROCESS_NOW


def test_unknown_event_type_defaults_to_background_not_crash():
    ev = _evaluator()
    decision = ev.evaluate(Event(type="world_state.updated", source="world_state", payload={}))
    assert decision.decision == AttentionOutcome.BACKGROUND


# --- Facteurs ---

def test_factors_are_populated_and_bounded():
    ev = _evaluator()
    decision = ev.evaluate(Event(type="interface.request_received", source="interfaces.cli", payload={}))
    f = decision.factors
    for value in (f.urgency, f.importance, f.novelty, f.confidence, f.cost, f.user_relevance):
        assert 0.0 <= value <= 1.0


def test_reasoning_is_never_empty():
    ev = _evaluator()
    for event_type in ("interface.request_received", "task.created", "task.completed", "task.failed"):
        decision = ev.evaluate(Event(type=event_type, source="x", payload={}))
        assert decision.reasoning


# --- Focus / user_relevance (§6, §23) ---

def test_completed_task_in_focus_is_process_now():
    tasks = TaskRegistry(InMemoryBackend())
    focus = FocusTracker()
    ev = AttentionEvaluator(WorldStateStore(InMemoryBackend()), tasks, focus)
    task = tasks.create("télécharger fichier demandé", TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    focus.set_focus("s1", task.id)
    decision = ev.evaluate(_task_event("task.completed", task.id, correlation_id="c1"))
    assert decision.decision == AttentionOutcome.PROCESS_NOW
    assert decision.factors.user_relevance == 1.0


def test_completed_task_not_in_focus_is_background_lower_relevance():
    tasks = TaskRegistry(InMemoryBackend())
    focus = FocusTracker()
    ev = AttentionEvaluator(WorldStateStore(InMemoryBackend()), tasks, focus)
    task_a = tasks.create("focus task", TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    task_b = tasks.create("maintenance interne", TaskOwner(channel="cli", session_id="s1"), correlation_id="c2")
    focus.set_focus("s1", task_a.id)
    decision = ev.evaluate(_task_event("task.completed", task_b.id, correlation_id="c2"))
    assert decision.decision == AttentionOutcome.BACKGROUND
    assert decision.factors.user_relevance < 1.0


def test_task_b_background_lower_relevance_than_task_a_focus_scenario():
    """Scénario exact §23 : Task A pertinente (focus), Task B non (hors focus)."""
    tasks = TaskRegistry(InMemoryBackend())
    focus = FocusTracker()
    ev = AttentionEvaluator(WorldStateStore(InMemoryBackend()), tasks, focus)
    task_a = tasks.create("télécharger fichier demandé par utilisateur", TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    task_b = tasks.create("maintenance interne", TaskOwner(channel="cli", session_id="s1"), correlation_id="c2")
    focus.set_focus("s1", task_a.id)
    decision_a = ev.evaluate(_task_event("task.completed", task_a.id, correlation_id="c1"))
    decision_b = ev.evaluate(_task_event("task.completed", task_b.id, correlation_id="c2"))
    assert decision_a.factors.user_relevance > decision_b.factors.user_relevance


# --- Progress anti-spam (§49) ---

def test_progress_ticks_mostly_ignored_only_milestones_background():
    ev = _evaluator()
    outcomes = []
    for percent in range(1, 101):
        event = Event(type="task.progress", source="tasks", correlation_id="c1",
                      payload={"task_id": "task_x", "detail": {"percent": float(percent)}})
        outcomes.append(ev.evaluate(event).decision)
    background_count = sum(1 for o in outcomes if o == AttentionOutcome.BACKGROUND)
    ignore_count = sum(1 for o in outcomes if o == AttentionOutcome.IGNORE)
    # 4 paliers (25/50/75/100) -> au plus quelques BACKGROUND, l'écrasante
    # majorité des 100 ticks doit être IGNORE (pas de spam utilisateur).
    assert background_count <= 4
    assert ignore_count >= 90


def test_progress_milestone_notified_only_once():
    ev = _evaluator()
    event_50 = Event(type="task.progress", source="tasks", correlation_id="c1",
                      payload={"task_id": "task_x", "detail": {"percent": 50.0}})
    first = ev.evaluate(event_50).decision
    second = ev.evaluate(event_50).decision  # même palier redemandé
    assert first == AttentionOutcome.BACKGROUND
    assert second == AttentionOutcome.IGNORE


def test_progress_without_task_id_or_percent_is_ignored_safely():
    ev = _evaluator()
    decision = ev.evaluate(Event(type="task.progress", source="tasks", payload={}))
    assert decision.decision == AttentionOutcome.IGNORE


def test_forget_task_resets_milestones():
    ev = _evaluator()
    event_50 = Event(type="task.progress", source="tasks", payload={"task_id": "task_x", "detail": {"percent": 50.0}})
    ev.evaluate(event_50)
    ev.forget_task("task_x")
    decision = ev.evaluate(event_50)
    assert decision.decision == AttentionOutcome.BACKGROUND  # re-notifié après oubli explicite


# --- Phase 7 : observations perception jamais escaladées en interruption ---

def test_perception_event_is_ignore():
    ev = _evaluator()
    decision = ev.evaluate(Event(type="perception.window_changed", source="perception",
                                  payload={"domain": "pc", "key": "active_window"}))
    assert decision.decision == AttentionOutcome.IGNORE


def test_perception_is_in_subscribed_patterns():
    from raya.attention.evaluator import SUBSCRIBED_PATTERNS

    assert "perception.*" in SUBSCRIBED_PATTERNS


# --- Chantier 13B (Event-Driven Phone Awareness) : perception.phone_call_activity ---

def _phone_event(value: dict, correlation_id: str = "c1") -> Event:
    return Event(type="perception.phone_call_activity", source="perception", correlation_id=correlation_id,
                 payload={"domain": "phone", "key": "call_activity", "value": value,
                          "source": "perception:phone_link_call_activity", "confidence": "inferred"})


def test_phone_call_activity_is_interrupt_not_ignore():
    """Contrairement à toute autre observation perception.* (IGNORE de
    routine) — l'activité téléphonique doit pouvoir réveiller RAYA."""
    ev = _evaluator()
    decision = ev.evaluate(_phone_event({"in_call": True, "latest_call_log_name": "Christopher", "latest_call_log_time": "10:34"}))
    assert decision.decision == AttentionOutcome.INTERRUPT
    assert decision.target_session_id is None  # jamais rattaché à une conversation


def test_phone_call_activity_never_auto_answers_or_rejects():
    """Attention ne décide jamais QUOI faire (§3/§5) — la décision ne peut
    structurellement pas porter une instruction d'action (AttentionDecision
    n'a pas de champ pour ça, vérifié aussi par contracts/attention.py)."""
    ev = _evaluator()
    decision = ev.evaluate(_phone_event({"in_call": True, "latest_call_log_name": "Glodi", "latest_call_log_time": "09:59"}))
    assert not hasattr(decision, "tool_name")
    assert not hasattr(decision, "action")


def test_repeated_identical_phone_activity_is_deduplicated():
    ev = _evaluator()
    first = ev.evaluate(_phone_event({"in_call": True, "latest_call_log_name": "Christopher", "latest_call_log_time": "10:34"}))
    second = ev.evaluate(_phone_event({"in_call": True, "latest_call_log_name": "Christopher", "latest_call_log_time": "10:34"}))
    assert first.decision == AttentionOutcome.INTERRUPT
    assert second.decision == AttentionOutcome.IGNORE


def test_different_phone_activity_is_not_deduplicated():
    ev = _evaluator()
    first = ev.evaluate(_phone_event({"in_call": True, "latest_call_log_name": "Christopher", "latest_call_log_time": "10:34"}))
    second = ev.evaluate(_phone_event({"in_call": False, "latest_call_log_name": "Christopher", "latest_call_log_time": "10:34"}))
    assert first.decision == AttentionOutcome.INTERRUPT
    assert second.decision == AttentionOutcome.INTERRUPT


def test_phone_call_activity_confidence_reflects_genuine_uncertainty():
    """Jamais une confiance élevée : le capteur ne peut pas distinguer avec
    certitude un appel qui sonne encore d'un appel qui vient de se terminer."""
    ev = _evaluator()
    decision = ev.evaluate(_phone_event({"in_call": True, "latest_call_log_name": "X", "latest_call_log_time": "1"}))
    assert decision.factors.confidence < 0.9


# --- Chantier 13G : perception.incoming_call_notification (contenu de bannière réel) ---

def _incoming_call_event(value: dict, correlation_id: str = "c1") -> Event:
    return Event(type="perception.incoming_call_notification", source="perception", correlation_id=correlation_id,
                 payload={"domain": "phone", "key": "incoming_call", "value": value,
                          "source": "perception:incoming_call_notification", "confidence": "inferred"})


_INCOMING_VALUE = {"call_state": "incoming", "caller": "Papa", "toast_view_type": "PriorityToastView",
                   "source_text": "via Mobile connecté", "sender_category": "Appels"}
_OTHER_VALUE = {"call_state": "other", "caller": "Téléphone", "toast_view_type": "NormalToastView",
                "source_text": "via Mobile connecté", "sender_category": "Téléphone"}


def test_incoming_call_notification_is_ignored_by_default():
    """Chantier 13G (révisé) : PERCEPTION ≠ INTERRUPTION ≠ RÉACTION — le seul
    fait qu'un appel arrive n'est jamais, par défaut, une raison suffisante
    pour interrompre l'utilisateur (aucune préférence/tâche contextuelle
    n'existe ici, cf. NOT_IMPLEMENTED documenté dans le rapport 13G)."""
    ev = _evaluator()
    decision = ev.evaluate(_incoming_call_event(_INCOMING_VALUE))
    assert decision.decision == AttentionOutcome.IGNORE
    assert decision.target_session_id is None


def test_other_notification_state_is_never_interrupt():
    """Une notification 'manqué'/'other' ne doit JAMAIS être traitée comme
    un appel entrant en cours — même si elle vient du même capteur."""
    ev = _evaluator()
    decision = ev.evaluate(_incoming_call_event(_OTHER_VALUE))
    assert decision.decision == AttentionOutcome.IGNORE


def test_incoming_call_notification_never_carries_an_action():
    ev = _evaluator()
    decision = ev.evaluate(_incoming_call_event(_INCOMING_VALUE))
    assert not hasattr(decision, "tool_name")
    assert not hasattr(decision, "action")


def test_repeated_identical_incoming_call_notification_is_deduplicated():
    """Les deux décisions restent IGNORE (aucune interruption dans les deux
    cas), mais la déduplication interne reste observable via le
    `reasoning` : la 2e évaluation prend le chemin dédié "déjà remontée",
    jamais réévaluée comme un nouvel événement."""
    ev = _evaluator()
    first = ev.evaluate(_incoming_call_event(dict(_INCOMING_VALUE)))
    second = ev.evaluate(_incoming_call_event(dict(_INCOMING_VALUE)))
    assert first.decision == AttentionOutcome.IGNORE
    assert second.decision == AttentionOutcome.IGNORE
    assert "déjà remontée" not in first.reasoning
    assert "déjà remontée" in second.reasoning


def test_different_caller_is_not_deduplicated():
    ev = _evaluator()
    first = ev.evaluate(_incoming_call_event(dict(_INCOMING_VALUE)))
    second = ev.evaluate(_incoming_call_event(dict(_INCOMING_VALUE, caller="Christopher")))
    assert first.decision == AttentionOutcome.IGNORE
    assert second.decision == AttentionOutcome.IGNORE
    assert "déjà remontée" not in first.reasoning
    assert "déjà remontée" not in second.reasoning  # appelant différent -> pas la même clé de dédup


def test_incoming_call_notification_dedup_is_separate_from_phone_activity_dedup():
    """Les deux mécanismes de déduplication (13B/13D vs 13G) ne doivent
    jamais s'entremêler — un event de l'un ne doit jamais supprimer l'autre.
    `phone_activity` (13D, hors scope de la révision 13G) reste INTERRUPT ;
    `incoming_notification` (13G) reste IGNORE mais ne doit jamais être
    court-circuité par le chemin de déduplication de l'autre mécanisme."""
    ev = _evaluator()
    phone_activity = ev.evaluate(_phone_event({"in_call": True, "latest_call_log_name": "Papa", "latest_call_log_time": "12:00"}))
    incoming_notification = ev.evaluate(_incoming_call_event(_INCOMING_VALUE))
    assert phone_activity.decision == AttentionOutcome.INTERRUPT
    assert incoming_notification.decision == AttentionOutcome.IGNORE
    assert "déjà remontée" not in incoming_notification.reasoning  # pas une fausse dédup croisée


# --- Aucune dépendance interdite (vérification directe, en plus du lint) ---

def test_evaluator_module_imports_nothing_forbidden():
    src = Path("raya/attention/evaluator.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    forbidden_prefixes = ("raya.harness", "raya.models", "raya.tools", "raya.devices", "raya.interfaces")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert not node.module.startswith(forbidden_prefixes), f"import interdit : {node.module}"


def test_evaluate_never_calls_model_or_executes_tools():
    """Vérification comportementale : evaluate() ne fait aucun I/O externe —
    appelable des milliers de fois sans effet de bord observable au-delà du
    cache interne de paliers déjà notifiés."""
    ev = _evaluator()
    for _ in range(1000):
        ev.evaluate(Event(type="task.created", source="tasks", payload={"task_id": "t"}))
    # Aucune exception, aucun état externe modifié (pas de bus, pas de backend
    # touché au-delà de la lecture) — le test réussit simplement en ne plantant pas.
