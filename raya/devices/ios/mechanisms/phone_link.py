"""Mécanisme Phone Link (Microsoft.YourPhone / PhoneExperienceHost.exe) —
UI Automation générique (AutomationId/ControlType), jamais des coordonnées
fixes ni un titre de fenêtre localisé en dur (résolution par NOM DE PROCESS +
CLASSE, insensible à la langue/l'état de connexion — consigne Chantier 13
§17). Réutilise EXACTEMENT les mêmes primitives que le Windows Device Agent
(raya/devices/windows/mechanisms/{uia,window_mgmt,keyboard,mouse,
applications}.py, raya/devices/windows/strategy.py) — aucune duplication de
logique UIA/fenêtre, seulement la connaissance des AutomationId propres à
Phone Link. Import intra-`devices/` (autorisé par le graphe de dépendance :
`devices/ios/` -> `devices/windows/` reste le MÊME subsystem top-level).

============================================================================
INVESTIGATION RÉELLE (Chantier 13, machine de développement, iPhone lié via
Bluetooth + Wi-Fi, permissions Contacts et SMS accordées) — tout ce qui suit
a été CONFIRMÉ par inspection UIA directe et/ou une action réelle (appels
réels vers 2 contacts consentants, SMS réel envoyé+reçu+répondu), jamais
supposé.
============================================================================

RÉSOLUTION DE FENÊTRE — PIÈGE RÉEL DÉCOUVERT : Phone Link expose DEUX
fenêtres top-level sous le même process ("PhoneExperienceHost.exe") : la
fenêtre principale (class_name="WinUIDesktopWin32WindowClass") ET une
fenêtre pont ("Hôte contextuel" / class_name="Microsoft.UI.Content.
PopupWindowSiteBridge") qui ne contient qu'un tooltip transitoire. Une
résolution par process seul est donc AMBIGUË et peut silencieusement cibler
la mauvaise fenêtre (observé : `read_elements` ne retournait que 2 éléments
d'un tooltip). Résolu en ajoutant `match_class="WinUIDesktopWin32WindowClass"`
à CHAQUE résolution de fenêtre — jamais uniquement le nom de process.

INVOKE vs CLIC RÉEL — COMPORTEMENT RÉEL DÉCOUVERT : le pattern UIA `Invoke`
(`uia.invoke`/`strategy.click_element`) rapporte `invoked: True` sur
`ButtonCall` SANS déclencher l'appel réel côté téléphone (vérifié deux fois :
aucune sonnerie côté destinataire malgré un retour "succès"). Un VRAI clic
souris (`SetCursorPos` + `mouse_event`), envoyé UNIQUEMENT après avoir
explicitement mis Phone Link au premier plan (`window_mgmt.focus_window`,
vérifié via `get_active_window()`), déclenche l'appel réel de façon fiable —
probablement une protection anti-automation délibérée sur cette action
précise. Par prudence et cohérence, les 3 actions à conséquence réelle
(`ButtonCall`, `EndCallButton`, `SendMessageButton`) utilisent TOUTES le
clic réel focus-vérifié ; les actions de navigation/sélection (onglets,
suggestion de contact) utilisent `strategy.click_element` (Invoke d'abord),
qui s'est montré fiable pour celles-ci.

CONFIRMATION DE NUMÉRO — COMPORTEMENT RÉEL DÉCOUVERT : `uia.set_value()`
(ValuePattern.SetValue) sur le champ de saisie NE SUFFIT PAS à activer
`ButtonCall` — un appui Entrée explicite est nécessaire après la saisie pour
que Phone Link reconnaisse le numéro comme composable.

AutomationId CONFIRMÉS (Calls) :
  NavView    : CallingNodeAutomationId (onglet Appels), ChatNodeAutomationId (onglet Messages)
  Dialer     : TextBox (recherche contact/numéro, name="Rechercher dans vos contacts"), ButtonCall
  Historique : CallLogsGrid / CallHistory (list), CallLogsDisplayName, CallLogTime
  En appel   : EndCallButton (name="Raccrocher"), MuteButton (ToggleButton, "Mise en sourdine")
               — widget flottant séparé ("Appel sur PC"/"Hôte contextuel"), pas dans la fenêtre principale
  Permission : ErrorTakeoverPanel / CallLogsErrorActionButton (contacts/SMS non autorisés côté iPhone)
AutomationId CONFIRMÉS (Messages) :
  Compose    : NewMessageButton, TextBox (name="À", destinataire), InputTextBox (corps), SendMessageButton
  Liste      : CVSListView, listitem par conversation (nom = "Conversation avec X. Aperçu du message. ...")
  Contenu    : MessageBody (texte), un listitem par message ("Message de vous."/"Message de <Nom>.")

NON CONFIRMÉ CETTE PHASE : l'écran D'APPEL ENTRANT (avant décroché) n'a pas
pu être observé en conditions réelles (les deux tentatives d'appel entrant
de test se sont résolues — répondu ailleurs / manqué — avant capture de
l'état "sonne"). `phone.answer`/`phone.reject` sont donc implémentés sur la
base du MÊME widget d'appel que `phone.end` (candidats AutomationId
raisonnables `AcceptButton`/`DeclineButton`/`AnswerButton`/`RejectButton`,
jamais confirmés) — voir `answer_call`/`reject_call` ci-dessous, marqués
BLOCKED/best-effort dans le rapport, jamais présentés comme validés.
"""

