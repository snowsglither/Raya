"""Tests Obsidian tools — list_notes, read_note, security, no auto-write."""

from __future__ import annotations

import pytest

from raya.contracts import ToolCall, ToolCallRequester, ToolResultStatus
from raya.safety import AuditTrail, SafetyService, StopController
from raya.tools import ToolRegistry
from raya.tools.catalog.obsidian import register_obsidian_tools
from raya.tools.execution import execute


def _call(tool_name: str, args: dict) -> ToolCall:
    return ToolCall(
        tool_name=tool_name, arguments=args,
        correlation_id="c1",
        requested_by=ToolCallRequester(subsystem="test", session_id="s1"),
    )


@pytest.fixture()
def vault(tmp_path):
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    (vault_dir / "Projets").mkdir()
    (vault_dir / "Journal").mkdir()
    (vault_dir / "Projets" / "RAYA.md").write_text("# RAYA\nDocs RAYA V2.", encoding="utf-8")
    (vault_dir / "Journal" / "2026-09-01.md").write_text("# Journal\nRéunion today.", encoding="utf-8")
    (vault_dir / "note.md").write_text("# Note racine", encoding="utf-8")
    return vault_dir


@pytest.fixture()
def registry_with_vault(vault):
    reg = ToolRegistry()
    svc = SafetyService(StopController(), AuditTrail())
    register_obsidian_tools(reg, vault, svc.should_stop)
    return reg, svc, vault


# ---------------------------------------------------------------------------
# list_notes
# ---------------------------------------------------------------------------

def test_list_notes_root(registry_with_vault):
    reg, svc, vault = registry_with_vault
    result = execute(reg, svc, _call("obsidian.list_notes", {}))
    assert result.status == ToolResultStatus.SUCCESS
    notes = result.output["notes"]
    assert any("RAYA.md" in n for n in notes)
    assert any("2026-09-01.md" in n for n in notes)
    assert any("note.md" in n for n in notes)


def test_list_notes_subfolder(registry_with_vault):
    reg, svc, vault = registry_with_vault
    result = execute(reg, svc, _call("obsidian.list_notes", {"folder": "Projets"}))
    assert result.status == ToolResultStatus.SUCCESS
    notes = result.output["notes"]
    assert all("Projets" in n for n in notes)
    assert any("RAYA.md" in n for n in notes)


def test_list_notes_missing_folder(registry_with_vault):
    reg, svc, vault = registry_with_vault
    result = execute(reg, svc, _call("obsidian.list_notes", {"folder": "NonExistent"}))
    assert result.status == ToolResultStatus.FAILURE


def test_list_notes_is_safe(registry_with_vault):
    reg, svc, vault = registry_with_vault
    from raya.contracts import PermissionDecision, PermissionLevel
    tool = reg.get("obsidian.list_notes")
    assert tool.permission_level == PermissionLevel.SAFE


# ---------------------------------------------------------------------------
# read_note
# ---------------------------------------------------------------------------

def test_read_note_success(registry_with_vault):
    reg, svc, vault = registry_with_vault
    result = execute(reg, svc, _call("obsidian.read_note", {"path": "Projets/RAYA.md"}))
    assert result.status == ToolResultStatus.SUCCESS
    assert "RAYA V2" in result.output["content"]


def test_read_note_auto_adds_md_extension(registry_with_vault):
    reg, svc, vault = registry_with_vault
    result = execute(reg, svc, _call("obsidian.read_note", {"path": "Projets/RAYA"}))
    assert result.status == ToolResultStatus.SUCCESS
    assert "RAYA V2" in result.output["content"]


def test_read_note_not_found(registry_with_vault):
    reg, svc, vault = registry_with_vault
    result = execute(reg, svc, _call("obsidian.read_note", {"path": "FantomeNote.md"}))
    assert result.status == ToolResultStatus.FAILURE


def test_read_note_no_directory_traversal(registry_with_vault):
    reg, svc, vault = registry_with_vault
    result = execute(reg, svc, _call("obsidian.read_note", {"path": "../../secret.txt"}))
    assert result.status == ToolResultStatus.FAILURE


def test_read_note_is_safe(registry_with_vault):
    reg, svc, vault = registry_with_vault
    from raya.contracts import PermissionLevel
    tool = reg.get("obsidian.read_note")
    assert tool.permission_level == PermissionLevel.SAFE


# ---------------------------------------------------------------------------
# no vault → no tools registered
# ---------------------------------------------------------------------------

def test_no_vault_no_tools():
    reg = ToolRegistry()
    svc = SafetyService(StopController(), AuditTrail())
    register_obsidian_tools(reg, None, svc.should_stop)
    assert reg.get("obsidian.list_notes") is None
    assert reg.get("obsidian.read_note") is None


def test_nonexistent_vault_no_tools(tmp_path):
    reg = ToolRegistry()
    svc = SafetyService(StopController(), AuditTrail())
    register_obsidian_tools(reg, tmp_path / "does_not_exist", svc.should_stop)
    assert reg.get("obsidian.list_notes") is None


# ---------------------------------------------------------------------------
# no auto-write tool exists
# ---------------------------------------------------------------------------

def test_no_write_tool_registered(registry_with_vault):
    reg, svc, vault = registry_with_vault
    assert reg.get("obsidian.write_note") is None
    assert reg.get("obsidian.create_note") is None
