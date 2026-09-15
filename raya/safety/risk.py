"""Classification de risque (RAYA_V2_TECHNICAL_ARCHITECTURE.md §10.1-10.2).

Généralise ce que V1 avait localement dans modules/pc_control/engine.py::_RISK
à TOUTE capacité passant par tools/, pas seulement PC (RAYA_V2_MIGRATION_MAP.md #53).
Phase 0 : table minimale par capability_tag, extensible sans changer le contrat.

Phase 11 (consigne §3, "browser.click peut être SAFE dans un contexte et
SENSITIVE dans un autre") : `browser.interact` (click/type/dismiss_overlay)
était classé SENSITIVE INCONDITIONNELLEMENT, rendant tout Computer Use
multi-étapes (ex: naviguer sur Netflix, choisir un profil, lancer une
lecture) quasi inutilisable — chaque clic exigeait une confirmation, quel
que soit ce qui était réellement cliqué. La classification est désormais
CONTEXTUELLE pour ce tag précis : SAFE par défaut, SENSITIVE seulement si
la description de l'action (l'argument `target`/`text` fourni par le
modèle) mentionne un verbe à conséquence réellement significative/
irréversible (acheter, supprimer, envoyer, publier, etc.) — DÉTECTÉ SUR LE
CONTENU DE L'ACTION elle-même, jamais sur un nom de site/application en dur
(interdit explicitement par la consigne : pas de `if clicked == netflix`).

Passe "Targeted Execution Repair" (preuve concrète : "ouvre la calculatrice
et fais 12492384 × 2746 / 25" exigeait une confirmation à CHAQUE clic sur un
chiffre) : `pc.interact` (keyboard.*, mouse.*, ui.click, ui.type, window.close,
application.close) rejoint `_CONTEXTUAL_TAGS` avec la MÊME logique de
contenu — MAIS un repli différent quand aucun texte n'est trouvé dans les
arguments (voir `_CONTEXTUAL_NO_TEXT_DEFAULT` ci-dessous) : `pc.mouse.click`/
`pc.mouse.move` ne portent que des coordonnées brutes, sans AUCUNE
description sémantique de leur cible (contrairement à `browser.dismiss_overlay`
sans arguments, dont l'action reste intrinsèquement bornée à "fermer un
overlay") — un clic aveugle sur un pixel reste donc SENSITIVE par défaut,
jamais reclassé SAFE faute d'information. Le scan de contenu est désormais
RÉCURSIF (`_extract_text_values`) : `pc.ui.click`/`pc.ui.type` portent leur
cible dans `selector` (dict imbriqué, ex: `{"name": "7"}`), pas dans une
simple chaîne de premier niveau comme `browser.click.target`."""

from __future__ import annotations

import re

from raya.contracts import PermissionLevel

# Tags dont le risque dépend du CONTENU de l'action (target/text/selector
# fournis par le modèle), pas seulement du tag lui-même — voir
# `classify_risk()`. La valeur associée est le niveau appliqué quand AUCUN
# texte n'est trouvé dans les arguments (ex: dismiss_overlay sans argument,
# ou un clic souris en coordonnées pures) — le risque réel diffère selon
# le tag dans ce cas précis, voir docstring du module.
_CONTEXTUAL_TAGS: dict[str, PermissionLevel] = {
    "browser.interact": PermissionLevel.SAFE,
    "pc.interact": PermissionLevel.SENSITIVE,
}

# Verbes à conséquence réellement significative/irréversible — GÉNÉRIQUE,
# jamais un nom de site/application. Détecté comme MOT ENTIER (ou préfixe de
# conjugaison courant en français) pour éviter les faux positifs de
# sous-chaîne (ex: "play" ne doit jamais matcher "display").
_DANGEROUS_ACTION_STEMS = (
    "delet", "supprim", "effac", "remov",
    "buy", "achet", "purchas", "commande", "checkout", "order", "payer", "paiement", "payment",
    "send", "envoi", "envoy", "publi", "post",
    "confirm", "valid", "submit",
    "uninstall", "désinstall", "desinstall",
    "unsubscri", "désabonn", "desabonn", "résili", "resili",
    "transfer", "virement", "retrait", "withdraw",
)
_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

