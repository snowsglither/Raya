"""OllamaCloudAdapter — provider RÉEL (RAYA_V2_TECHNICAL_ARCHITECTURE.md §4,
consigne Phase 3 §4-5). REBUILD complet depuis les contrats natifs RAYA —
aucun format Anthropic-shaped, aucune classe/traduction copiée de
`core/llm.py` V1. Seule la CONNAISSANCE OPÉRATIONNELLE de l'API Ollama réelle
est reprise (EXTRACT, RAYA_V2_MIGRATION_MAP.md #3) :

- endpoint `POST /api/chat`, payload {model, messages, tools?, think, options}
- `think: False` requis pour les modèles "thinking" (ex: deepseek-v4-flash) —
  sans ça le budget num_predict peut être entièrement consommé par le
  raisonnement interne du modèle avant d'atteindre `content` (réponse vide).
  Vérifié empiriquement en V1, RAYA_V2_MIGRATION_MAP.md #3.
- `done_reason="length"` signifie une réponse tronquée par num_predict — ce
  n'est PAS une fin normale, mappé ici sur FinishReason.TRUNCATED (jamais
  "end_turn" façon Anthropic).

Erreurs structurées (consigne §5, §29) : jamais de générique "token expired"
pour une erreur non identifiée — chaque cas HTTP/réseau/timeout a son propre
code ErrorInfo, avec le provider/status/message réels conservés.
"""

from __future__ import annotations

import json
import time

import requests

from raya.contracts import (
    ContentPart,
    ErrorInfo,
    FinishReason,
    Message,
    ModelCapability,
    ModelDescriptor,
    ModelRequest,
    ModelResponse,
    ModelUsage,
    RequestedToolCall,
)
from raya.observability import log

from .base import ProviderAdapter

_DEFAULT_HOST = "https://ollama.com"
_CHAT_PATH = "/api/chat"


def _messages_to_ollama(messages: list[Message]) -> list[dict]:
    """Bug corrigé (passe 'Targeted Execution Repair', trouvé en E2E réel) :
    un tour assistant demandant un appel d'outil était sérialisé comme un
    simple texte placeholder, sans le champ `tool_calls` structuré attendu
    par le format function-calling — le modèle réel, conditionné sur cette
    forme non standard répétée plusieurs tours de suite, a fini par recopier
    ce placeholder verbatim comme réponse finale au lieu de répondre
    réellement. `Message.tool_calls`/`tool_call_id` (additifs, voir
    contracts/model.py) portent maintenant l'info structurée jusqu'ici."""
    payload = []
    for m in messages:
        text = "".join(p.value for p in m.content if p.type == "text")
        entry: dict = {"role": m.role, "content": text}
        if m.tool_calls:
            entry["tool_calls"] = [
                {"id": tc.get("id"), "function": {"name": tc.get("name"), "arguments": tc.get("arguments") or {}}}
                for tc in m.tool_calls
            ]
        if m.tool_call_id:
            entry["tool_call_id"] = m.tool_call_id
        payload.append(entry)
    return payload


def _tools_to_ollama(available_tools: list[dict] | None) -> list[dict] | None:
    """Convertit le catalogue Tool (déjà JSON-Schema, RAYA_V2_CONTRACTS.md §9)
    au format function-calling attendu par l'API Ollama réelle."""
    if not available_tools:
        return None
    converted = []
    for t in available_tools:
        converted.append({
            "type": "function",
            "function": {
                "name": t.get("name"),
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object", "properties": {}},
            },
        })
    return converted


def _finish_reason(has_tool_calls: bool, done_reason: str | None) -> FinishReason:
    if has_tool_calls:
        return FinishReason.TOOL_CALL_PENDING
    if done_reason == "length":
        return FinishReason.TRUNCATED
    return FinishReason.COMPLETED


def _parse_tool_calls(raw_tool_calls: list[dict]) -> list[RequestedToolCall]:
    calls = []
    for tc in raw_tool_calls:
        fn = tc.get("function") or {}
        args = fn.get("arguments") or fn.get("args") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except json.JSONDecodeError:
                args = {}
        calls.append(RequestedToolCall(tool_name=str(fn.get("name", "")), arguments=args if isinstance(args, dict) else {}))
    return calls


