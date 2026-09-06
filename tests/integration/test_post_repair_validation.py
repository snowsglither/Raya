"""Passe 'Post-Repair Real-World Validation' — protège explicitement contre
la régression du bug tool_calls/tool_call_id (le modèle recopiait un
placeholder texte après plusieurs tours d'appel d'outil) avec un test LIVE
contre le VRAI Ollama Cloud (honnêtement BLOCKED si la clé n'est pas
disponible, jamais simulé silencieusement — même discipline que
test_phase3_scenarios.py). Complète par la régression `browser.type(submit=)`
trouvée en E2E réel pendant cette passe (recherche Coolblue jamais soumise)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.harness_factory import build_test_harness  # noqa: E402
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.local_http_server import LocalFixtureServer  # noqa: E402

import pytest  # noqa: E402

from raya.contracts import (  # noqa: E402
    Channel,
    ContentPart,
    FinishReason,
    HarnessRequest,
    HarnessStatus,
    InterfaceInput,
    ModelResponse,
    RequestedToolCall,
)
from raya.persistence import SqliteBackend  # noqa: E402


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


def _req(text: str, session_id: str = "s1") -> HarnessRequest:
    return HarnessRequest(channel=Channel.CLI, session_id=session_id, input=InterfaceInput(text=text))


def _read_v1_ollama_key() -> str | None:
    v1_env = Path(r"C:\Users\ruben\OneDrive\Bureau\RAYA\.env")
    if not v1_env.exists():
        return None
    for line in v1_env.read_text(encoding="utf-8").splitlines():
        if line.startswith("OLLAMA_API_KEY="):
            return line.split("=", 1)[1].strip().strip('"').strip("'") or None
    return None


# ======================================================================
# Scénario 8 (consigne) : TOOL CALL CHAIN — protège explicitement contre la
# régression exacte corrigée (placeholder recopié par le vrai modèle).
# ======================================================================

def test_live_real_ollama_multi_tool_call_chain_never_echoes_the_old_placeholder(tmp_path):
    """LIVE (vrai Ollama Cloud, honnêtement BLOCKED si aucune clé) : un
    objectif nécessitant DEUX appels d'outils réels successifs (écrire un
    fichier, puis en écrire un second) doit produire une chaîne de tool
    calls cohérente — jamais le texte "[demande d'appel d'outil]" (bug
    corrigé), jamais un ToolResult JSON brut dans la réponse finale."""
    api_key = _read_v1_ollama_key()
    if not api_key:
        pytest.skip("BLOCKED: OLLAMA_API_KEY indisponible — non testé en réel")

    from raya.runtime.bootstrap import bootstrap
    from raya.runtime.config import load_config

    cfg = load_config()
    cfg.db_path = tmp_path / "live.sqlite3"
    cfg.tool_workspace_dir = tmp_path / "workspace"
    cfg.ollama_api_key = api_key
    cfg.max_tool_iterations = 8

    handles = bootstrap(config=cfg, backend=SqliteBackend(cfg.db_path))
    try:
        req = HarnessRequest(
            channel=Channel.CLI, session_id="live-chain",
            input=InterfaceInput(text=(
                "Utilise l'outil filesystem.write_file pour créer un fichier nommé "
                "un.txt contenant exactement 'premier', PUIS utilise-le une seconde fois "
                "pour créer un fichier nommé deux.txt contenant exactement 'second'. "
                "Réponds seulement après avoir vraiment appelé l'outil les deux fois."
            )),
        )
        state = handles.harness.handle_request(req)
        assert state.status == HarnessStatus.COMPLETED, f"real Ollama call did not complete: {state.error}"

        assert (tmp_path / "workspace" / "un.txt").exists(), "1er appel d'outil réel jamais effectué"
        assert (tmp_path / "workspace" / "deux.txt").exists(), "2e appel d'outil réel jamais effectué"

        trace = handles.harness.last_tool_trace("live-chain")
        write_calls = [t for t in trace if t["tool_name"] == "filesystem.write_file" and t["status"] == "success"]
        assert len(write_calls) >= 2, "la chaîne de 2 tool calls réels n'a pas été observée"

        response = handles.harness.response_text("live-chain")
        assert "[demande d'appel d'outil]" not in response  # régression exacte corrigée
        assert '"status"' not in response  # jamais de ToolResult JSON brut
    finally:
        handles.shutdown()


# ======================================================================
# Scénario 9 (consigne) : MULTI-TOOL CONTEXT — le modèle traite le résultat
# d'un outil comme une VRAIE observation (pas un simple texte arbitraire) et
# adapte sa décision suivante en conséquence (scripté, comportement
# déterministe à vérifier).
# ======================================================================

def test_model_uses_tool_a_result_to_decide_tool_b_arguments(tmp_path):
    """tool A échoue avec un message d'erreur spécifique -> le tour suivant
    envoyé au modèle doit contenir CE résultat réel (jamais son intention),
    permettant à un modèle scripté de réagir en conséquence pour tool B."""
    from raya.contracts import ErrorInfo, PermissionLevel, Tool, ToolResult, ToolResultStatus

    def handler_a(call):
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                           error=ErrorInfo(code="A_UNAVAILABLE", message="service A indisponible", retryable=True))

    def handler_b(call):
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output={}, evidence={})

    script = [
        _tool_call_response("test.tool_a", {}),
        _tool_call_response("test.tool_b", {"fallback_reason": "A_UNAVAILABLE"}),
        _text_response("J'ai basculé sur le plan B car A était indisponible."),
    ]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=5)
    try:
        handles.tools.register(
            Tool(name="test.tool_a", description="a", capability_tags=["utils"],
                 input_schema={"type": "object", "properties": {}}, output_schema={"type": "object"},
                 permission_level=PermissionLevel.SAFE),
            handler_a,
        )
        handles.tools.register(
            Tool(name="test.tool_b", description="b", capability_tags=["utils"],
                 input_schema={"type": "object", "properties": {"fallback_reason": {"type": "string"}}, "required": ["fallback_reason"]},
                 output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE),
            handler_b,
        )
        handles.harness.handle_request(_req("essaie A puis B si besoin"))
        # `ModelRequest.messages` référence la MÊME liste mutable tout au
        # long de la boucle (jamais une copie par appel) — la dernière
        # requête scriptée porte donc l'historique complet ; on y vérifie
        # que le VRAI résultat de tool_a (jamais une invention) a bien
        # circulé jusqu'au modèle avant qu'il ne décide d'appeler tool_b.
        all_messages = fake.calls[-1].messages
        tool_messages = [m for m in all_messages if m.role == "tool"]
        assert len(tool_messages) == 2
        assert "A_UNAVAILABLE" in tool_messages[0].content[0].value
        response = handles.harness.response_text("s1")
        assert response == "J'ai basculé sur le plan B car A était indisponible."
    finally:
        handles.shutdown()


# ======================================================================
# Régression trouvée pendant cette passe (E2E réel Coolblue) : `browser.type`
# ne soumettait jamais une recherche (Playwright `.fill()` seul ne déclenche
# aucun événement clavier) — `submit=True` presse réellement Entrée.
# ======================================================================

@pytest.fixture(scope="module")
def server():
    s = LocalFixtureServer()
    yield s
    s.shutdown()


def test_browser_type_submit_true_enables_a_real_search_flow_end_to_end(tmp_path, server):
    """Intégration complète (Harness -> Tool -> Safety -> vrai Device Agent
    -> vrai Edge) : un scénario 'taper une recherche puis valider' complet,
    jamais bloqué par une confirmation superflue (target bénin)."""
    from raya.contracts import PermissionLevel, ToolCall, ToolCallRequester, ToolResultStatus
    from raya.devices.browser import BrowserDeviceAgent
    from raya.safety import AuditTrail, SafetyService, StopController
    from raya.tools import ToolRegistry, execute
    from raya.tools.catalog import register_browser_tools

    registry = ToolRegistry()
    agent = BrowserDeviceAgent(tmp_path / "screens")
    safety = SafetyService(StopController(), AuditTrail())
    register_browser_tools(registry, agent, should_stop=safety.should_stop)
    try:
        def _call(name, arguments):
            return ToolCall(tool_name=name, arguments=arguments, correlation_id="c1",
                             requested_by=ToolCallRequester(subsystem="harness", session_id="s1"))

        nav = execute(registry, safety, _call("browser.navigate", {"url": server.url_for("search.html")}))
        assert nav.status == ToolResultStatus.SUCCESS

        typed = execute(registry, safety, _call(
            "browser.type", {"target": "Rechercher un produit", "text": "manette PS5", "submit": True},
        ))
        assert typed.status != ToolResultStatus.PERMISSION_DENIED  # jamais de confirmation superflue
        assert typed.status == ToolResultStatus.SUCCESS

        read = execute(registry, safety, _call("browser.read_page", {}))
        assert read.output["url"] == server.url_for("products.html")  # VRAIE navigation déclenchée
    finally:
        agent.shutdown()
