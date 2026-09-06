"""Vérification (RAYA_V2_TECHNICAL_ARCHITECTURE.md §9, §14.3 ; consigne Phase 3
§9-11 "NO CLAIM WITHOUT EVIDENCE").

RÈGLE NON-NÉGOCIABLE : la SEULE source de vérité pour savoir si une action a
réussi est le `ToolResult` réel — jamais ce que le modèle affirme dans son
texte. Ce module ne lit JAMAIS le contenu texte d'une `ModelResponse` pour
décider d'un succès.
"""

from __future__ import annotations

import enum

from raya.contracts import ToolResult, ToolResultStatus


class VerificationOutcome(str, enum.Enum):
    SUCCESS = "SUCCESS"
    UNKNOWN = "UNKNOWN"
    FAILURE = "FAILURE"


def _normalize(value: object) -> str:
    return str(value).strip().lower()


def observation_matches_expectation(expected: object, observed: object) -> bool:
    """Comparaison GÉNÉRIQUE, tolérante, jamais spécifique à une application/
    URL en dur (consigne Phase 7 §13 "ne pas hard-coder des cas utilisateur") :
    correspondance par inclusion de sous-chaîne dans un sens ou l'autre, sans
    sensibilité à la casse — le même principe déjà utilisé par
    `devices/windows/mechanisms/window_mgmt.py::find_windows()` pour
    résoudre une fenêtre par titre/process approximatif, réappliqué ici pour
    comparer une observation post-action à l'intention d'origine (ex: le
    modèle demande 'target=calculatrice', la fenêtre réellement observée
    s'appelle 'Calculatrice' — pas une égalité stricte).
    Ni `expected` ni `observed` vide/`None` -> jamais une correspondance
    positive par défaut (évite un faux SUCCESS sur deux valeurs absentes)."""
    if expected is None or observed is None:
        return False
    e, o = _normalize(expected), _normalize(observed)
    if not e or not o:
        return False
    return e in o or o in e


def verify_observation_against_intent(expected: object, observed: object | None) -> VerificationOutcome:
    """PLAN → ACTION → OBSERVE → VERIFY, volet "l'état observé correspond-il
    à ce qui était attendu ?" (consigne Phase 7 §8-9) — jamais "le Tool n'a
    pas levé d'exception" seul comme preuve de succès. `observed=None`
    signifie qu'aucune observation post-action n'a pu être obtenue : reste
    UNKNOWN, jamais promu en SUCCESS ni en FAILURE par défaut."""
    if observed is None:
        return VerificationOutcome.UNKNOWN
    if observation_matches_expectation(expected, observed):
        return VerificationOutcome.SUCCESS
    return VerificationOutcome.FAILURE


def combine_outcomes(base: VerificationOutcome, content: VerificationOutcome | None) -> VerificationOutcome:
    """Combine le résultat d'exécution brut (`verify_tool_result`) et, s'il
    existe, le résultat de la vérification de contenu post-action — un
    ToolResult.status=SUCCESS dont l'état observé ne correspond PAS à
    l'intention n'est jamais un SUCCESS global (consigne Phase 7 §8 :
    "Ne jamais transformer UNCERTAIN en SUCCESS", et par extension jamais un
    FAILURE de contenu en SUCCESS global)."""
    if content is None:
        return base
    if base != VerificationOutcome.SUCCESS:
        return base  # un échec d'exécution reste un échec, quel que soit le contenu
    if content == VerificationOutcome.SUCCESS:
        return VerificationOutcome.SUCCESS
    return content  # FAILURE ou UNKNOWN de contenu prime sur un SUCCESS d'exécution seul


def verify_tool_result(result: ToolResult) -> VerificationOutcome:
    """PLAN → ACTION → OBSERVE → VERIFY (consigne §11). `result` EST
    l'observation — un ToolResult.status=success représente l'exécution
    réelle de l'outil, jamais une intention ou une déclaration du modèle."""
    if result.status == ToolResultStatus.SUCCESS:
        return VerificationOutcome.SUCCESS
    if result.status in (ToolResultStatus.FAILURE, ToolResultStatus.PERMISSION_DENIED):
        return VerificationOutcome.FAILURE
    # TIMEOUT / CANCELLED : l'effet réel est indéterminé, jamais présumé.
    return VerificationOutcome.UNKNOWN


def has_evidence(result: ToolResult) -> bool:
    """Un succès sans `evidence` reste un succès du point de vue du Tool
    System (l'outil a fait ce qu'on lui a demandé), mais cette fonction
    distingue explicitement les cas où une preuve tangible existe — utile
    pour des décisions Cognition plus prudentes sur des actions sensibles."""
    return result.status == ToolResultStatus.SUCCESS and bool(result.evidence)
