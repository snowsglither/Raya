"""notification_extraction.py (Chantier 13G) — classification STRUCTURELLE
(jamais textuelle) + stratégie de stabilisation bornée. `classify_notification`
et `read_notification_content_with_retry` sont testés avec des dicts/lecteurs
injectés (déterministe, sans dépendre d'un vrai Windows) ; `read_notification_
content` lui-même (touche réellement uiautomation) est testé pour sa
dégradation honnête uniquement (même style que les autres capteurs)."""

from __future__ import annotations

from raya.perception.notification_extraction import (
    classify_notification,
    read_notification_content,
    read_notification_content_with_retry,
)

_INCOMING_CONTENT = {
    "toast_view_type": "PriorityToastView",
    "sender_name": "Appels",
    "title": "Papa",
    "message_text": "Appel entrant",
    "attribution": "via Mobile connecté",
    "has_action_buttons": True,
    "action_button_texts": ["Accepter sur ordinateur personnel", "Message", "Refuser"],
}

_MISSED_CONTENT = {
    "toast_view_type": "NormalToastView",
    "sender_name": "Téléphone",
    "title": "Téléphone",
    "message_text": "Papa\nAppel manqué",
    "attribution": "via Mobile connecté",
    "has_action_buttons": False,
    "action_button_texts": [],
}

_EMPTY_CONTENT = {
    "toast_view_type": None,
    "sender_name": None,
    "title": None,
    "message_text": None,
    "attribution": None,
    "has_action_buttons": False,
    "action_button_texts": [],
}


# --- classify_notification : structurel, jamais textuel ---

def test_priority_view_with_action_buttons_is_incoming_call():
    assert classify_notification(_INCOMING_CONTENT) == "incoming_call"


def test_normal_view_without_action_buttons_is_other():
    assert classify_notification(_MISSED_CONTENT) == "other"


def test_priority_view_without_action_buttons_is_other_not_incoming():
    """La classification ne repose JAMAIS sur le seul type de vue — les
    boutons d'action sont une condition nécessaire, structurelle elle aussi."""
    content = dict(_INCOMING_CONTENT, has_action_buttons=False, action_button_texts=[])
    assert classify_notification(content) == "other"


def test_empty_content_is_not_identified():
    assert classify_notification(_EMPTY_CONTENT) == "not_identified"


def test_none_content_is_not_identified():
    assert classify_notification(None) == "not_identified"


def test_classification_never_inspects_text_fields():
    """Preuve directe de la consigne §4 (jamais 'if text == ...') : deux
    contenus avec des textes totalement différents mais la MÊME structure
    (vue + boutons) doivent produire la MÊME classification."""
    variant = dict(_INCOMING_CONTENT, title="Someone Else", message_text="Incoming Call",
                    attribution="via Something Else Entirely", sender_name="Whatever")
    assert classify_notification(variant) == classify_notification(_INCOMING_CONTENT) == "incoming_call"


# --- read_notification_content_with_retry : stabilisation BORNÉE ---

def test_retry_returns_first_stable_result_immediately():
    calls = {"n": 0}

    def reader(hwnd):
        calls["n"] += 1
        return _INCOMING_CONTENT

    result = read_notification_content_with_retry(123, reader=reader, sleep=lambda s: None)
    assert result == _INCOMING_CONTENT
    assert calls["n"] == 1  # stable dès le premier essai, jamais de re-tentative inutile


def test_retry_stabilizes_after_initially_empty_content():
    """Reproduit la découverte réelle du Chantier 13F : le tout premier
    événement peut arriver avant que le contenu ne soit construit."""
    responses = iter([_EMPTY_CONTENT, _EMPTY_CONTENT, _INCOMING_CONTENT])

    def reader(hwnd):
        return next(responses)

    result = read_notification_content_with_retry(123, attempts=5, reader=reader, sleep=lambda s: None)
    assert result == _INCOMING_CONTENT


def test_retry_is_bounded_never_infinite():
    calls = {"n": 0}

    def reader(hwnd):
        calls["n"] += 1
        return _EMPTY_CONTENT  # jamais stable

    result = read_notification_content_with_retry(123, attempts=3, reader=reader, sleep=lambda s: None)
    assert calls["n"] == 3  # borné exactement à `attempts`, jamais plus
    assert classify_notification(result) == "not_identified"


def test_default_read_notification_content_degrades_honestly():
    """Sans uiautomation disponible ou avec un hwnd invalide, retourne None
    plutôt que de lever — jamais un crash du thread de poll."""
    result = read_notification_content(0)
    assert result is None or isinstance(result, dict)
