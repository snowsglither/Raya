"""Chantier 2 Phase 2B — Router scoring + ModelRequirements (7 tests).

Chaque test a une raison d'existence liée à un comportement demandé,
un contrat architectural, une sécurité/régression, ou un E2E réel.
Tests E2E (7) ignorés si OLLAMA_API_KEY absent.
"""

from __future__ import annotations

import os

import pytest

from raya.contracts import (
    ContentPart,
    FinishReason,
    Message,
    ModelCapability,
    ModelDescriptor,
    ModelHealthState,
    ModelRequirements,
    ModelResponse,
    ModelStatus,
)
from raya.models import ModelRegistry, meets_requirements, route, select_model
from raya.models.providers.base import ProviderAdapter


# ---------------------------------------------------------------------------
# Helpers partagés
# ---------------------------------------------------------------------------


def _get_ollama_api_key() -> str | None:
    try:
        from raya.runtime.config import load_config
        return load_config().ollama_api_key
    except Exception:
        return os.environ.get("OLLAMA_API_KEY") or None


_OLLAMA_API_KEY = _get_ollama_api_key()
_E2E = pytest.mark.skipif(not _OLLAMA_API_KEY, reason="OLLAMA_API_KEY absent — test E2E ignoré")


class _FakeProvider(ProviderAdapter):
    def __init__(
        self,
        model_id: str,
        capabilities: list[ModelCapability],
        *,
        provider: str = "test",
        context_limit: int = 32_000,
        supports_tool_calls: bool = True,
        supports_vision: bool = False,
        estimated_latency_ms: int = 1000,
        cost_per_1k_tokens: float = 0.0,
    ) -> None:
        self._id = model_id
        self._caps = capabilities
        self._provider = provider
        self._ctx = context_limit
        self._tools = supports_tool_calls
        self._vision = supports_vision
        self._latency = estimated_latency_ms
        self._cost = cost_per_1k_tokens

    def descriptor(self) -> ModelDescriptor:
        return ModelDescriptor(
            id=self._id, provider=self._provider, capabilities=self._caps,
            context_limit=self._ctx, supports_tool_calls=self._tools,
            supports_vision=self._vision, estimated_latency_ms=self._latency,
            cost_per_1k_tokens=self._cost, available=True,
        )

    def is_available(self) -> bool:
        return True

    def request(self, req) -> ModelResponse:
        return ModelResponse(
            request_id=req.id, provider_used=self._id,
            content=[ContentPart(type="text", value="ok")],
            finish_reason=FinishReason.COMPLETED,
        )


def _req(capability=ModelCapability.REASONING, requirements=None):
    from raya.contracts import ModelRequest
    return ModelRequest(
        capability=capability, messages=[], correlation_id="test",
        context_budget_tokens=512, requirements=requirements,
    )


# ---------------------------------------------------------------------------
# Test 1 — BEHAVIOR : modèle UNAVAILABLE exclu par hard constraint
# ---------------------------------------------------------------------------
def test_unavailable_model_excluded_by_hard_constraint():
    registry = ModelRegistry()
    p = _FakeProvider("model-x", [ModelCapability.REASONING])
    registry.register(p)

    registry.update_health("model-x", failure=True)
    registry.update_health("model-x", failure=True)
    registry.update_health("model-x", failure=True)
    registry._health["model-x"].status = ModelStatus.UNAVAILABLE

    selection = select_model(registry, ModelCapability.REASONING)
    # NullProvider n'est pas enregistré ici, donc aucun provider éligible
    # (sauf si on en ajoute un valide)
    assert selection is None or selection.selected_model_id != "model-x"

    rejected_ids = [r.split(":")[0] for r in (selection.rejected if selection else [])]
    # Avec un seul provider marqué UNAVAILABLE, aucune sélection possible
    assert selection is None


# ---------------------------------------------------------------------------
# Test 2 — BEHAVIOR : modèle DEGRADED toujours éligible, score plus bas
# ---------------------------------------------------------------------------
def test_degraded_model_scores_lower_than_available():
    registry = ModelRegistry()
    p_degraded = _FakeProvider("degraded-model", [ModelCapability.REASONING])
    p_available = _FakeProvider("healthy-model", [ModelCapability.REASONING])
    registry.register(p_degraded)
    registry.register(p_available)

    # Marquer degraded-model comme DEGRADED
    for _ in range(3):
        registry.update_health("degraded-model", failure=True)
    assert registry.health_state("degraded-model").status == ModelStatus.DEGRADED

    selection = select_model(registry, ModelCapability.REASONING)
    assert selection is not None
    # healthy-model doit être sélectionné en premier (meilleur score)
    assert selection.selected_model_id == "healthy-model"
    # degraded-model doit être dans le fallback_chain mais pas premier
    assert "degraded-model" in selection.fallback_chain
    assert selection.fallback_chain[0] == "healthy-model"
    assert selection.score == pytest.approx(1.0, abs=0.01)  # healthy = score max


# ---------------------------------------------------------------------------
# Test 3 — BEHAVIOR : vision_required route vers modèle vision, rejette les autres
# ---------------------------------------------------------------------------
def test_vision_requirement_routes_to_vision_model():
    registry = ModelRegistry()
    p_no_vision = _FakeProvider("text-only", [ModelCapability.REASONING], supports_vision=False)
    p_vision = _FakeProvider("vision-model", [ModelCapability.REASONING, ModelCapability.VISION],
                              supports_vision=True)
    registry.register(p_no_vision)
    registry.register(p_vision)

    req = ModelRequirements(vision_required=True)
    selection = select_model(registry, ModelCapability.REASONING, requirements=req)

    assert selection is not None
    assert selection.selected_model_id == "vision-model"
    assert any("text-only" in r for r in selection.rejected)
    assert any("vision_required" in r for r in selection.rejected)


