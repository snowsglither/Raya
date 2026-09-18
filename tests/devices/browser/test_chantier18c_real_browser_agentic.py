"""Chantier 18C — Real Browser Agentic Execution & Security.

Tests RÉELS contre Edge/CDP via de vrais sites web — jamais de mocks Playwright.

Couvre :
- GAP CRITIQUE corrigé : input[type=password] ne jamais exposer el.value
- input_type exposé pour conscience du modèle (password/email/...)
- Audit hardcoding : aucun sélecteur site-spécifique résiduel
- Chemin production complet : browser.read_page → ToolResult → 0 fuite password
- Scénarios E2E réels avec vrai Ollama (skip si OLLAMA_API_KEY indisponible)

Sites :
  the-internet.herokuapp.com/login         — Username + Password (site de test dédié)
  the-internet.herokuapp.com/forgot_password — champ email
  example.com                               — page stable sans formulaire
  books.toscrape.com                        — catalogue produits (demo, sans achat réel)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from raya.contracts import Command, CommandStatus  # noqa: E402
from raya.devices.browser import BrowserDeviceAgent  # noqa: E402

_LOGIN = "https://the-internet.herokuapp.com/login"
_FORGOT_PWD = "https://the-internet.herokuapp.com/forgot_password"
_EXAMPLE = "https://example.com"
_BOOKS = "https://books.toscrape.com"

# Mot de passe de test — jamais un vrai secret (site de test Heroku public)
_TEST_PASSWORD = "SuperSecretPassword!"


def _read_ollama_key() -> str | None:
    for candidate in (
        Path(r"C:\Users\ruben\OneDrive\Bureau\RAYA\.env"),
        Path(__file__).resolve().parents[3] / ".env",
    ):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OLLAMA_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


@pytest.fixture(scope="module")
def agent(tmp_path_factory):
    a = BrowserDeviceAgent(tmp_path_factory.mktemp("browser18c"))
    yield a
    a.shutdown()


def _cmd(capability_name: str, arguments: dict) -> Command:
    return Command(device_id="browser_agent", capability_name=capability_name,
                   arguments=arguments, correlation_id="c18c")


# ─── Groupe 1 : GAP CRITIQUE — password JAMAIS exposé ────────────────────────

def test_password_field_value_not_in_text_in_read_page(agent):
    """input[type=password] : la VALEUR saisie absente du champ text —
    l'aria-label peut y figurer (le modèle sait que le champ existe, pas sa valeur)."""
    agent.execute(_cmd("browser.navigate", {"url": _LOGIN}))
    # Saisir un vrai mot de passe dans le champ Password
    agent.execute(_cmd("browser.type", {"target": "Password", "text": _TEST_PASSWORD}))
    r = agent.execute(_cmd("browser.read_page", {}))
    pw_inputs = [i for i in r.output["inputs"] if i.get("input_type") == "password"]
    assert len(pw_inputs) >= 1, "Champ password absent de read_page"
    for pw in pw_inputs:
        assert _TEST_PASSWORD not in pw["text"], (
            f"Valeur password exposée dans text : {pw['text']!r}"
        )


def test_secret_value_absent_from_inputs_text_list(agent):
    """La valeur du mot de passe absent de TOUS les inputs[].text."""
    agent.execute(_cmd("browser.navigate", {"url": _LOGIN}))
    agent.execute(_cmd("browser.type", {"target": "Password", "text": _TEST_PASSWORD}))
    r = agent.execute(_cmd("browser.read_page", {}))
    all_texts = [i["text"] for i in r.output["inputs"]]
    assert _TEST_PASSWORD not in all_texts, f"Valeur password dans texts : {all_texts}"
    assert not any(_TEST_PASSWORD in t for t in all_texts), "Valeur partielle password détectée"


def test_password_input_has_input_type_password(agent):
    """input[type=password] expose input_type='password' — le modèle peut savoir
    qu'un champ sensible existe sans jamais voir sa valeur."""
    agent.execute(_cmd("browser.navigate", {"url": _LOGIN}))
    r = agent.execute(_cmd("browser.read_page", {}))
    input_types = [i.get("input_type") for i in r.output["inputs"]]
    assert "password" in input_types, f"input_type='password' absent : {r.output['inputs']}"


def test_secret_absent_from_tool_result_output_json(agent):
    """ToolResult.output sérialisé en JSON ne contient pas le mot de passe."""
    agent.execute(_cmd("browser.navigate", {"url": _LOGIN}))
    agent.execute(_cmd("browser.type", {"target": "Password", "text": _TEST_PASSWORD}))
    result = agent.execute(_cmd("browser.read_page", {}))
    output_json = json.dumps(result.output)
    assert _TEST_PASSWORD not in output_json, (
        f"Mot de passe trouvé dans ToolResult.output: {output_json[:300]}"
    )


