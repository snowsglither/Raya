"""ModelCapability, ModelDescriptor, ModelRequest, ModelResponse (RAYA_V2_CONTRACTS.md §12-13).

Contrat natif RAYA — explicitement PAS Anthropic-shaped
(RAYA_V2_TECHNICAL_ARCHITECTURE.md §4.5). `finish_reason` n'utilise QUE le
vocabulaire ci-dessous, jamais `stop_reason`/`type="tool_use"` façon SDK Anthropic.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ._base import new_id
from .errors import ErrorInfo


class ModelCapability(str, enum.Enum):
    REASONING = "reasoning"
    PLANNING = "planning"
    CODING = "coding"
    VISION = "vision"
    FAST_RESPONSE = "fast_response"
    CLASSIFICATION = "classification"
    SUMMARIZATION = "summarization"


@dataclass
class ModelDescriptor:
    id: str
    provider: str
    capabilities: list[ModelCapability]
    context_limit: int
    supports_streaming: bool = False
    supports_tool_calls: bool = False
    supports_vision: bool = False
    estimated_latency_ms: int = 0
    cost_per_1k_tokens: float = 0.0
    vram_requirement_mb: int | None = None
    available: bool = True


@dataclass
class ContentPart:
    type: str  # text | image_ref | file_ref
    value: str


@dataclass
class Message:
    role: str  # user | assistant | tool
    content: list[ContentPart]
    # Passe "Targeted Execution Repair" (bug trouvé en E2E réel, jamais
    # signalé) : avant cet ajout, un tour "l'assistant a demandé un appel
    # d'outil" était sérialisé comme un simple TEXTE placeholder
    # ("[demande d'appel d'outil]"), sans structure `tool_calls` ni lien
    # `tool_call_id` vers la réponse `role=tool` suivante — un vrai modèle,
    # conditionné sur cette forme non standard répétée plusieurs tours de
    # suite, a fini par recopier littéralement ce placeholder comme réponse
    # finale au lieu de répondre réellement (observé sur un vrai test
    # Calculatrice). Champs additifs (défaut None, aucune rupture des
    # constructions existantes) permettant au provider de sérialiser le
    # VRAI format function-calling attendu par un LLM.
    tool_calls: list[dict] | None = None  # assistant : [{"id","name","arguments"}, ...]
    tool_call_id: str | None = None       # tool : lie cette réponse à l'appel assistant correspondant


@dataclass
class ModelConstraints:
    require_local: bool = False
    max_latency_ms: int | None = None
    max_cost: float | None = None


@dataclass
class ModelRequest:
    capability: ModelCapability
    messages: list[Message]
    correlation_id: str
    available_tools: list[dict] | None = None
    context_budget_tokens: int = 4096
    constraints: ModelConstraints = field(default_factory=ModelConstraints)
    stream: bool = False
    id: str = field(default_factory=lambda: new_id("mreq"))


class FinishReason(str, enum.Enum):
    """Vocabulaire natif RAYA. Jamais end_turn/max_tokens/tool_use (Anthropic)."""

    COMPLETED = "completed"
    TRUNCATED = "truncated"
    TOOL_CALL_PENDING = "tool_call_pending"
    REFUSED = "refused"
    ERROR = "error"


@dataclass
class RequestedToolCall:
    tool_name: str
    arguments: dict


@dataclass
class ModelUsage:
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ModelResponse:
    request_id: str
    provider_used: str
    content: list[ContentPart]
    finish_reason: FinishReason
    tool_calls_requested: list[RequestedToolCall] | None = None
    usage: ModelUsage = field(default_factory=ModelUsage)
    latency_ms: int = 0
    error: ErrorInfo | None = None

    def __post_init__(self) -> None:
        if self.finish_reason == FinishReason.ERROR and self.error is None:
            raise ValueError("ModelResponse: finish_reason=error exige un error non-null")