# ---------------------------------------------------------------------------
# Test 4 — BEHAVIOR : tool_calling_required rejette modèle sans tool support
# ---------------------------------------------------------------------------
def test_tool_calling_requirement_rejects_no_tools_model():
    registry = ModelRegistry()
    p_no_tools = _FakeProvider("no-tools", [ModelCapability.REASONING], supports_tool_calls=False)
    p_with_tools = _FakeProvider("with-tools", [ModelCapability.REASONING], supports_tool_calls=True)
    registry.register(p_no_tools)
    registry.register(p_with_tools)

    req = ModelRequirements(tool_calling_required=True)
    selection = select_model(registry, ModelCapability.REASONING, requirements=req)

    assert selection is not None
    assert selection.selected_model_id == "with-tools"
    assert any("no-tools" in r for r in selection.rejected)
    assert any("tool_calling_required" in r for r in selection.rejected)

    response = route(registry, _req(requirements=req))
    assert response.finish_reason == FinishReason.COMPLETED
    assert response.provider_used == "with-tools"


# ---------------------------------------------------------------------------
# Test 5 — ARCHITECTURE : ModelSelection explique pourquoi et les rejets
# ---------------------------------------------------------------------------
def test_model_selection_explains_reasons_and_rejected():
    registry = ModelRegistry()
    p_excluded = _FakeProvider("excluded", [ModelCapability.REASONING], supports_vision=False)
    p_selected = _FakeProvider("selected", [ModelCapability.REASONING], supports_vision=True)
    registry.register(p_excluded)
    registry.register(p_selected)

    req = ModelRequirements(vision_required=True)
    selection = select_model(registry, ModelCapability.REASONING, requirements=req)

    assert selection is not None
    assert len(selection.reasons) > 0, "reasons ne doit pas être vide"
    assert len(selection.rejected) > 0, "rejected ne doit pas être vide"
    assert any("excluded" in r for r in selection.rejected)
    assert selection.score > 0.0


# ---------------------------------------------------------------------------
# Test 6 — BEHAVIOR : Harness dérive mécaniquement les requirements
# ---------------------------------------------------------------------------
def test_harness_derives_requirements_mechanically(tmp_path):
    from tests.support.harness_factory import build_test_harness
    from tests.support.fake_provider import FakeScriptedProvider
    from raya.contracts import ModelRequest, ModelCapability, ContentPart, FinishReason, Message

    scripted_response = ModelResponse(
        request_id="", provider_used="fake",
        content=[ContentPart(type="text", value="ok")],
        finish_reason=FinishReason.COMPLETED,
    )
    handles, fake = build_test_harness(tmp_path, [scripted_response, scripted_response])

    # Test: tools present → tool_calling_required=True
    harness = handles.harness
    req_with_tools = ModelRequest(
        capability=ModelCapability.REASONING,
        messages=[Message(role="user", content=[ContentPart(type="text", value="test")])],
        correlation_id="c1",
        context_budget_tokens=512,
        available_tools=[{"name": "some_tool", "description": "test"}],
    )
    derived = harness._build_model_requirements(
        req_with_tools.messages, req_with_tools.available_tools, req_with_tools.context_budget_tokens,
    )
    assert derived.tool_calling_required is True
    assert derived.vision_required is False
    assert derived.min_context_tokens == 512

    # Test: image in messages → vision_required=True
    req_with_image = ModelRequest(
        capability=ModelCapability.REASONING,
        messages=[Message(role="user", content=[
            ContentPart(type="text", value="que vois-tu?"),
            ContentPart(type="image_ref", value="/tmp/test.png"),
        ])],
        correlation_id="c2",
        context_budget_tokens=256,
        available_tools=None,
    )
    derived_img = harness._build_model_requirements(
        req_with_image.messages, req_with_image.available_tools, req_with_image.context_budget_tokens,
    )
    assert derived_img.vision_required is True
    assert derived_img.tool_calling_required is False

    handles.shutdown()


# ---------------------------------------------------------------------------
# Test 7 — E2E : requirement vision → select_model sélectionne gemma4:31b
# ---------------------------------------------------------------------------
@_E2E
def test_e2e_vision_requirement_selects_vision_model():
    """Avec l'OLLAMA_API_KEY réel, le registry bootstrap a deepseek (no vision)
    et gemma4:31b (vision). Un requirement vision_required=True doit sélectionner
    gemma4:31b et rejeter deepseek."""
    from raya.runtime.config import load_config
    from raya.runtime.bootstrap import bootstrap
    from raya.persistence import SqliteBackend
    import tempfile, pathlib

    with tempfile.TemporaryDirectory() as tmpdir:
        cfg = load_config()
        cfg.db_path = pathlib.Path(tmpdir) / "test.sqlite3"
        cfg.enable_windows_device = False
        cfg.enable_browser_device = False
        cfg.enable_perception = False
        cfg.enable_phone_device = False
        cfg.enable_telegram = False

        handles = bootstrap(config=cfg, backend=SqliteBackend(cfg.db_path))
        try:
            registry = handles.models
            req = ModelRequirements(vision_required=True)
            selection = select_model(registry, ModelCapability.VISION, requirements=req)

            assert selection is not None, "Aucun modèle vision disponible — gemma4:31b manquant?"
            assert "gemma4:31b" in selection.selected_model_id, (
                f"Modèle sélectionné: {selection.selected_model_id} — attendu gemma4:31b"
            )
            # null_provider a VISION mais pas supports_vision → doit être rejeté
            assert any("null_provider" in r for r in selection.rejected), (
                f"null_provider absent de rejected: {selection.rejected}"
            )
        finally:
            handles.shutdown()
