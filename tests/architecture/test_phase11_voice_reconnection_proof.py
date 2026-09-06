"""Preuve — réponse vocale reconnectée dans le Cockpit (RAYA V2 Phase 11,
addendum "micro fonctionne mais la réponse vocale n'est pas délivrée").

Cause : le mic Cockpit (Web Speech API, `window.SpeechRecognition`) alimentait
`sendMessage()` en ENTRÉE, mais rien ne parlait la réponse en RETOUR — pas de
`window.speechSynthesis`. Système séparé et sans rapport avec le Phase 5
serveur (Whisper/Kokoro, haut-parleurs physiques de la machine via
`sounddevice`) : ceci parle uniquement dans l'onglet navigateur qui a émis la
requête vocale, jamais pour un message tapé au clavier. Aucun runner JS dans
ce dépôt — mêmes assertions textuelles/structurelles que le proof du panneau
Conversation (`test_cockpit_conversation_panel_proof.py`)."""

from __future__ import annotations

import re
from pathlib import Path

_UI_STATIC = Path(__file__).resolve().parents[2] / "raya" / "interfaces" / "ui" / "static"


def _app_js() -> str:
    return (_UI_STATIC / "app.js").read_text(encoding="utf-8")


def _extract_function(source: str, name: str) -> str:
    match = re.search(rf"function {re.escape(name)}\s*\([^)]*\)\s*{{", source)
    assert match, f"fonction {name} introuvable dans app.js"
    start = match.end() - 1
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"accolade fermante jamais trouvée pour {name}")


def test_mic_result_triggers_send_message_with_voice_flag():
    source = _app_js()
    assert re.search(r'recognizer\.onresult\s*=.*sendMessage\(text,\s*\{\s*viaVoice:\s*true\s*\}\)', source, re.DOTALL)


def test_typed_messages_never_pass_the_voice_flag():
    """Un message tapé au clavier (bouton Envoyer, touche Entrée) ne doit
    jamais déclencher la synthèse vocale — seul le mic le fait."""
    source = _app_js()
    assert 'el.sendBtn.addEventListener("click", () => sendMessage(el.input.value));' in source
    assert 'sendMessage(el.input.value)' in source
    # Aucun de ces deux call sites clavier ne porte `viaVoice: true`.
    keyboard_calls = re.findall(r"sendMessage\(el\.input\.value[^)]*\)", source)
    assert keyboard_calls, "aucun call site clavier trouvé"
    assert all("viaVoice" not in c for c in keyboard_calls)


def test_send_message_speaks_the_reply_only_when_triggered_by_voice():
    body = _extract_function(_app_js(), "sendMessage")
    assert "viaVoice" in body
    assert "speakLastReply(view)" in body
    assert re.search(r"if\s*\(\s*viaVoice\s*\)\s*speakLastReply\(view\)", body)


def test_speak_last_reply_uses_native_browser_speech_synthesis_only():
    """Jamais un second moteur TTS applicatif — l'API navigateur native
    uniquement, avec dégradation honnête (pas d'erreur visible) si absente."""
    body = _extract_function(_app_js(), "speakLastReply")
    assert '"speechSynthesis" in window' in body
    assert "new SpeechSynthesisUtterance(" in body
    assert "window.speechSynthesis.speak(" in body


def test_speak_last_reply_only_speaks_the_assistant_role_message():
    body = _extract_function(_app_js(), "speakLastReply")
    assert 'last.role !== "raya"' in body


def test_voice_reconnection_never_touches_the_separate_phase5_server_voice_system():
    """Non-régression : ce fix reste 100% côté navigateur (app.js) — jamais
    une modification de `interfaces/voice/` (VoiceChannel/VoiceRuntime,
    Whisper/Kokoro serveur, Phase 5)."""
    voice_dir = Path(__file__).resolve().parents[2] / "raya" / "interfaces" / "voice"
    for path in voice_dir.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "speechSynthesis" not in source
        assert "SpeechRecognition" not in source