def test_secret_absent_from_tool_result_evidence_json(agent):
    """ToolResult.evidence ne contient pas le mot de passe."""
    agent.execute(_cmd("browser.navigate", {"url": _LOGIN}))
    agent.execute(_cmd("browser.type", {"target": "Password", "text": _TEST_PASSWORD}))
    result = agent.execute(_cmd("browser.read_page", {}))
    evidence_json = json.dumps(result.evidence or {})
    assert _TEST_PASSWORD not in evidence_json, (
        f"Mot de passe trouvé dans ToolResult.evidence: {evidence_json}"
    )


def test_complete_read_page_observation_contains_no_secret(agent):
    """Sérialisation COMPLÈTE de l'observation ne contient pas le mot de passe."""
    agent.execute(_cmd("browser.navigate", {"url": _LOGIN}))
    agent.execute(_cmd("browser.type", {"target": "Password", "text": _TEST_PASSWORD}))
    r = agent.execute(_cmd("browser.read_page", {}))
    full_observation = json.dumps({
        "output": r.output,
        "evidence": r.evidence,
        "status": r.status.value,
        "mechanism_used": r.mechanism_used,
    })
    assert _TEST_PASSWORD not in full_observation, (
        f"Mot de passe détecté dans l'observation complète: {full_observation[:500]}"
    )


# ─── Groupe 2 : input_type exposé ────────────────────────────────────────────

def test_email_input_has_input_type_email(agent):
    """input[type=email] expose input_type='email' dans read_page."""
    agent.execute(_cmd("browser.navigate", {"url": _FORGOT_PWD}))
    r = agent.execute(_cmd("browser.read_page", {}))
    input_types = [i.get("input_type") for i in r.output["inputs"]]
    assert "email" in input_types, f"input_type='email' absent : {r.output['inputs']}"


def test_regular_text_input_value_still_visible(agent):
    """Champ texte ordinaire (non-password) : la valeur reste visible dans read_page."""
    agent.execute(_cmd("browser.navigate", {"url": _LOGIN}))
    agent.execute(_cmd("browser.type", {"target": "Username", "text": "tomsmith"}))
    r = agent.execute(_cmd("browser.read_page", {}))
    input_texts = [i["text"] for i in r.output["inputs"]]
    assert any("tomsmith" in t for t in input_texts), (
        f"Valeur champ texte Username invisible : {input_texts}"
    )


# ─── Groupe 3 : Audit hardcoding — zéro sélecteur site-spécifique ─────────────

def test_no_amazon_specific_ids_in_struct_js():
    """#desktop_buybox et #addToCart_feature_div retirés de _STRUCT_JS."""
    from raya.devices.browser.controller import _STRUCT_JS
    assert "#desktop_buybox" not in _STRUCT_JS
    assert "#addToCart_feature_div" not in _STRUCT_JS


def test_no_site_specific_domains_in_controller():
    """Aucune référence à des domaines spécifiques dans controller.py."""
    from raya.devices.browser import controller
    import inspect
    src = inspect.getsource(controller)
    banned = ("amazon", "netflix", "coolblue", "disney", "youtube", "fnac", "darty")
    found = [b for b in banned if b in src.lower()]
    assert not found, f"Références site-spécifiques trouvées : {found}"


# ─── Groupe 4 : Scénarios E2E réels (skip si OLLAMA_API_KEY indisponible) ─────

def _build_live_handles(tmp_path: Path):
    """Bootstrap complet : vrai Ollama + vrai Edge/CDP."""
    api_key = _read_ollama_key()
    if not api_key:
        pytest.skip("BLOCKED: OLLAMA_API_KEY indisponible — scénario E2E non testé")

    from raya.runtime.bootstrap import bootstrap
    from raya.runtime.config import load_config
    from raya.persistence import SqliteBackend

    cfg = load_config()
    cfg.db_path = tmp_path / "live18c.sqlite3"
    cfg.tool_workspace_dir = tmp_path / "workspace"
    cfg.ollama_api_key = api_key
    cfg.max_tool_iterations = 8
    cfg.enable_browser_device = True
    cfg.enable_windows_device = False
    cfg.enable_perception = False
    cfg.enable_phone_device = False

    return bootstrap(config=cfg, backend=SqliteBackend(cfg.db_path))


def _assert_completed_or_skip(state, session_id: str) -> None:
    from raya.contracts import HarnessStatus
    if state.status != HarnessStatus.COMPLETED:
        err_code = (state.error.code or "") if state.error else ""
        if any(k in err_code for k in ("AUTH", "UNAVAILABLE", "RATE", "NETWORK", "TIMEOUT", "NOT_IMPLEMENTED")):
            pytest.skip(f"BLOCKED: modèle indisponible ({err_code}) — E2E non testé")
        pytest.fail(f"E2E échoué [{session_id}]: status={state.status}, error={state.error}")


