"""Détection de cycle d'état — consigne Phase 4 §10 : "même URL répétée,
même page state, mêmes actions répétées, absence de progression, retour à
un état précédent" (ex : A→B→A→B→A→B sans nouvelle information).

Complémentaire à `LoopDetector` (Phase 3, `recovery.py`) : celui-ci détecte
des ÉCHECS identiques répétés (même tool_name+arguments, VerificationOutcome
FAILURE/UNKNOWN) — il ne détecte PAS une séquence d'actions qui réussissent
chacune individuellement mais ne font jamais progresser l'état global (ex :
naviguer A puis B puis A puis B, chaque navigation "réussit"). Fonction pure,
sans I/O, sans LLM — un second petit outil déterministe dans la boîte à
outils Cognition, pas une deuxième boucle de décision."""

from __future__ import annotations


def detect_repeating_cycle(history: list[str], max_cycle_len: int = 2, min_repeats: int = 2) -> bool:
    """True si la fin de `history` est composée d'un motif d'au moins 2 états
    distincts (longueur 2 à `max_cycle_len`) répété au moins `min_repeats`
    fois consécutives (ex: [...,"A","B","A","B"] -> True pour cycle_len=2,
    repeats=2). La stagnation sur un SEUL état est traitée séparément par
    `detect_no_progress` (un cycle de longueur 1 n'est pas un "va-et-vient")."""
    n = len(history)
    for cycle_len in range(2, max_cycle_len + 1):
        needed = cycle_len * min_repeats
        if n < needed:
            continue
        window = history[-needed:]
        pattern = window[:cycle_len]
        if len(set(pattern)) < 2:
            continue
        if all(window[i:i + cycle_len] == pattern for i in range(0, needed, cycle_len)):
            return True
    return False


def detect_no_progress(history: list[str], min_repeats: int = 3) -> bool:
    """True si les `min_repeats` derniers éléments de `history` sont TOUS
    identiques (stagnation pure, pas un cycle A/B) — ex: rester bloqué sur
    la même URL après plusieurs tentatives d'action."""
    if len(history) < min_repeats:
        return False
    tail = history[-min_repeats:]
    return len(set(tail)) == 1
