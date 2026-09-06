"""Harness.last_turn_intent (Chantier 12 §D) — dérivé du VRAI tool trace
d'un tour réel, via le pipeline agentique complet (même discipline que
test_observation_promotion.py : modèle scripté, Tools/Safety 100% réels)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.harness_factory import build_test_harness  # noqa: E402

from raya.cognition import Intent  # noqa: E402
from raya.contracts import (  # noqa: E402
    Channel,
    ContentPart,
    FinishReason,
    HarnessRequest,
    InterfaceInput,
    ModelResponse,
    RequestedToolCall,
)


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


def _req(text: str, session_id: str = "s1") -> HarnessRequest:
    return HarnessRequest(channel=Channel.CLI, session_id=session_id, input=InterfaceInput(text=text))


def test_pure_text_answer_with_no_tool_call_is_information(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("il est 14h.")])
    try:
        handles.harness.handle_request(_req("quelle heure est-il ?"))
        assert handles.harness.last_turn_intent("s1") == Intent.INFORMATION
    finally:
        handles.shutdown()


def test_read_only_tool_call_is_still_information(tmp_path):
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("pc.window.list", {}), _text_response("voici les fenêtres ouvertes."),
    ], enable_windows_device=True)
    try:
        handles.harness.handle_request(_req("qu'est-ce qui est ouvert ?"))
        assert handles.harness.last_turn_intent("s1") == Intent.INFORMATION
    finally:
        handles.shutdown()


def test_mutating_tool_call_is_action(tmp_path):
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("filesystem.write_file", {"path": "a.txt", "content": "x"}), _text_response("fait."),
    ])
    try:
        handles.harness.handle_request(_req("écris un fichier"))
        assert handles.harness.last_turn_intent("s1") == Intent.ACTION
    finally:
        handles.shutdown()
