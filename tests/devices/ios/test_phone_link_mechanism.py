"""devices/ios/mechanisms/phone_link.py (Chantier 13) — logique pure
(détection du panneau de permission, recherche par AutomationId), sans
dépendre d'un vrai Phone Link. La validation du VRAI mécanisme UIA
(navigation, saisie, clic réel) a été faite en conditions réelles
(appels/SMS réels, voir rapport) — ce fichier couvre les branches de
logique qui ne nécessitent pas de fenêtre réelle."""

from __future__ import annotations

from raya.devices.ios.mechanisms import phone_link


def test_permission_gate_detects_error_takeover_panel():
    elements = [{"automation_id": "ErrorTakeoverPanel", "name": ""}]
    assert phone_link._permission_gate(elements) == "permission_not_granted_on_phone"


def test_permission_gate_detects_call_logs_error_button():
    elements = [{"automation_id": "CallLogsErrorActionButton", "name": "Me montrer comment"}]
    assert phone_link._permission_gate(elements) == "permission_not_granted_on_phone"


def test_permission_gate_absent_when_no_error_panel():
    elements = [{"automation_id": "ButtonCall", "name": "Appel"}]
    assert phone_link._permission_gate(elements) is None


def test_find_by_id_returns_matching_element():
    elements = [{"automation_id": "ButtonCall", "name": "Appel"}, {"automation_id": "EndCallButton", "name": "Raccrocher"}]
    found = phone_link._find_by_id(elements, "EndCallButton")
    assert found is not None
    assert found["name"] == "Raccrocher"


def test_find_by_id_returns_none_when_absent():
    assert phone_link._find_by_id([{"automation_id": "ButtonCall"}], "EndCallButton") is None


def test_end_call_fails_honestly_when_no_active_call(monkeypatch):
    monkeypatch.setattr(phone_link, "call_state", lambda: {"status": "ok", "in_call": False})
    result = phone_link.end_call()
    assert result["status"] == "error"
    assert "aucun appel actif" in result["reason"]


def test_answer_call_fails_honestly_with_no_candidate_buttons_present(monkeypatch):
    monkeypatch.setattr(phone_link, "_elements", lambda max_count=150: {"elements": []})
    result = phone_link.answer_call()
    assert result["status"] == "error"
    assert "non confirmé" in result["reason"]


def test_reject_call_fails_honestly_with_no_candidate_buttons_present(monkeypatch):
    monkeypatch.setattr(phone_link, "_elements", lambda max_count=150: {"elements": []})
    result = phone_link.reject_call()
    assert result["status"] == "error"
    assert "non confirmé" in result["reason"]
