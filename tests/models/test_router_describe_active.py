"""describe_active() (stabilisation pré-Phase 7) — expose au Context Engine
le modèle RÉELLEMENT sélectionnable, jamais un nom inventé, et garanti
cohérent avec ce que route() choisirait vraiment (même fonction de tri,
tests/architecture vérifie qu'il n'existe pas de logique de routage dupliquée)."""

from __future__ import annotations

from raya.contracts import ContentPart, FinishReason, ModelCapability, ModelDescriptor, ModelRequest, ModelResponse
from raya.models import ModelRegistry, describe_active, route
from raya.models.providers.base import ProviderAdapter


class _FakeProvider(ProviderAdapter):
    def __init__(self, model_id: str, provider: str, capabilities, available: bool = True) -> None:
        self._model_id = model_id
        self._provider = provider
        self._capabilities = capabilities
        self._available = available

    def descriptor(self) -> ModelDescriptor:
        return ModelDescriptor(id=self._model_id, provider=self._provider, capabilities=self._capabilities, context_limit=8192)

    def is_available(self) -> bool:
        return self._available

    def request(self, req: ModelRequest) -> ModelResponse:
        return ModelResponse(request_id=req.id, provider_used=self._provider, content=[ContentPart(type="text", value="ok")],
                              finish_reason=FinishReason.COMPLETED)


def test_describe_active_returns_none_when_registry_empty():
    registry = ModelRegistry()
    assert describe_active(registry, ModelCapability.REASONING) is None


def test_describe_active_returns_none_when_no_capable_provider():
    registry = ModelRegistry()
    registry.register(_FakeProvider("m1", "ollama_cloud", [ModelCapability.CODING]))
    assert describe_active(registry, ModelCapability.REASONING) is None


def test_describe_active_ignores_unavailable_provider():
    registry = ModelRegistry()
    registry.register(_FakeProvider("m1", "ollama_cloud", [ModelCapability.REASONING], available=False))
    assert describe_active(registry, ModelCapability.REASONING) is None


def test_describe_active_returns_real_descriptor():
    registry = ModelRegistry()
    registry.register(_FakeProvider("deepseek-v4-flash:cloud", "ollama_cloud", [ModelCapability.REASONING]))
    descriptor = describe_active(registry, ModelCapability.REASONING)
    assert descriptor is not None
    assert descriptor.id == "deepseek-v4-flash:cloud"
    assert descriptor.provider == "ollama_cloud"


def test_describe_active_matches_what_route_actually_selects():
    """Non-régression anti-'deuxième logique de routage' : describe_active()
    doit toujours pointer vers le même provider que celui qui répond via route()."""
    registry = ModelRegistry()
    registry.register(_FakeProvider("cloud-model", "ollama_cloud", [ModelCapability.REASONING]))
    registry.register(_FakeProvider("local-model", "ollama_local", [ModelCapability.REASONING]))

    descriptor = describe_active(registry, ModelCapability.REASONING, prefer_local=False)
    req = ModelRequest(capability=ModelCapability.REASONING, messages=[], correlation_id="c1")
    response = route(registry, req, prefer_local=False)
    assert descriptor.provider in response.provider_used


def test_describe_active_respects_prefer_local():
    registry = ModelRegistry()
    registry.register(_FakeProvider("cloud-model", "ollama_cloud", [ModelCapability.REASONING]))
    registry.register(_FakeProvider("local-model", "ollama_local", [ModelCapability.REASONING]))
    descriptor = describe_active(registry, ModelCapability.REASONING, prefer_local=True)
    assert descriptor.provider == "ollama_local"
