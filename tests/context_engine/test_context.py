"""Priorité D — Context Engine : ranking, filtering, isolation de canal,
freshness, budget, provenance, task relevance, faits stale, sortie déterministe."""

from __future__ import annotations

import time

from raya.context_engine import assemble
from raya.contracts import (
    ChannelScope,
    Confidence,
    FactStatus,
    MemoryEntry,
    MemoryLayer,
    MemoryLifecycle,
    MemoryType,
    SectionKind,
    Task,
    TaskOwner,
    WorldStateFact,
)
from raya.memory import MemoryStore
from raya.persistence import InMemoryBackend
from raya.world_state import WorldStateStore


def _stores():
    return WorldStateStore(InMemoryBackend()), MemoryStore(InMemoryBackend())


def test_system_rules_always_present():
    ws, mem = _stores()
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    assert any(s.kind == SectionKind.SYSTEM_RULES for s in ctx.sections)


def test_task_state_included_when_task_given():
    ws, mem = _stores()
    task = Task(objective="obj", owner=TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, task=task)
    task_sections = [s for s in ctx.sections if s.kind == SectionKind.TASK_STATE]
    assert len(task_sections) == 1
    assert task_sections[0].content["task_id"] == task.id
    assert ctx.task_id == task.id


def test_task_state_absent_when_no_task():
    ws, mem = _stores()
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem)
    assert not any(s.kind == SectionKind.TASK_STATE for s in ctx.sections)


# --- Chantier 16 : active_tasks (visibles hors d'un step de tâche en cours) ---

def test_active_tasks_visible_without_a_current_running_task():
    ws, mem = _stores()
    background = Task(objective="télécharger X", owner=TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem,
                    active_tasks=(background,))
    active_sections = [s for s in ctx.sections if s.kind == SectionKind.ACTIVE_TASKS]
    assert len(active_sections) == 1
    assert active_sections[0].content["task_id"] == background.id
    assert active_sections[0].content["objective"] == "télécharger X"


def test_active_tasks_excludes_the_currently_running_task_to_avoid_duplication():
    ws, mem = _stores()
    running = Task(objective="obj courant", owner=TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    other = Task(objective="autre tâche", owner=TaskOwner(channel="cli", session_id="s1"), correlation_id="c2")
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem,
                    task=running, active_tasks=(running, other))
    active_ids = {s.content["task_id"] for s in ctx.sections if s.kind == SectionKind.ACTIVE_TASKS}
    assert active_ids == {other.id}  # `running` reste seulement dans TASK_STATE, jamais dupliqué
    assert any(s.kind == SectionKind.TASK_STATE and s.content["task_id"] == running.id for s in ctx.sections)


def test_active_tasks_is_never_mandatory_can_be_trimmed_by_budget():
    """Contrairement à TASK_STATE, ACTIVE_TASKS ne doit jamais forcer le
    budget — un utilisateur avec de nombreuses tâches actives ne doit pas
    toutes les voir imposées peu importe le budget demandé (consigne §22)."""
    ws, mem = _stores()
    many = tuple(
        Task(objective=f"tâche {i} avec un objectif assez long pour peser sur le budget", owner=TaskOwner(channel="cli", session_id="s1"), correlation_id=f"c{i}")
        for i in range(50)
    )
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem,
                    active_tasks=many, budget_tokens=200)
    assert ctx.used_tokens_estimate <= 200
    active_sections = [s for s in ctx.sections if s.kind == SectionKind.ACTIVE_TASKS]
    assert len(active_sections) < 50  # au moins certaines ont dû être rognées


def test_relevant_memory_selected_irrelevant_excluded_by_channel():
    ws, mem = _stores()
    mem.write(MemoryEntry(
        type=MemoryType.PREFERENCE, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.CHAT,
        content="Ruben préfère Chrome", provenance="test",
    ))
    mem.write(MemoryEntry(
        type=MemoryType.PREFERENCE, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.VOICE,
        content="secret vocal sans rapport", provenance="test",
    ))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="chrome")
    memory_sections = [s for s in ctx.sections if s.kind == SectionKind.MEMORY]
    contents = [s.content["content"] for s in memory_sections]
    assert "Ruben préfère Chrome" in contents
    assert "secret vocal sans rapport" not in contents