from __future__ import annotations

import time

from raya.devices.windows import strategy
from raya.devices.windows.mechanisms import applications, keyboard, mouse, uia, window_mgmt

# Résolution par NOM DE PROCESS + CLASSE (jamais un titre affiché, localisé
# et variable selon l'état de connexion — consigne §17 ; jamais le process
# seul, voir découverte "Hôte contextuel" ci-dessus).
_WINDOW_SPEC = {"query": "PhoneExperienceHost", "match_class": "WinUIDesktopWin32WindowClass"}
# URI protocole officiel de lancement (enregistré par l'installation de Phone
# Link) — jamais un chemin d'exécutable deviné.
_LAUNCH_URI = "ms-phone:"

NAV_CALLS = "CallingNodeAutomationId"
NAV_MESSAGES = "ChatNodeAutomationId"
DIAL_TEXTBOX = "TextBox"
BUTTON_CALL = "ButtonCall"
END_CALL_BUTTON = "EndCallButton"
MUTE_BUTTON = "MuteButton"
NEW_MESSAGE_BUTTON = "NewMessageButton"
SMS_RECIPIENT_TEXTBOX = "TextBox"
SMS_INPUT_TEXTBOX = "InputTextBox"
SEND_MESSAGE_BUTTON = "SendMessageButton"

# Candidats jamais confirmés en conditions réelles (voir docstring du module)
# — utilisés en dernier recours par answer_call/reject_call, qui échouent
# honnêtement (status=error) si aucun n'est trouvé, jamais un succès inventé.
_CANDIDATE_ANSWER_IDS = ("AcceptButton", "AnswerButton", "AcceptCallButton")
_CANDIDATE_REJECT_IDS = ("DeclineButton", "RejectButton", "DeclineCallButton", "IgnoreButton")


def is_running() -> bool:
    return len(window_mgmt.find_windows(_WINDOW_SPEC["query"], match_class=_WINDOW_SPEC["match_class"])) > 0


def ensure_open(wait_timeout_s: float = 10.0) -> dict:
    """Lance Phone Link si nécessaire et attend sa fenêtre — jamais un sleep
    fixe (condition réelle, cf. window_mgmt.wait_for_window)."""
    if is_running():
        return {"status": "ok", "already_open": True}
    launched = applications.launch(_LAUNCH_URI, wait_timeout_s=0.0)
    if launched.get("status") == "error":
        return {"status": "error", "error": launched.get("error", "échec du lancement de Phone Link")}
    ready = window_mgmt.wait_for_window(_WINDOW_SPEC["query"], timeout_s=wait_timeout_s,
                                         match_class=_WINDOW_SPEC["match_class"])
    if ready["status"] != "ready":
        return {"status": "error", "error": "Phone Link lancé mais aucune fenêtre détectée à temps"}
    return {"status": "ok", "already_open": False}


def _elements(max_count: int = 300) -> dict:
    return uia.read_elements(_WINDOW_SPEC, max_count=max_count)


def _find_by_id(elements: list[dict], automation_id: str) -> dict | None:
    return next((e for e in elements if e.get("automation_id") == automation_id), None)


