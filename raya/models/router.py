"""Model Router — pipeline simple en 5 étapes (RAYA_V2_TECHNICAL_ARCHITECTURE.md §4.4,
clarifié par RAYA_V2_ARCHITECTURE_FINAL_REVIEW.md §7) :

    capability match (filtre dur) -> availability (filtre dur) ->
    qualité minimale (seuil, pas de score complexe) -> préférence local/cloud
    (cloud par défaut, Ollama Cloud-first) -> fallback (provider suivant si le
    premier échoue à l'exécution).

Les critères avancés (VRAM précis, coût monétaire fin, latence mesurée en
continu) sont V2+ futurs, pas requis Phase 3 — a fortiori pas Phase 0.
"""

from __future__ import annotations

from raya.contracts import ErrorInfo, FinishReason, ModelCapability, ModelDescriptor, ModelRequest, ModelResponse

from .providers.base import ProviderAdapter
from .registry import ModelRegistry


def _is_local(p: ProviderAdapter) -> bool:
    return "local" in p.descriptor().provider


def _ordered_candidates(registry: ModelRegistry, capability: ModelCapability, prefer_local: bool = False) -> list[ProviderAdapter]:
    """Étapes 1-4 partagées par `route()` et `describe_active()` — un seul
    endroit décide de l'ordre de sélection (jamais une deuxième logique de
    routage dupliquée ailleurs)."""
    candidates = registry.providers_for(capability)  # 1. capability match
    candidates = [p for p in candidates if p.is_available()]  # 2. availability
    # 4. préférence local/cloud (pas de scoring qualité complexe en Phase 0 —
    #    "qualité minimale" = tout provider enregistré est présumé qualifié).
    return sorted(candidates, key=lambda p: _is_local(p) != prefer_local)


def describe_active(registry: ModelRegistry, capability: ModelCapability, prefer_local: bool = False) -> ModelDescriptor | None:
    """Décrit le modèle qui SERAIT réellement sélectionné par `route()` pour
    cette capability, à cet instant — jamais un nom inventé : `None` si aucun
    provider n'est enregistré/disponible (stabilisation pré-Phase 7, exposé
    au Context Engine via `runtime_identity` pour que le modèle sache
    honnêtement quel runtime l'exécute plutôt que de l'halluciner)."""
    ordered = _ordered_candidates(registry, capability, prefer_local)
    return ordered[0].descriptor() if ordered else None


def route(registry: ModelRegistry, req: ModelRequest, prefer_local: bool = False) -> ModelResponse:
    ordered = _ordered_candidates(registry, req.capability, prefer_local)

    if not ordered:
        return ModelResponse(
            request_id=req.id,
            provider_used="none",
            content=[],
            finish_reason=FinishReason.ERROR,
            error=ErrorInfo(
                code="NO_MODEL_AVAILABLE",
                message=f"Aucun provider disponible pour capability={req.capability.value}",
                retryable=False,
            ),
        )

    last_response: ModelResponse | None = None
    for provider in ordered:  # 5. fallback si le précédent échoue
        response = provider.request(req)
        if response.finish_reason != FinishReason.ERROR:
            return response
        last_response = response  # gardé tel quel — jamais réécrit avec un content vide

    assert last_response is not None  # ordered est non-vide ici (candidates vérifié plus haut)
    return last_response
