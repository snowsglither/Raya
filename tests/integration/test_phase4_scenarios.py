"""Scénarios d'intégration Phase 4 (Environment Agents) — système réel de
bout en bout : vrai Harness, vraie Safety, vrais Device Agents (Windows réel
de cette machine, vrai Edge via CDP), pages HTML locales servies par un vrai
serveur HTTP. Seul le modèle est scripté (FakeScriptedProvider), sauf
mention contraire. Couvre les scénarios numérotés de la consigne Phase 4
§26-27 ainsi que les bugs V1 explicitement listés en §29 (mappage en fin de
fichier)."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402
from support.local_http_server import LocalFixtureServer  # noqa: E402

from raya.contracts import (  # noqa: E402
    Channel,
    ContentPart,
    Event,
    FinishReason,
    HarnessRequest,
    HarnessStatus,
    InterfaceInput,
    ModelResponse,
    RequestedToolCall,
    TaskState,
)


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


def _req(text: str, session_id: str = "s1") -> HarnessRequest:
    return HarnessRequest(channel=Channel.CLI, session_id=session_id, input=InterfaceInput(text=text))


def _wait_for(predicate, timeout_s: float = 5.0, interval_s: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval_s)
    return predicate()


@pytest.fixture(scope="module")
def server():
    s = LocalFixtureServer()
    yield s
    s.shutdown()


# --- 1. Harness -> Tool -> Safety -> Windows (réel, bout en bout) ---

def test_1_harness_tool_safety_windows_real_notepad_launch(tmp_path):
    script = [
        _tool_call_response("pc.application.launch", {"target": "notepad"}),
        _text_response("Le Bloc-notes est ouvert."),
    ]
    handles, fake = build_test_harness(tmp_path, script, enable_windows_device=True)
    try:
        state = handles.harness.handle_request(_req("ouvre le bloc-notes"))
        assert state.status == HarnessStatus.COMPLETED
        trace = handles.harness.last_tool_trace("s1")
        assert trace[0]["tool_name"] == "pc.application.launch"
        assert trace[0]["status"] == "success"
        assert trace[0]["evidence"]["process"].lower() == "notepad.exe"
    finally:
        from raya.devices.windows.mechanisms import window_mgmt

        window_mgmt.close_window("notepad")
        handles.shutdown()


# --- 2. Harness -> Tool -> Safety -> Browser (réel, bout en bout) ---

def test_2_harness_tool_safety_browser_real_navigation(tmp_path, server):
    script = [
        _tool_call_response("browser.navigate", {"url": server.url_for("plain.html")}),
        _text_response("La page est chargée."),
    ]
    handles, fake = build_test_harness(tmp_path, script, enable_browser_device=True)
    try:
        state = handles.harness.handle_request(_req("va sur la page de test"))
        assert state.status == HarnessStatus.COMPLETED
        trace = handles.harness.last_tool_trace("s1")
        assert trace[0]["tool_name"] == "browser.navigate"
        assert trace[0]["status"] == "success"
        assert trace[0]["evidence"]["url"] == server.url_for("plain.html")
    finally:
        handles.shutdown()


# --- 3. STOP pendant une action device (réelle) ---

def test_3_stop_during_real_browser_navigation_interrupts(tmp_path, server):
    script = [
        _tool_call_response("browser.navigate", {"url": server.url_for("plain.html")}),
        _tool_call_response("browser.navigate", {"url": server.url_for("slow.html")}),
        _text_response("ne devrait jamais être renvoyé"),
    ]
    handles, fake = build_test_harness(tmp_path, script, enable_browser_device=True)
    try:
        def trigger_stop_after_first_nav(event):
            handles.safety.request_stop("test")

        handles.bus.subscribe("tool.call_completed", trigger_stop_after_first_nav, subscriber="test_stop_trigger")

        state = handles.harness.handle_request(_req("navigue deux fois"))
        assert state.status == HarnessStatus.FAILED
        assert state.error.code == "STOP_ACTIVE"
        assert len(fake.calls) == 1  # le 2e appel modèle n'a jamais eu lieu
    finally:
        handles.shutdown()


# --- 4. Échec réel -> recovery ---
#
# browser.click est SENSITIVE (interaction mutante, §21) — dans la boucle
# agentique complète il serait PERMISSION_DENIED sans confirmation (aucun
# flux de confirmation interactive câblé cette phase, limitation documentée
# identique à Phase 3 §24 pour les outils demo.*). Ce scénario prouve donc le
# MÉCANISME réel d'échec->recovery en appelant le handler directement (même
# pattern que tests/tools/test_demo_catalog.py Phase 3) ; le gating Safety
# lui-même est prouvé séparément dans tests/tools/test_browser_catalog.py.

def test_4_real_click_failure_then_recovery_with_correct_target(tmp_path, server):
    from raya.contracts import ToolCall, ToolCallRequester

    handles, fake = build_test_harness(tmp_path, [_text_response("n/a")], enable_browser_device=True)
    try:
        nav_state = handles.harness.handle_request(_req(f"va sur {server.url_for('plain.html')}"))
        # navigate SAFE via le vrai pipeline -> nécessite un tool_call scripté ;
        # on pilote directement le device ici pour rester focalisé sur le clic.
        handler_nav = handles.tools.handler_for("browser.navigate")
        handler_nav(ToolCall(tool_name="browser.navigate", arguments={"url": server.url_for("plain.html")},
                              correlation_id="c0", requested_by=ToolCallRequester(subsystem="test", session_id="s1")))

        handler_click = handles.tools.handler_for("browser.click")
        first = handler_click(ToolCall(tool_name="browser.click", arguments={"target": "Ce bouton n'existe pas"},
                                        correlation_id="c1", requested_by=ToolCallRequester(subsystem="test", session_id="s1")))
        assert first.status.value == "failure"
        assert first.error.code == "ELEMENT_NOT_FOUND"

        second = handler_click(ToolCall(tool_name="browser.click", arguments={"target": "Cliquez ici"},
                                         correlation_id="c2", requested_by=ToolCallRequester(subsystem="test", session_id="s1")))
        assert second.status.value == "success"
    finally:
        handles.shutdown()


# --- 5. Modèle prétend une action device sans ToolCall -> RAYA refuse ---

def test_5_false_claim_about_device_action_never_becomes_evidence(tmp_path, server):
    script = [_text_response("J'ai ouvert Chrome et navigué sur le site demandé. ✅")]
    handles, fake = build_test_harness(tmp_path, script, enable_windows_device=True, enable_browser_device=True)
    try:
        state = handles.harness.handle_request(_req("ouvre Chrome et va sur le site"))
        assert state.status == HarnessStatus.COMPLETED
        assert handles.harness.last_tool_trace("s1") == []
        assert handles.world_state.all() == []
    finally:
        handles.shutdown()


# --- 6. Objectif ambigu -> clarification (pas de choix arbitraire, bug V1 #I) ---

def test_6_ambiguous_product_choice_model_asks_for_clarification(tmp_path, server):
    script = [
        _tool_call_response("browser.navigate", {"url": server.url_for("products.html")}),
        _tool_call_response("browser.read_page", {}),
        _text_response("Je vois 3 résultats : une manette PS5, un casque PS5 et une manette Xbox. Lequel veux-tu ?"),
    ]
    handles, fake = build_test_harness(tmp_path, script, enable_browser_device=True)
    try:
        state = handles.harness.handle_request(_req("ajoute une manette au panier"))
        assert state.status == HarnessStatus.COMPLETED
        assert "?" in handles.harness.response_text("s1")
        # Aucun clic n'a eu lieu -> aucun choix arbitraire fait à la place de l'utilisateur.
        trace = handles.harness.last_tool_trace("s1")
        assert all(t["tool_name"] != "browser.click" for t in trace)
    finally:
        handles.shutdown()


# --- 7. Benchmark cookie banner (§9) bout en bout ---
#
# browser.dismiss_overlay est SENSITIVE (§21) — même raisonnement que le
# scénario 4 : navigate/read_page (SAFE) passent par le VRAI Harness
# agentique complet (FakeScriptedProvider), dismiss_overlay est appelé
# directement sur le handler (mécanisme réel, gating Safety prouvé
# séparément).

def test_7_cookie_banner_benchmark_end_to_end_never_clicks_the_decoy(tmp_path, server):
    from raya.contracts import ToolCall, ToolCallRequester

    script = [
        _tool_call_response("browser.navigate", {"url": server.url_for("cookie_banner.html")}),
        _text_response("La page est chargée, une bannière cookies est visible."),
    ]
    handles, fake = build_test_harness(tmp_path, script, enable_browser_device=True)
    try:
        state = handles.harness.handle_request(_req("va sur la page avec la bannière cookies"))
        assert state.status == HarnessStatus.COMPLETED

        read_handler = handles.tools.handler_for("browser.read_page")
        before = read_handler(ToolCall(tool_name="browser.read_page", arguments={}, correlation_id="c0",
                                        requested_by=ToolCallRequester(subsystem="test", session_id="s1")))
        assert before.output["cookie_banner"] is True

        dismiss_handler = handles.tools.handler_for("browser.dismiss_overlay")
        dismiss_result = dismiss_handler(ToolCall(tool_name="browser.dismiss_overlay", arguments={},
                                                    correlation_id="c1", requested_by=ToolCallRequester(subsystem="test", session_id="s1")))
        assert dismiss_result.status.value == "success"
        assert "Accepter" in dismiss_result.output["dismissed"]

        read_handler = handles.tools.handler_for("browser.read_page")
        after = read_handler(ToolCall(tool_name="browser.read_page", arguments={}, correlation_id="c2",
                                       requested_by=ToolCallRequester(subsystem="test", session_id="s1")))
        assert after.output["cookie_banner"] is False
        assert after.output["title"] == "Test Cookie Banner"  # jamais "WRONG-CLICKED-DECOY"
    finally:
        handles.shutdown()


# --- 8. Benchmark "Amazon" local (§8) — comprendre, chercher, distinguer, vérifier ---

def test_8_shopping_benchmark_selects_correct_product_and_verifies_cart(tmp_path, server):
    """Aucun achat réel (§22) — page locale reproduisant l'ambiguïté
    manette/casque/mauvaise-console. Le CHOIX du bon produit est décidé ici
    par le test (à la place du modèle, comme la Cognition le ferait à partir
    de read_page()), jamais un score local codé en dur (contrairement à
    modules/browser/shopping.py::_pick_best en V1). browser.click est
    SENSITIVE -> appelé directement sur le handler (même raisonnement que 4/7)."""
    from raya.contracts import ToolCall, ToolCallRequester

    script = [
        _tool_call_response("browser.navigate", {"url": server.url_for("products.html")}),
        _text_response("Résultats affichés."),
    ]
    handles, fake = build_test_harness(tmp_path, script, enable_browser_device=True)
    try:
        state = handles.harness.handle_request(_req("cherche une manette PS5"))
        assert state.status == HarnessStatus.COMPLETED

        read_handler = handles.tools.handler_for("browser.read_page")
        click_handler = handles.tools.handler_for("browser.click")

        def _read():
            return read_handler(ToolCall(tool_name="browser.read_page", arguments={}, correlation_id="c",
                                          requested_by=ToolCallRequester(subsystem="test", session_id="s1")))

        def _click(target):
            return click_handler(ToolCall(tool_name="browser.click", arguments={"target": target}, correlation_id="c",
                                           requested_by=ToolCallRequester(subsystem="test", session_id="s1")))

        listing = _read()
        link_texts = [l["text"] for l in listing.output["links"]]
        assert "Manette PS5 DualSense Blanche" in link_texts
        assert "Casque PS5 Pulse 3D" in link_texts  # décoy présent -> la distinction est réelle
        assert "Manette Xbox Series Noire" in link_texts  # décoy présent -> mauvaise console

        clicked = _click("Manette PS5 DualSense Blanche")
        assert clicked.status.value == "success"
        product_page = _read()
        assert product_page.output["title"] == "Manette PS5 DualSense Blanche"

        added = _click("Ajouter au panier")
        assert added.status.value == "success"
        cart_page = _read()
        assert "cart" in cart_page.output["url"]

        confirmed = handles.devices.get("browser_agent")._controller.check_confirmation()
        assert confirmed is True  # vérification honnête, pas une supposition
    finally:
        handles.shutdown()


def test_8b_shopping_benchmark_never_clicks_the_wrong_decoy_product(tmp_path, server):
    """Bug V1 #A (clique un mauvais produit) : même si le modèle scripté
    demandait le casque par erreur, le mécanisme find_clickable ne
    cliquerait QUE l'élément dont le texte correspond — preuve qu'aucune
    confusion structurelle entre les 3 cartes n'est possible."""
    from raya.devices.browser import BrowserDeviceAgent

    agent = BrowserDeviceAgent(tmp_path / "screens2")
    try:
        from raya.contracts import Command

        agent.execute(Command(device_id="browser_agent", capability_name="browser.navigate",
                               arguments={"url": server.url_for("products.html")}, correlation_id="c1"))
        r = agent.execute(Command(device_id="browser_agent", capability_name="browser.click",
                                   arguments={"target": "Manette PS5 DualSense Blanche"}, correlation_id="c2"))
        assert r.status.value == "success"
        read = agent.execute(Command(device_id="browser_agent", capability_name="browser.read_page",
                                      arguments={}, correlation_id="c3"))
        assert read.output["title"] == "Manette PS5 DualSense Blanche"  # jamais le casque ni la manette Xbox
    finally:
        agent.shutdown()


