"""tools/catalog/notify.py — capacité générique d'envoi proactif de message
(RAYA V2 Phase 11, addendum "Telegram outbound capability"). Injection de
dépendance étroite (`NotifyOps`, même pattern que `TaskControlOps`) : ces
tests utilisent un callable factice à la place du vrai `TelegramChannel.
send_proactive`, exactement comme `test_task_control_catalog.py` teste
`TaskControlOps` sans dépendre de `raya.harness`."""

from __future__ import annotations

from raya.contracts import PermissionLevel, ToolCall, ToolCallRequester, ToolResultStatus
from raya.safety import AuditTrail, SafetyService, StopController
from raya.safety.risk import classify_risk
from raya.tools import ToolRegistry, execute
from raya.tools.catalog import NotifyOps, register_notify_tools


def _call(arguments: dict) -> ToolCall:
    return ToolCall(tool_name="telegram.send_message", arguments=arguments, correlation_id="c1",
                     requested_by=ToolCallRequester(subsystem="harness", session_id="s1"))


def _setup(send_telegram):
    registry = ToolRegistry()
    register_notify_tools(registry, NotifyOps(send_telegram=send_telegram))
    safety = SafetyService(StopController(), AuditTrail())
    return registry, safety


def test_tool_registered_with_expected_shape():
    registry, _safety = _setup(lambda text: True)
    tool = registry.get("telegram.send_message")
    assert tool is not None
    assert tool.capability_tags == ["notify.telegram"]
    assert tool.permission_level == PermissionLevel.SAFE
    assert tool.input_schema["required"] == ["text"]


def test_send_succeeds_and_calls_the_injected_callable():
    sent = []
    registry, safety = _setup(lambda text: (sent.append(text), True)[1])
    result = execute(registry, safety, _call({"text": "je suis rentré"}))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output == {"sent": True}
    assert sent == ["je suis rentré"]


def test_send_never_requires_confirmation_safe_by_construction():
    """Le destinataire n'est jamais un paramètre du modèle (toujours résolu
    vers le seul chat_id déjà connu/autorisé) — jamais bloqué par Safety."""
    registry, safety = _setup(lambda text: True)
    result = execute(registry, safety, _call({"text": "salut"}))
    assert result.status != ToolResultStatus.PERMISSION_DENIED


def test_missing_text_argument_fails_honestly():
    """`text` est requis par le schéma -> rejeté par la validation générique
    (tools/validation.py), avant même d'atteindre le handler."""
    registry, safety = _setup(lambda text: True)
    result = execute(registry, safety, _call({}))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "VALIDATION_ERROR"


def test_blank_text_argument_fails_honestly_in_the_handler():
    """Une chaîne vide/blanche passe la validation de schéma (c'est bien une
    string) mais reste refusée par le handler lui-même — jamais un envoi
    Telegram vide."""
    registry, safety = _setup(lambda text: True)
    result = execute(registry, safety, _call({"text": "   "}))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "MISSING_ARGUMENT"


def test_no_known_chat_fails_honestly_never_silently_swallowed():
    registry, safety = _setup(lambda text: False)  # aucun chat_id connu
    result = execute(registry, safety, _call({"text": "salut"}))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "NO_KNOWN_TELEGRAM_CHAT"


def test_send_exception_never_crashes_the_pipeline():
    def boom(text):
        raise RuntimeError("panne réseau")

    registry, safety = _setup(boom)
    result = execute(registry, safety, _call({"text": "salut"}))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "TELEGRAM_SEND_FAILED"


def test_notify_telegram_capability_tag_is_safe_in_risk_table():
    assert classify_risk(["notify.telegram"]) == PermissionLevel.SAFE