def test_live_e2e_observe_and_read_page_title(tmp_path):
    """Scénario 1 — navigate → read_page → réponse avec titre réel de la page."""
    handles = _build_live_handles(tmp_path)
    try:
        from raya.contracts import Channel, HarnessRequest, InterfaceInput
        req = HarnessRequest(
            channel=Channel.CLI, session_id="e2e_s1",
            input=InterfaceInput(text=(
                f"Utilise browser.navigate pour aller sur {_EXAMPLE} "
                "puis browser.read_page pour lire la page. "
                "Dis-moi le titre exact de la page."
            )),
        )
        state = handles.harness.handle_request(req)
        _assert_completed_or_skip(state, "e2e_s1")
        trace = handles.harness.last_tool_trace("e2e_s1")
        tool_names = [t["tool_name"] for t in trace]
        assert "browser.navigate" in tool_names
        assert "browser.read_page" in tool_names
    finally:
        handles.shutdown()


def test_live_e2e_click_primary_control(tmp_path):
    """Scénario 2 — navigate → click bouton réel → verify."""
    handles = _build_live_handles(tmp_path)
    try:
        from raya.contracts import Channel, HarnessRequest, InterfaceInput
        req = HarnessRequest(
            channel=Channel.CLI, session_id="e2e_s2",
            input=InterfaceInput(text=(
                f"Va sur https://the-internet.herokuapp.com/add_remove_elements "
                "et clique le bouton 'Add Element'. Confirme quand c'est fait."
            )),
        )
        state = handles.harness.handle_request(req)
        _assert_completed_or_skip(state, "e2e_s2")
        trace = handles.harness.last_tool_trace("e2e_s2")
        click_calls = [t for t in trace if t["tool_name"] == "browser.click"]
        assert any(t["status"] == "success" for t in click_calls), (
            f"Aucun clic réussi : {click_calls}"
        )
    finally:
        handles.shutdown()


def test_live_e2e_login_no_password_in_trace(tmp_path):
    """Scénario 3 — login réel, sécurité password. Le mot de passe ne doit
    jamais apparaître dans la trace d'outils envoyée au modèle."""
    handles = _build_live_handles(tmp_path)
    try:
        from raya.contracts import Channel, HarnessRequest, InterfaceInput
        req = HarnessRequest(
            channel=Channel.CLI, session_id="e2e_s3",
            input=InterfaceInput(text=(
                f"Va sur {_LOGIN}. Connecte-toi avec le login 'tomsmith' "
                f"et le mot de passe '{_TEST_PASSWORD}'. Dis-moi si tu es connecté."
            )),
        )
        state = handles.harness.handle_request(req)
        _assert_completed_or_skip(state, "e2e_s3")
        trace = handles.harness.last_tool_trace("e2e_s3")
        trace_json = json.dumps(trace)
        assert _TEST_PASSWORD not in trace_json, (
            "FUITE CRITIQUE : mot de passe trouvé dans la trace d'outils envoyée au modèle"
        )
    finally:
        handles.shutdown()


def test_live_e2e_recovery_after_element_not_found(tmp_path):
    """Scénario 4 — DOM fallback. Le modèle tente un clic invalide,
    reçoit ELEMENT_NOT_FOUND, lit la page, et réessaie."""
    handles = _build_live_handles(tmp_path)
    try:
        from raya.contracts import Channel, HarnessRequest, InterfaceInput
        req = HarnessRequest(
            channel=Channel.CLI, session_id="e2e_s4",
            input=InterfaceInput(text=(
                f"Va sur {_EXAMPLE}. "
                "Essaie de cliquer 'Bouton Inexistant'. Si ça échoue, "
                "utilise browser.read_page pour voir les éléments disponibles "
                "et clique le premier lien disponible."
            )),
        )
        state = handles.harness.handle_request(req)
        _assert_completed_or_skip(state, "e2e_s4")
        trace = handles.harness.last_tool_trace("e2e_s4")
        click_calls = [t for t in trace if t["tool_name"] == "browser.click"]
        assert len(click_calls) >= 1, "Aucun appel browser.click dans la trace"
    finally:
        handles.shutdown()


def test_live_e2e_tool_success_confirmed_via_read_page(tmp_path):
    """Scénario 5 — tool SUCCESS ≠ objectif atteint. Le modèle navigue,
    puis appelle browser.read_page pour confirmer le résultat réel."""
    handles = _build_live_handles(tmp_path)
    try:
        from raya.contracts import Channel, HarnessRequest, InterfaceInput
        req = HarnessRequest(
            channel=Channel.CLI, session_id="e2e_s5",
            input=InterfaceInput(text=(
                f"Va sur {_BOOKS} et lis le titre de la première page. "
                "Utilise browser.read_page pour confirmer le contenu réel."
            )),
        )
        state = handles.harness.handle_request(req)
        _assert_completed_or_skip(state, "e2e_s5")
        trace = handles.harness.last_tool_trace("e2e_s5")
        tool_names = [t["tool_name"] for t in trace]
        assert "browser.read_page" in tool_names, "Pas de read_page dans la trace"
    finally:
        handles.shutdown()
