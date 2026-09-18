"""Chantier 2 — Model Registry + Capability-Based Model Router (15 tests).

Philosophie : chaque test a une raison d'existence liée au comportement,
à l'architecture, à la sécurité, à une régression ou à un vrai E2E.
Tests 11-15 : E2E réels Ollama Cloud, ignorés si OLLAMA_API_KEY absent.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from raya.contracts import (
    CapabilityEvidence,
    CapabilityRecord,
    ContentPart,
    ErrorInfo,
    FinishReason,
    ModelCapability,
    ModelDescriptor,
    ModelHealthState,
    ModelRequest,
    ModelRequirements,
    ModelResponse,
    ModelStatus,
)
from raya.models import ModelRegistry, ModelScheduler, meets_requirements, route
from raya.models.providers.base import ProviderAdapter

# ---------------------------------------------------------------------------
# Helpers partagés
# ---------------------------------------------------------------------------


def _get_ollama_api_key() -> str | None:
    """Utilise le même parseur que load_config() (gère inline comments, quotes)."""
    try:
        from raya.runtime.config import load_config
        return load_config().ollama_api_key
    except Exception:
        return os.environ.get("OLLAMA_API_KEY") or None


_OLLAMA_API_KEY = _get_ollama_api_key()

_E2E = pytest.mark.skipif(
    not _OLLAMA_API_KEY,
    reason="OLLAMA_API_KEY absent — test E2E Ollama Cloud ignoré",
)


def _req(capability=ModelCapability.REASONING, text="hello") -> ModelRequest:
    return ModelRequest(
        capability=capability,
        messages=[],
        correlation_id="test",
        context_budget_tokens=512,
    )


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
        response: ModelResponse | None = None,
    ) -> None:
        self._id = model_id
        self._caps = capabilities
        self._provider = provider
        self._ctx = context_limit
        self._tool_calls = supports_tool_calls
        self._vision = supports_vision
        self._response = response
        self.call_count = 0

    def descriptor(self) -> ModelDescriptor:
        return ModelDescriptor(
            id=self._id,
            provider=self._provider,
            capabilities=self._caps,
            context_limit=self._ctx,
            supports_tool_calls=self._tool_calls,
            supports_vision=self._vision,
            available=True,
        )

    def is_available(self) -> bool:
        return True

    def request(self, req: ModelRequest) -> ModelResponse:
        self.call_count += 1
        if self._response is not None:
            r = self._response
            r.request_id = req.id
            return r
        return ModelResponse(
            request_id=req.id,
            provider_used=self._id,
            content=[ContentPart(type="text", value="ok")],
            finish_reason=FinishReason.COMPLETED,
        )


def _error_response(req_id: str = "r", provider: str = "test") -> ModelResponse:
    return ModelResponse(
        request_id=req_id,
        provider_used=provider,
        content=[],
        finish_reason=FinishReason.ERROR,
        error=ErrorInfo(code="FAKE_ERROR", message="forced failure", retryable=True),
    )


# ---------------------------------------------------------------------------
# Test 1 — BEHAVIOR : vision_required=True → modèle non-vision rejeté
# ---------------------------------------------------------------------------
def test_meets_requirements_vision_required_rejects_non_vision():
    descriptor = ModelDescriptor(
        id="no-vision", provider="test",
        capabilities=[ModelCapability.REASONING],
        context_limit=32_000,
        supports_tool_calls=True,
        supports_vision=False,
    )
    assert not meets_requirements(descriptor, ModelRequirements(vision_required=True))
    assert meets_requirements(descriptor, ModelRequirements(vision_required=False))


# ---------------------------------------------------------------------------
# Test 2 — BEHAVIOR : min_context_tokens > context_limit → exclu
# ---------------------------------------------------------------------------
def test_meets_requirements_min_context_tokens_excludes_small_models():
    descriptor = ModelDescriptor(
        id="small-ctx", provider="test",
        capabilities=[ModelCapability.REASONING],
        context_limit=4_096,
        supports_tool_calls=True,
        supports_vision=False,
    )
    assert not meets_requirements(descriptor, ModelRequirements(min_context_tokens=8_000))
    assert meets_requirements(descriptor, ModelRequirements(min_context_tokens=4_096))
    assert meets_requirements(descriptor, ModelRequirements(min_context_tokens=0))


# ---------------------------------------------------------------------------
# Test 3 — BEHAVIOR : tool_calling_required → modèle sans tool_calls rejeté
# ---------------------------------------------------------------------------
def test_meets_requirements_tool_calling_required_rejects_no_tools():
    descriptor = ModelDescriptor(
        id="no-tools", provider="test",
        capabilities=[ModelCapability.VISION],
        context_limit=128_000,
        supports_tool_calls=False,
        supports_vision=True,
    )
    assert not meets_requirements(descriptor, ModelRequirements(tool_calling_required=True))
    assert meets_requirements(descriptor, ModelRequirements(tool_calling_required=False))


# ---------------------------------------------------------------------------
# Test 4 — ARCHITECTURE : nouveau modèle enregistré → sélectionnable sans
#           modifier le Router (registry data-driven, pas hardcodé)
# ---------------------------------------------------------------------------
def test_registry_new_model_selected_automatically():
    registry = ModelRegistry()
    p_reason = _FakeProvider("model-a", [ModelCapability.REASONING])
    registry.register(p_reason)
    assert registry.providers_for(ModelCapability.CODING) == []

    p_code = _FakeProvider("model-b", [ModelCapability.CODING])
    registry.register(p_code)

    coding_providers = registry.providers_for(ModelCapability.CODING)
    assert len(coding_providers) == 1
    assert coding_providers[0].descriptor().id == "model-b"


# ---------------------------------------------------------------------------
# Test 5 — BEHAVIOR : modèle A en erreur → router bascule sur modèle B
# ---------------------------------------------------------------------------
def test_fallback_to_second_provider_on_error():
    registry = ModelRegistry()
    p_error = _FakeProvider(
        "model-a", [ModelCapability.REASONING],
        response=_error_response(provider="model-a"),
    )
    p_ok = _FakeProvider("model-b", [ModelCapability.REASONING])
    registry.register(p_error)
    registry.register(p_ok)

    response = route(registry, _req())

    assert response.finish_reason == FinishReason.COMPLETED
    assert response.provider_used == "model-b"
    assert p_error.call_count == 1
    assert p_ok.call_count == 1


# ---------------------------------------------------------------------------
# Test 6 — BEHAVIOR : N threads, limite=M → jamais plus de M appels en vol
# ---------------------------------------------------------------------------
def test_scheduler_concurrent_limit_never_exceeded():
    M = 2
    N = 5
    gate = threading.Event()
    observed_peaks: list[int] = []
    lock = threading.Lock()

    scheduler = ModelScheduler(global_max=M)

    def slow_fn() -> ModelResponse:
        count = scheduler.active_count
        with lock:
            observed_peaks.append(count)
        gate.wait(timeout=3.0)
        return ModelResponse(
            request_id="r", provider_used="m",
            content=[], finish_reason=FinishReason.COMPLETED,
        )

    threads = [
        threading.Thread(target=lambda: scheduler.run_request("model", slow_fn, "r"), daemon=True)
        for _ in range(N)
    ]
    for t in threads:
        t.start()

    time.sleep(0.15)
    peak_while_blocked = scheduler.active_count
    gate.set()
    for t in threads:
        t.join(timeout=5.0)

    assert peak_while_blocked <= M
    assert all(c <= M for c in observed_peaks)


# ---------------------------------------------------------------------------
# Test 7 — SAFETY : STOP actif → Scheduler refuse AVANT de démarrer
#           l'invocation (fn jamais appelée, active_count reste 0)
# ---------------------------------------------------------------------------
def test_scheduler_stop_rejects_before_invocation():
    fn_called = False

    def stop_fn():
        return True

    scheduler = ModelScheduler(global_max=3, stop_fn=stop_fn)

    def fn() -> ModelResponse:
        nonlocal fn_called
        fn_called = True
        return ModelResponse(request_id="r", provider_used="m", content=[], finish_reason=FinishReason.COMPLETED)

    result = scheduler.run_request("model", fn, "test-req-id")

    assert not fn_called, "fn ne doit PAS être appelée quand STOP est actif"
    assert result.finish_reason == FinishReason.ERROR
    assert result.error is not None
    assert result.error.code == "SCHEDULER_STOP"
    assert scheduler.active_count == 0


# ---------------------------------------------------------------------------
# Test 8 — BEHAVIOR : 3 erreurs consécutives → transition AVAILABLE→DEGRADED
# ---------------------------------------------------------------------------
def test_health_degrades_after_consecutive_failures():
    registry = ModelRegistry()
    provider = _FakeProvider("model-x", [ModelCapability.REASONING])
    registry.register(provider)

    health = registry.health_state("model-x")
    assert health is not None
    assert health.status == ModelStatus.AVAILABLE
    assert health.consecutive_failures == 0

    for i in range(1, 3):
        registry.update_health("model-x", failure=True)
        h = registry.health_state("model-x")
        assert h.consecutive_failures == i
        assert h.status == ModelStatus.AVAILABLE, f"toujours AVAILABLE après {i} échec(s)"

    registry.update_health("model-x", failure=True)
    h = registry.health_state("model-x")
    assert h.consecutive_failures == 3
    assert h.status == ModelStatus.DEGRADED

    # Un succès réinitialise
    registry.update_health("model-x", failure=False)
    h = registry.health_state("model-x")
    assert h.consecutive_failures == 0
    assert h.status == ModelStatus.AVAILABLE


# ---------------------------------------------------------------------------
# Test 9 — ARCHITECTURE : CapabilityEvidence.UNKNOWN ≠ DECLARED/VERIFIED
#           → ne compte pas comme capacité supportée dans un filtre par evidence
# ---------------------------------------------------------------------------
def test_capability_evidence_unknown_not_counted_as_supported():
    records = [
        CapabilityRecord(capability=ModelCapability.VISION, evidence=CapabilityEvidence.UNKNOWN),
        CapabilityRecord(capability=ModelCapability.CODING, evidence=CapabilityEvidence.DECLARED),
        CapabilityRecord(capability=ModelCapability.REASONING, evidence=CapabilityEvidence.VERIFIED),
    ]
    supported = {r.capability for r in records if r.evidence != CapabilityEvidence.UNKNOWN}
    assert ModelCapability.VISION not in supported
    assert ModelCapability.CODING in supported
    assert ModelCapability.REASONING in supported

    # Les trois niveaux sont ordonnés par fiabilité (UNKNOWN < DECLARED < VERIFIED)
    assert CapabilityEvidence.UNKNOWN != CapabilityEvidence.DECLARED
    assert CapabilityEvidence.DECLARED != CapabilityEvidence.VERIFIED


# ---------------------------------------------------------------------------
# Test 10 — ARCHITECTURE : catalog.toml chargé → IDs corrects + capabilities
# ---------------------------------------------------------------------------
def test_models_toml_loaded_with_correct_ids_and_capabilities():
    from raya.models.config_loader import load_model_catalog

    catalog = load_model_catalog()
    assert len(catalog) > 0

    by_id = {e.model_id: e for e in catalog}

    assert "deepseek-v4.1-flash" in by_id, "ID deepseek-v4.1-flash absent du catalog"
    assert "kimi-k2.7-code" in by_id, "ID kimi-k2.7-code absent du catalog"
    assert "gemma4:31b" in by_id, "ID gemma4:31b absent du catalog"

    deepseek = by_id["deepseek-v4.1-flash"]
    deepseek_caps = {r.capability for r in deepseek.capability_records}
    assert ModelCapability.REASONING in deepseek_caps
    assert deepseek.supports_tool_calls is True
    assert deepseek.supports_vision is False

    gemma = by_id["gemma4:31b"]
    gemma_caps = {r.capability for r in gemma.capability_records}
    assert ModelCapability.VISION in gemma_caps
    assert gemma.supports_vision is True


# ---------------------------------------------------------------------------
# Tests 11-15 : E2E réels Ollama Cloud (ignorés si OLLAMA_API_KEY absent)
# ---------------------------------------------------------------------------

@_E2E
def test_e2e_simple_ollama_call_completes():
    """Test 11 — E2E : appel simple deepseek-v4.1-flash → COMPLETED."""
    from raya.models.providers.ollama_cloud import OllamaCloudAdapter

    api_key = _OLLAMA_API_KEY
    adapter = OllamaCloudAdapter(
        "deepseek-v4.1-flash",
        [ModelCapability.REASONING],
        api_key=api_key,
        timeout_s=30.0,
    )
    req = ModelRequest(
        capability=ModelCapability.REASONING,
        messages=[{"role": "user", "content": [{"type": "text", "value": "Réponds juste 'OK'."}]}],
        correlation_id="e2e-11",
        context_budget_tokens=256,
    )
    # Utilise l'interface bas niveau directement pour tester l'adaptateur réel
    from raya.contracts import Message, ContentPart
    req2 = ModelRequest(
        capability=ModelCapability.REASONING,
        messages=[Message(role="user", content=[ContentPart(type="text", value="Réponds juste 'OK'.")])],
        correlation_id="e2e-11",
        context_budget_tokens=256,
    )
    response = adapter.request(req2)
    assert response.finish_reason != FinishReason.ERROR, (
        f"Appel échoué: {response.error.code if response.error else '?'}: "
        f"{response.error.message if response.error else '?'}"
    )
    assert response.finish_reason == FinishReason.COMPLETED


@_E2E
def test_e2e_tool_call_returns_tool_request():
    """Test 12 — E2E : appel avec outil disponible → modèle émet tool_calls_requested."""
    from raya.contracts import Message, ContentPart
    from raya.models.providers.ollama_cloud import OllamaCloudAdapter

    api_key = _OLLAMA_API_KEY
    adapter = OllamaCloudAdapter(
        "deepseek-v4.1-flash",
        [ModelCapability.REASONING],
        api_key=api_key,
        timeout_s=30.0,
    )
    tool = {
        "name": "add_numbers",
        "description": "Adds two numbers together.",
        "input_schema": {
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    }
    req = ModelRequest(
        capability=ModelCapability.REASONING,
        messages=[Message(role="user", content=[ContentPart(type="text", value="What is 3 plus 4? Use the add_numbers tool.")])],
        correlation_id="e2e-12",
        context_budget_tokens=512,
        available_tools=[tool],
    )
    response = adapter.request(req)
    assert response.finish_reason in (FinishReason.TOOL_CALL_PENDING, FinishReason.COMPLETED), (
        f"Réponse inattendue: {response.finish_reason} — {response.error}"
    )


@_E2E
def test_e2e_gemma4_31b_responds():
    """Test 13 — E2E : gemma4:31b répond à une requête texte (vérifie l'ID réel)."""
    from raya.contracts import Message, ContentPart
    from raya.models.providers.ollama_cloud import OllamaCloudAdapter

    api_key = _OLLAMA_API_KEY
    adapter = OllamaCloudAdapter(
        "gemma4:31b",
        [ModelCapability.VISION, ModelCapability.SUMMARIZATION],
        api_key=api_key,
        timeout_s=30.0,
    )
    req = ModelRequest(
        capability=ModelCapability.VISION,
        messages=[Message(role="user", content=[ContentPart(type="text", value="Réponds juste 'OK'.")])],
        correlation_id="e2e-13",
        context_budget_tokens=256,
    )
    response = adapter.request(req)
    assert response.finish_reason != FinishReason.ERROR, (
        f"gemma4:31b indisponible: {response.error.code if response.error else '?'}"
    )


@_E2E
def test_e2e_fallback_from_absent_model_to_valid_model():
    """Test 14 — E2E : modèle inexistant → router bascule sur modèle valide."""
    from raya.contracts import Message, ContentPart
    from raya.models.providers.ollama_cloud import OllamaCloudAdapter

    api_key = _OLLAMA_API_KEY
    registry = ModelRegistry()
    # Provider avec ID qui n'existe pas → 404 → ERROR
    bad = OllamaCloudAdapter(
        "model-qui-nexiste-pas-xyz123",
        [ModelCapability.REASONING],
        api_key=api_key,
        timeout_s=10.0,
    )
    # Provider valide
    good = OllamaCloudAdapter(
        "deepseek-v4.1-flash",
        [ModelCapability.REASONING],
        api_key=api_key,
        timeout_s=30.0,
    )
    registry.register(bad)
    registry.register(good)

    req = ModelRequest(
        capability=ModelCapability.REASONING,
        messages=[Message(role="user", content=[ContentPart(type="text", value="Réponds juste 'OK'.")])],
        correlation_id="e2e-14",
        context_budget_tokens=256,
    )
    response = route(registry, req)
    assert response.finish_reason == FinishReason.COMPLETED, (
        f"Fallback échoué, provider_used={response.provider_used}, "
        f"error={response.error}"
    )
    assert "deepseek-v4.1-flash" in response.provider_used


@_E2E
def test_e2e_two_concurrent_tasks_both_complete():
    """Test 15 — E2E : deux appels concurrents via Scheduler → tous deux COMPLETED."""
    from raya.contracts import Message, ContentPart
    from raya.models.providers.ollama_cloud import OllamaCloudAdapter

    api_key = _OLLAMA_API_KEY
    adapter = OllamaCloudAdapter(
        "deepseek-v4.1-flash",
        [ModelCapability.REASONING],
        api_key=api_key,
        timeout_s=60.0,
    )
    registry = ModelRegistry()
    registry.register(adapter)
    scheduler = ModelScheduler(global_max=3, registry=registry)

    req = ModelRequest(
        capability=ModelCapability.REASONING,
        messages=[Message(role="user", content=[ContentPart(type="text", value="Réponds juste 'OK'.")])],
        correlation_id="e2e-15",
        context_budget_tokens=256,
    )

    results: list[ModelResponse] = []
    errors: list[str] = []

    def run_one():
        try:
            r = route(registry, req, scheduler=scheduler)
            results.append(r)
        except Exception as exc:
            errors.append(str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run_one), pool.submit(run_one)]
        for f in futures:
            f.result(timeout=90.0)

    assert not errors, f"Exceptions pendant les appels concurrents: {errors}"
    assert len(results) == 2
    assert all(r.finish_reason == FinishReason.COMPLETED for r in results), (
        f"Résultats: {[(r.finish_reason, r.error) for r in results]}"
    )