# Défaut prudent : une capability inconnue est traitée comme "sensitive"
# (jamais "safe" par défaut) tant qu'elle n'est pas explicitement classée.
_DEFAULT_UNKNOWN_RISK = PermissionLevel.SENSITIVE

_RISK_BY_TAG: dict[str, PermissionLevel] = {
    "utils": PermissionLevel.SAFE,
    "notes": PermissionLevel.SAFE,
    "documents.read": PermissionLevel.SAFE,
    "documents.write": PermissionLevel.SENSITIVE,
    "browser": PermissionLevel.SENSITIVE,
    "pc": PermissionLevel.SENSITIVE,
    "pc.uninstall": PermissionLevel.DESTRUCTIVE,
    "filesystem.delete": PermissionLevel.DESTRUCTIVE,
    "mail.send": PermissionLevel.SENSITIVE,
    # Outils de démonstration Phase 3 (raya/tools/catalog/demo.py) :
    # "filesystem" y désigne des lectures/écritures STRICTEMENT sandboxées
    # dans config.tool_workspace_dir (chemin jamais accessible hors de ce
    # dossier, RAYA_V2_TECHNICAL_ARCHITECTURE.md §14 — `_resolve_safe_path`
    # refuse toute évasion). Risque réel faible -> SAFE. Un futur vrai Tool
    # filesystem non sandboxé (Phase 4+) devra être classé séparément,
    # explicitement SENSITIVE/DESTRUCTIVE selon la portée réelle.
    "filesystem": PermissionLevel.SAFE,
    "demo": PermissionLevel.SENSITIVE,
    # Device Agents Phase 4 (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.13) : lecture/
    # observation/navigation réversible -> SAFE ; interaction mutante (clic,
    # saisie, fermeture de fenêtre) -> SENSITIVE. Granularité par tag, pas par
    # device entier (cf. "documents.read" vs "documents.write" ci-dessus).
    "pc.read": PermissionLevel.SAFE,       # window.list, application.list, screen.capture, ui.inspect, process.list
    # AJOUTÉ passe "Targeted Execution Repair" : "pc.interact" (keyboard.*,
    # mouse.*, ui.click, ui.type, window.close, application.close) n'est plus
    # SENSITIVE inconditionnellement — voir _CONTEXTUAL_TAGS/classify_risk()
    # ci-dessous. Cette valeur reste le REPLI (jamais atteinte tant que
    # `arguments` est fourni ; sert de filet si un futur appelant oublie de
    # les transmettre — mieux vaut alors sur-confirmer que bypasser).
    "pc.interact": PermissionLevel.SENSITIVE,
    "pc.launch": PermissionLevel.SAFE,     # application.launch/focus, window.focus — réversible, non destructif
    "browser.read": PermissionLevel.SAFE,      # navigate, read_page, screenshot, list_tabs — visiter une URL n'est pas dangereux en soi
    # AJOUTÉ Phase 11 : "browser.interact" (click/type/dismiss_overlay) n'est
    # plus classé SENSITIVE de façon inconditionnelle — voir _CONTEXTUAL_TAGS/
    # classify_risk() ci-dessous. Cette valeur reste le REPLI (jamais atteinte
    # tant que `arguments` est fourni ; sert de filet si un futur appelant
    # oublie de les transmettre — mieux vaut alors sur-confirmer que bypasser).
    "browser.interact": PermissionLevel.SENSITIVE,
    # Steering de tâches Phase 5 (tools/catalog/tasks.py) : pause/resume sont
    # pleinement réversibles (SAFE) ; cancel s'arrêter SUR SA PROPRE tâche
    # est, en pratique, un "stop" scopé — cohérent avec le fait que le STOP
    # global lui-même (safety.request_stop) n'exige AUCUNE confirmation
    # nulle part dans le système (c'est le mécanisme le plus prioritaire,
    # jamais gated) ; classer tasks.cancel SENSITIVE aurait été inconsistant
    # avec ce principe déjà établi. steer (changer CE QUE fait la tâche,
    # potentiellement son résultat) reste SENSITIVE — décision qualitativement
    # différente d'un simple arrêt.
    "tasks.control": PermissionLevel.SAFE,      # tasks.pause / tasks.resume / tasks.cancel
    "tasks.modify": PermissionLevel.SENSITIVE,  # tasks.steer
    # Chantier 14 (Natural Language Tasks) : lecture pure de la liste des
    # tâches — aucune mutation possible, même famille que les autres tags
    # "*.read" déjà établis (pc.read/browser.read/system.read/phone.read).
    "tasks.read": PermissionLevel.SAFE,         # tasks.list
    # Adaptive UI Phase 6 (tools/catalog/ui_views.py) : ui.show_view/hide_view
    # ne mutent jamais World State/Task, uniquement un event présentationnel
    # consommé par le Cockpit — aucune conséquence réelle sur le monde.
    "ui.presentation": PermissionLevel.SAFE,
    # Creative/Spatial Agent Phase 8 (tools/catalog/spatial.py) : mutations
    # d'une scène en mémoire, jamais un effet sur l'environnement réel
    # (contrairement à pc.*/browser.*) — SAFE pour toutes les capacités.
    "spatial.create": PermissionLevel.SAFE,
    "spatial.modify": PermissionLevel.SAFE,
    "spatial.read": PermissionLevel.SAFE,
    "spatial.render": PermissionLevel.SAFE,
    "spatial.export": PermissionLevel.SAFE,
    # Capacité d'envoi proactif Phase 11 (addendum Telegram outbound,
    # tools/catalog/notify.py) : SAFE (contrairement à "mail.send" ci-dessus)
    # car le DESTINATAIRE n'est jamais un paramètre du modèle — il est résolu
    # côté injection vers le SEUL chat_id Telegram déjà connu/autorisé
    # (le téléphone du propriétaire, cf. TelegramAuthorizer/allowlist Phase
    # 9). Le pire cas possible est donc "s'envoyer un message à soi-même",
    # jamais un message à un tiers — qualitativement différent de mail.send
    # qui peut atteindre n'importe quelle adresse fournie par le modèle.
    "notify.telegram": PermissionLevel.SAFE,
    # Chantier 12 §A (Temporal) : lecture pure de l'horloge système, aucune
    # conséquence possible — SAFE inconditionnellement, comme les autres tags
    # "*.read" déjà établis (pc.read, browser.read).
    "system.read": PermissionLevel.SAFE,
    # Chantier 12 §E (Channel defaults) : écrit une préférence DURABLE en
    # Memory (jamais une action sur l'environnement réel) — réversible via
    # `MemoryStore.correct()` comme toute autre préférence, SAFE comme
    # "notes"/"utils" ci-dessus.
    "preferences.write": PermissionLevel.SAFE,
    # Chantier 13 (Phone Integration MVP) : initier un appel/envoyer un SMS/
    # répondre à un appel a des conséquences réelles sur un tiers (dérangé,
    # engagé dans une conversation, reçoit un message) -> SENSITIVE
    # inconditionnellement, jamais contextuel (contrairement à pc.interact) —
    # CHAQUE appel/SMS est consequent, pas seulement certains contenus.
    "phone.call": PermissionLevel.SENSITIVE,
    "phone.sms": PermissionLevel.SENSITIVE,
    "phone.answer": PermissionLevel.SENSITIVE,
    # Chantier 20 (External Interaction Continuity) : écriture en World State
    # uniquement (domain="interaction"), jamais un effet sur l'environnement
    # réel. Même raisonnement que "preferences.write"/"spatial.*"/"notes" —
    # local, réversible (TTL 24h), pas de tiers directement contacté.
    "interaction.track": PermissionLevel.SAFE,
    "interaction.reply": PermissionLevel.SAFE,
    # Terminer/refuser un appel déjà en cours reste fondamentalement un
    # "arrêt" scopé (même raisonnement que tasks.control ci-dessus) — jamais
    # une nouvelle conséquence créée, juste l'arrêt d'une déjà en cours.
    "phone.control": PermissionLevel.SAFE,
    # Lecture seule (état d'appel/connexion, recherche de contact) — jamais
    # d'effet sur l'environnement réel, même famille que pc.read/browser.read.
    "phone.read": PermissionLevel.SAFE,
    # Chantier 16 (Capability Discovery / CLI vs GUI) : exécution d'une
    # commande shell arbitraire — SENSITIVE INCONDITIONNELLEMENT, jamais
    # contextuel comme pc.interact/browser.interact. Une commande CLI peut
    # faire strictement n'importe quoi (contrairement à un clic UI borné à
    # une cible visible) ; classer ce tag SAFE par défaut serait un vrai
    # contournement de Safety pour la capability la plus puissante du
    # système. La découverte (capability.discover) reste séparément SAFE
    # (tag "pc.read", lecture pure — voir tools/catalog/pc.py).
    "pc.shell": PermissionLevel.SENSITIVE,
    # Chantier 1 (Software Environment Awareness) : lecture seule — découverte
    # d'applications installées, inspection de chemins, recherche de paquets.
    # Même profil de risque que pc.read/browser.read : jamais une mutation
    # de l'environnement réel, jamais une exécution.
    "pc.software": PermissionLevel.SAFE,
    # Obsidian vault read-only tools — same risk profile as browser.read/documents.read:
    # reads local .md files within a sandboxed vault directory, no mutation possible.
    "obsidian.read": PermissionLevel.SAFE,
    # Vision Foundation : capture + analyse visuelle locale (screenshot + modèle Vision).
    # Même profil de risque que pc.read/browser.read — lecture pure de l'état
    # visuel, jamais une mutation de l'environnement réel.
    "vision": PermissionLevel.SAFE,
}


