"""SpeechSynthesizer (consigne Phase 5 §10-11/§33) — speaking, completion,
cancellation, interruption. `FakeTTS` pour le contrôle déterministe ; tests
réels contre Kokoro (audio réellement synthétisé ET joué sur le vrai
haut-parleur) pour ne pas "déclarer le TTS fonctionnel parce qu'un mock
renvoie 'audio generated'" (consigne §31)."""

from __future__ import annotations

import threading
import time

import pytest

from raya.interfaces.voice.tts.base import FakeTTS, TTSState
from raya.interfaces.voice.tts.kokoro_adapter import KokoroTTSAdapter, kokoro_available


def test_fake_tts_idle_then_speaking_then_completed():
    tts = FakeTTS()
    assert tts.state() == TTSState.IDLE
    tts.speak("bonjour")
    assert tts.state() == TTSState.COMPLETED
    assert tts.spoken == ["bonjour"]


def test_fake_tts_on_complete_callback_receives_final_state():
    tts = FakeTTS()
    received = []
    tts.speak("bonjour", on_complete=received.append)
    assert received == [TTSState.COMPLETED]


def test_fake_tts_cancel_while_hanging_reaches_cancelled():
    tts = FakeTTS()
    tts.arm_hang()
    received = []
    tts.speak("phrase longue", on_complete=received.append)
    assert tts.is_speaking() is True
    tts.cancel()
    assert tts.state() == TTSState.CANCELLED
    assert received == [TTSState.CANCELLED]


def test_fake_tts_double_cancel_is_idempotent():
    """Consigne §20 : double cancellation ne doit jamais produire un
    deuxième effet/callback."""
    tts = FakeTTS()
    tts.arm_hang()
    received = []
    tts.speak("phrase", on_complete=received.append)
    tts.cancel()
    tts.cancel()
    assert tts.cancel_calls == 2
    assert received == [TTSState.CANCELLED]  # un seul callback, pas deux


def test_fake_tts_cancel_when_idle_is_a_safe_noop():
    tts = FakeTTS()
    tts.cancel()  # jamais parlé -> aucun effet, aucune exception
    assert tts.state() == TTSState.IDLE


def test_fake_tts_failure_reaches_error_state_honestly():
    tts = FakeTTS()
    tts.arm_failure()
    received = []
    tts.speak("phrase", on_complete=received.append)
    assert received == [TTSState.ERROR]
    assert tts.state() == TTSState.ERROR


@pytest.mark.skipif(not kokoro_available(), reason="BLOCKED: kokoro_onnx/modèle indisponible sur cette machine")
class TestRealKokoro:
    def test_real_speak_is_non_blocking_and_completes(self):
        """Consigne §10 : "Le TTS ne doit pas bloquer le Harness" — speak()
        doit rendre la main immédiatement, la lecture se termine plus tard
        sur un thread dédié."""
        tts = KokoroTTSAdapter()
        done = threading.Event()
        result = {}

        def on_complete(state):
            result["state"] = state
            done.set()

        t0 = time.monotonic()
        tts.speak("Bonjour.", on_complete=on_complete)
        call_duration = time.monotonic() - t0
        assert call_duration < 0.5  # retour quasi immédiat, pas d'attente de la synthèse complète
        assert tts.is_speaking() is True
        assert done.wait(timeout=15)
        assert result["state"] == TTSState.COMPLETED
        assert tts.is_speaking() is False

    def test_real_cancellation_latency_is_reasonable(self):
        """Mesure réelle (consigne §36), pas une fausse précision — mais on
        vérifie que l'annulation reste rapide (barge-in doit être réactif,
        consigne §9 : "NE PAS attendre la fin de la phrase")."""
        tts = KokoroTTSAdapter()
        done = threading.Event()
        result = {}

        def on_complete(state):
            result["state"] = state
            result["t_complete"] = time.monotonic()
            done.set()

        tts.speak("Ceci est une phrase suffisamment longue pour laisser le temps à une annulation réelle de se produire pendant la lecture.", on_complete=on_complete)
        time.sleep(1.0)  # laisse la lecture réellement démarrer
        t_cancel = time.monotonic()
        tts.cancel()
        assert done.wait(timeout=10)
        latency = result["t_complete"] - t_cancel
        assert result["state"] == TTSState.CANCELLED
        assert latency < 1.0  # largement sous la seconde — mesuré, pas supposé

    def test_real_double_cancel_is_idempotent_no_crash(self):
        tts = KokoroTTSAdapter()
        done = threading.Event()
        tts.speak("Une autre phrase de test pour l'annulation double.", on_complete=lambda s: done.set())
        time.sleep(0.5)
        tts.cancel()
        tts.cancel()  # ne doit jamais lever, jamais déclencher un second callback erroné
        assert done.wait(timeout=10)
        assert tts.state() == TTSState.CANCELLED
