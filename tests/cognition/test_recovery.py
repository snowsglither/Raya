"""Détection de boucle / recovery (consigne Phase 3 §12) : jamais de retry
aveugle indéfini, escalade après échecs identiques répétés."""

from __future__ import annotations

from raya.cognition import LoopDetector, RecoveryAction, VerificationOutcome


def test_success_returns_continue():
    detector = LoopDetector()
    action = detector.record("key1", "tool_x", {"a": 1}, VerificationOutcome.SUCCESS)
    assert action == RecoveryAction.CONTINUE


def test_first_failure_returns_replan_not_escalate():
    detector = LoopDetector(max_identical_failures=2)
    action = detector.record("key1", "tool_x", {"a": 1}, VerificationOutcome.FAILURE)
    assert action == RecoveryAction.REPLAN


def test_repeated_identical_failure_escalates():
    detector = LoopDetector(max_identical_failures=2)
    detector.record("key1", "tool_x", {"a": 1}, VerificationOutcome.FAILURE)
    action = detector.record("key1", "tool_x", {"a": 1}, VerificationOutcome.FAILURE)
    assert action == RecoveryAction.ESCALATE


def test_different_arguments_do_not_count_as_identical():
    detector = LoopDetector(max_identical_failures=2)
    detector.record("key1", "tool_x", {"a": 1}, VerificationOutcome.FAILURE)
    action = detector.record("key1", "tool_x", {"a": 2}, VerificationOutcome.FAILURE)
    assert action == RecoveryAction.REPLAN  # arguments différents -> pas une boucle


def test_success_resets_failure_history():
    detector = LoopDetector(max_identical_failures=2)
    detector.record("key1", "tool_x", {"a": 1}, VerificationOutcome.FAILURE)
    detector.record("key1", "tool_x", {"a": 1}, VerificationOutcome.SUCCESS)
    # après un succès, l'historique repart de zéro
    action = detector.record("key1", "tool_x", {"a": 1}, VerificationOutcome.FAILURE)
    assert action == RecoveryAction.REPLAN


def test_different_keys_are_independent():
    detector = LoopDetector(max_identical_failures=2)
    detector.record("session_a", "tool_x", {"a": 1}, VerificationOutcome.FAILURE)
    action = detector.record("session_b", "tool_x", {"a": 1}, VerificationOutcome.FAILURE)
    assert action == RecoveryAction.REPLAN  # une autre session/tâche n'hérite pas de l'historique


def test_forget_clears_history_for_key():
    detector = LoopDetector(max_identical_failures=2)
    detector.record("key1", "tool_x", {"a": 1}, VerificationOutcome.FAILURE)
    detector.forget("key1")
    action = detector.record("key1", "tool_x", {"a": 1}, VerificationOutcome.FAILURE)
    assert action == RecoveryAction.REPLAN


def test_unknown_outcome_counts_toward_escalation_too():
    detector = LoopDetector(max_identical_failures=2)
    detector.record("key1", "tool_x", {}, VerificationOutcome.UNKNOWN)
    action = detector.record("key1", "tool_x", {}, VerificationOutcome.UNKNOWN)
    assert action == RecoveryAction.ESCALATE