def _focus_and_click_real(automation_id: str) -> dict:
    """Clic souris RÉEL sur un élément mis au premier plan explicitement —
    réservé aux actions à conséquence réelle où Invoke s'est montré
    peu fiable (voir docstring du module : ButtonCall/EndCallButton/
    SendMessageButton)."""
    focus = window_mgmt.focus_window(_WINDOW_SPEC["query"], match_class=_WINDOW_SPEC["match_class"])
    if focus.get("status") != "ok":
        return {"status": "error", "reason": "impossible de mettre Phone Link au premier plan", "detail": focus}
    time.sleep(0.4)
    active = window_mgmt.get_active_window()
    if active.get("active", {}).get("process") != "PhoneExperienceHost.exe":
        return {"status": "error", "reason": "Phone Link pas réellement au premier plan", "detail": active}
    center = uia.get_center(_WINDOW_SPEC, {"automation_id": automation_id})
    if not center.get("found"):
        return {"status": "error", "reason": "élément introuvable", "detail": center}
    ok = mouse.click_at(center["x"], center["y"])
    return {"status": "ok" if ok else "error", "clicked": automation_id}


def connection_state() -> dict:
    """État de connexion RÉEL lu depuis l'UI — jamais supposé depuis le seul
    fait que le process tourne (Phone Link peut être ouvert mais en cours de
    (ré)appairage : le titre de fenêtre reste identique dans les deux cas,
    seul le contenu UI le distingue, cf. investigation Chantier 13)."""
    if not is_running():
        return {"status": "ok", "connected": False, "reason": "not_running"}
    r = _elements()
    if r.get("status") != "ok":
        return {"status": "error", "error": r.get("error", "lecture UIA échouée")}
    elements = r["elements"]
    if _find_by_id(elements, "QrCodeButton") is not None:
        return {"status": "ok", "connected": False, "reason": "pairing_onboarding_screen"}
    status_el = _find_by_id(elements, "ConnectivityStatusTextBlock")
    phone_name_el = _find_by_id(elements, "PhoneNameTextBlock")
    if status_el is None:
        return {"status": "ok", "connected": False, "reason": "connectivity_status_not_found"}
    return {
        "status": "ok", "connected": True,
        "status_text": status_el["name"],
        "phone_name": phone_name_el["name"] if phone_name_el else None,
    }


def _permission_gate(elements: list[dict]) -> str | None:
    """Détecte le panneau 'Accorder l'autorisation...' (contacts/SMS non
    accordés côté iPhone) — générique (AutomationId), jamais un texte
    localisé comparé en dur."""
    if _find_by_id(elements, "ErrorTakeoverPanel") is not None:
        return "permission_not_granted_on_phone"
    if _find_by_id(elements, "CallLogsErrorActionButton") is not None:
        return "permission_not_granted_on_phone"
    return None


def lookup_contact(query: str, max_results: int = 6) -> dict:
    """Recherche un contact par nom via le champ de recherche RÉEL de Phone
    Link (jamais une base de contacts dupliquée côté RAYA) — retourne TOUTES
    les correspondances, la désambiguïsation reste à la charge de
    l'appelant (Cognition/Harness), jamais devinée ici."""
    opened = ensure_open()
    if opened["status"] != "ok":
        return {"status": "error", "stage": "open", **opened}
    nav = strategy.click_element(_WINDOW_SPEC, {"automation_id": NAV_CALLS})
    if nav["status"] != "ok":
        return {"status": "error", "stage": "navigate_calls", "detail": nav}
    time.sleep(0.3)

    gate = _permission_gate(_elements().get("elements", []))
    if gate:
        return {"status": "blocked", "reason": gate}

    typed = strategy.type_into_element(_WINDOW_SPEC, {"automation_id": DIAL_TEXTBOX}, query)
    if typed["status"] != "ok":
        return {"status": "error", "stage": "type_query", "detail": typed}
    time.sleep(0.6)

    r = _elements()
    if r.get("status") != "ok":
        return {"status": "error", "stage": "read_suggestions", "error": r.get("error")}
    # Un contact réel porte "Nom. numéro. Label." — les autres listitem
    # visibles ici (notifications, historique) ne matchent jamais ce format
    # ET ne contiennent pas le texte recherché en tête de chaîne.
    query_lower = query.strip().lower()
    matches = [
        e["name"] for e in r["elements"]
        if e["control_type"] == "listitem" and e["name"].strip().lower().startswith(query_lower)
    ]
    return {"status": "ok", "query": query, "matches": matches, "count": len(matches)}


