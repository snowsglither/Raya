"""tools/catalog/browser.py — délégation réelle au BrowserDeviceAgent +
gating Safety réel (mêmes garanties que test_pc_catalog.py)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.local_http_server import LocalFixtureServer  # noqa: E402

from raya.contracts import PermissionLevel, ToolCall, ToolCallRequester, ToolResultStatus  # noqa: E402
from raya.devices.browser import BrowserDeviceAgent  # noqa: E402
from raya.event_bus import EventBus  # noqa: E402
from raya.safety import AuditTrail, SafetyService, StopController  # noqa: E402
from raya.tools import ToolRegistry, execute  # noqa: E402
from raya.tools.catalog import register_browser_tools  # noqa: E402


@pytest.fixture(scope="module")
def server():
    s = LocalFixtureServer()
    yield s
    s.shutdown()


def _setup(tmp_path):
    bus = EventBus()
    registry = ToolRegistry()
    agent = BrowserDeviceAgent(tmp_path / "screens")
    safety = SafetyService(StopController(bus), AuditTrail())
    register_browser_tools(registry, agent, should_stop=safety.should_stop)
    return registry, safety, bus, agent


def _call(name: str, arguments: dict) -> ToolCall:
    return ToolCall(tool_name=name, arguments=arguments, correlation_id="c1",
                     requested_by=ToolCallRequester(subsystem="harness", session_id="s1"))


def test_all_browser_tools_registered_with_browser_device_requirement(tmp_path):
    registry, safety, bus, agent = _setup(tmp_path)
    tools = {t.name: t for t in registry.all() if t.name.startswith("browser.")}
    assert "browser.navigate" in tools
    assert "browser.click" in tools
    for t in tools.values():
        assert t.requires_device == "browser_agent"
    agent.shutdown()


def test_navigate_declares_current_url_observation_spec(tmp_path):
    """Partie 12 (consigne 'browser.navigate appelé mais page inchangée ->
    ne pas considérer SUCCESS') : le mécanisme générique Phase 7 (déjà
    prouvé par tests/harness/test_observation_promotion.py) n'a d'effet ici
    que si `browser.navigate` déclare bien un `ObservationSpec` comparant
    l'URL observée à l'URL demandée — vérifie le câblage réel, pas
    seulement le mécanisme abstrait."""
    registry, safety, bus, agent = _setup(tmp_path)
    tool = registry.get("browser.navigate")
    assert len(tool.observation) == 1
    spec = tool.observation[0]
    assert spec.domain == "browser" and spec.key == "current_url"
    assert spec.expected_argument == "url"
    agent.shutdown()


def test_navigate_is_safe_and_executes_for_real_through_full_pipeline(tmp_path, server):
    registry, safety, bus, agent = _setup(tmp_path)
    try:
        tool = registry.get("browser.navigate")
        assert tool.permission_level == PermissionLevel.SAFE
        result = execute(registry, safety, _call("browser.navigate", {"url": server.url_for("plain.html")}))
        assert result.status == ToolResultStatus.SUCCESS
        assert result.output["title"] == "Page simple"
    finally:
        agent.shutdown()


def test_click_on_benign_target_is_safe_and_never_blocked(tmp_path, server):
    """Phase 11 (§3, "browser.click peut être SAFE dans un contexte et
    SENSITIVE dans un autre") : cliquer un élément dont la description ne
    mentionne aucun verbe à conséquence significative (ex: un profil, une
    vidéo, un bouton Play/Pause) n'est plus bloqué par Safety — la requête
    passe la permission (jamais PERMISSION_DENIED), qu'elle réussisse ou
    échoue ensuite au niveau du device (élément introuvable sur cette page)."""
    registry, safety, bus, agent = _setup(tmp_path)
    try:
        result = execute(registry, safety, _call("browser.click", {"target": "quoi que ce soit"}))
        assert result.status != ToolResultStatus.PERMISSION_DENIED
    finally:
        agent.shutdown()


def test_click_mentioning_a_dangerous_action_is_still_blocked_without_confirmation(tmp_path, server):
    """Non-régression Safety (§9/§16 : jamais de bypass) : le même Tool
    `browser.click`, sur une description mentionnant un verbe à conséquence
    réellement significative/irréversible (ici : supprimer), reste bloqué
    tant qu'aucune confirmation n'a été donnée — la classification reste
    fondée sur le CONTENU de l'action, jamais sur un site/une app en dur."""
    registry, safety, bus, agent = _setup(tmp_path)
    try:
        result = execute(registry, safety, _call("browser.click", {"target": "supprimer mon compte"}))
        assert result.status == ToolResultStatus.PERMISSION_DENIED
    finally:
        agent.shutdown()


def test_dismiss_overlay_is_safe_and_executes_without_confirmation(tmp_path, server):
    """Phase 11 (§3) : fermer une bannière (cookies, pub) — un des exemples
    SAFE explicitement donnés par la consigne — ne demande plus de
    confirmation. `dismiss_overlay` ne porte de toute façon aucun argument
    texte (rien à scanner pour un verbe dangereux) : toujours SAFE."""
    registry, safety, bus, agent = _setup(tmp_path)
    try:
        result = execute(registry, safety, _call("browser.dismiss_overlay", {}))
        assert result.status != ToolResultStatus.PERMISSION_DENIED
    finally:
        agent.shutdown()


def test_click_adding_to_cart_is_safe_never_confused_with_buying(tmp_path, server):
    """Addendum 'Browser Robustness / Task Completion' : ajouter un produit
    au panier est une action réversible et sans conséquence financière —
    contrairement à un achat, elle ne doit jamais exiger de confirmation.
    Aucun stem dangereux ('buy'/'achet'/'payer'/'commande'/...) n'est
    présent dans une description de type 'ajouter au panier'."""
    registry, safety, bus, agent = _setup(tmp_path)
    try:
        for target in ("ajouter au panier", "add to cart", "Ajouter au panier — Manette PS5"):
            result = execute(registry, safety, _call("browser.click", {"target": target}))
            assert result.status != ToolResultStatus.PERMISSION_DENIED, target
    finally:
        agent.shutdown()


def test_click_buying_or_checking_out_still_requires_confirmation(tmp_path, server):
    """Non-régression : une action financière réelle (acheter/commander/
    payer/checkout) reste SENSITIVE, quel que soit le produit ou le site —
    jamais résolu en demandant une confirmation par clic générique, mais en
    détectant le CONTENU financier de l'action elle-même."""
    registry, safety, bus, agent = _setup(tmp_path)
    try:
        for target in ("acheter maintenant", "buy now", "passer la commande", "checkout", "payer"):
            result = execute(registry, safety, _call("browser.click", {"target": target}))
            assert result.status == ToolResultStatus.PERMISSION_DENIED, target
    finally:
        agent.shutdown()


def test_click_on_consent_customization_choice_still_requires_confirmation(tmp_path, server):
    """Addendum 'Cookie Safety' : une bannière cookies simple (accepter/
    fermer) est un obstacle visuel non sensible (SAFE, cf. test ci-dessus),
    mais un choix de consentement explicite nécessitant une validation
    ('valider mes choix', 'confirmer mes préférences') reste bloqué — la
    consigne interdit un contournement généralisé du consentement."""
    registry, safety, bus, agent = _setup(tmp_path)
    try:
        result = execute(registry, safety, _call("browser.click", {"target": "valider mes choix de cookies"}))
        assert result.status == ToolResultStatus.PERMISSION_DENIED
    finally:
        agent.shutdown()


def test_click_dismissing_a_simple_cookie_or_language_prompt_is_safe(tmp_path, server):
    """Addendum 'Cookie / Language' : un clic sur un bouton d'acceptation
    simple ou un sélecteur de langue, décrit sans verbe financier/
    destructeur, reste SAFE — traité comme un obstacle de navigation
    courant, pas comme une décision sensible."""
    registry, safety, bus, agent = _setup(tmp_path)
    try:
        for target in ("Accepter tous les cookies", "Alle akzeptieren", "English", "Nederlands"):
            result = execute(registry, safety, _call("browser.click", {"target": target}))
            assert result.status != ToolResultStatus.PERMISSION_DENIED, target
    finally:
        agent.shutdown()


def test_safety_stop_active_blocks_browser_tool_before_device_execution(tmp_path, server):
    registry, safety, bus, agent = _setup(tmp_path)
    try:
        safety.request_stop("test")
        result = execute(registry, safety, _call("browser.navigate", {"url": server.url_for("plain.html")}))
        assert result.status == ToolResultStatus.CANCELLED
        assert result.error.code == "STOP_ACTIVE"
    finally:
        agent.shutdown()


def test_risk_classification_matches_browser_read_vs_interact_tags():
    from raya.safety.risk import classify_risk

    assert classify_risk(["browser.read"]) == PermissionLevel.SAFE
    assert classify_risk(["browser.interact"]) == PermissionLevel.SENSITIVE
