"""VoiceChannel — LE client mince du Harness pour le canal voix (consigne
Phase 5, RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.14 : "clients minces du
Core"). Traduit une transcription finale en `HarnessRequest`, appelle
`harness.handle_request()` (jamais `cognition`/`tools`/`devices`/`models`
directement — invariant déjà vérifié par le lint pour `interfaces/`), décide
SPEAK/TEXT_ONLY/SILENT via `VoiceResponsePolicy`, déclenche la synthèse.

STOP suit EXACTEMENT le chemin déjà établi (Phase 0, CLI) : publication d'un
`Event(type="interface.stop_requested")` sur l'EventBus — jamais un appel
direct à `safety.request_stop()` (consigne §18, aucun `voice.stop_all()`)."""

from __future__ import annotations

from raya.contracts import Channel, Event, HarnessRequest, HarnessStatus, InterfaceInput
from raya.event_bus import EventBus
from raya.harness import Harness

from .events import publish_voice_event
from .language import VoiceTurn, decide_response_language, detect_explicit_language_request
from .policy import VoiceResponseContext, VoiceResponseDecision, decide_voice_response
from .presence import PresenceTracker
from .session import VoiceSession
from .tts.base import SpeechSynthesizer, TTSState


class VoiceChannel:
    def __init__(self, harness: Harness, bus: EventBus, synthesizer: SpeechSynthesizer,
                 session_id: str = "voice-default") -> None:
        self._harness = harness
        self._bus = bus
        self._tts = synthesizer
        self.session = VoiceSession(session_id=session_id)
        self.presence = PresenceTracker(session_id=session_id)
        self.last_turn: VoiceTurn | None = None
        bus.subscribe("task.*", self.presence.on_event, subscriber=f"voice.presence.{session_id}")
        bus.subscribe("attention.decision_made", self.presence.on_event, subscriber=f"voice.presence.{session_id}")
        # STOP peut être déclenché par N'IMPORTE QUEL canal (consigne §18) —
        # une synthèse en cours sur CE canal voix doit s'arrêter même si ce
        # n'est pas ce canal qui a demandé le STOP. `cancel()` est idempotent
        # (§20) : aucun risque de double-effet avec `request_stop()` ci-dessous.
        bus.subscribe("interface.stop_requested", self._on_global_stop, subscriber=f"voice.stop.{session_id}")

    def _on_global_stop(self, event: Event) -> None:
        if self._tts.is_speaking():
            self._tts.cancel()

    @property
    def bus(self) -> EventBus:
        return self._bus

    def tts_is_speaking(self) -> bool:
        return self._tts.is_speaking()

    # ------------------------------------------------------------------
    # Transcription finale -> Harness (SEUL point d'entrée cognitif)
    # ------------------------------------------------------------------

    def handle_final_transcript(self, text: str, *, confidence: float | None = None, language: str | None = None) -> HarnessStatus:
        """Une transcription PARTIELLE n'arrive jamais ici (consigne §5) —
        uniquement le texte final validé par le VAD (SPEECH_END) + STT."""
        self.session.new_turn()
        publish_voice_event(self._bus, "voice.speech_final", self.session.session_id,
                             correlation_id=self.session.correlation_id, text=text, confidence=confidence,
                             language=language, is_final=True)

        # Décision de langue de RÉPONSE (§LANGUAGE AWARENESS) — AVANT le tour
        # Harness : jamais recalculée après coup, jamais devinée par le TTS.
        explicit_request = detect_explicit_language_request(text)
        response_language = decide_response_language(
            detected_language=language, language_confidence=confidence,
            session_language=self.session.session_language, explicit_request=explicit_request,
        )
        # Un changement de langue reste un attribut de contexte — même
        # session, pas de nouvelle session/tâche (consigne).
        self.session.set_session_language(response_language)
        self.last_turn = VoiceTurn(transcript=text, detected_language=language, language_confidence=confidence,
                                    session_language=response_language, response_language=response_language)

        self._bus.publish(Event(type="interface.request_received", source="interfaces.voice",
                                 payload={"session_id": self.session.session_id, "channel": "voice"}))

        request = HarnessRequest(channel=Channel.VOICE, session_id=self.session.session_id,
                                  input=InterfaceInput(text=text))
        state = self._harness.handle_request(request)
        response_text = self._harness.response_text(self.session.session_id)

        # Une réponse DIRECTE à une question posée reste toujours PROCESS_NOW
        # (règle déjà établie RAYA_V2_TECHNICAL_ARCHITECTURE.md/harness/loop.py :
        # "conversation != task execution... toujours PROCESS_NOW pour une
        # requête directe") — jamais la DERNIÈRE décision Attention observée
        # sur le bus, qui concerne le plus souvent un event de tâche de fond
        # sans rapport (ex: IGNORE sur task.created) et suppprimerait à tort
        # une réponse à une question pourtant explicitement posée (bug réel
        # trouvé pendant cette phase, voir rapport §Bugs). `announce()`
        # ci-dessous, elle, utilise bien la présence pour une narration NON
        # sollicitée d'un résultat de tâche de fond.
        decision = self._decide_response(has_text=bool(response_text), attention_decision="PROCESS_NOW")
        if decision == VoiceResponseDecision.SPEAK:
            self.speak(response_text, language=response_language)
        publish_voice_event(self._bus, "voice.response_completed", self.session.session_id,
                             correlation_id=self.session.correlation_id, text=response_text,
                             language=response_language,
                             detail={"decision": decision.value, "status": state.status.value})
        return state.status

    def announce(self, text: str, *, importance: float = 0.0, language: str | None = None) -> VoiceResponseDecision:
        """Narration NON sollicitée (ex: résultat d'une tâche de fond) —
        contrairement à `handle_final_transcript`, utilise réellement la
        dernière décision Attention connue (consigne §12 : "si Attention =
        IGNORE, aucune réponse vocale"). Jamais appelée pour une réponse
        directe à une question de l'utilisateur."""
        decision = self._decide_response(has_text=bool(text), attention_decision=self.presence.snapshot().attention_state,
                                          importance=importance)
        if decision == VoiceResponseDecision.SPEAK:
            self.speak(text, language=language)
        return decision

    def _decide_response(self, *, has_text: bool, attention_decision: str | None, importance: float = 0.0) -> VoiceResponseDecision:
        presence = self.presence.snapshot()
        ctx = VoiceResponseContext(
            has_response_text=has_text,
            attention_decision=attention_decision,
            user_is_focus=True,
            tts_capable=True,
            interrupted_by_user=presence.state.value == "interrupted",
            importance=importance,
        )
        return decide_voice_response(ctx)

    # ------------------------------------------------------------------
    # TTS — jamais bloquant pour le Harness (consigne §10)
    # ------------------------------------------------------------------

    def speak(self, text: str, *, language: str | None = None) -> None:
        if not text:
            return
        language = language or self.session.session_language
        publish_voice_event(self._bus, "voice.tts_started", self.session.session_id,
                             correlation_id=self.session.correlation_id, text=text, language=language)
        self.presence.set_speaking(True)
        self.session.set_tts_state(TTSState.SPEAKING)

        def _on_complete(state: TTSState) -> None:
            self.presence.set_speaking(False)
            self.session.set_tts_state(state)
            event_type = "voice.tts_completed" if state == TTSState.COMPLETED else (
                "voice.tts_cancelled" if state == TTSState.CANCELLED else "voice.tts_error")
            publish_voice_event(self._bus, event_type, self.session.session_id, correlation_id=self.session.correlation_id,
                                 language=getattr(self._tts, "last_language_used", language))

        self._tts.speak(text, language=language, on_complete=_on_complete)

    def barge_in(self) -> None:
        """Interruption LOCALE de la synthèse en cours — mécanique, pas une
        décision cognitive (consigne §9) : n'importe quelle parole détectée
        pendant que RAYA parle interrompt toujours la synthèse, sans passer
        par Safety/Harness (ça n'annule JAMAIS une tâche de fond, consigne
        §16 — seule la synthèse en cours est concernée)."""
        if not self._tts.is_speaking():
            return
        publish_voice_event(self._bus, "voice.tts_cancel_requested", self.session.session_id,
                             correlation_id=self.session.correlation_id)
        self._tts.cancel()
        self.presence.set_interrupted(True)
        publish_voice_event(self._bus, "voice.barge_in", self.session.session_id, correlation_id=self.session.correlation_id)

    # ------------------------------------------------------------------
    # STOP global — Event -> EventBus -> Safety (jamais d'appel direct, §18)
    # ------------------------------------------------------------------

    def request_stop(self) -> None:
        self._tts.cancel()  # arrêt immédiat de CE qui est sous le contrôle direct du canal voix
        self._bus.publish(Event(type="interface.stop_requested", source="interfaces.voice",
                                 payload={"session_id": self.session.session_id}))
