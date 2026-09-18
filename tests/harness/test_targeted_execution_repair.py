"""Passe 'Targeted Execution Repair' — nudge anti-répétition générique
(Partie 6), explication détaillée à l'arrêt (Partie 10), continuité
d'objectif multi-tour (Partie 9). Même discipline que les autres tests
Harness : seul le modèle est scripté (FakeScriptedProvider), Tools/Safety/
World State/Cognition restent 100% réels."""

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


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _tool_call_response(tool_name: str, arguments: dict) -> ModelResponse:
    return ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.TOOL_CALL_PENDING,
        tool_calls_requested=[RequestedToolCall(tool_name=tool_name, arguments=arguments)],
    )


def _req(text: str, session_id: str = "s1") -> HarnessRequest:
    return HarnessRequest(channel=Channel.CLI, session_id=session_id, input=InterfaceInput(text=text))


def _register_always_fail_tool(handles, name: str = "test.always_fail_varying") -> None:
    """Un outil SAFE qui échoue toujours, quels que soient les arguments —
    simule un modèle qui retente la MÊME action avec des arguments DIFFÉRENTS
    à chaque fois (ex: deviner une URL différente), le point aveugle exact de
    `LoopDetector` (signature exacte tool+arguments) documenté Partie 6."""
    handles.tools.register(
        Tool(name=name, description="échoue toujours (test)", capability_tags=["utils"],
             input_schema={"type": "object", "properties": {"attempt": {"type": "integer"}}, "required": ["attempt"]},
             output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE),
        lambda call: ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                                 error=ErrorInfo(code="ALWAYS_FAILS", message="échec simulé", retryable=True)),
    )


def test_nudge_injected_after_repeated_same_tool_with_varying_arguments(tmp_path):
    """Partie 6 : le MÊME tool_name échoue 3 fois de suite avec des arguments
    DIFFÉRENTS à chaque fois (jamais détecté par LoopDetector, signature
    exacte) — un message système générique de changement de stratégie doit
    être injecté avant le 4e appel modèle."""
    script = [
        _tool_call_response("test.always_fail_varying", {"attempt": 1}),
        _tool_call_response("test.always_fail_varying", {"attempt": 2}),
        _tool_call_response("test.always_fail_varying", {"attempt": 3}),
        _text_response("j'abandonne cette approche."),
    ]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=4)
    try:
        _register_always_fail_tool(handles)
        handles.harness.handle_request(_req("essaie encore et encore avec des variantes"))
        assert len(fake.calls) == 4
        fourth_request = fake.calls[3]
        system_texts = [
            p.value for m in fourth_request.messages if m.role == "system" for p in m.content if p.type == "text"
        ]
        assert any("times in a row" in t and "test.always_fail_varying" in t for t in system_texts)
    finally:
        handles.shutdown()


def test_no_nudge_before_threshold_reached(tmp_path):
    """Non-régression : un seul échec, ou deux, ne doit PAS injecter de nudge
    (seuil = 3) — éviter un bruit prématuré."""
    script = [
        _tool_call_response("test.always_fail_varying", {"attempt": 1}),
        _tool_call_response("test.always_fail_varying", {"attempt": 2}),
        _text_response("bon."),
    ]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=4)
    try:
        _register_always_fail_tool(handles)
        handles.harness.handle_request(_req("essaie"))
        for call in fake.calls:
            system_texts = [p.value for m in call.messages if m.role == "system" for p in m.content if p.type == "text"]
            assert not any("times in a row" in t for t in system_texts)
    finally:
        handles.shutdown()