def dial_number(number: str) -> dict:
    """Compose un numéro EXPLICITE (jamais résolu ici — la résolution de nom
    de contact vers numéro passe par `lookup_contact` puis la sélection
    explicite d'UNE correspondance, en amont, chez l'appelant). Chaque étape
    est vérifiée avant de passer à la suivante — jamais une séquence
    aveugle de clics."""
    opened = ensure_open()
    if opened["status"] != "ok":
        return {"status": "error", "stage": "open", **opened}

    nav = strategy.click_element(_WINDOW_SPEC, {"automation_id": NAV_CALLS})
    if nav["status"] != "ok":
        return {"status": "error", "stage": "navigate_calls", "detail": nav}
    time.sleep(0.3)

    typed = strategy.type_into_element(_WINDOW_SPEC, {"automation_id": DIAL_TEXTBOX}, number)
    if typed["status"] != "ok":
        return {"status": "error", "stage": "type_number", "detail": typed}

    # Découverte comportementale (voir docstring du module) : confirme le
    # numéro (Entrée) — SetValue seul ne suffit pas à activer ButtonCall.
    keyboard.press("enter")
    time.sleep(0.3)

    call_btn = _find_by_id(_elements().get("elements", []), BUTTON_CALL)
    if call_btn is None:
        return {"status": "error", "stage": "confirm_number", "reason": "ButtonCall introuvable après confirmation"}
    if not call_btn["enabled"]:
        return {"status": "error", "stage": "confirm_number",
                "reason": "ButtonCall toujours désactivé — numéro non reconnu comme composable"}

    clicked = _focus_and_click_real(BUTTON_CALL)
    if clicked["status"] != "ok":
        return {"status": "error", "stage": "invoke_call", "detail": clicked}

    time.sleep(1.2)
    return {"status": "ok", "number": number, "call_state": call_state()}


def dial_contact_suggestion(name: str) -> dict:
    """Sélectionne une suggestion de contact déjà affichée (après
    `lookup_contact`/une saisie dans le dialer) PAR NOM EXACT (tel que
    retourné par `lookup_contact`) puis appelle — jamais une correspondance
    approximative devinée ici, la désambiguïsation est déjà faite par
    l'appelant avant ce point."""
    picked = strategy.click_element(_WINDOW_SPEC, {"control_type": "listitem", "name": name})
    if picked["status"] != "ok":
        return {"status": "error", "stage": "select_contact", "detail": picked}
    time.sleep(0.5)

    call_btn = _find_by_id(_elements().get("elements", []), BUTTON_CALL)
    if call_btn is None or not call_btn["enabled"]:
        return {"status": "error", "stage": "select_contact",
                "reason": "ButtonCall non activé après sélection du contact"}

    clicked = _focus_and_click_real(BUTTON_CALL)
    if clicked["status"] != "ok":
        return {"status": "error", "stage": "invoke_call", "detail": clicked}

    time.sleep(1.2)
    return {"status": "ok", "contact": name, "call_state": call_state()}


def call_state() -> dict:
    """Observation générique de l'état d'appel — la présence du widget
    d'appel (EndCallButton) est le seul signal CONFIRMÉ d'un appel actif ;
    tout le reste (nom de l'appelé, texte 'Appel en cours') est renvoyé s'il
    est présent, jamais supposé."""
    r = _elements(max_count=100)
    if r.get("status") != "ok":
        return {"status": "error", "error": r.get("error", "lecture UIA échouée")}
    elements = r["elements"]
    end_btn = _find_by_id(elements, END_CALL_BUTTON)
    status_texts = [e["name"] for e in elements if e["control_type"] == "text" and
                     ("appel" in e["name"].lower() or "call" in e["name"].lower()) and e["automation_id"] == ""]
    return {"status": "ok", "in_call": end_btn is not None, "status_hint": status_texts}


def end_call() -> dict:
    """Termine l'appel actif — VÉRIFIE l'état réel après coup (jamais une
    affirmation sans preuve, consigne §9 héritée du reste de RAYA V2)."""
    before = call_state()
    if not before.get("in_call"):
        return {"status": "error", "reason": "aucun appel actif détecté", "call_state": before}
    clicked = _focus_and_click_real(END_CALL_BUTTON)
    if clicked["status"] != "ok":
        return {"status": "error", "stage": "end_call", "detail": clicked}
    time.sleep(1.0)
    after = call_state()
    return {"status": "ok" if not after.get("in_call") else "error", "call_state": after}


