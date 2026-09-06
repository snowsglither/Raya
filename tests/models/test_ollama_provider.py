"""Priorité Model Layer — OllamaCloudAdapter : payload réel, parsing réel,
erreurs structurées distinctes (jamais un générique "token expired",
consigne Phase 3 §5, §29). Utilise de VRAIS objets ModelRequest/ModelResponse,
seul `requests.post/get` est monkeypatché (aucun réseau dans ces tests unitaires
— les tests réseau réels sont dans tests/integration/test_phase3_scenarios.py,
marqués live et BLOCKED si aucune clé n'est disponible)."""

from __future__ import annotations

import requests

from raya.contracts import ContentPart, FinishReason, Message, ModelCapability, ModelRequest
from raya.models.providers import OllamaCloudAdapter, OllamaLocalAdapter


class _FakeResponse:
    def __init__(self, status_code: int, json_data: dict | None = None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data or {}
        self.text = text

    def json(self):
        return self._json_data


def _request(text: str = "salut", tools=None) -> ModelRequest:
    return ModelRequest(
        capability=ModelCapability.REASONING,
        messages=[Message(role="user", content=[ContentPart(type="text", value=text)])],
        correlation_id="corr_1",
        available_tools=tools,
    )


def test_successful_response_parsed_into_native_contract(monkeypatch):
    def fake_post(url, headers, json, timeout):
        assert json["think"] is False  # bug empirique deepseek connu (RAYA_V2_MIGRATION_MAP.md #3)
        assert "Authorization" in headers
        return _FakeResponse(200, {"message": {"content": "OK"}, "done_reason": "stop", "prompt_eval_count": 5, "eval_count": 1})

    monkeypatch.setattr(requests, "post", fake_post)
    provider = OllamaCloudAdapter("deepseek-v4-flash:cloud", [ModelCapability.REASONING], api_key="k")
    resp = provider.request(_request())
    assert resp.finish_reason == FinishReason.COMPLETED
    assert resp.content[0].value == "OK"
    assert resp.usage.input_tokens == 5
    assert resp.usage.output_tokens == 1
    assert resp.error is None


def test_tool_calls_parsed_into_requested_tool_call(monkeypatch):
    def fake_post(url, headers, json, timeout):
        assert json["tools"][0]["function"]["name"] == "filesystem.write_file"
        return _FakeResponse(200, {
            "message": {"content": "", "tool_calls": [{"function": {"name": "filesystem.write_file", "arguments": {"path": "a.txt"}}}]},
            "done_reason": "stop",
        })

    monkeypatch.setattr(requests, "post", fake_post)
    provider = OllamaCloudAdapter("deepseek-v4-flash:cloud", [ModelCapability.REASONING], api_key="k")
    tools = [{"name": "filesystem.write_file", "description": "x", "input_schema": {"type": "object"}}]
    resp = provider.request(_request(tools=tools))
    assert resp.finish_reason == FinishReason.TOOL_CALL_PENDING
    assert resp.tool_calls_requested[0].tool_name == "filesystem.write_file"
    assert resp.tool_calls_requested[0].arguments == {"path": "a.txt"}


def test_assistant_tool_calls_are_serialized_as_structured_function_calls(monkeypatch):
    """Bug corrigé (passe 'Targeted Execution Repair', trouvé en E2E réel) :
    un tour assistant demandant un appel d'outil était envoyé comme un
    simple texte, sans le champ `tool_calls` structuré — un vrai modèle
    conditionné sur cette forme non standard finissait par la recopier
    verbatim au lieu de répondre réellement."""
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["payload"] = json
        return _FakeResponse(200, {"message": {"content": "fait."}, "done_reason": "stop"})

    monkeypatch.setattr(requests, "post", fake_post)
    provider = OllamaCloudAdapter("deepseek-v4-flash:cloud", [ModelCapability.REASONING], api_key="k")
    messages = [
        Message(role="user", content=[ContentPart(type="text", value="ouvre notepad")]),
        Message(role="assistant", content=[ContentPart(type="text", value="")],
                tool_calls=[{"id": "tc_1", "name": "pc.application.launch", "arguments": {"target": "notepad"}}]),
        Message(role="tool", tool_call_id="tc_1", content=[ContentPart(type="text", value='{"status": "success"}')]),
    ]
    provider.request(ModelRequest(capability=ModelCapability.REASONING, messages=messages, correlation_id="c1"))

    sent = captured["payload"]["messages"]
    assistant_entry = sent[1]
    assert assistant_entry["role"] == "assistant"
    assert assistant_entry["tool_calls"] == [
        {"id": "tc_1", "function": {"name": "pc.application.launch", "arguments": {"target": "notepad"}}},
    ]
    tool_entry = sent[2]
    assert tool_entry["role"] == "tool"
    assert tool_entry["tool_call_id"] == "tc_1"


def test_plain_messages_without_tool_calls_carry_no_tool_calls_field(monkeypatch):
    """Non-régression : un message ordinaire (sans `Message.tool_calls`) ne
    doit jamais gagner un champ `tool_calls` vide/fantôme dans le payload."""
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["payload"] = json
        return _FakeResponse(200, {"message": {"content": "salut."}, "done_reason": "stop"})

    monkeypatch.setattr(requests, "post", fake_post)
    provider = OllamaCloudAdapter("deepseek-v4-flash:cloud", [ModelCapability.REASONING], api_key="k")
    provider.request(_request())
    sent = captured["payload"]["messages"]
    assert "tool_calls" not in sent[0]
    assert "tool_call_id" not in sent[0]


def test_done_reason_length_maps_to_truncated_not_end_turn(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(200, {"message": {"content": "..."}, "done_reason": "length"}))
    provider = OllamaCloudAdapter("m", [ModelCapability.REASONING], api_key="k")
    resp = provider.request(_request())
    assert resp.finish_reason == FinishReason.TRUNCATED


def test_http_401_maps_to_auth_error_not_generic(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(401, text="unauthorized"))
    provider = OllamaCloudAdapter("m", [ModelCapability.REASONING], api_key="bad-key")
    resp = provider.request(_request())
    assert resp.finish_reason == FinishReason.ERROR
    assert resp.error.code == "OLLAMA_AUTH_ERROR"
    assert resp.error.details["provider"] == "ollama_cloud"


def test_http_404_maps_to_model_unavailable(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(404))
    provider = OllamaCloudAdapter("modele-inexistant", [ModelCapability.REASONING], api_key="k")
    resp = provider.request(_request())
    assert resp.error.code == "OLLAMA_MODEL_UNAVAILABLE"


def test_http_429_maps_to_rate_limited_retryable(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(429))
    provider = OllamaCloudAdapter("m", [ModelCapability.REASONING], api_key="k")
    resp = provider.request(_request())
    assert resp.error.code == "OLLAMA_RATE_LIMITED"
    assert resp.error.retryable is True


def test_http_500_maps_to_generic_http_error_retryable(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: _FakeResponse(500, text="internal error"))
    provider = OllamaCloudAdapter("m", [ModelCapability.REASONING], api_key="k")
    resp = provider.request(_request())
    assert resp.error.code == "OLLAMA_HTTP_ERROR"
    assert resp.error.retryable is True
    assert resp.error.details["http_status"] == 500


def test_timeout_is_distinguished_from_network_error(monkeypatch):
    def raise_timeout(*a, **k):
        raise requests.exceptions.Timeout("too slow")

    monkeypatch.setattr(requests, "post", raise_timeout)
    provider = OllamaCloudAdapter("m", [ModelCapability.REASONING], api_key="k", timeout_s=1.0)
    resp = provider.request(_request())
    assert resp.error.code == "OLLAMA_TIMEOUT"
    assert resp.error.retryable is True


def test_connection_error_distinguished_from_timeout(monkeypatch):
    def raise_conn(*a, **k):
        raise requests.exceptions.ConnectionError("dns failure")

    monkeypatch.setattr(requests, "post", raise_conn)
    provider = OllamaCloudAdapter("m", [ModelCapability.REASONING], api_key="k")
    resp = provider.request(_request())
    assert resp.error.code == "OLLAMA_NETWORK_ERROR"


def test_invalid_json_response_reported_not_masked(monkeypatch):
    class BadJsonResponse(_FakeResponse):
        def json(self):
            raise ValueError("not json")

    monkeypatch.setattr(requests, "post", lambda *a, **k: BadJsonResponse(200))
    provider = OllamaCloudAdapter("m", [ModelCapability.REASONING], api_key="k")
    resp = provider.request(_request())
    assert resp.error.code == "OLLAMA_INVALID_RESPONSE"


def test_no_api_key_means_unavailable():
    provider = OllamaCloudAdapter("m", [ModelCapability.REASONING], api_key="")
    assert provider.is_available() is False
    assert provider.descriptor().available is False


def test_local_adapter_checks_real_daemon_presence(monkeypatch):
    def fake_get(url, timeout):
        assert "localhost" in url or "127.0.0.1" in url
        raise requests.exceptions.ConnectionError("no daemon")

    monkeypatch.setattr(requests, "get", fake_get)
    provider = OllamaLocalAdapter("llama3", [ModelCapability.REASONING])
    assert provider.is_available() is False  # aucun démon local réellement présent en CI


def test_local_adapter_no_auth_header(monkeypatch):
    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["headers"] = headers
        return _FakeResponse(200, {"message": {"content": "ok"}, "done_reason": "stop"})

    monkeypatch.setattr(requests, "post", fake_post)
    provider = OllamaLocalAdapter("llama3", [ModelCapability.REASONING])
    provider.request(_request())
    assert "Authorization" not in captured["headers"]