def test_nudge_resets_on_success(tmp_path):
    """Un succès efface le compteur de répétition — appeler le même outil
    2 fois en échec, puis 1 fois en succès, puis 2 fois en échec ne doit PAS
    déclencher le nudge (jamais 3 échecs consécutifs réels)."""
    calls_seen: list[int] = []

    def handler(call):
        calls_seen.append(call.arguments["attempt"])
        if call.arguments["attempt"] == 3:
            return ToolResult(tool_call_id=call.id, status=ToolResultStatus.SUCCESS, output={}, evidence={})
        return ToolResult(tool_call_id=call.id, status=ToolResultStatus.FAILURE,
                           error=ErrorInfo(code="X", message="échec", retryable=True))

    script = [
        _tool_call_response("test.mixed", {"attempt": 1}),
        _tool_call_response("test.mixed", {"attempt": 2}),
        _tool_call_response("test.mixed", {"attempt": 3}),
        _tool_call_response("test.mixed", {"attempt": 4}),
        _tool_call_response("test.mixed", {"attempt": 5}),
        _text_response("fini."),
    ]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=6)
    try:
        handles.tools.register(
            Tool(name="test.mixed", description="d", capability_tags=["utils"],
                 input_schema={"type": "object", "properties": {"attempt": {"type": "integer"}}, "required": ["attempt"]},
                 output_schema={"type": "object"}, permission_level=PermissionLevel.SAFE),
            handler,
        )
        handles.harness.handle_request(_req("essaie"))
        for call in fake.calls:
            system_texts = [p.value for m in call.messages if m.role == "system" for p in m.content if p.type == "text"]
            assert not any("times in a row" in t for t in system_texts)
    finally:
        handles.shutdown()


def test_finalize_turn_used_at_budget_exhaustion(tmp_path):
    """Partie 10 : le message de fin de budget est désormais construit par
    `_finalize_turn` (un appel modèle borné supplémentaire) — jamais
    le JSON brut, jamais une invention au-delà de la vraie trace.
    BUDGET EXHAUSTED ≠ TASK FAILED."""
    infinite_tool_calls = [_tool_call_response("test.always_fail_varying", {"attempt": i}) for i in range(10)]
    handles, fake = build_test_harness(tmp_path, infinite_tool_calls, max_tool_iterations=3)
    try:
        _register_always_fail_tool(handles)
        state = handles.harness.handle_request(_req("boucle"))
        response = handles.harness.response_text("s1")
        assert "atteint" in response  # fallback _finalize_turn: "J'ai atteint la limite de mes actions"
        assert '"status"' not in response  # jamais de ToolResult JSON brut
        assert len(fake.calls) == 4  # 3 itérations + 1 appel de finalisation
    finally:
        handles.shutdown()


def test_explain_blocked_turn_uses_real_model_explanation_when_available(tmp_path):
    """Quand un modèle est disponible et répond avec un texte exploitable,
    ce texte (jamais le repli générique) devient la réponse finale."""
    script = [
        _tool_call_response("test.always_fail_varying", {"attempt": 1}),
        _tool_call_response("test.always_fail_varying", {"attempt": 1}),  # identique -> LoopDetector ESCALATE
        _text_response("Je suis bloqué : l'action échoue systématiquement, il faudrait vérifier la configuration."),
    ]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=5)
    try:
        _register_always_fail_tool(handles)
        handles.harness.handle_request(_req("essaie"))
        response = handles.harness.response_text("s1")
        assert response == "Je suis bloqué : l'action échoue systématiquement, il faudrait vérifier la configuration."
    finally:
        handles.shutdown()


def test_model_stopping_itself_with_a_human_block_explanation_ends_the_turn(tmp_path):
    """Partie 5 : quand le modèle reconnaît un blocage nécessitant une action
    humaine (login/profil/CAPTCHA/2FA), il s'arrête simplement de demander
    des outils et répond en texte — chemin déjà existant (aucune tool_call
    demandée -> réponse finale immédiate), jamais un nouveau mécanisme de
    pause. L'utilisateur peut répondre au tour suivant (continuité Phase 11)."""
    script = [
        _tool_call_response("test.always_fail_varying", {"attempt": 1}),
        _text_response("Le site me demande de choisir un profil. Choisis 'Ruben' et dis-moi quand c'est fait."),
    ]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=5)
    try:
        _register_always_fail_tool(handles)
        state = handles.harness.handle_request(_req("lance la vidéo"))
        response = handles.harness.response_text("s1")
        assert response == "Le site me demande de choisir un profil. Choisis 'Ruben' et dis-moi quand c'est fait."
        assert len(fake.calls) == 2  # s'arrête dès que le modèle ne redemande plus d'outil
    finally:
        handles.shutdown()


