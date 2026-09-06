"""Chantier 16 (Contextualisation, Task context §7) : une tâche active doit
être visible dans le contexte d'une conversation NORMALE, pas seulement
depuis l'intérieur du step d'une tâche de fond en cours — nécessaire pour
que "arrête ça" puisse référer à une tâche active pendant une conversation
ordinaire. Système réel de bout en bout (vrai TaskRegistry/Harness/Context
Engine, seul le Model Layer est scripté)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import Channel, ContentPart, FinishReason, HarnessRequest, InterfaceInput, ModelResponse, SectionKind, TaskOwner  # noqa: E402


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def test_active_task_visible_in_context_during_a_normal_conversation_turn(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("D'accord.")])
    try:
        background = handles.tasks.create(
            "télécharger le fichier X", TaskOwner(channel="cli", session_id="s1"), correlation_id="c1",
        )
        handles.tasks.start(background.id)

        req = HarnessRequest(channel=Channel.CLI, session_id="s1", input=InterfaceInput(text="arrête ça"))
        handles.harness.handle_request(req)

        context = handles.harness.last_context("s1")
        active_ids = {s.content["task_id"] for s in context.sections if s.kind == SectionKind.ACTIVE_TASKS}
        assert background.id in active_ids
    finally:
        handles.shutdown()


def test_completed_task_never_appears_as_an_active_task_in_context(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("D'accord.")])
    try:
        done = handles.tasks.create("déjà fini", TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
        handles.tasks.start(done.id)
        handles.tasks.complete(done.id, {"ok": True})

        req = HarnessRequest(channel=Channel.CLI, session_id="s1", input=InterfaceInput(text="salut"))
        handles.harness.handle_request(req)

        context = handles.harness.last_context("s1")
        active_ids = {s.content["task_id"] for s in context.sections if s.kind == SectionKind.ACTIVE_TASKS}
        assert done.id not in active_ids
    finally:
        handles.shutdown()
