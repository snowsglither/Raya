"""No Claim Without Evidence (consigne Phase 3 §9) : la SEULE source de
vérité pour verify_tool_result() est le ToolResult réel."""

from __future__ import annotations

from raya.cognition import (
    VerificationOutcome,
    combine_outcomes,
    has_evidence,
    observation_matches_expectation,
    verify_observation_against_intent,
    verify_tool_result,
)
from raya.contracts import ErrorInfo, ToolResult, ToolResultStatus


def test_success_status_verifies_success():
    result = ToolResult(tool_call_id="tc1", status=ToolResultStatus.SUCCESS, evidence={"x": 1})
    assert verify_tool_result(result) == VerificationOutcome.SUCCESS


def test_failure_status_verifies_failure():
    result = ToolResult(tool_call_id="tc1", status=ToolResultStatus.FAILURE, error=ErrorInfo(code="X", message="y"))
    assert verify_tool_result(result) == VerificationOutcome.FAILURE


def test_permission_denied_verifies_failure():
    result = ToolResult(tool_call_id="tc1", status=ToolResultStatus.PERMISSION_DENIED, error=ErrorInfo(code="X", message="y"))
    assert verify_tool_result(result) == VerificationOutcome.FAILURE


def test_timeout_verifies_unknown_never_assumed_success():
    result = ToolResult(tool_call_id="tc1", status=ToolResultStatus.TIMEOUT, error=ErrorInfo(code="X", message="y"))
    assert verify_tool_result(result) == VerificationOutcome.UNKNOWN


def test_cancelled_verifies_unknown_never_assumed_success():
    result = ToolResult(tool_call_id="tc1", status=ToolResultStatus.CANCELLED, error=ErrorInfo(code="X", message="y"))
    assert verify_tool_result(result) == VerificationOutcome.UNKNOWN


def test_has_evidence_true_for_success_with_evidence():
    result = ToolResult(tool_call_id="tc1", status=ToolResultStatus.SUCCESS, evidence={"path_exists": True})
    assert has_evidence(result) is True


def test_has_evidence_false_for_success_without_evidence():
    result = ToolResult(tool_call_id="tc1", status=ToolResultStatus.SUCCESS)
    assert has_evidence(result) is False


def test_has_evidence_false_for_failure_even_with_dict_present():
    result = ToolResult(tool_call_id="tc1", status=ToolResultStatus.FAILURE, error=ErrorInfo(code="X", message="y"))
    assert has_evidence(result) is False


def test_verify_never_reads_model_text_only_tool_result():
    """Vérification structurelle : verify_tool_result() ne prend qu'un
    ToolResult en paramètre — il n'existe littéralement aucun moyen de lui
    passer un texte de modèle à la place."""
    import inspect

    from raya.cognition.verification import verify_tool_result as fn

    sig = inspect.signature(fn)
    assert list(sig.parameters.keys()) == ["result"]


# --- Phase 7 : vérification post-action générique (jamais de succès sur "pas d'exception") ---

def test_observation_matches_exact():
    assert observation_matches_expectation("Calculatrice", "Calculatrice") is True


def test_observation_matches_case_insensitive_substring_either_direction():
    assert observation_matches_expectation("calculatrice", "Calculatrice - En cours d'exécution") is True
    assert observation_matches_expectation("youtube.com", "https://www.youtube.com/") is True


def test_observation_does_not_match_unrelated_value():
    assert observation_matches_expectation("Calculatrice", "Bloc-notes") is False


def test_observation_never_matches_on_empty_or_none():
    assert observation_matches_expectation("", "Calculatrice") is False
    assert observation_matches_expectation("Calculatrice", None) is False
    assert observation_matches_expectation(None, None) is False


def test_verify_observation_against_intent_success():
    assert verify_observation_against_intent("calculatrice", "Calculatrice") == VerificationOutcome.SUCCESS


def test_verify_observation_against_intent_failure_when_mismatched():
    assert verify_observation_against_intent("calculatrice", "Bloc-notes") == VerificationOutcome.FAILURE


def test_verify_observation_against_intent_unknown_when_no_observation():
    """Aucune observation obtenue -> UNKNOWN, jamais promu SUCCESS ni FAILURE
    (consigne Phase 7 §8 : 'Ne jamais transformer UNCERTAIN en SUCCESS')."""
    assert verify_observation_against_intent("calculatrice", None) == VerificationOutcome.UNKNOWN


def test_combine_outcomes_execution_failure_stays_failure_regardless_of_content():
    assert combine_outcomes(VerificationOutcome.FAILURE, VerificationOutcome.SUCCESS) == VerificationOutcome.FAILURE


def test_combine_outcomes_success_execution_but_content_mismatch_is_failure():
    """LE cas central de la consigne §8-9 : ToolResult.status=SUCCESS mais
    l'état observé ne correspond pas à l'intention -> jamais un succès global."""
    assert combine_outcomes(VerificationOutcome.SUCCESS, VerificationOutcome.FAILURE) == VerificationOutcome.FAILURE


def test_combine_outcomes_success_execution_but_content_unknown_stays_unknown():
    assert combine_outcomes(VerificationOutcome.SUCCESS, VerificationOutcome.UNKNOWN) == VerificationOutcome.UNKNOWN


def test_combine_outcomes_both_success_is_success():
    assert combine_outcomes(VerificationOutcome.SUCCESS, VerificationOutcome.SUCCESS) == VerificationOutcome.SUCCESS


def test_combine_outcomes_no_content_check_passes_base_through():
    assert combine_outcomes(VerificationOutcome.SUCCESS, None) == VerificationOutcome.SUCCESS
