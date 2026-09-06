"""Model Registry (RAYA_V2_TECHNICAL_ARCHITECTURE.md §4.1)."""

from __future__ import annotations

from raya.contracts import ModelCapability

from .providers.base import ProviderAdapter


class ModelRegistry:
    def __init__(self) -> None:
        self._providers: list[ProviderAdapter] = []

    def register(self, provider: ProviderAdapter) -> None:
        self._providers.append(provider)

    def providers_for(self, capability: ModelCapability) -> list[ProviderAdapter]:
        return [p for p in self._providers if capability in p.descriptor().capabilities]

    def all(self) -> list[ProviderAdapter]:
        return list(self._providers)
