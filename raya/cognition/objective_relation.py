"""Objective Relation Classification — RC1 Cognition primitive (post-loop).

Classifies the semantic relation between a new user turn and an active
conversational objective. Same pattern as planning.py: single model call,
structured JSON output, deterministic safe fallback on any error.

AUTHORITY:
  Cognition PROPOSES via ObjectiveRelationProposal.
  Runtime (Harness) DECIDES whether to apply the proposal.
  Runtime alone writes to TaskRegistry.

CALL POINT: post-loop only, from Harness._finalize_conversational_objective(),
after _run_agentic_loop() and _promote_observations_and_verify() have completed.

SAFE FALLBACK: always CONTINUE + low confidence. The cost asymmetry is clear —
wrong CONTINUE = polluted relevant_information (expires in 10 turns);
wrong REPLACE = destroyed user session. Never return REPLACE on failure.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from raya.contracts import ContentPart, Message, ModelCapability, ModelRequest
from raya.models import ModelRegistry
from raya.models import route as model_route

_VALID_RELATIONS = frozenset({"CONTINUE", "CORRECT", "REPLACE", "ASK"})
_VALID_CONFIDENCES = frozenset({"high", "medium", "low"})

_SYSTEM_PROMPT = (
    "You are a semantic objective classifier for an AI assistant. "
    "Given the current user message, the active conversational objective state, "
    "and a summary of this turn's tool activity — classify the RELATION between "
    "the user's new turn and the existing objective.\n\n"
    "Return ONLY a JSON object with these exact fields:\n"
    '  "relation": one of CONTINUE | CORRECT | REPLACE | ASK\n'
    '  "confidence": one of high | medium | low\n'
    '  "short_reason": string, <= 120 characters\n'
    '  "proposed_new_objective": string (REQUIRED if relation=REPLACE, <= 120 chars)\n'
    '  "ask_question": string (REQUIRED if relation=ASK, <= 200 chars, in user language)\n'
    '  "proposed_next_checkpoint": {"domain":str,"key":str,"expected_value":any} or null\n\n'
    "Definitions:\n"
    "  CONTINUE — same objective, new progress or evidence toward the same goal\n"
    "  CORRECT — same underlying intent, correcting a target, constraint, or detail\n"
    "  REPLACE — clearly new and different objective; the previous one should be abandoned\n"
    "  ASK — too ambiguous to classify reliably; clarification needed from user\n\n"
    "Critical rules:\n"
    "  Domain change alone does NOT mean REPLACE (user may compare prices across vendors)\n"
    "  REPLACE requires clear semantic divergence from the previous objective\n"
    "  When uncertain between CONTINUE and REPLACE, choose CONTINUE\n"
    "  Use low confidence when information is insufficient to decide reliably"
)


@dataclass
class ActiveTaskContext:
    objective: str
    relevant_information_summary: str
    last_domain_of_activity: str | None
    turns_since_created: int


@dataclass
class TraceSummary:
    domains_touched: list[str] = field(default_factory=list)
    tools_called: list[str] = field(default_factory=list)
    action_or_read: str = "INFO"


@dataclass
class RecentlyCompletedContext:
    objective: str
    result_summary: str
    turns_since_completed: int


@dataclass
class ObjectiveRelationRequest:
    user_text: str
    active_task: ActiveTaskContext | None = None
    trace_summary: TraceSummary | None = None
    recently_completed_objective: RecentlyCompletedContext | None = None


@dataclass
class ObjectiveRelationProposal:
    relation: str
    confidence: str
    short_reason: str = ""
    proposed_new_objective: str | None = None
    ask_question: str | None = None
    proposed_next_checkpoint: dict | None = None


# Safe fallback: preserve existing Task by returning CONTINUE with low confidence.
# Runtime applies the decision matrix (§1.3) and will escalate to ASK on low confidence.
_SAFE_FALLBACK = ObjectiveRelationProposal(
    relation="CONTINUE",
    confidence="low",
    short_reason="classification_failed",
)


def classify_objective_relation(
    request: ObjectiveRelationRequest,
    model_registry: ModelRegistry,
    correlation_id: str,
) -> ObjectiveRelationProposal:
    """Single structured model call. Returns _SAFE_FALLBACK on any error.
    Never raises — all exceptions result in safe fallback."""
    parts: list[str] = [f"User message: {request.user_text!r}"]

    if request.active_task:
        t = request.active_task
        parts.append(f"Active objective: {t.objective!r}")
        if t.relevant_information_summary:
            parts.append(f"Known items: {t.relevant_information_summary}")
        if t.last_domain_of_activity:
            parts.append(f"Last active domain: {t.last_domain_of_activity}")
        parts.append(f"Turns since created: {t.turns_since_created}")
    else:
        parts.append("Active objective: none")

    if request.trace_summary:
        ts = request.trace_summary
        parts.append(
            f"This turn: {ts.action_or_read}, "
            f"domains={ts.domains_touched}, "
            f"tools={ts.tools_called}"
        )

    if request.recently_completed_objective:
        rc = request.recently_completed_objective
        parts.append(
            f"Recently completed ({rc.turns_since_completed} turns ago): "
            f"{rc.objective!r} — result: {rc.result_summary}"
        )

    user_content = "\n".join(parts)
    model_request = ModelRequest(
        capability=ModelCapability.CLASSIFICATION,
        messages=[
            Message(role="system", content=[ContentPart(type="text", value=_SYSTEM_PROMPT)]),
            Message(role="user", content=[ContentPart(type="text", value=user_content)]),
        ],
        correlation_id=correlation_id,
    )
    try:
        response = model_route(model_registry, model_request)
    except Exception:
        return _SAFE_FALLBACK

    if response.finish_reason.value == "error":
        return _SAFE_FALLBACK

    text = "".join(p.value for p in response.content if p.type == "text").strip()
    return _parse_proposal(text)


def _parse_proposal(text: str) -> ObjectiveRelationProposal:
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end <= start:
        return _SAFE_FALLBACK
    try:
        data = json.loads(text[start:end])
    except (json.JSONDecodeError, ValueError):
        return _SAFE_FALLBACK

    relation = data.get("relation", "")
    if relation not in _VALID_RELATIONS:
        return _SAFE_FALLBACK

    confidence = data.get("confidence", "")
    if confidence not in _VALID_CONFIDENCES:
        confidence = "low"

    short_reason = str(data.get("short_reason", ""))[:120]
    proposed_new_objective = data.get("proposed_new_objective")
    ask_question = data.get("ask_question")
    proposed_next_checkpoint = data.get("proposed_next_checkpoint")

    # Validate required fields for specific relations
    if relation == "REPLACE" and not proposed_new_objective:
        return _SAFE_FALLBACK
    if relation == "ASK" and not ask_question:
        return _SAFE_FALLBACK

    # Validate next_checkpoint structure
    if proposed_next_checkpoint is not None:
        if not (
            isinstance(proposed_next_checkpoint, dict)
            and "domain" in proposed_next_checkpoint
            and "key" in proposed_next_checkpoint
            and "expected_value" in proposed_next_checkpoint
        ):
            proposed_next_checkpoint = None

    return ObjectiveRelationProposal(
        relation=relation,
        confidence=confidence,
        short_reason=short_reason,
        proposed_new_objective=proposed_new_objective,
        ask_question=ask_question,
        proposed_next_checkpoint=proposed_next_checkpoint,
    )
