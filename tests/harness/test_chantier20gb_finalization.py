"""Chantier 20G-B — Objective Finalization + Context Compaction.

Tests couvrant :
- T1  : budget épuisé + dernière action SUCCESS → réponse non-failure
- T2  : budget épuisé → evidence dans le contexte de finalisation
- T3  : budget épuisé + evidence insuffisante → pas de faux succès
- T4  : _finalize_turn passe available_tools=None structurellement
- T5  : ESCALATE continue d'utiliser _explain_blocked_turn (inchangé)
- T6  : evidence du dernier outil présente dans les messages de finalisation
- T7  : trace de finalisation bornée (_build_finalization_trace)
- T8  : evidence ancienne conservée si elle existe
- T9  : _compact_read_page_output préserve url/title/cookie_banner/inputs
- T10 : _compact_read_page_output limite buttons à 20
- T11 : _compact_read_page_output limite links à 25
- T12 : aucun mot-clé lexical de completion dans _finalize_turn

Discipline : seul le modèle est scripté (FakeScriptedProvider) —
Tools/Safety/World State/Cognition restent 100% réels.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.contracts import (  # noqa: E402
    Channel,
    ContentPart,
    ErrorInfo,
    FinishReason,
    HarnessRequest,
    InterfaceInput,
    ModelResponse,
    PermissionLevel,
    RequestedToolCall,
    Tool,
    ToolResult,
    ToolResultStatus,
)
from raya.harness import Harness


# ---------------------------------------------------------------------------
# Helpers communs
# ---------------------------------------------------------------------------

def _text_response(text: str) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake",
        content=[ContentPart(type="text", value=text)],
        finish_reason=FinishReason.COMPLETED,
    )


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[],
        finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


def _req(text: str, session_id: str = "s1") -> HarnessRequest:
    return HarnessRequest(channel=Channel.CLI, session_id=session_id, input=InterfaceInput(text=text))


def _register_success_with_evidence(handles, name: str = "test.success_evidence") -> None:
    """Outil SAFE qui réussit toujours et retourne une evidence structurée."""
    handles.tools.register(
        Tool(
            name=name, description="réussit avec evidence (test)", capability_tags=["utils"],
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE,
        ),
        lambda call: ToolResult(
            tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
            output={"status": "ok", "url": "/confirmed"},
            evidence={"url": "/confirmed", "cart": "1 article"},
        ),
    )


def _register_success_no_evidence(handles, name: str = "test.success_no_evidence") -> None:
    """Outil SAFE qui réussit mais sans evidence."""
    handles.tools.register(
        Tool(
            name=name, description="réussit sans evidence (test)", capability_tags=["utils"],
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE,
        ),
        lambda call: ToolResult(
            tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
            output={"status": "ok"}, evidence=None,
        ),
    )


def _register_always_fail_identical(handles, name: str = "test.fail_identical") -> None:
    """Outil qui échoue toujours avec le même résultat — déclenche ESCALATE."""
    handles.tools.register(
        Tool(
            name=name, description="échoue toujours (test ESCALATE)", capability_tags=["utils"],
            input_schema={"type": "object", "properties": {"x": {"type": "integer"}}, "required": ["x"]},
            output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE,
        ),
        lambda call: ToolResult(
            tool_call_id=call.id, status=ToolResultStatus.FAILURE,
            error=ErrorInfo(code="FAIL_ALWAYS", message="échec identique", retryable=True),
        ),
    )


# ---------------------------------------------------------------------------
# T1 — Budget épuisé + dernière action SUCCESS → réponse non-failure
# ---------------------------------------------------------------------------

def test_budget_exhausted_last_action_success(tmp_path):
    """T1 : quand le budget est épuisé après une action réussie, la réponse
    finale n'est PAS une déclaration d'échec — c'est le résultat de
    _finalize_turn, qui peut honnêtement déclarer le succès si l'evidence
    le montre."""
    script = [
        _tool_call_response("test.success_evidence", {}),
        _tool_call_response("test.success_evidence", {}),
        _text_response("Action accomplie — 1 article dans le panier."),
    ]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=2)
    try:
        _register_success_with_evidence(handles)
        handles.harness.handle_request(_req("ajoute au panier"))
        response = handles.harness.response_text("s1")
        # La réponse est celle de _finalize_turn (3e call modèle), pas "got stuck"
        assert "got stuck" not in response.lower()
        assert "accomplie" in response or "panier" in response
        assert len(fake.calls) == 3
    finally:
        handles.shutdown()


# ---------------------------------------------------------------------------
# T2 — Budget épuisé → evidence dans le contexte de finalisation
# ---------------------------------------------------------------------------

def test_budget_exhausted_evidence_in_finalization_context(tmp_path):
    """T2 : _finalize_turn transmet l'evidence de la dernière action dans le
    contexte envoyé au modèle — le modèle peut voir les preuves réelles."""
    script = [
        _tool_call_response("test.success_evidence", {}),
        _text_response("ok"),
    ]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=1)
    try:
        _register_success_with_evidence(handles)
        handles.harness.handle_request(_req("fais une action"))
        assert len(fake.calls) == 2
        finalization_call = fake.calls[1]
        user_texts = [
            p.value
            for m in finalization_call.messages if m.role == "user"
            for p in m.content if p.type == "text"
        ]
        combined = " ".join(user_texts)
        assert "/confirmed" in combined, "evidence url not found in finalization context"
        assert "1 article" in combined, "evidence cart not found in finalization context"
    finally:
        handles.shutdown()


# ---------------------------------------------------------------------------
# T3 — Budget épuisé + evidence insuffisante → système n'affirme pas le succès
# ---------------------------------------------------------------------------

def test_budget_exhausted_no_evidence_system_honest(tmp_path):
    """T3 : quand le budget est épuisé et que les outils n'ont produit aucune
    evidence, le prompt système de _finalize_turn demande l'honnêteté sans
    affirmer l'échec — jamais 'got stuck'."""
    script = [
        _tool_call_response("test.success_no_evidence", {}),
        _text_response("Je n'ai pas pu confirmer le résultat."),
    ]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=1)
    try:
        _register_success_no_evidence(handles)
        handles.harness.handle_request(_req("essaie quelque chose"))
        assert len(fake.calls) == 2
        finalization_call = fake.calls[1]
        system_texts = [
            p.value
            for m in finalization_call.messages if m.role == "system"
            for p in m.content if p.type == "text"
        ]
        combined = " ".join(system_texts)
        assert "got stuck" not in combined, "_finalize_turn ne doit pas dire 'got stuck'"
        assert "budget" in combined.lower() or "action budget" in combined.lower()
    finally:
        handles.shutdown()


