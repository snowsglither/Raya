"""Chantier 18 — Referential Resolution repair.

Tests ciblés couvrant :
1. Directive système générique (résolution de référents + distinction write/type)
2. Structure des ObservationSpecs de browser.type et browser.click
3. Promotion World State après interactions browser
4. Context assembly inclut les faits browser
5. Contexte multi-tour : faits browser disponibles au tour suivant
6. Scenarios : le modèle reçoit le bon contexte pour résoudre les référents
7. Non-régressions Safety et Phase 11

Principe des tests : on ne scripte PAS le raisonnement du modèle (il est externe),
on vérifie que les DONNÉES nécessaires à sa décision sont bien transmises.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402
from support.harness_factory import build_test_harness  # noqa: E402

from raya.context_engine import assemble  # noqa: E402
from raya.context_engine.render import render_system_prompt  # noqa: E402
from raya.contracts import (  # noqa: E402
    Channel,
    ChannelScope,
    ContentPart,
    Context,
    ContextSection,
    ErrorInfo,
    FinishReason,
    HarnessRequest,
    InterfaceInput,
    ModelResponse,
    ObservationSpec,
    PermissionLevel,
    RequestedToolCall,
    SectionKind,
    Tool,
    ToolResult,
    ToolResultStatus,
    WorldStateFact,
)
from raya.memory import MemoryStore  # noqa: E402
from raya.persistence import InMemoryBackend  # noqa: E402
from raya.world_state import WorldStateStore  # noqa: E402

_KNOWN = "known_fact"  # raccourci pour les tests


def _wsfact(domain: str, key: str, value: object, source: str,
            freshness_ttl_s: int | None = None) -> WorldStateFact:
    from raya.contracts import Confidence
    return WorldStateFact(domain=domain, key=key, value=value, source=source,
                          confidence=Confidence.KNOWN_FACT, freshness_ttl_s=freshness_ttl_s)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _system_rules_ctx() -> Context:
    return Context(
        session_id="s1", budget_tokens=4096,
        sections=[ContextSection(
            kind=SectionKind.SYSTEM_RULES,
            content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
            provenance="context_engine:runtime_identity",
        )],
        used_tokens_estimate=0,
    )


def _stores():
    return WorldStateStore(InMemoryBackend()), MemoryStore(InMemoryBackend())


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
    return HarnessRequest(
        channel=Channel.CLI, session_id=session_id, input=InterfaceInput(text=text),
    )


def _register_fake_browser_type(handles):
    """Fake browser.type avec les mêmes ObservationSpecs que le catalogue."""
    specs = (
        ObservationSpec(domain="browser", key="last_typed_text",
                        evidence_field="typed_text", freshness_ttl_s=300),
        ObservationSpec(domain="browser", key="last_typed_target",
                        evidence_field="typed_target", freshness_ttl_s=300),
    )
    handles.tools.register(
        Tool(
            name="browser.type", description="saisit du texte",
            capability_tags=["browser.interact"],
            input_schema={"type": "object", "properties": {
                "target": {"type": "string"}, "text": {"type": "string"},
            }, "required": ["target", "text"]},
            output_schema={"type": "object"},
            permission_level=PermissionLevel.SENSITIVE, idempotent=False, observation=specs,
        ),
        lambda call: ToolResult(
            tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
            output={"typed": call.arguments["target"]},
            evidence={"typed_text": call.arguments["text"], "typed_target": call.arguments["target"]},
        ),
    )


def _register_fake_browser_click(handles):
    """Fake browser.click avec le même ObservationSpec que le catalogue."""
    spec = (
        ObservationSpec(domain="browser", key="last_clicked_target",
                        evidence_field="clicked_target", freshness_ttl_s=300),
    )
    handles.tools.register(
        Tool(
            name="browser.click", description="clique un élément",
            capability_tags=["browser.interact"],
            input_schema={"type": "object", "properties": {
                "target": {"type": "string"},
            }, "required": ["target"]},
            output_schema={"type": "object"},
            permission_level=PermissionLevel.SENSITIVE, idempotent=False, observation=spec,
        ),
        lambda call: ToolResult(
            tool_call_id=call.id, status=ToolResultStatus.SUCCESS,
            output={"clicked": call.arguments["target"]},
            evidence={"clicked_target": call.arguments["target"]},
        ),
    )


# ---------------------------------------------------------------------------
# Groupe 1 — Directive système : résolution de référents (3 tests)
# ---------------------------------------------------------------------------

def test_referential_resolution_directive_present_in_system_prompt():
    """La directive générique de résolution de référents est présente dans
    le system prompt rendu — le modèle reçoit l'ordre de priorité de contexte
    avant tout message utilisateur."""
    rendered = render_system_prompt(_system_rules_ctx())
    assert "priority order" in rendered
    assert "Observed environment state" in rendered
    assert "last typed" in rendered or "last_typed" in rendered


def test_context_priority_order_world_state_before_general_memory():
    """Requirement 10 : l'état observé (World State) doit apparaître AVANT
    la mémoire générale dans la directive — garantit que le contexte récent
    prime sur les souvenirs anciens lors de la résolution d'un référent."""
    rendered = render_system_prompt(_system_rules_ctx())
    idx_world = rendered.find("Observed environment state")
    idx_memory = rendered.find("general memory")
    assert idx_world != -1, "Pas de mention 'Observed environment state' dans la directive"
    assert idx_memory != -1, "Pas de mention 'general memory' dans la directive"
    assert idx_world < idx_memory, (
        "La mémoire générale apparaît avant l'état observé — ordre de priorité incorrect"
    )


