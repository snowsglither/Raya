"""Outils de démonstration RÉELS (consigne Phase 3 §0, §25-27) — pas de mock :
lisent/écrivent réellement des fichiers sandboxés, réussissent/échouent réellement."""

from __future__ import annotations

from raya.contracts import PermissionDecision, ToolCall, ToolCallRequester, ToolResultStatus
from raya.safety import AuditTrail, SafetyService, StopController
from raya.event_bus import EventBus
from raya.tools import ToolRegistry, execute
from raya.tools.catalog import register_demo_tools


def _setup(tmp_path):
    bus = EventBus()
    registry = ToolRegistry()
    register_demo_tools(registry, tmp_path / "workspace")
    safety = SafetyService(StopController(bus), AuditTrail())
    return registry, safety, bus


def _call(tool_name: str, arguments: dict) -> ToolCall:
    return ToolCall(
        tool_name=tool_name, arguments=arguments, correlation_id="c1",
        requested_by=ToolCallRequester(subsystem="harness", session_id="s1"),
    )


def test_write_file_creates_real_file_on_disk(tmp_path):
    registry, safety, bus = _setup(tmp_path)
    result = execute(registry, safety, _call("filesystem.write_file", {"path": "a.txt", "content": "hello"}))
    assert result.status == ToolResultStatus.SUCCESS
    real_file = tmp_path / "workspace" / "a.txt"
    assert real_file.exists()
    assert real_file.read_text(encoding="utf-8") == "hello"
    assert result.evidence["path_exists"] is True


def test_read_file_returns_real_content(tmp_path):
    registry, safety, bus = _setup(tmp_path)
    execute(registry, safety, _call("filesystem.write_file", {"path": "b.txt", "content": "contenu réel"}))
    result = execute(registry, safety, _call("filesystem.read_file", {"path": "b.txt"}))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["content"] == "contenu réel"


def test_read_missing_file_fails_honestly(tmp_path):
    registry, safety, bus = _setup(tmp_path)
    result = execute(registry, safety, _call("filesystem.read_file", {"path": "does_not_exist.txt"}))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "FILE_NOT_FOUND"


def test_path_traversal_rejected(tmp_path):
    registry, safety, bus = _setup(tmp_path)
    result = execute(registry, safety, _call("filesystem.write_file", {"path": "../../escape.txt", "content": "x"}))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "INVALID_ARGUMENT"
    assert not (tmp_path.parent.parent / "escape.txt").exists()


def test_always_fail_fails_deterministically(tmp_path):
    """Test du HANDLER directement (registry.handler_for) — demo.* est
    volontairement SENSITIVE (test dédié ci-dessous), donc passer par execute()
    testerait la passerelle Safety, pas le comportement de l'outil lui-même."""
    registry, safety, bus = _setup(tmp_path)
    handler = registry.handler_for("demo.always_fail")
    result = handler(_call("demo.always_fail", {}))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "DEMO_DETERMINISTIC_FAILURE"


def test_idempotent_counter_same_key_never_double_increments(tmp_path):
    registry, safety, bus = _setup(tmp_path)
    handler = registry.handler_for("demo.idempotent_counter")
    call1 = _call("demo.idempotent_counter", {})
    call1.idempotency_key = "stable-key-1"
    result1 = handler(call1)
    call2 = _call("demo.idempotent_counter", {})
    call2.idempotency_key = "stable-key-1"  # même clé -> même appel logique rejoué
    result2 = handler(call2)
    assert result1.output["counter"] == result2.output["counter"]
    assert result2.output["deduplicated"] is True


def test_idempotent_counter_different_keys_increment_separately(tmp_path):
    registry, safety, bus = _setup(tmp_path)
    handler = registry.handler_for("demo.idempotent_counter")
    call1 = _call("demo.idempotent_counter", {})
    call1.idempotency_key = "key-a"
    result1 = handler(call1)
    call2 = _call("demo.idempotent_counter", {})
    call2.idempotency_key = "key-b"
    result2 = handler(call2)
    assert result2.output["counter"] == result1.output["counter"] + 1


def test_non_idempotent_append_adds_a_line_every_call(tmp_path):
    """Preuve concrète qu'un retry aveugle sur cet outil SERAIT dangereux —
    chaque appel a un effet réel, jamais dédupliqué (consigne §8)."""
    registry, safety, bus = _setup(tmp_path)
    handler = registry.handler_for("demo.non_idempotent_append")
    handler(_call("demo.non_idempotent_append", {"text": "ligne 1"}))
    result2 = handler(_call("demo.non_idempotent_append", {"text": "ligne 1"}))
    # même texte, même "intention" logique — mais deux lignes réelles quand même
    assert result2.evidence["line_count_after"] == 2


def test_filesystem_tools_are_safe_permission_level_sandboxed(tmp_path):
    """Documente la décision explicite (raya/safety/risk.py) : filesystem.*
    est SAFE car strictement sandboxé, contrairement à un vrai outil non
    sandboxé qui resterait SENSITIVE/DESTRUCTIVE."""
    registry, safety, bus = _setup(tmp_path)
    result = execute(registry, safety, _call("filesystem.write_file", {"path": "c.txt", "content": "x"}))
    assert result.status == ToolResultStatus.SUCCESS  # jamais bloqué par Safety (SAFE)


def test_demo_tag_tools_require_confirmation_safety_never_bypassed(tmp_path):
    """demo.* reste SENSITIVE — prouve qu'un Tool ne peut jamais s'auto-
    autoriser, même dans le catalogue de démonstration (consigne §15)."""
    registry, safety, bus = _setup(tmp_path)
    result = execute(registry, safety, _call("demo.idempotent_counter", {}))
    assert result.status == ToolResultStatus.PERMISSION_DENIED


def test_registered_tools_have_explicit_idempotent_flag(tmp_path):
    registry, safety, bus = _setup(tmp_path)
    names_idempotent = {t.name: t.idempotent for t in registry.all()}
    assert names_idempotent["filesystem.write_file"] is True
    assert names_idempotent["demo.always_fail"] is False
    assert names_idempotent["demo.idempotent_counter"] is True
    assert names_idempotent["demo.non_idempotent_append"] is False


def test_all_capability_tags_includes_filesystem_and_demo(tmp_path):
    registry, safety, bus = _setup(tmp_path)
    tags = registry.all_capability_tags()
    assert "filesystem" in tags
    assert "demo" in tags
