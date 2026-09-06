"""Cognition (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.6, §9). Raisonnement,
incertitude, vérification, recovery/replanning — jamais d'exécution directe
d'outil/device, jamais un second Harness (vérifié par le lint architectural).
"""

from .intent import Intent, derive_intent
from .planning import build_plan, replan_step
from .reasoning import not_implemented_error
from .recovery import LoopDetector, RecoveryAction
from .state_cycle import detect_no_progress, detect_repeating_cycle
from .verification import (
    VerificationOutcome,
    combine_outcomes,
    has_evidence,
    observation_matches_expectation,
    verify_observation_against_intent,
    verify_tool_result,
)

__all__ = [
    "Intent",
    "LoopDetector",
    "RecoveryAction",
    "VerificationOutcome",
    "build_plan",
    "combine_outcomes",
    "derive_intent",
    "detect_no_progress",
    "detect_repeating_cycle",
    "has_evidence",
    "not_implemented_error",
    "observation_matches_expectation",
    "replan_step",
    "verify_observation_against_intent",
    "verify_tool_result",
]