# ---------------------------------------------------------------------------
# T4 — _finalize_turn passe available_tools=None
# ---------------------------------------------------------------------------

def test_finalization_available_tools_none(tmp_path):
    """T4 : _finalize_turn appelle _invoke_model avec available_tools=None —
    le modèle ne peut structurellement pas demander un 13e outil."""
    script = [
        _tool_call_response("test.success_evidence", {}),
        _tool_call_response("test.success_evidence", {}),
        _text_response("finalisation"),
    ]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=2)
    try:
        _register_success_with_evidence(handles)
        handles.harness.handle_request(_req("deux actions"))
        assert len(fake.calls) == 3
        # Les 2 premiers appels ont des outils disponibles (ils demandent des tools)
        assert fake.calls[0].available_tools is not None
        assert fake.calls[1].available_tools is not None
        # Le 3e appel (finalisation) n'a PAS d'outils
        finalization_call = fake.calls[2]
        assert finalization_call.available_tools is None, (
            "_finalize_turn must pass available_tools=None"
        )
    finally:
        handles.shutdown()


# ---------------------------------------------------------------------------
# T5 — ESCALATE continue d'utiliser _explain_blocked_turn (inchangé)
# ---------------------------------------------------------------------------

def test_escalate_uses_explain_blocked_not_finalize(tmp_path):
    """T5 : LoopDetector.ESCALATE → _explain_blocked_turn (sémantique 'got stuck'
    inchangée) — jamais _finalize_turn. Séparation BLOCKED ≠ BUDGET_EXHAUSTED."""
    # Même outil, mêmes arguments, 2 fois → ESCALATE après la 2e
    script = [
        _tool_call_response("test.fail_identical", {"x": 1}),
        _tool_call_response("test.fail_identical", {"x": 1}),
        _text_response("l'agent était bloqué"),
    ]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=4)
    try:
        _register_always_fail_identical(handles)
        handles.harness.handle_request(_req("essaie"))
        # 3 calls : iter1, iter2 (ESCALATE détecté), _explain_blocked_turn
        assert len(fake.calls) == 3
        escalate_call = fake.calls[2]
        system_texts = [
            p.value
            for m in escalate_call.messages if m.role == "system"
            for p in m.content if p.type == "text"
        ]
        combined = " ".join(system_texts)
        assert "got stuck" in combined, (
            "_explain_blocked_turn doit contenir 'got stuck' pour les vrais blocages"
        )
        assert "action budget" not in combined, (
            "_explain_blocked_turn ne doit pas dire 'action budget' — c'est _finalize_turn"
        )
    finally:
        handles.shutdown()