def _extract_text_values(value: object) -> list[str]:
    """Extrait RÉCURSIVEMENT toutes les valeurs texte d'une structure
    d'arguments (dict/list imbriqués) — ex: `pc.ui.click` porte sa cible dans
    `selector` (`{"name": "7", "automation_id": "num7Button"}`), pas dans une
    simple chaîne de premier niveau comme `browser.click.target`. Un scan
    limité au premier niveau laisserait passer un verbe dangereux caché dans
    un champ imbriqué."""
    texts: list[str] = []
    if isinstance(value, str):
        texts.append(value)
    elif isinstance(value, dict):
        for v in value.values():
            texts.extend(_extract_text_values(v))
    elif isinstance(value, (list, tuple)):
        for v in value:
            texts.extend(_extract_text_values(v))
    return texts


def _mentions_dangerous_action(arguments: dict) -> bool:
    """Scanne les valeurs texte des arguments (ex: `target`, `text`,
    `selector.name`) pour un verbe à conséquence significative — jamais un
    nom de site/application. Correspondance par MOT (ou préfixe de
    conjugaison), pas sous-chaîne brute (évite qu'un mot innocent contenant
    la sous-chaîne matche par accident)."""
    words: list[str] = []
    for text in _extract_text_values(arguments):
        words.extend(w.lower() for w in _WORD_RE.findall(text))
    return any(word.startswith(stem) for word in words for stem in _DANGEROUS_ACTION_STEMS)


