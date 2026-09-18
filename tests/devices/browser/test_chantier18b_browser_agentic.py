"""Chantier 18B — Browser Agentic Execution & Recovery.

Tests RÉELS contre Edge/CDP via de vrais sites web — jamais de mocks Playwright.

Couvre :
- _STRUCT_JS enrichi (boutons avec left/top/aria_label)
- browser.type text optionnel (submit sans modifier le contenu)
- browser.click_at_position (coordonnées depuis read_page)
- Pattern observe → act → verify (tool SUCCESS ≠ objective SUCCESS)
- DOM fallback : click échoue → read_page → alternative → succès
- LoopDetector applicable aux failures browser
- Non-régression Chantier 19 (replace/append/submit)

Sites :
  the-internet.herokuapp.com/add_remove_elements — bouton Add Element → Delete (stable)
  duckduckgo.com     — recherche avec vrai submit
  the-internet.herokuapp.com/login — formulaire avec inputs labellisés
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from raya.contracts import Command, CommandStatus  # noqa: E402
from raya.devices.browser import BrowserDeviceAgent  # noqa: E402

_ADD_REMOVE = "https://the-internet.herokuapp.com/add_remove_elements"
_DDG = "https://duckduckgo.com"
_LOGIN = "https://the-internet.herokuapp.com/login"
_EXAMPLE = "https://example.com"


def _cmd(capability_name: str, arguments: dict) -> Command:
    return Command(device_id="browser_agent", capability_name=capability_name,
                   arguments=arguments, correlation_id="c18b")


import pytest


@pytest.fixture(scope="module")
def agent(tmp_path_factory):
    a = BrowserDeviceAgent(tmp_path_factory.mktemp("browser18b"))
    yield a
    a.shutdown()


# ─── Groupe 1 : _STRUCT_JS enrichi (buttons avec position) ───────────────────

def test_buttons_expose_left_and_top_in_read_page(agent):
    """Après fix _STRUCT_JS, les boutons ont left et top dans read_page."""
    agent.execute(_cmd("browser.navigate", {"url": _ADD_REMOVE}))
    r = agent.execute(_cmd("browser.read_page", {}))
    btn = next((b for b in r.output["buttons"] if "Add" in b["text"]), None)
    assert btn is not None, f"Bouton 'Add' absent : {[b['text'] for b in r.output['buttons']]}"
    assert "left" in btn and "top" in btn
    assert isinstance(btn["left"], int) and isinstance(btn["top"], int)


def test_buttons_expose_aria_label_when_present(agent):
    """Les boutons qui ont un aria-label exposent 'aria_label' dans read_page."""
    # DuckDuckGo a des boutons avec aria-labels dans son interface
    agent.execute(_cmd("browser.navigate", {"url": _DDG}))
    r = agent.execute(_cmd("browser.read_page", {}))
    # On vérifie juste que le schéma de retour est correct (aria_label optionnel)
    for btn in r.output["buttons"]:
        assert "text" in btn
        assert "left" in btn and "top" in btn
        # aria_label peut ou pas être présent, mais si présent c'est une string
        if "aria_label" in btn:
            assert isinstance(btn["aria_label"], str)


# ─── Groupe 2 : Audit hardcoding — zéro sélecteur site-spécifique ────────────

def test_no_amazon_specific_ids_in_struct_js():
    """#desktop_buybox et #addToCart_feature_div retirés de _STRUCT_JS —
    seuls les patterns génériques [id*=buybox i] etc. restent."""
    from raya.devices.browser.controller import _STRUCT_JS
    assert "#desktop_buybox" not in _STRUCT_JS, "#desktop_buybox encore présent"
    assert "#addToCart_feature_div" not in _STRUCT_JS, "#addToCart_feature_div encore présent"


def test_generic_buybox_pattern_present_in_struct_js():
    """Pattern générique [id*=buybox i] présent dans _STRUCT_JS — sans Amazon-specifics."""
    from raya.devices.browser.controller import _STRUCT_JS
    assert "buybox" in _STRUCT_JS.lower(), "Pattern buybox générique absent de _STRUCT_JS"


# ─── Groupe 3 : browser.type text optionnel ──────────────────────────────────

def test_type_empty_text_replace_does_not_clear_existing_content(agent):
    """text='' + mode='replace' + submit=False → champ inchangé (pas de fill vide)."""
    agent.execute(_cmd("browser.navigate", {"url": _LOGIN}))
    # Remplir d'abord le champ
    agent.execute(_cmd("browser.type", {"target": "Username", "text": "Contenu original"}))
    # text vide avec replace — ne doit PAS effacer
    r = agent.execute(_cmd("browser.type", {"target": "Username", "text": "", "mode": "replace"}))
    assert r.status == CommandStatus.SUCCESS
    read = agent.execute(_cmd("browser.read_page", {}))
    input_texts = [i["text"] for i in read.output["inputs"]]
    assert any("Contenu original" in t for t in input_texts), \
        f"text='' + mode='replace' a effacé le champ : {input_texts}"


def test_type_empty_text_append_submit_presses_enter(agent):
    """text='' + mode='append' + submit=True → Enter déclenché sans modifier le contenu."""
    agent.execute(_cmd("browser.navigate", {"url": _DDG}))
    agent.execute(_cmd("browser.type", {"target": "q", "text": "manette PS5"}))
    r = agent.execute(_cmd("browser.type", {"target": "q", "text": "", "mode": "append", "submit": True}))
    assert r.status == CommandStatus.SUCCESS
    read = agent.execute(_cmd("browser.read_page", {}))
    # La recherche a été soumise, URL a changé
    assert "q=" in read.output["url"] or "manette" in read.output["url"].lower(), \
        f"Soumission Enter non effectuée : {read.output['url']}"


# ─── Groupe 4 : browser.click_at_position (coordonnées issues de read_page) ──

def test_click_at_position_capability_registered(agent):
    """browser.click_at_position est bien enregistré dans le dispatch."""
    from raya.devices.browser.agent import _DISPATCH
    assert "browser.click_at_position" in _DISPATCH


def test_click_at_position_using_read_page_coordinates(agent):
    """Coordonnées (left, top) issues de read_page → clic réel → état page changé."""
    agent.execute(_cmd("browser.navigate", {"url": _ADD_REMOVE}))
    read = agent.execute(_cmd("browser.read_page", {}))
    btn = next(b for b in read.output["buttons"] if "Add" in b["text"])
    x, y = btn["left"] + 5, btn["top"] + 5
    r = agent.execute(_cmd("browser.click_at_position", {"x": x, "y": y}))
    assert r.status == CommandStatus.SUCCESS
    assert r.output == {"x": x, "y": y}
    read2 = agent.execute(_cmd("browser.read_page", {}))
    btn_texts = [b["text"] for b in read2.output["buttons"]]
    # Le clic sur "Add Element" a créé un bouton "Delete"
    assert any("Delete" in t for t in btn_texts), \
        f"Le clic à position n'a pas créé le bouton Delete : {btn_texts}"


# ─── Groupe 5 : Pattern observe → act → verify ───────────────────────────────

def test_click_success_then_read_page_confirms_navigation(agent):
    """Clic sur un lien → ToolResult SUCCESS → read_page confirme nouvelle URL.
    Prouve que tool SUCCESS seul ne suffit pas : c'est read_page qui confirme."""
    agent.execute(_cmd("browser.navigate", {"url": _ADD_REMOVE}))
    # Clic sur "Add Element" change l'état de la page
    click_result = agent.execute(_cmd("browser.click", {"target": "Add Element"}))
    assert click_result.status == CommandStatus.SUCCESS
    read = agent.execute(_cmd("browser.read_page", {}))
    btn_texts = [b["text"] for b in read.output["buttons"]]
    assert any("Delete" in t for t in btn_texts), \
        f"L'état de la page n'a pas changé suite au clic : {btn_texts}"


