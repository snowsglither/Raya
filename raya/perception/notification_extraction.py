"""Extraction structurée du contenu d'une bannière de notification Windows
(Chantier 13G, bâti sur l'investigation Chantier 13F) — lecture UI
Automation directe (indépendante de `raya/devices/`, même principe que
`phone_sensors.py`/`windows_sensors.py` : `perception/` n'a pas le droit
d'importer `raya.devices`).

============================================================================
CE QUI A ÉTÉ RÉELLEMENT OBSERVÉ (Chantier 13F, appel entrant réel)
============================================================================

La bannière (`ShellExperienceHost.exe`, `Windows.UI.Core.CoreWindow`) expose
un arbre UI Automation stable :

    CoreWindow
     └─ Pane[ToastCenterScrollViewer]
         └─ Window[<ToastViewAutomationId>]   ex: "PriorityToastView", "NormalToastView"
             ├─ Text[SenderName]      -- catégorie/app, ex "Appels"/"Téléphone"
             ├─ Text[Title]           -- ex "Papa" (appel entrant) ou "Téléphone" (manqué)
             ├─ Text[MessageText]     -- ex "Appel entrant" ou "Papa\nAppel manqué"
             ├─ Text[Attribution]     -- ex "via Mobile connecté"
             ├─ Button[VerbButton] x N -- SEULEMENT présents sur un appel entrant
             │    └─ Text[VerbText]     ACTIF (Accepter/Message/Refuser observés) —
             │                          jamais utilisés pour agir, lecture seule (§12)
             ├─ Button[DismissButton]
             └─ Button[SettingsButton]

DISTINCTION STRUCTURELLE incoming vs missed (jamais textuelle, consigne §4) :
un appel entrant RÉEL observait `automation_id="PriorityToastView"` AVEC des
`VerbButton` descendants ; la notification "manqué" qui suit (après-coup)
observait `automation_id="NormalToastView"` SANS aucun `VerbButton`. C'est
cette combinaison (type de vue + présence de boutons d'action), jamais le
texte visible, qui sert de signal.

LIMITATION HONNÊTE : `Attribution`/`SenderName` restent des CHAÎNES DE TEXTE
(traduites, ex: "via Mobile connecté" en français). Aucune propriété UIA
structurelle indépendante de la langue permettant d'identifier l'APPLICATION
source (AUMID, PackageFamilyName) n'a été trouvée/vérifiée accessible cette
phase — elles sont extraites et exposées TELLES QUELLES (jamais filtrées
sur une valeur figée), jamais utilisées comme condition bloquante pour
décider si l'observation est publiée. Une autre app produisant un toast
avec un `VerbButton` + vue "Priority" (ex: un appel Teams) matcherait la
MÊME signature structurelle — non discriminé plus finement cette phase,
documenté comme limitation connue (voir rapport)."""

from __future__ import annotations

import time

_TOAST_VIEW_AUTOMATION_IDS = ("PriorityToastView", "NormalToastView")
_INCOMING_CALL_VIEW = "PriorityToastView"

_DEFAULT_RETRY_ATTEMPTS = 3
_DEFAULT_RETRY_DELAY_S = 0.3


def read_notification_content(hwnd: int, max_depth: int = 10, max_nodes: int = 300) -> dict | None:
    """Lit l'arbre UI Automation d'une CoreWindow de notification et en
    extrait les champs structurels connus (voir docstring du module).
    Retourne `None` si la fenêtre est illisible ; un dict avec des valeurs
    `None`/vides pour les champs non trouvés sinon (jamais une exception)."""
    try:
        import uiautomation as auto
    except ImportError:
        return None
    try:
        control = auto.ControlFromHandle(hwnd)
    except Exception:
        return None
    if control is None:
        return None

    fields = {
        "toast_view_type": None,
        "sender_name": None,
        "title": None,
        "message_text": None,
        "attribution": None,
        "has_action_buttons": False,
        "action_button_texts": [],
    }
    try:
        for child, _depth in auto.WalkControl(control, includeTop=False, maxDepth=max_depth):
            aid = getattr(child, "AutomationId", "") or ""
            name = getattr(child, "Name", "") or ""
            if aid in _TOAST_VIEW_AUTOMATION_IDS and fields["toast_view_type"] is None:
                fields["toast_view_type"] = aid
            elif aid == "SenderName" and fields["sender_name"] is None:
                fields["sender_name"] = name
            elif aid == "Title" and fields["title"] is None:
                fields["title"] = name
            elif aid == "MessageText" and fields["message_text"] is None:
                fields["message_text"] = name
            elif aid == "Attribution" and fields["attribution"] is None:
                fields["attribution"] = name
            elif aid == "VerbButton":
                fields["has_action_buttons"] = True
            elif aid == "VerbText" and name:
                fields["action_button_texts"].append(name)
    except Exception:
        return None
    return fields


def _content_is_stable(content: dict | None) -> bool:
    """Le contenu est jugé exploitable dès qu'un type de vue de toast a été
    identifié — c'est le SEUL champ garanti présent dès que la bannière
    réelle (pas juste le conteneur vide, cf. investigation Chantier 13F) est
    construite ; les autres champs peuvent légitimement rester vides selon
    le type de notification."""
    return bool(content and content.get("toast_view_type"))


def read_notification_content_with_retry(
    hwnd: int, attempts: int = _DEFAULT_RETRY_ATTEMPTS, delay_s: float = _DEFAULT_RETRY_DELAY_S,
    reader=read_notification_content, sleep=time.sleep,
) -> dict | None:
    """Stratégie de STABILISATION bornée (Chantier 13G §10) — PAS un
    polling périodique du téléphone : ce retry ne s'exécute QU'UNE FOIS,
    synchronement, immédiatement après un événement Windows déjà reçu (le
    hook a déjà signalé une activité réelle), jamais sur un minuteur
    indépendant. Investigation Chantier 13F : le tout premier événement
    d'une nouvelle notification peut arriver avant que son contenu ne soit
    construit ; le contenu devient stable en ~1 seconde. Borné (`attempts`),
    court (`delay_s` par défaut 0.3s), jamais une boucle infinie."""
    content = reader(hwnd)
    tries = 1
    while not _content_is_stable(content) and tries < attempts:
        sleep(delay_s)
        content = reader(hwnd)
        tries += 1
    return content


def classify_notification(content: dict | None) -> str:
    """Classification STRUCTURELLE (jamais textuelle, consigne §4) :
    - "incoming_call" : vue de toast "prioritaire" ET boutons d'action
      présents (Accepter/Refuser observés en conditions réelles) ;
    - "other" : un type de vue a été identifié mais ne correspond pas à un
      appel entrant en cours (ex: notification "manqué", vue "normale" sans
      bouton d'action) ;
    - "not_identified" : contenu absent/incomplet même après stabilisation
      — ne jamais deviner, ne jamais publier une observation à partir de ceci
      (consigne §11)."""
    if not _content_is_stable(content):
        return "not_identified"
    if content["toast_view_type"] == _INCOMING_CALL_VIEW and content["has_action_buttons"]:
        return "incoming_call"
    return "other"