def answer_call() -> dict:
    """NON CONFIRMÉ en conditions réelles (voir docstring du module) —
    échoue honnêtement si aucun des AutomationId candidats n'est présent,
    jamais un succès inventé."""
    elements = _elements(max_count=150).get("elements", [])
    for aid in _CANDIDATE_ANSWER_IDS:
        if _find_by_id(elements, aid) is not None:
            clicked = _focus_and_click_real(aid)
            if clicked["status"] == "ok":
                time.sleep(1.0)
                return {"status": "ok", "call_state": call_state()}
    return {"status": "error", "reason": "aucun bouton de réponse reconnu (BLOCKED — non confirmé en réel)"}


def reject_call() -> dict:
    """NON CONFIRMÉ en conditions réelles (voir docstring du module) —
    échoue honnêtement si aucun des AutomationId candidats n'est présent,
    jamais un succès inventé."""
    elements = _elements(max_count=150).get("elements", [])
    for aid in _CANDIDATE_REJECT_IDS:
        if _find_by_id(elements, aid) is not None:
            clicked = _focus_and_click_real(aid)
            if clicked["status"] == "ok":
                time.sleep(1.0)
                return {"status": "ok", "call_state": call_state()}
    return {"status": "error", "reason": "aucun bouton de refus reconnu (BLOCKED — non confirmé en réel)"}


def send_sms(recipient: str, message: str) -> dict:
    """Envoie un SMS à un destinataire déjà résolu (nom exact de contact tel
    que retourné par `lookup_contact`, ou numéro explicite) — jamais un
    envoi sans confirmation de destinataire (désambiguïsation en amont,
    chez l'appelant)."""
    opened = ensure_open()
    if opened["status"] != "ok":
        return {"status": "error", "stage": "open", **opened}

    nav = strategy.click_element(_WINDOW_SPEC, {"automation_id": NAV_MESSAGES})
    if nav["status"] != "ok":
        return {"status": "error", "stage": "navigate_messages", "detail": nav}
    time.sleep(0.3)

    gate = _permission_gate(_elements().get("elements", []))
    if gate:
        return {"status": "blocked", "reason": gate}

    new_msg = strategy.click_element(_WINDOW_SPEC, {"automation_id": NEW_MESSAGE_BUTTON})
    if new_msg["status"] != "ok":
        return {"status": "error", "stage": "new_message", "detail": new_msg}
    time.sleep(0.5)

    typed_to = strategy.type_into_element(_WINDOW_SPEC, {"automation_id": SMS_RECIPIENT_TEXTBOX, "name": "À"}, recipient)
    if typed_to["status"] != "ok":
        return {"status": "error", "stage": "type_recipient", "detail": typed_to}
    time.sleep(0.8)

    picked = strategy.click_element(_WINDOW_SPEC, {"control_type": "listitem", "name": recipient})
    if picked["status"] != "ok":
        return {"status": "error", "stage": "select_recipient", "detail": picked,
                "reason": "aucune correspondance sélectionnable pour ce destinataire — jamais envoyé sans destinataire confirmé"}
    time.sleep(0.5)

    typed_msg = strategy.type_into_element(_WINDOW_SPEC, {"automation_id": SMS_INPUT_TEXTBOX}, message)
    if typed_msg["status"] != "ok":
        return {"status": "error", "stage": "type_message", "detail": typed_msg}
    time.sleep(0.3)

    send_btn = _find_by_id(_elements().get("elements", []), SEND_MESSAGE_BUTTON)
    if send_btn is None or not send_btn["enabled"]:
        return {"status": "error", "stage": "send", "reason": "SendMessageButton introuvable ou désactivé"}

    clicked = _focus_and_click_real(SEND_MESSAGE_BUTTON)
    if clicked["status"] != "ok":
        return {"status": "error", "stage": "send", "detail": clicked}
    time.sleep(1.0)
    return {"status": "ok", "recipient": recipient}


def read_latest_message(conversation_name: str) -> dict:
    """Lit le DERNIER message visible d'une conversation déjà ouverte —
    lecture seule, aucune action. Utilisé pour vérifier une réponse reçue,
    jamais pour un envoi automatique."""
    r = _elements(max_count=300)
    if r.get("status") != "ok":
        return {"status": "error", "error": r.get("error")}
    bodies = [e for e in r["elements"] if e.get("automation_id") == "MessageBody"]
    if not bodies:
        return {"status": "ok", "found": False}
    return {"status": "ok", "found": True, "text": bodies[-1]["name"]}
