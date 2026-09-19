"""Capability Selection — RC2 Cognition primitive (pre-loop).

Selects the minimal set of capability tags needed for the current user turn
before the main agentic loop, reducing the tool surface exposed to the
reasoning model.

Pattern: same as objective_relation.py — single model call, structured JSON
output, deterministic safe fallback on any error.

AUTHORITY:
  Cognition PROPOSES via CapabilitySelectionProposal.
  Runtime (Harness) DECIDES: merges BASELINE, intersects with registered tags,
  applies fallback. Runtime never delegates this decision to a tool.

CALL POINT: pre-loop only, from Harness._select_capability_tags(),
before _discover_tool_schemas() and _run_agentic_loop().

SAFE FALLBACK: None — signals Harness to use all_capability_tags() (current
full-discovery behavior). Cost asymmetry: a wrong selection starves the model
of a needed tool; None is always recoverable by falling back to current state.
Never raise; never let unknown/hallucinated tags reach tool discovery.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from raya.contracts import ContentPart, Message, ModelCapability, ModelRequest
from raya.models import ModelRegistry
from raya.models import route as model_route

_VALID_CONFIDENCES = frozenset({"high", "medium", "low"})

_SYSTEM_PROMPT = (
    "You are a capability tag selector for an AI assistant. "
    "Your task: select the MINIMUM set of capability tags needed to handle "
    "the current user turn.\n\n"
    "Return ONLY a JSON object:\n"
    '  "selected_tags": list of strings, each must be from available_tags\n'
    '  "confidence": "high" | "medium" | "low"\n'
    '  "short_reason": string <= 120 chars\n\n'
    "PRIORITY ORDER (apply in this order — do not override a higher priority "
    "with a lower one):\n\n"
    "1. CURRENT USER TURN — the user's actual message determines the primary "
    "capability. A clear request (time, task list, browse a site) overrides "
    "all other signals.\n\n"
    "2. ACTIVE OBJECTIVE — when the current message is short, ambiguous, or "
    "a follow-up ('et chez Fnac ?', 'continue', 'fais ça', 'show the cheapest'), "
    "infer the domain from the active objective. A shopping/price-search "
    "objective means browser capabilities even if the current text is one word.\n\n"
    "3. LAST DOMAIN — use only as a tiebreaker when both user text and "
    "objective are ambiguous. CRITICAL: last_domain can be stale after a "
    "side-question. Example: user turns 1+3 pursue a browser-shopping "
    "objective; turn 2 asks the time (last_domain becomes 'system'). "
    "Turn 3 'Et chez Fnac?' must still select browser capabilities because "
    "the ACTIVE OBJECTIVE dominates a stale last_domain.\n\n"
    "4. RECENTLY COMPLETED OBJECTIVE — context for what the user was doing "
    "when there is no active objective.\n\n"
    "5. NEXT CHECKPOINT DOMAIN — prefer capabilities from this domain if "
    "no clearer signal exists.\n\n"
    "SEMANTIC GUIDANCE (for the model, not hardcoded routing):\n"
    "  web search / product price / navigate website → browser.read, browser.interact\n"
    "  screenshot, read page content → browser.read\n"
    "  click/type on webpage → browser.interact\n"
    "  visual grounding on screen/page → vision (alongside browser.read)\n"
    "  time / date → system.read\n"
    "  PC window/app state → pc.read\n"
    "  PC keyboard/mouse/click actions → pc.interact\n"
    "  open application → pc.launch\n"
    "  shell command → pc.shell\n"
    "  installed software → pc.software\n"
    "  task list / task status → tasks.read\n"
    "  pause / resume / cancel task → tasks.control\n"
    "  Telegram message → notify.telegram\n"
    "  phone call / SMS → phone.call or phone.sms\n"
    "  phone state / contacts → phone.read\n"
    "  Obsidian notes → obsidian.read\n"
    "  3D scene → spatial.* capabilities\n"
    "  memory / preferences → preferences.write\n"
    "  ambiguous follow-up with active browser objective → browser capabilities\n"
    "  completely ambiguous with no active objective → low confidence\n\n"
    "RULES:\n"
    "  - ONLY return tags from the available_tags list — never invent tags\n"
    "  - Prefer read capabilities for information requests, add interact/action "
    "capabilities only when the request requires an action\n"
    "  - system.read, tasks.control, tasks.read are baseline and always "
    "available regardless of your selection — no need to include them "
    "explicitly unless the request specifically needs them\n"
    "  - Use low confidence when you cannot reliably infer needed capabilities\n"
    "  - Low confidence signals the runtime to use full tool discovery"
)


@dataclass
class CapabilitySelectionRequest:
    user_text: str
    available_tags: list[str]
    objective_text: str | None = None
    last_domain: str | None = None
    next_checkpoint_domain: str | None = None
    recently_completed_objective: str | None = None


@dataclass
class CapabilitySelectionProposal:
    selected_tags: list[str]
    confidence: str
    short_reason: str = ""


_SAFE_FALLBACK = None


def select_capabilities(
    request: CapabilitySelectionRequest,
    model_registry: ModelRegistry,
    correlation_id: str,
) -> CapabilitySelectionProposal | None:
    """Single structured model call. Returns None on any error.
    Never raises — all exceptions result in safe fallback (None)."""
    parts: list[str] = [f"User message: {request.user_text!r}"]
    parts.append(f"Available capability tags: {request.available_tags!r}")

    if request.objective_text:
        parts.append(f"Active objective: {request.objective_text!r}")
    else:
        parts.append("Active objective: none")

    if request.last_domain:
        parts.append(f"Last active domain: {request.last_domain!r}")

    if request.recently_completed_objective:
        parts.append(f"Recently completed objective: {request.recently_completed_objective!r}")

    if request.next_checkpoint_domain:
        parts.append(f"Expected next checkpoint domain: {request.next_checkpoint_domain!r}")

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
    return _parse_proposal(text, request.available_tags)


def _parse_proposal(
    text: str, available_tags: list[str]
) -> CapabilitySelectionProposal | None:
    start = text.find("{")
    end = text.rfind("}") + 1
    if start == -1 or end <= start:
        return _SAFE_FALLBACK
    try:
        data = json.loads(text[start:end])
    except (json.JSONDecodeError, ValueError):
        return _SAFE_FALLBACK

    raw_tags = data.get("selected_tags")
    if not isinstance(raw_tags, list):
        return _SAFE_FALLBACK

    available_set = set(available_tags)
    # Unknown/hallucinated tags never escape the primitive
    filtered = [t for t in raw_tags if isinstance(t, str) and t in available_set]

    confidence = data.get("confidence", "")
    if confidence not in _VALID_CONFIDENCES:
        confidence = "low"

    short_reason = str(data.get("short_reason", ""))[:120]

    return CapabilitySelectionProposal(
        selected_tags=filtered,
        confidence=confidence,
        short_reason=short_reason,
    )