def classify_risk(capability_tags: list[str], arguments: dict | None = None) -> PermissionLevel:
    """Le tag le plus risqué de la liste détermine le niveau global de
    l'action. `arguments` (Phase 11, additif — `None` préserve le
    comportement précédent) permet une classification CONTEXTUELLE pour les
    tags de `_CONTEXTUAL_TAGS` : le même outil (ex: browser.click) peut être
    SAFE ou SENSITIVE selon CE QUI est réellement demandé, jamais selon un
    site/application codé en dur. Quand AUCUN texte n'est trouvé dans les
    arguments, le repli est SPÉCIFIQUE AU TAG (`_CONTEXTUAL_TAGS[tag]`) —
    un clic aveugle sur coordonnées (`pc.interact`) n'a aucune description
    sémantique et reste prudent (SENSITIVE), alors qu'un `dismiss_overlay`
    sans argument (`browser.interact`) reste une action intrinsèquement
    bornée (SAFE, décision Phase 11 déjà validée)."""
    levels = []
    for tag in capability_tags:
        base = _RISK_BY_TAG.get(tag, _DEFAULT_UNKNOWN_RISK)
        if tag in _CONTEXTUAL_TAGS and arguments is not None:
            texts = _extract_text_values(arguments)
            if not texts:
                base = _CONTEXTUAL_TAGS[tag]
            else:
                base = PermissionLevel.SENSITIVE if _mentions_dangerous_action(arguments) else PermissionLevel.SAFE
        levels.append(base)
    if PermissionLevel.DESTRUCTIVE in levels:
        return PermissionLevel.DESTRUCTIVE
    if PermissionLevel.SENSITIVE in levels or not levels:
        return PermissionLevel.SENSITIVE if levels else _DEFAULT_UNKNOWN_RISK
    return PermissionLevel.SAFE