def test_write_content_vs_write_to_person_distinction_in_directive():
    """Requirements 3/4/5 : la directive distingue 'écris X' (contenu) de
    'écris à X' (communication) sans mentionner de prénom ou d'application
    spécifiques — la règle est générique et linguistique."""
    rendered = render_system_prompt(_system_rules_ctx())
    # Présence de la distinction directionnelle
    assert "contact lookup" in rendered
    assert "preposition" in rendered or ("à" in rendered and "to" in rendered)
    # Aucun prénom hardcodé (vérification de généricité)
    assert "Pauliner" not in rendered
    assert "Google Traduction" not in rendered
    assert "YouTube" not in rendered


# ---------------------------------------------------------------------------
# Groupe 2 — Structure des ObservationSpecs (3 tests)
# ---------------------------------------------------------------------------

def test_browser_type_declares_last_typed_text_observation_spec():
    """browser.type déclare un ObservationSpec pour browser.last_typed_text —
    le mécanisme générique Phase 7 promouvra le texte saisi en World State."""
    from raya.tools.catalog.browser import _LAST_TYPED_OBSERVATION
    domains_keys = [(s.domain, s.key) for s in _LAST_TYPED_OBSERVATION]
    assert ("browser", "last_typed_text") in domains_keys


def test_browser_type_declares_last_typed_target_observation_spec():
    """browser.type déclare un ObservationSpec pour browser.last_typed_target —
    le modèle sait dans quel champ se trouve le dernier texte saisi."""
    from raya.tools.catalog.browser import _LAST_TYPED_OBSERVATION
    domains_keys = [(s.domain, s.key) for s in _LAST_TYPED_OBSERVATION]
    assert ("browser", "last_typed_target") in domains_keys


def test_browser_click_declares_last_clicked_target_observation_spec():
    """Requirement 2 ('relance la lecture') : browser.click déclare un
    ObservationSpec pour browser.last_clicked_target — le modèle peut
    résoudre 'relance' en voyant 'dernier clic : écouter/play/...' dans
    le World State du tour suivant."""
    from raya.tools.catalog.browser import _LAST_CLICKED_OBSERVATION
    assert len(_LAST_CLICKED_OBSERVATION) >= 1
    assert _LAST_CLICKED_OBSERVATION[0].domain == "browser"
    assert _LAST_CLICKED_OBSERVATION[0].key == "last_clicked_target"
    assert _LAST_CLICKED_OBSERVATION[0].evidence_field == "clicked_target"


# ---------------------------------------------------------------------------
# Groupe 3 — Promotion World State après interactions browser (4 tests)
# ---------------------------------------------------------------------------

def test_browser_type_promotes_last_typed_text_to_world_state(tmp_path):
    """Requirement 12 : après une exécution de browser.type, le World State
    contient browser.last_typed_text avec le texte saisi — source de vérité
    pour résoudre 'écris maintenant X' au tour suivant."""
    handles, _ = build_test_harness(tmp_path, [
        _tool_call_response("browser.type", {"target": "source field", "text": "Glodi retourne toi"}),
        _text_response("J'ai écrit 'Glodi retourne toi'."),
    ])
    try:
        _register_fake_browser_type(handles)
        handles.harness.handle_request(_req("écris Glodi retourne toi"))
        fact = handles.world_state.get_fact("browser", "last_typed_text")
        assert fact is not None
        assert fact.value == "Glodi retourne toi"
        assert fact.source == "tool:browser.type"
    finally:
        handles.shutdown()


