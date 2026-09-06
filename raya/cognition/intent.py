"""Intent (Chantier 12 §D, Response vs Action) — distinction information vs
action DÉRIVÉE du comportement réellement observé pendant un tour (quels
Tools ont été appelés), jamais un classifieur de texte qui déciderait
AVANT/À LA PLACE du modèle. Le modèle reste seul décideur de CE QU'IL FAIT
(Harness exécute, invariant inchangé) — cette primitive rend la distinction
OBSERVABLE (télémétrie/tests), elle ne bloque et ne réoriente jamais un appel.

Réutilise la convention de tag déjà établie (`*.read` = lecture pure,
pc.read/browser.read/system.read...) plutôt qu'une liste de noms de tools
codée en dur — un nouveau Tool de lecture pure suit automatiquement cette
règle sans modification ici."""

from __future__ import annotations

import enum


class Intent(str, enum.Enum):
    INFORMATION = "information"
    ACTION = "action"


def derive_intent(capability_tags: list[str]) -> Intent:
    """INFORMATION si aucun Tool n'a été appelé, ou si tous les tags appelés
    sont des tags de lecture pure (suffixe `.read`) — ACTION dès qu'au moins
    un tag appelé a un effet réel sur l'environnement/l'état durable."""
    if not capability_tags:
        return Intent.INFORMATION
    if all(tag.endswith(".read") for tag in capability_tags):
        return Intent.INFORMATION
    return Intent.ACTION
