"""VoiceChannel (consigne Phase 5 §9/§18/§33) — le client mince du Harness
pour la voix. Système réel de bout en bout (vrai Harness/Safety/EventBus,
FakeTTS pour la synthèse — mêmes garanties que le pattern déjà établi
Phase 3/4 : seul ce qui a besoin d'être scripté l'est)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import ContentPart, Event, FinishReason, HarnessStatus, ModelResponse  # noqa: E402
from raya.interfaces.voice.channel import VoiceChannel  # noqa: E402
from raya.interfaces.voice.tts.base import FakeTTS, TTSState  # noqa: E402


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def test_final_transcript_reaches_real_harness_and_gets_a_response(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("Il fait beau aujourd'hui.")])
    try:
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        status = channel.handle_final_transcript("Quel temps fait-il ?")
        assert status == HarnessStatus.COMPLETED
        assert tts.spoken == ["Il fait beau aujourd'hui."]
    finally:
        handles.shutdown()


def test_turn_counter_increments_per_final_transcript(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("ok"), _text_response("ok2")])
    try:
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        channel.handle_final_transcript("un")
        channel.handle_final_transcript("deux")
        assert channel.session.current_turn == 2
    finally:
        handles.shutdown()


def test_no_response_text_never_speaks_silent_is_a_valid_outcome(tmp_path):
    """Consigne §12 : le silence est un résultat valide — un texte de
    réponse vide ne doit jamais déclencher une synthèse vide."""
    handles, fake = build_test_harness(tmp_path, [_text_response("")])
    try:
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        channel.handle_final_transcript("...")
        assert tts.spoken == []
    finally:
        handles.shutdown()


def test_barge_in_cancels_active_tts_and_never_cancels_background_task(tmp_path):
    """Consigne §16 : le barge-in interrompt SEULEMENT la synthèse — jamais
    une tâche de fond en cours (isolation tâche/conversation)."""
    from raya.contracts import TaskState

    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    try:
        tts = FakeTTS()
        tts.arm_hang()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        task = handles.harness.start_background_task("recherche longue", total_steps=50, set_as_focus=False)

        channel.speak("Une réponse en cours de lecture...")
        assert tts.is_speaking() is True
        channel.barge_in()
        assert tts.state() == TTSState.CANCELLED
        assert handles.tasks.get(task.id).state in (TaskState.RUNNING, TaskState.PENDING)  # jamais annulée par le barge-in
    finally:
        handles.shutdown()


def test_barge_in_when_not_speaking_is_a_safe_noop(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    try:
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        channel.barge_in()  # rien ne parle -> aucun effet, aucune exception
        assert tts.cancel_calls == 0
    finally:
        handles.shutdown()


def test_request_stop_publishes_interface_stop_requested_event(tmp_path):
    """Consigne §18 : STOP publie un Event, jamais un appel direct à Safety."""
    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    try:
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        received = []
        handles.bus.subscribe("interface.stop_requested", lambda e: received.append(e), subscriber="test")
        channel.request_stop()
        handles.bus.wait_idle(timeout_s=1.0)
        assert len(received) == 1
        assert received[0].source == "interfaces.voice"
    finally:
        handles.shutdown()


def test_global_stop_from_another_interface_still_cancels_this_channels_tts(tmp_path):
    """STOP déclenché par un AUTRE canal (ex: CLI) doit aussi couper la
    synthèse de ce canal voix — testé §19 "STOP + TTS"."""
    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    try:
        tts = FakeTTS()
        tts.arm_hang()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        channel.speak("réponse longue")
        assert tts.is_speaking() is True
        handles.bus.publish(Event(type="interface.stop_requested", source="interfaces.cli", payload={}))
        handles.bus.wait_idle(timeout_s=1.0)
        assert tts.state() == TTSState.CANCELLED
    finally:
        handles.shutdown()


def test_global_stop_cancels_tts_on_every_channel_but_state_stays_per_channel(tmp_path):
    """STOP n'est PAS scopé par session — c'est un signal global (cohérent
    Phase 0-2) : les DEUX canaux voix voient leur TTS coupé. Mais chaque
    session garde son PROPRE objet d'état (présence/session), jamais
    partagé entre canaux (consigne §22)."""
    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")])
    try:
        tts_a, tts_b = FakeTTS(), FakeTTS()
        tts_a.arm_hang()
        tts_b.arm_hang()
        channel_a = VoiceChannel(handles.harness, handles.bus, tts_a, session_id="voice-A")
        channel_b = VoiceChannel(handles.harness, handles.bus, tts_b, session_id="voice-B")
        assert channel_a.session is not channel_b.session
        assert channel_a.presence is not channel_b.presence

        channel_a.speak("réponse A")
        channel_b.speak("réponse B")
        assert tts_a.is_speaking() and tts_b.is_speaking()

        handles.bus.publish(Event(type="interface.stop_requested", source="interfaces.cli", payload={}))
        handles.bus.wait_idle(timeout_s=1.0)

        assert tts_a.state() == TTSState.CANCELLED
        assert tts_b.state() == TTSState.CANCELLED
    finally:
        handles.shutdown()


def test_false_claim_without_tool_call_never_becomes_world_state_fact(tmp_path):
    """Consigne §33 "NO FALSE CLAIMS" appliqué au canal voix — même garantie
    Phase 3, revalidée ici via le canal voix spécifiquement."""
    handles, fake = build_test_harness(tmp_path, [_text_response("J'ai annulé la tâche comme demandé. ✅")])
    try:
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        channel.handle_final_transcript("annule la tâche")
        assert handles.harness.last_tool_trace("v1") == []
        assert handles.world_state.all() == []
    finally:
        handles.shutdown()


def test_presence_reflects_speaking_during_and_after_tts(tmp_path):
    handles, fake = build_test_harness(tmp_path, [_text_response("réponse")])
    try:
        tts = FakeTTS()
        channel = VoiceChannel(handles.harness, handles.bus, tts, session_id="v1")
        channel.handle_final_transcript("bonjour")
        # FakeTTS complète synchroniquement -> speaking déjà retombé à False ici
        assert channel.presence.snapshot().speaking is False
    finally:
        handles.shutdown()
