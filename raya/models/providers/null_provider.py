"""NullProvider — provider par défaut Phase 0 (RAYA_V2_MIGRATION_PLAN.md §13).

Ne fait AUCUN appel réseau, ne simule AUCUNE intelligence. Retourne
explicitement finish_reason=error + ErrorInfo(code=NOT_IMPLEMENTED) — c'est le
comportement honnête demandé pour Phase 0 (section 8 de la consigne) plutôt
qu'une fausse réponse. Le vrai OllamaCloudAdapter arrive en Phase 3.
"""

from __future__ import annotations

from raya.contracts import (
    ContentPart,
    ErrorInfo,
    FinishReason,
    ModelCapability,
    ModelDescriptor,
    ModelRequest,
    ModelResponse,
)

from .base import ProviderAdapter


class NullProvider(ProviderAdapter):
    def descriptor(self) -> ModelDescriptor:
        return ModelDescriptor(
            id="null_provider",
            provider="none",
            capabilities=list(ModelCapability),
            context_limit=0,
            available=True,
        )

    def is_available(self) -> bool:
        return True

    def request(self, req: ModelRequest) -> ModelResponse:
        user_text = ""
        if req.messages:
            last = req.messages[-1]
            user_text = "".join(p.value for p in last.content if p.type == "text")
        return ModelResponse(
            request_id=req.id,
            provider_used="null_provider",
            content=[
                ContentPart(
                    type="text",
                    value=(
                        f"[Phase 0 — Model Layer non implémenté] reçu : {user_text!r}. "
                        "Aucun provider réel n'est encore branché (voir raya/models/providers/, Phase 3)."
                    ),
                )
            ],
            finish_reason=FinishReason.ERROR,
            error=ErrorInfo(
                code="NOT_IMPLEMENTED",
                message="Aucun ModelProvider réel n'est encore enregistré (Phase 0 stub).",
                retryable=False,
            ),
        )