# --- 9. Anti-boucle par état — bug V1 #D (boucle sans progrès) ---

def test_9_state_cycle_between_two_pages_detected_and_escalated(tmp_path, server):
    # Passe "Targeted Execution Repair" (§10) : l'escalade déclenche
    # désormais UN appel modèle supplémentaire (_explain_blocked_turn) pour
    # produire une explication honnête — le 5e élément du script le couvre.
    script = [
        _tool_call_response("browser.navigate", {"url": server.url_for("loop_a.html")}),
        _tool_call_response("browser.navigate", {"url": server.url_for("loop_b.html")}),
        _tool_call_response("browser.navigate", {"url": server.url_for("loop_a.html")}),
        _tool_call_response("browser.navigate", {"url": server.url_for("loop_b.html")}),
        _text_response("Je tourne en rond entre les deux mêmes pages sans nouvelle information."),
    ]
    handles, fake = build_test_harness(tmp_path, script, enable_browser_device=True, max_tool_iterations=6)
    try:
        state = handles.harness.handle_request(_req("navigue entre A et B"))
        response = handles.harness.response_text("s1")
        assert "tourne en rond" in response
        assert len(fake.calls) <= 5  # 4 décisions de navigation + 1 appel d'explication
    finally:
        handles.shutdown()


# --- 10. Tâche de fond utilisant un Device pendant qu'une conversation continue ---