class OllamaCloudAdapter(ProviderAdapter):
    def __init__(
        self,
        model_id: str,
        capabilities: list[ModelCapability],
        api_key: str,
        *,
        host: str = _DEFAULT_HOST,
        context_limit: int = 128_000,
        timeout_s: float = 60.0,
    ) -> None:
        self._model_id = model_id
        self._capabilities = capabilities
        self._api_key = api_key
        self._host = host.rstrip("/")
        self._context_limit = context_limit
        self._timeout_s = timeout_s

    def descriptor(self) -> ModelDescriptor:
        return ModelDescriptor(
            id=self._model_id,
            provider="ollama_cloud",
            capabilities=self._capabilities,
            context_limit=self._context_limit,
            supports_streaming=True,
            supports_tool_calls=True,
            supports_vision=ModelCapability.VISION in self._capabilities,
            estimated_latency_ms=1500,
            cost_per_1k_tokens=0.0,  # Ollama Cloud : pas de coût par token exposé à ce niveau
            available=bool(self._api_key),
        )

    def is_available(self) -> bool:
        return bool(self._api_key)

    def request(self, req: ModelRequest) -> ModelResponse:
        start = time.monotonic()
        payload = {
            "model": self._model_id,
            "messages": _messages_to_ollama(req.messages),
            "think": False,
            "stream": False,
            "options": {
                "num_predict": max(256, req.context_budget_tokens // 4),
                "num_ctx": min(self._context_limit, req.context_budget_tokens * 4 or self._context_limit),
            },
        }
        tools_payload = _tools_to_ollama(req.available_tools)
        if tools_payload:
            payload["tools"] = tools_payload

        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        try:
            response = requests.post(
                f"{self._host}{_CHAT_PATH}", headers=headers, json=payload, timeout=self._timeout_s
            )
        except requests.exceptions.Timeout as exc:
            return self._error_response(req, "OLLAMA_TIMEOUT", f"Timeout après {self._timeout_s}s", True, start, exc)
        except requests.exceptions.ConnectionError as exc:
            return self._error_response(req, "OLLAMA_NETWORK_ERROR", str(exc), True, start, exc)
        except requests.exceptions.RequestException as exc:
            return self._error_response(req, "OLLAMA_REQUEST_ERROR", str(exc), False, start, exc)

        if response.status_code == 401:
            return self._error_response(
                req, "OLLAMA_AUTH_ERROR", "Authentification refusée (clé API invalide/absente)", False, start,
                details={"http_status": 401},
            )
        if response.status_code == 404:
            return self._error_response(
                req, "OLLAMA_MODEL_UNAVAILABLE", f"Modèle {self._model_id!r} indisponible", False, start,
                details={"http_status": 404},
            )
        if response.status_code == 429:
            return self._error_response(
                req, "OLLAMA_RATE_LIMITED", "Limite de requêtes atteinte", True, start,
                details={"http_status": 429},
            )
        if response.status_code != 200:
            body = response.text.strip()[:400]
            return self._error_response(
                req, "OLLAMA_HTTP_ERROR", f"HTTP {response.status_code} : {body}", response.status_code >= 500, start,
                details={"http_status": response.status_code, "body": body},
            )

        try:
            data = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            return self._error_response(req, "OLLAMA_INVALID_RESPONSE", f"Réponse non-JSON : {exc}", True, start, exc)

        message = data.get("message") or {}
        content_text = message.get("content", "")
        raw_tool_calls = message.get("tool_calls") or []
        tool_calls = _parse_tool_calls(raw_tool_calls) if raw_tool_calls else None

        content = [ContentPart(type="text", value=str(content_text))] if content_text else []
        finish_reason = _finish_reason(bool(tool_calls), data.get("done_reason"))

        latency_ms = int((time.monotonic() - start) * 1000)
        log("info", "ollama_cloud request completed", model=self._model_id, finish_reason=finish_reason.value,
            latency_ms=latency_ms, correlation_id=req.correlation_id)

        return ModelResponse(
            request_id=req.id,
            provider_used=f"ollama_cloud:{self._model_id}",
            content=content,
            finish_reason=finish_reason,
            tool_calls_requested=tool_calls,
            usage=ModelUsage(
                input_tokens=int(data.get("prompt_eval_count", 0) or 0),
                output_tokens=int(data.get("eval_count", 0) or 0),
            ),
            latency_ms=latency_ms,
        )

    def _error_response(
        self, req: ModelRequest, code: str, message: str, retryable: bool, start: float,
        exc: Exception | None = None, details: dict | None = None,
    ) -> ModelResponse:
        latency_ms = int((time.monotonic() - start) * 1000)
        error_details = dict(details or {})
        error_details["provider"] = "ollama_cloud"
        error_details["model"] = self._model_id
        if exc is not None:
            error_details["exception_type"] = type(exc).__name__
        log("error", "ollama_cloud request failed", code=code, detail=message, correlation_id=req.correlation_id)
        return ModelResponse(
            request_id=req.id,
            provider_used=f"ollama_cloud:{self._model_id}",
            content=[],
            finish_reason=FinishReason.ERROR,
            latency_ms=latency_ms,
            error=ErrorInfo(code=code, message=message, retryable=retryable, details=error_details),
        )