# ─── Groupe 6 : DOM fallback — click échoue → re-read → alternative ──────────

def test_click_fails_read_page_reveals_real_target_second_click_succeeds(agent):
    """Premier click mauvaise cible → FAILURE → read_page révèle le vrai texte
    → deuxième click avec la bonne cible → SUCCESS."""
    agent.execute(_cmd("browser.navigate", {"url": _ADD_REMOVE}))
    fail = agent.execute(_cmd("browser.click", {"target": "Cible imaginaire introuvable xyz"}))
    assert fail.status == CommandStatus.FAILURE
    assert fail.error.code == "ELEMENT_NOT_FOUND"
    read = agent.execute(_cmd("browser.read_page", {}))
    real_target = next(b["text"] for b in read.output["buttons"] if b["text"])
    success = agent.execute(_cmd("browser.click", {"target": real_target}))
    assert success.status == CommandStatus.SUCCESS


# ─── Groupe 7 : LoopDetector applicable aux failures browser ─────────────────

def test_loop_detector_escalates_after_two_identical_browser_failures():
    """LoopDetector ESCALATE après 2 échecs identiques."""
    from raya.cognition import LoopDetector, RecoveryAction, VerificationOutcome
    detector = LoopDetector(max_identical_failures=2)
    key = "browser-session-1"
    r1 = detector.record(key, "browser.click", {"target": "Bouton introuvable"}, VerificationOutcome.FAILURE)
    assert r1 == RecoveryAction.REPLAN
    r2 = detector.record(key, "browser.click", {"target": "Bouton introuvable"}, VerificationOutcome.FAILURE)
    assert r2 == RecoveryAction.ESCALATE


