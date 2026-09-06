"""Language awareness (consigne Phase 5 — ajout "LANGUAGE AWARENESS").

RAYA est multilingue au niveau de l'interaction, sans jamais hardcoder une
langue dans le canal voix. `VoiceTurn` capture l'état linguistique d'UN
tour ; `decide_response_language()` est une fonction PURE (mêmes garanties
que `policy.py::decide_voice_response`) — aucune liste de phrases pour
DÉCIDER quoi faire, uniquement de la logique déterministe sur des signaux
déjà extraits (détection STT, préférence de session, demande explicite).

`detect_explicit_language_request()` est volontairement un détecteur
ÉTROIT et documenté (pas une compréhension d'intention façon Cognition) :
il reconnaît un petit ensemble de marqueurs linguistiques structurels
("in english"/"en français"/"in het nederlands"...), jamais une action
métier — analogue à un signal extrait par VAD/STT, pas une décision."""

from __future__ import annotations

from dataclasses import dataclass

_DEFAULT_FALLBACK_LANGUAGE = "en"
_DEFAULT_MIN_CONFIDENCE = 0.5

# Marqueurs explicites de demande de langue — volontairement un petit
# ensemble FERMÉ et documenté (limitation connue : ne comprend pas une
# formulation arbitraire, ex: "je préférerais que tu me répondes en
# italien la prochaine fois" ne sera pas reconnu ; seule une vraie
# compréhension via Cognition couvrirait ce cas, hors scope ici).
_EXPLICIT_LANGUAGE_MARKERS: dict[str, tuple[str, ...]] = {
    "fr": ("en français", "en francais", "réponds en français", "parle français", "answer in french", "in french"),
    "en": ("in english", "answer in english", "speak english", "respond in english", "en anglais", "réponds en anglais"),
    "nl": ("in het nederlands", "antwoord in het nederlands", "spreek nederlands", "in dutch", "answer in dutch", "en néerlandais"),
    "es": ("en español", "en espanol", "responde en español", "in spanish", "answer in spanish"),
}


@dataclass
class VoiceTurn:
    transcript: str
    detected_language: str | None
    language_confidence: float | None
    session_language: str | None
    response_language: str


def detect_explicit_language_request(text: str) -> str | None:
    lowered = (text or "").lower()
    for lang, markers in _EXPLICIT_LANGUAGE_MARKERS.items():
        if any(marker in lowered for marker in markers):
            return lang
    return None


def decide_response_language(
    *, detected_language: str | None, language_confidence: float | None, session_language: str | None,
    explicit_request: str | None = None, min_confidence: float = _DEFAULT_MIN_CONFIDENCE,
    fallback: str = _DEFAULT_FALLBACK_LANGUAGE,
) -> str:
    """response_language = detected_language PAR DÉFAUT, sauf :
    - demande explicite ("réponds en anglais") -> priorité absolue ;
    - détection trop incertaine (confidence < seuil ou absente) -> repli sur
      la préférence de session déjà connue, puis un fallback documenté."""
    if explicit_request:
        return explicit_request
    if detected_language and (language_confidence or 0.0) >= min_confidence:
        return detected_language
    if session_language:
        return session_language
    return fallback