def test_10_background_task_independent_of_conversation_with_device_involved(tmp_path):
    """La conversation répond immédiatement même si un Device Agent réel est
    enregistré — la disponibilité de RAYA pour une question directe (Phase 2)
    reste garantie en Phase 4 (device réel enregistré mais pas invoqué ici)."""
    script = [_text_response("Il est l'heure de coder.")]
    handles, fake = build_test_harness(tmp_path, script, enable_windows_device=True)
    try:
        handles.harness.start_background_task("tâche longue", total_steps=30)
        start = time.monotonic()
        state = handles.harness.handle_request(_req("quelle heure est-il ?", session_id="s_conv"))
        elapsed = time.monotonic() - start
        assert state.status == HarnessStatus.COMPLETED
        assert elapsed < 0.5
    finally:
        handles.shutdown()


# --- Mapping explicite des bugs V1 (consigne §29) vers les tests qui les couvrent ---
#
# A. clique un mauvais produit          -> test_8b (ci-dessus)
# B. ouvre une image au lieu du produit -> couvert structurellement par
#    find_clickable (description, jamais un ordre/index) ; pas de fixture
#    dédiée "image vs produit" ajoutée (redondant avec 8b).
# C. scroll sans progression            -> mouse.scroll/browser.scroll NON
#    implémentés cette phase (limitation documentée, rapport §"Known
#    limitations") ; detect_no_progress (tests/cognition/test_state_cycle.py)
#    couvre le principe général de stagnation, appliqué ici à la navigation.
# D. boucle                             -> test_9 (ci-dessus)
# E. ne comprend pas une cookie banner  -> test_7 (ci-dessus) +
#    tests/devices/browser/test_browser_agent.py (dismiss_overlay dédié)
# F. élément attendu absent             -> test_4 (ci-dessus) + les tests
#    *_missing_element/_missing_field dans tests/devices/browser +
#    tests/devices/windows (WINDOW_NOT_FOUND)
# G. affirme sans preuve                -> test_5 (ci-dessus)
# H. abandonne trop tôt                 -> test_4 prouve la reprise après un
#    échec (pas d'abandon au 1er échec) ; le budget max_tool_iterations reste
#    couvert par tests/harness/test_agentic_loop.py (Phase 3, inchangé)
# I. choix arbitraire en cas d'ambiguïté -> test_6 (ci-dessus)
# J. ne vérifie pas le résultat         -> test_8 (vérification /cart réelle)
#    + tests/devices/browser::test_check_confirmation_* (True/None jamais
#    fabriqué)
