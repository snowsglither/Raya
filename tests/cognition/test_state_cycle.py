"""Anti-boucle par état (consigne Phase 4 §10) — complémentaire à LoopDetector
(échecs identiques répétés) : détecte un va-et-vient d'états qui RÉUSSISSENT
chacun individuellement (ex: navigue A->B->A->B) sans jamais faire progresser
l'objectif."""

from __future__ import annotations

from raya.cognition import detect_no_progress, detect_repeating_cycle


def test_no_cycle_on_progressing_history():
    assert detect_repeating_cycle(["A", "B", "C", "D"]) is False


def test_detects_simple_ab_cycle():
    assert detect_repeating_cycle(["X", "A", "B", "A", "B"]) is True


def test_does_not_flag_cycle_before_min_repeats():
    assert detect_repeating_cycle(["A", "B", "A"]) is False


def test_does_not_flag_short_history():
    assert detect_repeating_cycle(["A"]) is False
    assert detect_repeating_cycle([]) is False


def test_three_state_cycle_detected_with_wider_window():
    assert detect_repeating_cycle(["A", "B", "C", "A", "B", "C"], max_cycle_len=3) is True


def test_three_state_cycle_not_detected_with_default_window():
    # max_cycle_len=2 par défaut -> un cycle de longueur 3 n'est pas cherché
    assert detect_repeating_cycle(["A", "B", "C", "A", "B", "C"]) is False


def test_no_progress_flags_pure_stagnation():
    assert detect_no_progress(["A", "A", "A"]) is True


def test_no_progress_false_when_states_differ():
    assert detect_no_progress(["A", "B", "A"]) is False


def test_no_progress_false_before_min_repeats():
    assert detect_no_progress(["A", "A"]) is False


def test_single_repeated_state_is_not_a_cycle():
    """Un seul état répété (stagnation) n'est PAS un "cycle" au sens
    va-et-vient — c'est detect_no_progress qui le capture, pas
    detect_repeating_cycle (qui exige >=2 états distincts dans le motif)."""
    assert detect_repeating_cycle(["A", "A", "A", "A"]) is False
    assert detect_no_progress(["A", "A", "A", "A"]) is True
