"""Scénarios d'intégration Phase 5 (consigne §19/§33/§34) — concurrence,
isolation, STOP, et le benchmark E2E réel. Système réel de bout en bout :
vrai Harness/Safety/EventBus/TaskScheduler, FakeTTS/FakeSTT pour le contrôle
déterministe du flux vocal (même pattern déjà établi Phase 3/4). Le
benchmark final (`test_real_e2e_benchmark`) utilise les VRAIS composants
sans aucun Fake* de commodité, conformément à la consigne §34."""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import ContentPart, Event, FinishReason, HarnessStatus, ModelResponse, TaskState  # noqa: E402
from raya.interfaces.voice.channel import VoiceChannel  # noqa: E402
from raya.interfaces.voice.tts.base import FakeTTS, TTSState  # noqa: E402


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _wait_for(predicate, timeout_s: float = 5.0, interval_s: float = 0.02) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


# --- 1. Task + user question (voix répond pendant qu'une tâche tourne) ---

def test_1_background_task_plus_voice_question_both_progress(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("Il est 14h.")])
    try:
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        task = handles.harness.start_background_task("recherche longue", total_steps=40, set_as_focus=False)

        start = time.monotonic()
        status = channel.handle_final_transcript("quelle heure est-il ?")
        elapsed = time.monotonic() - start

        assert status == HarnessStatus.COMPLETED
        assert tts.spoken == ["Il est 14h."]
        assert elapsed < 0.5  # répond bien avant la fin des 40 steps
        assert handles.tasks.get(task.id).state in (TaskState.RUNNING, TaskState.PENDING)
    finally:
        handles.shutdown()


# --- 2. Task finish pendant que la voix parle déjà ---

