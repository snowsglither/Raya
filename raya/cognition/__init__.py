"""Cognition (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.6, §9). Raisonnement,
incertitude, vérification, recovery/replanning — jamais d'exécution directe
d'outil/device, jamais un second Harness (vérifié par le lint architectural).
"""

from .capability_selection import (
    CapabilitySelectionProposal,
    CapabilitySelectionRequest,
    select_capabilities,
)
from .intent import Intent, derive_intent
from .objective_relation import (
    ActiveTaskContext,
    ObjectiveRelationProposal,
    ObjectiveRelationRequest,
    RecentlyCompletedContext,
    TraceSummary,
    classify_objective_relation,
)
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
    "ActiveTaskContext",
    "CapabilitySelectionProposal",
    "CapabilitySelectionRequest",
    "Intent",
    "LoopDetector",
    "ObjectiveRelationProposal",
    "ObjectiveRelationRequest",
    "RecoveryAction",
    "RecentlyCompletedContext",
    "TraceSummary",
    "VerificationOutcome",
    "build_plan",
    "classify_objective_relation",
    "select_capabilities",
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