# ---------------------------------------------------------------------------
# T6 — Evidence présente dans les messages de finalisation
# ---------------------------------------------------------------------------

def test_evidence_present_in_finalization_messages(tmp_path):
    """T6 : les messages envoyés au modèle lors de la finalisation contiennent
    bien les evidence des dernières actions de la trace."""
    script = [
        _tool_call_response("test.success_evidence", {}),
        _tool_call_response("test.success_evidence", {}),
        _tool_call_response("test.success_evidence", {}),
        _text_response("résultat"),
    ]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=3)
    try:
        _register_success_with_evidence(handles)
        handles.harness.handle_request(_req("fais 3 actions"))
        assert len(fake.calls) == 4
        finalization_call = fake.calls[3]
        all_texts = [
            p.value
            for m in finalization_call.messages
            for p in m.content if p.type == "text"
        ]
        combined = " ".join(all_texts)
        assert "/confirmed" in combined, "evidence url must appear in finalization messages"
    finally:
        handles.shutdown()


# ---------------------------------------------------------------------------
# T7 — Trace de finalisation bornée (_build_finalization_trace)
# ---------------------------------------------------------------------------

def test_build_finalization_trace_bounded():
    """T7 : _build_finalization_trace limite la trace à max_entries — les
    dernières actions sont toujours incluses (recency)."""
    trace = [
        {"tool_name": f"t{i}", "status": "success", "outcome": "SUCCESS", "evidence": None}
        for i in range(15)
    ]
    result = Harness._build_finalization_trace(trace, max_entries=8)
    assert len(result) <= 8
    # Les 4 dernières actions sont toujours incluses (recent_count=4)
    tool_names = [e["tool_name"] for e in result]
    assert "t14" in tool_names
    assert "t13" in tool_names
    assert "t12" in tool_names
    assert "t11" in tool_names


def test_build_finalization_trace_short_trace_unchanged():
    """T7b : trace courte (≤ max_entries) → retournée intégralement."""
    trace = [
        {"tool_name": f"t{i}", "status": "success", "outcome": "SUCCESS", "evidence": None}
        for i in range(5)
    ]
    result = Harness._build_finalization_trace(trace, max_entries=8)
    assert len(result) == 5


# ---------------------------------------------------------------------------
# T8 — Evidence ancienne conservée
# ---------------------------------------------------------------------------

def test_build_finalization_trace_older_evidence_included():
    """T8 : une action ancienne avec evidence est conservée même si elle
    dépasse les 4 dernières actions (recency window) — la pertinence prime."""
    trace = [
        # Action ancienne avec evidence critique
        {"tool_name": "t0_important", "status": "success", "outcome": "SUCCESS",
         "evidence": {"url": "/cart/confirmed"}},
        # 9 actions sans evidence
        *[{"tool_name": f"t{i}", "status": "success", "outcome": "SUCCESS", "evidence": None}
          for i in range(1, 10)],
    ]
    result = Harness._build_finalization_trace(trace, max_entries=8)
    tool_names = [e["tool_name"] for e in result]
    assert "t0_important" in tool_names, (
        "older step with evidence should be included in finalization trace"
    )


# ---------------------------------------------------------------------------
# T9 — _compact_read_page_output préserve url/title/cookie_banner/inputs
# ---------------------------------------------------------------------------