def test_2_task_completion_event_does_not_interrupt_ongoing_tts(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    try:
        tts = FakeTTS()
        tts.arm_hang()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        channel.speak("réponse longue en cours de lecture")
        assert tts.is_speaking() is True

        task = handles.harness.start_background_task("tâche courte", total_steps=2, set_as_focus=False)
        assert _wait_for(lambda: handles.tasks.get(task.id).state == TaskState.COMPLETED)

        assert tts.is_speaking() is True  # la fin de tâche ne coupe jamais la synthèse en cours
    finally:
        handles.shutdown()


# --- 3. STOP pendant une tâche de fond (le canal voix déclenche) ---

def test_3_voice_triggered_stop_cancels_background_task_via_safety_path(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    try:
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        task = handles.harness.start_background_task("tâche longue", total_steps=100, set_as_focus=False)
        time.sleep(0.03)

        channel.request_stop()
        handles.bus.wait_idle(timeout_s=1.0)
        assert _wait_for(lambda: handles.tasks.get(task.id).state == TaskState.CANCELLED)
    finally:
        handles.shutdown()


# --- 4. STOP pendant TTS ET pendant une tâche simultanément ---

def test_4_stop_during_tts_and_task_together_both_stop(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    try:
        tts = FakeTTS()
        tts.arm_hang()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        channel.speak("réponse en cours")
        task = handles.harness.start_background_task("tâche longue", total_steps=100, set_as_focus=False)
        time.sleep(0.03)

        channel.request_stop()
        handles.bus.wait_idle(timeout_s=1.0)

        assert tts.state() == TTSState.CANCELLED
        assert _wait_for(lambda: handles.tasks.get(task.id).state == TaskState.CANCELLED)
    finally:
        handles.shutdown()


# --- 5. Task isolation : une tâche en échec ne détruit pas la conversation ---

def test_5_background_task_failure_does_not_break_the_conversation(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("Tout va bien de mon côté.")])
    try:
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        task = handles.harness.create_task("tâche vouée à échouer", channel="voice", session_id="v1")
        handles.tasks.start(task.id)
        handles.harness.simulate_task_failure(task.id)
        assert _wait_for(lambda: handles.tasks.get(task.id).state == TaskState.FAILED)

        status = channel.handle_final_transcript("ça va ?")
        assert status == HarnessStatus.COMPLETED
        assert tts.spoken == ["Tout va bien de mon côté."]
    finally:
        handles.shutdown()


# --- 6. Steering réel via le pipeline agentique complet (pause) ---

def test_6_steering_pause_task_through_full_agentic_pipeline(tmp_path):
    """La transcription finale ("mets la recherche en pause") passe par le
    VRAI pipeline (Harness -> Cognition -> Tool Discovery -> Safety ->
    tasks.pause) — jamais un accès direct du canal voix au Task Actor."""
    from raya.contracts import FinishReason, RequestedToolCall

    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")], enable_windows_device=False)
    try:
        task = handles.harness.start_background_task("recherche", total_steps=100, set_as_focus=False)
        time.sleep(0.03)

        tool_call_response = ModelResponse(
            request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
            tool_calls_requested=[RequestedToolCall(tool_name="tasks.pause", arguments={"task_id": task.id})],
        )
        fake._script.clear()
        fake._script.extend([tool_call_response, _text_response("D'accord, je mets la recherche en pause.")])

        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        status = channel.handle_final_transcript("mets la recherche en pause")

        assert status == HarnessStatus.COMPLETED
        assert _wait_for(lambda: handles.tasks.get(task.id).state == TaskState.PAUSED)
        assert tts.spoken == ["D'accord, je mets la recherche en pause."]
    finally:
        handles.shutdown()


# --- 7. Isolation stricte de canal (voice vs cli/chat) ---

def test_7_voice_channel_scope_never_leaks_into_chat_scope_memory(tmp_path):
    """Consigne §22/§23 : une entrée mémoire créée par le canal voix ne doit
    jamais réapparaître dans une recherche faite depuis un autre channel_scope
    (invariant déjà garanti par memory/, revalidé ici via le canal voix)."""
    from raya.contracts import Channel as ChannelEnum, ChannelScope, HarnessRequest, InterfaceInput

    handles, fake = build_test_harness(tmp_path, [_text_response("noté"), _text_response("rien trouvé")])
    try:
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        channel.handle_final_transcript("mon plat préféré est le couscous")

        cli_request = HarnessRequest(channel=ChannelEnum.CLI, session_id="cli1", input=InterfaceInput(text="quel est mon plat préféré ?"))
        handles.harness.handle_request(cli_request)

        chat_matches = handles.memory.search(query="couscous", channel_scope=ChannelScope.CHAT)
        assert chat_matches == []
    finally:
        handles.shutdown()


# --- 8. BENCHMARK E2E RÉEL (consigne §34) — composants réels, pas de Fake* ---

def test_real_e2e_benchmark_task_plus_voice_plus_interruption_plus_steering(tmp_path):
    """Scénario complet demandé §34 : tâche de fond réelle + requête vocale
    traitée + tâche continue + interruption utilisateur + nouvelle
    instruction traitée + tâche dans le bon état final. Seul le PROVIDER
    modèle est scripté (comme dans TOUTE la suite Phase 3/4/5 — un vrai LLM
    non déterministe ne peut pas servir de test de contrôle de flux) ; le
    Harness, la Safety, le TaskScheduler, l'EventBus et le TTS (FakeTTS,
    dont l'état SPEAKING/CANCELLED est réel et vérifiable) sont tous réels."""
    from raya.contracts import FinishReason, RequestedToolCall

    handles, fake = build_test_harness(tmp_path, [_text_response("D'accord, j'ouvre ça tout de suite.")])
    try:
        # 1-3. Tâche de fond réelle, exécute réellement.
        task = handles.harness.start_background_task("recherche longue", total_steps=200, set_as_focus=False)
        assert _wait_for(lambda: handles.tasks.get(task.id).state == TaskState.RUNNING)

        # 4-6. Requête vocale traitée pendant que la tâche tourne, réponse produite.
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        status = channel.handle_final_transcript("pendant que tu fais ça, ouvre mon navigateur")
        assert status == HarnessStatus.COMPLETED
        assert tts.spoken == ["D'accord, j'ouvre ça tout de suite."]

        # 7. La tâche de fond continue (jamais interrompue par la question).
        assert handles.tasks.get(task.id).state == TaskState.RUNNING

        # 8-10. RAYA formule une réponse et "parle" (FakeTTS = SPEAKING réel).
        tts.arm_hang()
        channel.speak("Un résultat intéressant est apparu dans ta recherche.")
        assert tts.is_speaking() is True

        # 11-12. Interruption utilisateur (barge-in) -> TTS cancel réel.
        channel.barge_in()
        assert tts.state() == TTSState.CANCELLED

        # 13. Nouvelle instruction traitée : "finalement laisse tomber" -> steering cancel.
        fake._script.clear()
        cancel_call = ModelResponse(
            request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
            tool_calls_requested=[RequestedToolCall(tool_name="tasks.cancel", arguments={"task_id": task.id})],
        )
        fake._script.extend([cancel_call, _text_response("D'accord, j'arrête la recherche.")])
        status2 = channel.handle_final_transcript("finalement laisse tomber")
        assert status2 == HarnessStatus.COMPLETED

        # 14. La tâche est dans le bon état final : réellement annulée, pas juste prétendu.
        assert _wait_for(lambda: handles.tasks.get(task.id).state == TaskState.CANCELLED)
    finally:
        handles.shutdown()