def test_explain_blocked_turn_never_invents_a_cause_with_empty_trace(tmp_path):
    """Partie 10 : cause inconnue -> honnêteté explicite, jamais une
    invention, même sans aucune action réelle dans la trace (le seul modèle
    disponible échoue -> repli générique, jamais un texte fabriqué)."""
    error_response = ModelResponse(
        request_id="", provider_used="fake", content=[], finish_reason=FinishReason.ERROR,
        error=ErrorInfo(code="TEST_ERROR", message="panne simulée"),
    )
    handles, fake = build_test_harness(tmp_path, [error_response])
    try:
        result = handles.harness._explain_blocked_turn("objectif de test", [], "Raison inconnue.", "corr1")
        assert "aucune action" in result
        assert "Raison inconnue." in result
        assert "prétendre avoir terminé" in result
    finally:
        handles.shutdown()


def test_tool_call_round_trip_uses_structured_ids_not_a_text_placeholder(tmp_path):
    """Bug corrigé (trouvé en E2E réel, jamais signalé) : le tour assistant
    demandant un appel d'outil doit désormais porter `Message.tool_calls`
    (jamais le texte placeholder "[demande d'appel d'outil]"), et la réponse
    `role=tool` suivante doit porter le MÊME `tool_call_id` que celui utilisé
    par le `ToolCall` réellement exécuté — jamais deux identifiants
    divergents pour le même appel."""
    script = [
        _tool_call_response("test.always_fail_varying", {"attempt": 1}),
        _text_response("bon."),
    ]
    handles, fake = build_test_harness(tmp_path, script, max_tool_iterations=4)
    try:
        _register_always_fail_tool(handles)
        handles.harness.handle_request(_req("essaie"))
        second_request = fake.calls[1]
        assistant_messages = [m for m in second_request.messages if m.role == "assistant" and m.tool_calls]
        tool_messages = [m for m in second_request.messages if m.role == "tool"]
        assert len(assistant_messages) == 1
        assert len(tool_messages) == 1
        assistant_call_id = assistant_messages[0].tool_calls[0]["id"]
        assert assistant_call_id == tool_messages[0].tool_call_id
        # Plus jamais le placeholder texte non structuré.
        assistant_text = "".join(p.value for p in assistant_messages[0].content if p.type == "text")
        assert "[demande d'appel d'outil]" not in assistant_text
    finally:
        handles.shutdown()


def test_multi_turn_objective_continuation_love_me_not_pattern(tmp_path):
    """Partie 9, scénario réel exact de la consigne : 'Tu peux me mettre Love
    Me Not mais la version d'Olivia Dean' puis 'Sur YouTube' — le 2e message
    doit être traité avec le 1er tour dans l'historique de conversation
    (Phase 11, déjà câblé), jamais une tâche déconnectée. Prouvé ici en
    vérifiant que le 2e appel modèle voit bien le 1er message ET la 1re
    réponse dans les sections rendues du system prompt."""
    script = [
        _text_response("Je m'en occupe, il me faut juste savoir où la lancer."),
        _text_response("Je lance Love Me Not d'Olivia Dean sur YouTube."),
    ]
    handles, fake = build_test_harness(tmp_path, script)
    try:
        handles.harness.handle_request(_req("Tu peux me mettre Love Me Not mais la version d'Olivia Dean"))
        handles.harness.handle_request(_req("Sur YouTube"))
        second_call = fake.calls[1]
        system_texts = " ".join(
            p.value for m in second_call.messages if m.role == "system" for p in m.content if p.type == "text"
        )
        assert "Love Me Not" in system_texts and "Olivia Dean" in system_texts
        assert "Je m'en occupe" in system_texts
    finally:
        handles.shutdown()