def test_compact_read_page_preserves_essential_fields():
    """T9 : url, title, cookie_banner et inputs sont intégralement conservés
    après compaction."""
    data = {
        "url": "https://amazon.fr/dp/B0D",
        "title": "AirPods Pro 3 - Amazon.fr",
        "cookie_banner": True,
        "inputs": [
            {"kind": "input", "text": "Rechercher", "placeholder": "Rechercher Amazon.fr", "left": 100, "top": 50}
        ],
        "buttons": [{"kind": "button", "text": f"btn{i}", "tag": "button"} for i in range(50)],
        "links": [{"kind": "link", "text": f"link{i}", "tag": "a"} for i in range(30)],
    }
    result = Harness._compact_read_page_output(data)
    assert isinstance(result, dict)
    assert result["url"] == "https://amazon.fr/dp/B0D"
    assert result["title"] == "AirPods Pro 3 - Amazon.fr"
    assert result["cookie_banner"] is True
    assert len(result["inputs"]) == 1
    assert result["inputs"][0]["text"] == "Rechercher"


# ---------------------------------------------------------------------------
# T10 — _compact_read_page_output limite buttons à 20
# ---------------------------------------------------------------------------

def test_compact_read_page_buttons_limit():
    """T10 : buttons limités à 20 — les premiers éléments (prioritaires
    selon _STRUCT_JS) sont conservés, les suivants tronqués."""
    data = {
        "url": "https://amazon.fr/dp/B0D",
        "title": "Produit",
        "cookie_banner": False,
        "inputs": [],
        "buttons": [{"kind": "button", "text": f"btn{i}", "tag": "button"} for i in range(120)],
        "links": [],
    }
    result = Harness._compact_read_page_output(data)
    assert len(result["buttons"]) == 20
    # Les premiers boutons (prioritaires) sont conservés en ordre
    assert result["buttons"][0]["text"] == "btn0"
    assert result["buttons"][19]["text"] == "btn19"
    # Méta-information de troncature
    assert result.get("buttons_capped") == 100


def test_compact_read_page_no_truncation_when_few_buttons():
    """T10b : si buttons ≤ 20, pas de troncature, pas de champ buttons_capped."""
    data = {
        "url": "https://example.com",
        "title": "Page",
        "cookie_banner": False,
        "inputs": [],
        "buttons": [{"kind": "button", "text": f"btn{i}", "tag": "button"} for i in range(15)],
        "links": [],
    }
    result = Harness._compact_read_page_output(data)
    assert len(result["buttons"]) == 15
    assert "buttons_capped" not in result


# ---------------------------------------------------------------------------
# T11 — _compact_read_page_output limite links à 25
# ---------------------------------------------------------------------------

def test_compact_read_page_links_limit():
    """T11 : links limités à 25 — les premiers liens (ordre DOM) conservés."""
    data = {
        "url": "https://amazon.fr",
        "title": "Amazon",
        "cookie_banner": False,
        "inputs": [],
        "buttons": [],
        "links": [{"kind": "link", "text": f"link{i}", "tag": "a"} for i in range(60)],
    }
    result = Harness._compact_read_page_output(data)
    assert len(result["links"]) == 25
    assert result["links"][0]["text"] == "link0"
    assert result.get("links_capped") == 35


def test_compact_read_page_safe_fallback_on_invalid_string():
    """T11b : si l'output est une string non-JSON, retour inchangé (repli sûr)."""
    bad_output = "not json at all"
    result = Harness._compact_read_page_output(bad_output)
    assert result == bad_output


def test_compact_read_page_none_passthrough():
    """T11c : None passé → None retourné (repli sûr)."""
    assert Harness._compact_read_page_output(None) is None


# ---------------------------------------------------------------------------
# T12 — Aucun mot-clé lexical de completion dans _finalize_turn
# ---------------------------------------------------------------------------

def test_no_lexical_completion_keyword_in_finalize_turn():
    """T12 : _finalize_turn ne contient aucune heuristique lexicale de détection
    de complétion (cart/panier/added/commande/order) — c'est le modèle qui
    interprète les preuves, jamais une string-match codée en dur."""
    import inspect
    source = inspect.getsource(Harness._finalize_turn)
    forbidden = ["\"cart\"", "'cart'", "\"panier\"", "'panier'", "\"added\"", "'added'",
                 "\"commande\"", "'commande'", "\"order\"", "'order'"]
    violations = [kw for kw in forbidden if kw in source]
    assert not violations, (
        f"_finalize_turn contains lexical completion keywords: {violations}"
    )
