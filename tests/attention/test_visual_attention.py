"""Visual events dans AttentionEvaluator — IGNORE/BACKGROUND, jamais INTERRUPT."""

from __future__ import annotations

from raya.attention import AttentionEvaluator, FocusTracker
from raya.contracts import AttentionOutcome, Event
from raya.persistence import InMemoryBackend
from raya.tasks import TaskRegistry
from raya.world_state import WorldStateStore


def _ev(event_type: str, payload: dict | None = None) -> Event:
    return Event(type=event_type, source="perception", payload=payload or {})


def _evaluator():
    return AttentionEvaluator(
        WorldStateStore(InMemoryBackend()),
        TaskRegistry(InMemoryBackend()),
        FocusTracker(),
    )


def test_screen_changed_is_ignored():
    ev = _evaluator()
    decision = ev.evaluate(_ev("perception.visual.screen_changed", {"change_detected": True}))
    assert decision.decision == AttentionOutcome.IGNORE


def test_camera_observation_is_ignored():
    ev = _evaluator()
    decision = ev.evaluate(_ev("perception.visual.camera_observation", {"change_detected": True}))
    assert decision.decision == AttentionOutcome.IGNORE


def test_screen_observation_is_background():
    """Une observation Vision enrichie (description textuelle) → BACKGROUND, pas INTERRUPT."""
    ev = _evaluator()
    decision = ev.evaluate(_ev("perception.visual.screen_observation", {"description": "a window"}))
    assert decision.decision == AttentionOutcome.BACKGROUND


def test_browser_observation_is_background():
    ev = _evaluator()
    decision = ev.evaluate(_ev("perception.visual.browser_observation", {"description": "YouTube"}))
    assert decision.decision == AttentionOutcome.BACKGROUND


def test_visual_events_never_interrupt():
    """Aucun event visuel ne doit jamais devenir INTERRUPT — règle architecturale."""
    ev = _evaluator()
    visual_events = [
        "perception.visual.screen_changed",
        "perception.visual.camera_observation",
        "perception.visual.screen_observation",
        "perception.visual.browser_observation",
    ]
    for event_type in visual_events:
        decision = ev.evaluate(_ev(event_type))
        assert decision.decision != AttentionOutcome.INTERRUPT, (
            f"{event_type} a produit INTERRUPT — violation de la règle PERCEPTION ≠ INTERRUPTION"
        )


def test_visual_events_never_process_now():
    """Les events visuels passifs ne déclenchent jamais PROCESS_NOW."""
    ev = _evaluator()
    for event_type in ["perception.visual.screen_changed", "perception.visual.camera_observation"]:
        decision = ev.evaluate(_ev(event_type))
        assert decision.decision != AttentionOutcome.PROCESS_NOW