def test_browser_type_promotes_last_typed_target_to_world_state(tmp_path):
    """Le champ cible (target) est aussi promu — le modèle sait dans quel
    champ se trouve le dernier texte, ce qui distingue 'champ de traduction'
    de 'barre de recherche'."""
    handles, _ = build_test_harness(tmp_path, [
        _tool_call_response("browser.type", {"target": "champ source", "text": "bonjour monde"}),
        _text_response("Texte saisi."),
    ])
    try:
        _register_fake_browser_type(handles)
        handles.harness.handle_request(_req("écris bonjour monde"))
        fact = handles.world_state.get_fact("browser", "last_typed_target")
        assert fact is not None
        assert fact.value == "champ source"
    finally:
        handles.shutdown()


def test_browser_click_promotes_last_clicked_target_to_world_state(tmp_path):
    """Requirement 2 : après un browser.click, le World State contient
    browser.last_clicked_target — la base pour résoudre 'relance' au tour
    suivant sans ambiguïté."""
    handles, _ = build_test_harness(tmp_path, [
        _tool_call_response("browser.click", {"target": "bouton écouter"}),
        _text_response("Lecture lancée."),
    ])
    try:
        _register_fake_browser_click(handles)
        handles.harness.handle_request(_req("lance la lecture vocale"))
        fact = handles.world_state.get_fact("browser", "last_clicked_target")
        assert fact is not None
        assert fact.value == "bouton écouter"
    finally:
        handles.shutdown()


def test_browser_type_failure_does_not_promote_to_world_state(tmp_path):
    """Requirement 17 (no false success) : un browser.type qui échoue
    (champ introuvable) ne doit JAMAIS écrire en World State — jamais un
    faux 'texte saisi' si l'action n'a pas eu lieu réellement."""
    handles, _ = build_test_harness(tmp_path, [
        _tool_call_response("browser.type", {"target": "champ inexistant", "text": "test"}),
        _text_response("Je n'ai pas trouvé le champ."),
    ])
    try:
        specs = (
            ObservationSpec(domain="browser", key="last_typed_text",
                            evidence_field="typed_text", freshness_ttl_s=300),
        )
        handles.tools.register(
            Tool(
                name="browser.type", description="fail",
                capability_tags=["browser.interact"],
                input_schema={"type": "object", "properties": {
                    "target": {"type": "string"}, "text": {"type": "string"},
                }, "required": ["target", "text"]},
                output_schema={"type": "object"},
                permission_level=PermissionLevel.SENSITIVE, observation=specs,
            ),
            lambda call: ToolResult(
                tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                error=ErrorInfo(code="FIELD_NOT_FOUND", message="champ introuvable"),
            ),
        )
        handles.harness.handle_request(_req("essaie d'écrire dans un champ inexistant"))
        assert handles.world_state.get_fact("browser", "last_typed_text") is None
    finally:
        handles.shutdown()


# ---------------------------------------------------------------------------
# Groupe 4 — Context assembly inclut les faits browser (3 tests)
# ---------------------------------------------------------------------------

def test_browser_world_state_facts_appear_in_assembled_context():
    """Les faits browser (last_typed_text, last_clicked_target, current_url)
    sont tous inclus dans le contexte assemblé pour le modèle — aucun n'est
    filtré par défaut."""
    ws, mem = _stores()
    ws.apply_update(_wsfact("browser", "current_url", "https://translate.google.com/", "tool:browser.navigate"))
    ws.apply_update(_wsfact("browser", "last_typed_text", "Glodi retourne toi", "tool:browser.type"))
    ws.apply_update(_wsfact("browser", "last_clicked_target", "bouton écouter", "tool:browser.click"))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT,
                   world_state=ws, memory=mem)
    ws_sections = [s for s in ctx.sections if s.kind == SectionKind.WORLD_STATE]
    keys_found = {s.content["key"] for s in ws_sections}
    assert "current_url" in keys_found
    assert "last_typed_text" in keys_found
    assert "last_clicked_target" in keys_found


def test_browser_world_state_rendered_in_system_prompt():
    """Les faits browser promus en World State sont rendus comme état observé
    dans le system prompt — le modèle voit 'browser.last_typed_text = X'."""
    ws, mem = _stores()
    ws.apply_update(_wsfact("browser", "last_typed_text", "Glodi retourne toi", "tool:browser.type"))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT,
                   world_state=ws, memory=mem)
    rendered = render_system_prompt(ctx)
    assert "browser.last_typed_text" in rendered
    assert "Glodi retourne toi" in rendered


