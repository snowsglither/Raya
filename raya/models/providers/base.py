"""ProviderAdapter — interface commune (RAYA_V2_TECHNICAL_ARCHITECTURE.md §4.1).

Tout ce qui est spécifique à un provider (Ollama Cloud/Local, futur) vit
STRICTEMENT dans providers/. Rien au-dessus de models/ ne voit jamais un
format spécifique à un provider (§4.5 — pas de contrat Anthropic-shaped).
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from raya.contracts import ModelDescriptor, ModelRequest, ModelResponse


class ProviderAdapter(ABC):
    @abstractmethod
    def descriptor(self) -> ModelDescriptor: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def request(self, req: ModelRequest) -> ModelResponse: ...
