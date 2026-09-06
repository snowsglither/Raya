"""BrowserDeviceAgent — tests RÉELS (consigne Phase 4 §27) contre le VRAI
Edge de cette machine (profil dédié RAYA V2, CDP réel) et des pages HTML
locales servies par un vrai serveur HTTP (tests/fixtures/browser/) — jamais
un mock de Playwright. Un seul Device Agent partagé pour toute la suite
(module scope) : lancer Edge/CDP coûte plusieurs secondes, la réutilisation
est nécessaire pour un temps de suite raisonnable (consigne §26 "dosage")."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from support.local_http_server import LocalFixtureServer  # noqa: E402

from raya.contracts import Command, CommandStatus, DeviceStatus  # noqa: E402
from raya.devices.browser import BrowserDeviceAgent  # noqa: E402


@pytest.fixture(scope="module")
def server():
    s = LocalFixtureServer()
    yield s
    s.shutdown()


@pytest.fixture(scope="module")
def agent(tmp_path_factory):
    a = BrowserDeviceAgent(tmp_path_factory.mktemp("browser_screens"))
    yield a
    a.shutdown()


def _cmd(capability_name: str, arguments: dict) -> Command:
    return Command(device_id="browser_agent", capability_name=capability_name, arguments=arguments, correlation_id="c1")


def test_navigate_to_real_local_page(agent, server):
    r = agent.execute(_cmd("browser.navigate", {"url": server.url_for("plain.html")}))
    assert r.status == CommandStatus.SUCCESS
    assert r.output["title"] == "Page simple"
    assert r.evidence["url"] == server.url_for("plain.html")


def test_health_after_session_started_is_online(agent, server):
    agent.execute(_cmd("browser.navigate", {"url": server.url_for("plain.html")}))
    h = agent.health()
    assert h.device_id == "browser_agent"
    assert h.status == DeviceStatus.ONLINE


def test_read_page_extracts_real_title_and_url(agent, server):
    agent.execute(_cmd("browser.navigate", {"url": server.url_for("plain.html")}))
    r = agent.execute(_cmd("browser.read_page", {}))
    assert r.status == CommandStatus.SUCCESS
    assert r.output["title"] == "Page simple"
    assert r.output["cookie_banner"] is False


def test_read_page_lists_real_interactive_elements(agent, server):
    agent.execute(_cmd("browser.navigate", {"url": server.url_for("plain.html")}))
    r = agent.execute(_cmd("browser.read_page", {}))
    button_texts = [b["text"] for b in r.output["buttons"]]
    assert "Cliquez ici" in button_texts
    assert len(r.output["inputs"]) >= 1


def test_list_tabs_shows_the_active_tab(agent, server):
    agent.execute(_cmd("browser.navigate", {"url": server.url_for("plain.html")}))
    r = agent.execute(_cmd("browser.list_tabs", {}))
    assert r.status == CommandStatus.SUCCESS
    assert r.output["count"] >= 1
    assert any(t["url"] == server.url_for("plain.html") for t in r.output["tabs"])


def test_click_real_button_changes_real_page_state(agent, server):
    agent.execute(_cmd("browser.navigate", {"url": server.url_for("plain.html")}))
    r = agent.execute(_cmd("browser.click", {"target": "Cliquez ici"}))
    assert r.status == CommandStatus.SUCCESS
    read = agent.execute(_cmd("browser.read_page", {}))
    button_texts = [b["text"] for b in read.output["buttons"]]
    assert any(t.startswith("Cliqu") and t != "Cliquez ici" for t in button_texts)  # la VRAIE page a changé suite au VRAI clic


def test_click_missing_element_fails_honestly(agent, server):
    agent.execute(_cmd("browser.navigate", {"url": server.url_for("plain.html")}))
    r = agent.execute(_cmd("browser.click", {"target": "Bouton qui n'existe pas du tout"}))
    assert r.status == CommandStatus.FAILURE
    assert r.error.code == "ELEMENT_NOT_FOUND"


def test_type_into_real_input_field(agent, server):
    agent.execute(_cmd("browser.navigate", {"url": server.url_for("plain.html")}))
    r = agent.execute(_cmd("browser.type", {"target": "Votre nom", "text": "Ruben"}))
    assert r.status == CommandStatus.SUCCESS
    read = agent.execute(_cmd("browser.read_page", {}))
    input_texts = [i["text"] for i in read.output["inputs"]]
    assert "Ruben" in input_texts  # valeur réellement saisie, relue depuis le VRAI DOM


def test_type_without_submit_never_triggers_navigation(agent, server):
    """Non-régression : le comportement par défaut (`submit` omis/False)
    reste EXACTEMENT `.fill()` seul — jamais une soumission implicite."""
    agent.execute(_cmd("browser.navigate", {"url": server.url_for("search.html")}))
    r = agent.execute(_cmd("browser.type", {"target": "Rechercher un produit", "text": "manette PS5"}))
    assert r.status == CommandStatus.SUCCESS
    read = agent.execute(_cmd("browser.read_page", {}))
    assert read.output["url"] == server.url_for("search.html")  # page inchangée


def test_type_with_submit_presses_enter_and_real_navigation_occurs(agent, server):
    """Bug corrigé (passe 'Post-Repair Validation', trouvé en E2E réel —
    recherche Coolblue jamais soumise) : `.fill()` seul ne déclenche jamais
    une recherche pour un champ qui n'agit qu'au clavier (touche Entrée),
    contrairement à une vraie saisie utilisateur. `submit=True` presse
    réellement Entrée après la saisie — vérifié par un VRAI changement de
    page (navigation réelle déclenchée par le JS de la page, pas un mock)."""
    agent.execute(_cmd("browser.navigate", {"url": server.url_for("search.html")}))
    r = agent.execute(_cmd("browser.type", {"target": "Rechercher un produit", "text": "manette PS5", "submit": True}))
    assert r.status == CommandStatus.SUCCESS
    assert r.output["submitted"] is True
    read = agent.execute(_cmd("browser.read_page", {}))
    assert read.output["url"] == server.url_for("products.html")  # VRAIE navigation déclenchée


def test_type_missing_field_fails_honestly(agent, server):
    agent.execute(_cmd("browser.navigate", {"url": server.url_for("plain.html")}))
    r = agent.execute(_cmd("browser.type", {"target": "Champ inexistant xyz", "text": "x"}))
    assert r.status == CommandStatus.FAILURE
    assert r.error.code == "FIELD_NOT_FOUND"


def test_screenshot_creates_a_real_file(agent, server, tmp_path_factory):
    agent.execute(_cmd("browser.navigate", {"url": server.url_for("plain.html")}))
    r = agent.execute(_cmd("browser.screenshot", {"filename": "page.png"}))
    assert r.status == CommandStatus.SUCCESS
    assert Path(r.output["path"]).exists()
    assert Path(r.output["path"]).stat().st_size > 500


def test_slow_page_auto_wait_succeeds_without_fixed_sleep(agent, server):
    """La page répond avec ~1.2s de délai côté serveur — Playwright attend
    réellement (domcontentloaded), aucun sleep fixe ajouté côté RAYA
    (consigne Phase 4 §12)."""
    r = agent.execute(_cmd("browser.navigate", {"url": server.url_for("slow.html")}))
    assert r.status == CommandStatus.SUCCESS
    assert r.output["title"] == "Page lente"


def test_unknown_capability_fails_honestly(agent, server):
    r = agent.execute(_cmd("browser.teleport", {}))
    assert r.status == CommandStatus.FAILURE
    assert r.error.code == "UNKNOWN_CAPABILITY"


def test_should_stop_active_before_execution_returns_cancelled(agent, server):
    r = agent.execute(_cmd("browser.read_page", {}), should_stop=lambda: True)
    assert r.status == CommandStatus.CANCELLED
    assert r.error.code == "STOP_ACTIVE"


# --- Benchmark cookie banner (consigne Phase 4 §9) ---

def test_dismiss_overlay_clicks_accept_inside_banner_never_the_decoy_ad(agent, server):
    """LE test central du benchmark cookie banner : une pub sponsorisée
    hors-bannière porte aussi un bouton texte "OK" — dismiss_overlay ne doit
    JAMAIS le cliquer (bug de production V1 réel reproduit ici en local,
    voir modules/pc_control/browser.py::dismiss_overlays)."""
    agent.execute(_cmd("browser.navigate", {"url": server.url_for("cookie_banner.html")}))
    before = agent.execute(_cmd("browser.read_page", {}))
    assert before.output["cookie_banner"] is True

    r = agent.execute(_cmd("browser.dismiss_overlay", {}))
    assert r.status == CommandStatus.SUCCESS
    assert "Accepter" in r.output["dismissed"]

    after = agent.execute(_cmd("browser.read_page", {}))
    assert after.output["cookie_banner"] is False  # la vraie bannière a disparu
    assert after.output["title"] == "Test Cookie Banner"  # PAS "WRONG-CLICKED-DECOY" -> la pub n'a jamais été cliquée


def test_dismiss_overlay_is_noop_when_no_overlay_present(agent, server):
    agent.execute(_cmd("browser.navigate", {"url": server.url_for("plain.html")}))
    r = agent.execute(_cmd("browser.dismiss_overlay", {}))
    assert r.status == CommandStatus.SUCCESS
    assert r.output["dismissed"] == []


# --- Vérification honnête (mécanisme check_confirmation, consigne §17/§18) ---

def test_check_confirmation_true_on_real_cart_url(agent, server):
    agent.execute(_cmd("browser.navigate", {"url": server.url_for("cart.html?item=ps5-dualsense")}))
    confirmed = agent._controller.check_confirmation()
    assert confirmed is True


def test_check_confirmation_none_when_inconclusive_never_fabricates_success(agent, server):
    agent.execute(_cmd("browser.navigate", {"url": server.url_for("plain.html")}))
    confirmed = agent._controller.check_confirmation()
    assert confirmed is None  # jamais True sans preuve réelle