def test_stale_world_state_lower_ranked_than_fresh():
    """Requirement 13 : un fait stale a un rank_score inférieur à un fait
    actif — jamais automatiquement le r��férent dominant quand il vieillit."""
    ws, mem = _stores()
    ws.apply_update(_wsfact("browser", "last_typed_text", "texte récent", "s", freshness_ttl_s=300))
    ws.apply_update(_wsfact("browser", "old_url", "ancien site", "s", freshness_ttl_s=1))
    ws.expire_fact("browser", "old_url")
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT,
                   world_state=ws, memory=mem)
    fresh = [s for s in ctx.sections
             if s.kind == SectionKind.WORLD_STATE and s.content.get("key") == "last_typed_text"]
    stale = [s for s in ctx.sections
             if s.kind == SectionKind.WORLD_STATE and s.content.get("key") == "old_url"]
    if fresh and stale:
        assert fresh[0].rank_score > stale[0].rank_score


# ---------------------------------------------------------------------------
# Groupe 5 — Contexte multi-tour (3 tests)
# ---------------------------------------------------------------------------

def test_browser_type_world_state_available_in_next_turn_context(tmp_path):
    """Requirement 1/2 : les faits World State de browser.type du tour 1
    sont présents dans le contexte ASSEMBLÉ du tour 2 — le modèle reçoit
    'dernier texte saisi' pour résoudre 'relance' ou 'écris maintenant X'."""
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("browser.type", {"target": "source", "text": "Glodi retourne toi"}),
        _text_response("Texte saisi."),
        _text_response("Je relance la lecture."),
    ])
    try:
        _register_fake_browser_type(handles)
        handles.harness.handle_request(_req("écris Glodi retourne toi"))
        handles.harness.handle_request(_req("relance la lecture"))
        ctx2 = handles.harness.last_context("s1")
        ws_keys = {s.content["key"] for s in ctx2.sections if s.kind == SectionKind.WORLD_STATE}
        assert "last_typed_text" in ws_keys, (
            "browser.last_typed_text absent du contexte du tour 2 — "
            "le modèle ne peut pas résoudre le référent 'relance'"
        )
    finally:
        handles.shutdown()


def test_assistant_response_in_conversation_history_for_next_turn(tmp_path):
    """Requirement 12 (non-régression Phase 11) : la réponse de RAYA du
    tour 1 est dans la mémoire CONVERSATION et visible dans le contexte
    du tour 2 avec le rôle 'assistant'."""
    handles, fake = build_test_harness(tmp_path, [
        _text_response("J'ai ouvert Google Traduction et lancé la lecture vocale."),
        _text_response("Je relance."),
    ])
    try:
        handles.harness.handle_request(_req("va sur Google Traduction et lance la lecture"))
        handles.harness.handle_request(_req("relance la lecture"))
        ctx2 = handles.harness.last_context("s1")
        conv = [s for s in ctx2.sections if s.kind == SectionKind.CONVERSATION_HISTORY]
        assert len(conv) == 1
        assistant_texts = [
            e["content"] for e in conv[0].content["recent"]
            if e.get("role") == "assistant"
        ]
        assert any(
            "Google Traduction" in t or "lecture" in t for t in assistant_texts
        ), "La réponse RAYA du tour 1 absente ou sans mention de lecture/Google Traduction"
    finally:
        handles.shutdown()


def test_model_receives_browser_click_fact_in_system_prompt_on_turn2(tmp_path):
    """Requirement 2 : au tour 2 ('relance la lecture'), le system prompt
    transmis au modèle contient browser.last_clicked_target — il dispose
    de la source structurée pour résoudre le référent sans ambiguïté."""
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("browser.click", {"target": "écouter"}),
        _text_response("Lecture lancée."),
        _text_response("Je relance."),
    ])
    try:
        _register_fake_browser_click(handles)
        handles.harness.handle_request(_req("lance la lecture vocale"))
        handles.harness.handle_request(_req("relance la lecture"))
        # 3e appel modèle (index 2) = premier appel du tour 2
        turn2_call = fake.calls[2]
        system_msgs = [m for m in turn2_call.messages if m.role == "system"]
        assert system_msgs, "Aucun message système transmis au modèle — contexte absent"
        system_text = system_msgs[0].content[0].value
        assert "browser.last_clicked_target" in system_text, (
            "browser.last_clicked_target absent du system prompt du tour 2"
        )
        assert "écouter" in system_text, (
            "La valeur du clic ('écouter') absente du system prompt du tour 2"
        )
    finally:
        handles.shutdown()


