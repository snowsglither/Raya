"""Classification CONTEXTUELLE du risque (RAYA V2 Phase 11, §3 : "browser.click
peut être SAFE dans un contexte et SENSITIVE dans un autre"). Avant cette
phase, `browser.interact` (click/type/dismiss_overlay) était SENSITIVE de
façon inconditionnelle, rendant tout Computer Use multi-étapes (ex: Netflix —
choisir un profil, lancer une lecture) quasi inutilisable. La classification
est désormais fondée sur le CONTENU de l'action (verbe à conséquence
significative détecté dans les arguments texte), jamais sur un nom de
site/application en dur (§16 : interdiction explicite de `if clicked ==
netflix`)."""

from __future__ import annotations

import pytest

from raya.contracts import PermissionDecision, PermissionLevel
from raya.safety import AuditTrail, SafetyService, StopController
from raya.safety.risk import classify_risk


@pytest.mark.parametrize("target", [
    "le profil de Ruben",
    "Play",
    "Pause",
    "YouTube",
    "la page suivante",
    "la vidéo recommandée",
    "le bouton lecture",
])
def test_browser_interact_on_benign_content_is_safe(target):
    assert classify_risk(["browser.interact"], {"target": target}) == PermissionLevel.SAFE


@pytest.mark.parametrize("text", [
    "supprimer mon compte",
    "acheter ce produit",
    "envoyer le message",
    "publier ce post",
    "confirmer la commande",
    "se désabonner",
    "résilier l'abonnement",
    "delete this item",
    "buy now",
    "send message",
    "publish now",
    "confirm the order",
])
def test_browser_interact_mentioning_dangerous_verb_is_sensitive(text):
    assert classify_risk(["browser.interact"], {"target": text}) == PermissionLevel.SENSITIVE


def test_browser_interact_without_arguments_falls_back_to_static_sensitive_table():
    """`arguments=None` (jamais transmis) reste SENSITIVE par repli honnête —
    filet de sécurité pour un futur appelant qui oublierait de transmettre
    les arguments : mieux vaut sur-confirmer que bypasser silencieusement."""
    assert classify_risk(["browser.interact"]) == PermissionLevel.SENSITIVE
    assert classify_risk(["browser.interact"], None) == PermissionLevel.SENSITIVE


def test_browser_interact_with_empty_arguments_is_safe():
    assert classify_risk(["browser.interact"], {}) == PermissionLevel.SAFE


def test_word_boundary_prevents_false_positive_substring_match():
    """Un mot ne doit matcher un stem dangereux QUE s'il en commence
    réellement par lui — jamais une sous-chaîne en plein milieu du mot."""
    assert classify_risk(["browser.interact"], {"target": "affiche la page"}) == PermissionLevel.SAFE


def test_contextual_classification_never_depends_on_a_site_name():
    """Interdiction explicite de la consigne (§16) : jamais `if clicked ==
    netflix`. Même contenu bénin, quel que soit le nom du site -> SAFE."""
    for site in ("Netflix", "YouTube", "Gmail", "une-app-inconnue-xyz"):
        result = classify_risk(["browser.interact"], {"target": f"le bouton lecture sur {site}"})
        assert result == PermissionLevel.SAFE, f"site={site}"


def test_non_contextual_tags_are_unaffected_by_arguments():
    """Non-régression : un tag hors de `_CONTEXTUAL_TAGS` garde son niveau
    statique quel que soit ce que contiennent les arguments — la
    contextualisation reste scopée à `browser.interact` uniquement (décision
    documentée : pas de preuve concrète encore pour étendre à pc.interact)."""
    assert classify_risk(["pc.interact"], {"text": "supprimer"}) == PermissionLevel.SENSITIVE
    assert classify_risk(["pc.read"], {"text": "supprimer"}) == PermissionLevel.SAFE


def test_destructive_tag_still_wins_aggregation_over_contextual_safe():
    result = classify_risk(["filesystem.delete", "browser.interact"], {"target": "page"})
    assert result == PermissionLevel.DESTRUCTIVE


def test_notify_telegram_is_safe():
    """Phase 11 (addendum Telegram outbound) : SAFE par construction — le
    destinataire n'est jamais un paramètre du modèle, toujours résolu vers le
    seul chat_id déjà connu/autorisé (jamais un tiers)."""
    assert classify_risk(["notify.telegram"]) == PermissionLevel.SAFE


def test_check_permission_passes_arguments_through_for_contextual_classification():
    """Bout en bout SafetyService -> classify_risk : les arguments réels du
    ToolCall doivent influencer la décision de permission, pas seulement le
    tag."""
    safety = SafetyService(StopController(), AuditTrail())
    allowed = safety.check_permission(
        action_ref="browser.click", capability_tags=["browser.interact"], arguments={"target": "Play"},
    )
    assert allowed.decision == PermissionDecision.ALLOWED

    blocked = safety.check_permission(
        action_ref="browser.click", capability_tags=["browser.interact"],
        arguments={"target": "supprimer le compte"},
    )
    assert blocked.decision == PermissionDecision.REQUIRES_CONFIRMATION