def test_loop_detector_resets_after_browser_success():
    """Un succès remet le détecteur à zéro."""
    from raya.cognition import LoopDetector, RecoveryAction, VerificationOutcome
    detector = LoopDetector(max_identical_failures=2)
    key = "browser-session-2"
    detector.record(key, "browser.click", {"target": "Btn"}, VerificationOutcome.FAILURE)
    r = detector.record(key, "browser.click", {"target": "Btn"}, VerificationOutcome.SUCCESS)
    assert r == RecoveryAction.CONTINUE


# ─── Groupe 8 : Non-régression Chantier 19 ───────────────────────────────────

def test_type_replace_mode_regression(agent):
    """mode='replace' avec texte non-vide remplace le contenu — non-régression."""
    agent.execute(_cmd("browser.navigate", {"url": _LOGIN}))
    agent.execute(_cmd("browser.type", {"target": "Username", "text": "Ancien contenu"}))
    agent.execute(_cmd("browser.type", {"target": "Username", "text": "Nouveau contenu", "mode": "replace"}))
    read = agent.execute(_cmd("browser.read_page", {}))
    input_texts = [i["text"] for i in read.output["inputs"]]
    assert any("Nouveau contenu" in t for t in input_texts)
    assert not any("Ancien contenu" in t for t in input_texts)


def test_type_submit_true_regression(agent):
    """submit=True avec texte réel presse Enter → navigation — non-régression."""
    agent.execute(_cmd("browser.navigate", {"url": _DDG}))
    r = agent.execute(_cmd("browser.type", {"target": "q", "text": "test", "submit": True}))
    assert r.status == CommandStatus.SUCCESS
    assert r.output["submitted"] is True
    read = agent.execute(_cmd("browser.read_page", {}))
    assert "q=" in read.output["url"] or "test" in read.output["url"].lower()


def test_type_text_as_optional_field_in_schema():
    """text n'est plus dans 'required' du schéma browser.type — non-régression schéma."""
    from raya.devices.browser.agent import _CAPABILITIES
    type_cap = next(c for c in _CAPABILITIES if c.name == "browser.type")
    required = type_cap.input_schema.get("required", [])
    assert "text" not in required
    assert "target" in required