# ---------------------------------------------------------------------------
# Groupe 6 — Scénarios additionnels et non-régressions (4 tests)
# ---------------------------------------------------------------------------

def test_active_task_preserved_in_context_during_conversation(tmp_path):
    """Requirement 14 (non-régression Chantier 16) : une tâche active reste
    visible dans le contexte d'une conversation normale."""
    from raya.contracts import TaskOwner
    handles, _ = build_test_harness(tmp_path, [_text_response("D'accord.")])
    try:
        bg = handles.tasks.create(
            "rappel dans 10 min",
            TaskOwner(channel="cli", session_id="s1"),
            correlation_id="c1",
        )
        handles.tasks.start(bg.id)
        handles.harness.handle_request(_req("que fais-tu en ce moment ?"))
        ctx = handles.harness.last_context("s1")
        active_ids = {s.content["task_id"] for s in ctx.sections if s.kind == SectionKind.ACTIVE_TASKS}
        assert bg.id in active_ids
    finally:
        handles.shutdown()


def test_safety_not_bypassed_by_new_observation_mechanism(tmp_path):
    """Requirement 15/16 (Safety non-regression) : l'ajout d'ObservationSpecs
    ne bypass AUCUNE protection Safety — un browser.click avec cible sensible
    reste bloqué AVANT exécution, donc aucune observation n'est promue."""
    handles, fake = build_test_harness(tmp_path, [
        _tool_call_response("browser.click", {"target": "supprimer mon compte"}),
        _text_response("Je ne peux pas confirmer cette action."),
    ])
    try:
        _register_fake_browser_click(handles)
        state = handles.harness.handle_request(_req("supprime mon compte"))
        from raya.contracts import HarnessStatus
        # Le tour s'arrête sur confirmation requise ou réponse honnête
        assert state.status in (HarnessStatus.AWAITING_USER_INPUT, HarnessStatus.COMPLETED)
        # Aucun fait browser ne doit avoir été promu — l'action est bloquée
        assert handles.world_state.get_fact("browser", "last_clicked_target") is None
    finally:
        handles.shutdown()


def test_multiple_successive_types_latest_wins_in_world_state(tmp_path):
    """Requirement 3 : deux browser.type consécutifs → seul le dernier reste
    en World State (upsert). 'Pauliner il est bientôt 19h lève ton cul' est
    le texte actif, 'premier texte' est superseded."""
    handles, _ = build_test_harness(tmp_path, [
        _tool_call_response("browser.type", {"target": "source", "text": "premier texte"}),
        _tool_call_response("browser.type", {"target": "source", "text": "Pauliner il est bientôt 19h lève ton cul"}),
        _text_response("Texte saisi."),
    ])
    try:
        _register_fake_browser_type(handles)
        handles.harness.handle_request(
            _req("écris d'abord 'premier texte' puis 'Pauliner il est bientôt 19h lève ton cul'")
        )
        fact = handles.world_state.get_fact("browser", "last_typed_text")
        assert fact is not None
        assert fact.value == "Pauliner il est bientôt 19h lève ton cul"
    finally:
        handles.shutdown()


def test_no_browser_world_state_when_no_browser_action_taken(tmp_path):
    """Non-régression : sans action browser, le World State ne contient
    aucun fait browser inventé — jamais de faux contexte d'interaction."""
    handles, _ = build_test_harness(tmp_path, [_text_response("Bonjour !")])
    try:
        handles.harness.handle_request(_req("bonjour"))
        assert handles.world_state.get_fact("browser", "last_typed_text") is None
        assert handles.world_state.get_fact("browser", "last_clicked_target") is None
        assert handles.world_state.get_fact("browser", "current_url") is None
    finally:
        handles.shutdown()


def test_information_vs_action_directive_still_present_non_regression():
    """Requirement 11 (non-régression Chantier 12 §D) : la directive
    INFORMATION vs ACTION est toujours présente aux côtés de la nouvelle
    directive de résolution de référents — les deux coexistent."""
    rendered = render_system_prompt(_system_rules_ctx())
    assert "information request" in rendered.lower() or "INFORMATION" in rendered
    assert "action request" in rendered.lower() or "ACTION" in rendered
    # La nouvelle directive doit AUSSI être présente
    assert "priority order" in rendered
