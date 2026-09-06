"""OllamaLocalAdapter — même API Ollama, sans authentification, hôte local.
Réutilise la logique de payload/parsing d'OllamaCloudAdapter (même fournisseur,
même API réelle) sans dupliquer le code — seule la configuration diffère
(host, absence de clé, vérification de disponibilité active)."""

from __future__ import annotations

import requests

from raya.contracts import ModelCapability, ModelDescriptor
from .ollama_cloud import OllamaCloudAdapter

_DEFAULT_LOCAL_HOST = "http://localhost:11434"


class OllamaLocalAdapter(OllamaCloudAdapter):
    def __init__(
        self,
        model_id: str,
        capabilities: list[ModelCapability],
        *,
        host: str = _DEFAULT_LOCAL_HOST,
        context_limit: int = 32_000,
        timeout_s: float = 60.0,
    ) -> None:
        super().__init__(model_id, capabilities, api_key="", host=host, context_limit=context_limit, timeout_s=timeout_s)

    def descriptor(self) -> ModelDescriptor:
        d = super().descriptor()
        d.provider = "ollama_local"
        d.cost_per_1k_tokens = 0.0
        d.available = self.is_available()
        return d

    def is_available(self) -> bool:
        """Contrairement au cloud (disponibilité = clé présente), le local
        n'a pas de coût réseau/quota — une vérification active légère est
        justifiée : le démon Ollama local tourne-t-il vraiment ?"""
        try:
            response = requests.get(f"{self._host}/api/tags", timeout=1.5)
            return response.status_code == 200
        except requests.exceptions.RequestException:
            return False