def test_fresh_fact_marked_active():
    ws, mem = _stores()
    ws.apply_update(WorldStateFact(domain="pc", key="k", value="v", source="s", confidence=Confidence.KNOWN_FACT))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, world_state_domains=("pc",))
    ws_section = next(s for s in ctx.sections if s.kind == SectionKind.WORLD_STATE)
    assert ws_section.freshness.status == FactStatus.ACTIVE


def test_stale_fact_marked_not_excluded():
    ws, mem = _stores()
    ws.apply_update(WorldStateFact(
        domain="pc", key="k", value="v", source="s", confidence=Confidence.KNOWN_FACT, freshness_ttl_s=0
    ))
    time.sleep(0.01)
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, world_state_domains=("pc",))
    ws_section = next(s for s in ctx.sections if s.kind == SectionKind.WORLD_STATE)
    assert ws_section.freshness.status == FactStatus.STALE  # jamais présenté comme actif silencieusement


def test_superseded_fact_excluded_entirely():
    ws, mem = _stores()
    ws.apply_update(WorldStateFact(domain="pc", key="k", value="v1", source="s", confidence=Confidence.KNOWN_FACT))
    ws.apply_update(WorldStateFact(domain="pc", key="k", value="v2", source="s", confidence=Confidence.KNOWN_FACT))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, world_state_domains=("pc",))
    ws_sections = [s for s in ctx.sections if s.kind == SectionKind.WORLD_STATE]
    assert len(ws_sections) == 1
    assert ws_sections[0].content["value"] == "v2"


def test_provenance_present_on_every_section():
    ws, mem = _stores()
    ws.apply_update(WorldStateFact(domain="pc", key="k", value="v", source="perception:x", confidence=Confidence.KNOWN_FACT))
    mem.write(MemoryEntry(
        type=MemoryType.FACT, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.CHAT,
        content="x", provenance="user:explicit",
    ))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, world_state_domains=("pc",))
    assert all(s.provenance for s in ctx.sections)


def test_budget_never_exceeded_for_optional_sections():
    ws, mem = _stores()
    for i in range(50):
        mem.write(MemoryEntry(
            type=MemoryType.FACT, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.CHAT,
            content=f"fait numero {i} avec du texte pour occuper de la place " * 5,
            provenance="test",
        ))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, budget_tokens=200, query_text="fait")
    assert ctx.used_tokens_estimate <= 200
    assert len(ctx.sections) < 50  # certaines sections ont été exclues par le budget


def test_deterministic_output_same_inputs_same_sections():
    ws, mem = _stores()
    mem.write(MemoryEntry(
        type=MemoryType.FACT, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.CHAT,
        content="fait stable", provenance="test",
    ))
    ctx1 = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="fait")
    ctx2 = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="fait")
    kinds1 = [s.kind for s in ctx1.sections]
    kinds2 = [s.kind for s in ctx2.sections]
    assert kinds1 == kinds2


def test_conversation_history_separate_from_memory_section():
    ws, mem = _stores()
    mem.write(MemoryEntry(
        type=MemoryType.FACT, layer=MemoryLayer.CONVERSATION, channel_scope=ChannelScope.CHAT,
        content="salut ça va", provenance="interface:cli",
    ))
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, query_text="")
    assert any(s.kind == SectionKind.CONVERSATION_HISTORY for s in ctx.sections)
    assert not any(s.kind == SectionKind.MEMORY for s in ctx.sections)


def test_tool_discovery_read_only_wiring():
    from raya.contracts import PermissionLevel, Tool
    from raya.tools import ToolRegistry

    ws, mem = _stores()
    registry = ToolRegistry()
    registry.register(Tool(
        name="utils.calc", description="calculatrice", capability_tags=["utils"],
        input_schema={}, output_schema={}, permission_level=PermissionLevel.SAFE,
    ))
    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem,
        tools_registry=registry, capability_tags=("utils",),
    )
    tool_sections = [s for s in ctx.sections if s.kind == SectionKind.TOOL_SCHEMAS]
    assert len(tool_sections) == 1
    assert tool_sections[0].content["tools"][0]["name"] == "utils.calc"
