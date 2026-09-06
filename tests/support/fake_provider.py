"""FakeScriptedProvider — double de test pour le Model Layer UNIQUEMENT
(consigne Phase 3 §23 : "les tests doivent utiliser les vrais objets produits
par les composants upstream... le bug Phase 2 TaskEvent.payload doit servir
de leçon"). Ici, seule la RÉPONSE DU MODÈLE est scriptée (un vrai LLM n'est
pas déterministe, donc pas testable en boucle de contrôle) — Tools/Safety/
ExecutionRecord/Persistence en aval restent 100% réels, jamais mockés.

Implémente le VRAI `ProviderAdapter` (raya.models.providers.base) et retourne
de VRAIS objets `ModelResponse` (jamais un dict improvisé), exactement comme
un provider réel — c'est la garantie que le Harness ne voit aucune différence.
"""

from __future__ import annotations

from typing import Callable

from raya.contracts import ModelCapability, ModelDescriptor, ModelRequest, ModelResponse
from raya.models.providers.base import ProviderAdapter

ScriptEntry = ModelResponse | Callable[[ModelRequest], ModelResponse]


class FakeScriptedProvider(ProviderAdapter):
    def __init__(self, script: list[ScriptEntry], capabilities: list[ModelCapability] | None = None) -> None:
        self._script = list(script)
        self._calls: list[ModelRequest] = []
        self._capabilities = capabilities or [ModelCapability.REASONING]

    def descriptor(self) -> ModelDescriptor:
        return ModelDescriptor(
            id="fake-scripted", provider="fake", capabilities=self._capabilities,
            context_limit=32_000, supports_tool_calls=True, available=True,
        )

    def is_available(self) -> bool:
        return True

    @property
    def calls(self) -> list[ModelRequest]:
        return list(self._calls)

    def request(self, req: ModelRequest) -> ModelResponse:
        self._calls.append(req)
        if not self._script:
            raise AssertionError("FakeScriptedProvider: script épuisé — le Harness a appelé le modèle plus de fois que prévu")
        entry = self._script.pop(0)
        if callable(entry):
            response = entry(req)
        else:
            response = entry
        # request_id doit correspondre à CETTE requête, comme un vrai provider.
        response.request_id = req.id
        return response
