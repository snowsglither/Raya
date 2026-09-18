"""Chantier 18A — Referential Continuity: preuve de bout en bout que
l'état environnemental (World State) ET l'état d'une tâche de fond
(ACTIVE_TASKS avec progress/steering) sont tous deux présents dans le
contexte assemblé pendant une side question.

Ces tests utilisent assemble() directement — pas de vraie session Harness
(pas de provider modèle nécessaire) — pour vérifier que le chemin
complet world_state/active_tasks → Context → render fonctionne ensemble.
"""

from __future__ import annotations

from raya.context_engine import assemble
from raya.context_engine.render import render_system_prompt
from raya.contracts import (
    ChannelScope,
    Confidence,
    SectionKind,
    Task,
    TaskOwner,
    TaskProgress,
    TaskState,
    WorldStateFact,
)
from raya.memory import MemoryStore
from raya.persistence import InMemoryBackend
from raya.world_state import WorldStateStore


def _stores():
    return WorldStateStore(InMemoryBackend()), MemoryStore(InMemoryBackend())


def _make_active_task(
    *,
    objective: str = "background task",
    state: TaskState = TaskState.RUNNING,
    progress: TaskProgress | None = None,
    checkpoint: dict | None = None,
) -> Task:
    return Task(
        objective=objective,
        owner=TaskOwner(channel="cli", session_id="s1"),
        correlation_id="corr-1",
        state=state,
        progress=progress or TaskProgress(),
        checkpoint=checkpoint,
    )


# --- Active task visible in assembled context (side question path) ---

def test_active_task_visible_in_assembled_context():
    """assemble() avec active_tasks → section ACTIVE_TASKS présente."""
    ws, mem = _stores()
    task = _make_active_task(objective="rédiger un rapport")
    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT,
        world_state=ws, memory=mem,
        active_tasks=(task,),
    )
    at_sections = [s for s in ctx.sections if s.kind == SectionKind.ACTIVE_TASKS]
    assert len(at_sections) == 1
    assert at_sections[0].content["objective"] == "rédiger un rapport"


def test_active_task_with_progress_in_assembled_context():
    ws, mem = _stores()
    task = _make_active_task(
        objective="créer un diaporama",
        progress=TaskProgress(current_step="slide 4 sur 10", percent=40.0),
    )
    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT,
        world_state=ws, memory=mem,
        active_tasks=(task,),
    )
    at_sections = [s for s in ctx.sections if s.kind == SectionKind.ACTIVE_TASKS]
    progress = at_sections[0].content.get("progress")
    assert progress is not None
    assert progress["current_step"] == "slide 4 sur 10"
    assert progress["percent"] == 40.0


def test_active_task_with_steering_in_assembled_context():
    ws, mem = _stores()
    task = _make_active_task(
        checkpoint={"steering_guidance": "en néerlandais"},
    )
    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT,
        world_state=ws, memory=mem,
        active_tasks=(task,),
    )
    at_sections = [s for s in ctx.sections if s.kind == SectionKind.ACTIVE_TASKS]
    assert at_sections[0].content.get("steering_guidance") == "en néerlandais"


# --- World State + Active task coexist ---

def test_world_state_and_active_task_coexist_in_context():
    """World State et ACTIVE_TASKS coexistent dans le même contexte assemblé."""
    ws, mem = _stores()
    ws.apply_update(WorldStateFact(
        domain="browser", key="current_url", value="https://youtube.com",
        source="tool:browser.navigate", confidence=Confidence.KNOWN_FACT,
    ))
    task = _make_active_task(
        objective="traitement de fond",
        progress=TaskProgress(current_step="étape 2", percent=20.0),
    )
    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT,
        world_state=ws, memory=mem,
        active_tasks=(task,),
    )
    ws_sections = [s for s in ctx.sections if s.kind == SectionKind.WORLD_STATE]
    at_sections = [s for s in ctx.sections if s.kind == SectionKind.ACTIVE_TASKS]
    assert len(ws_sections) == 1
    assert len(at_sections) == 1
    assert ws_sections[0].content["value"] == "https://youtube.com"
    assert at_sections[0].content["objective"] == "traitement de fond"


# --- Full render path during a side question ---

def test_side_question_render_shows_task_progress():
    ws, mem = _stores()
    task = _make_active_task(
        objective="compiler les slides",
        progress=TaskProgress(current_step="slide 7 sur 20", percent=35.0),
    )
    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT,
        world_state=ws, memory=mem,
        query_text="à quelle slide en es-tu ?",
        active_tasks=(task,),
    )
    rendered = render_system_prompt(ctx)
    assert "Other active task" in rendered
    assert "compiler les slides" in rendered
    assert "slide 7 sur 20" in rendered
    assert "35%" in rendered


def test_side_question_render_shows_task_steering():
    ws, mem = _stores()
    task = _make_active_task(
        objective="rédiger en français",
        checkpoint={"steering_guidance": "passe en anglais"},
    )
    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT,
        world_state=ws, memory=mem,
        active_tasks=(task,),
    )
    rendered = render_system_prompt(ctx)
    assert "Active task directive" in rendered
    assert "passe en anglais" in rendered


def test_side_question_render_shows_world_state_and_task_progress():
    """World State (URL) + ACTIVE_TASKS (progress) sont tous deux dans le rendu."""
    ws, mem = _stores()
    ws.apply_update(WorldStateFact(
        domain="browser", key="current_url", value="https://docs.google.com",
        source="tool:browser.navigate", confidence=Confidence.KNOWN_FACT,
    ))
    task = _make_active_task(
        objective="analyser le doc",
        progress=TaskProgress(current_step="section 3", percent=60.0),
    )
    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT,
        world_state=ws, memory=mem,
        active_tasks=(task,),
    )
    rendered = render_system_prompt(ctx)
    assert "https://docs.google.com" in rendered
    assert "section 3" in rendered
    assert "60%" in rendered


# --- exclude_task_id correctly hides the current task from ACTIVE_TASKS ---

def test_current_task_excluded_from_active_tasks_section():
    """La tâche courante (task=...) ne doit PAS apparaître dans ACTIVE_TASKS
    — elle est déjà dans TASK_STATE (sinon doublon dans le contexte)."""
    ws, mem = _stores()
    from raya.contracts import Task as _Task
    t1 = _make_active_task(objective="task principale")
    t2 = _make_active_task(objective="task secondaire")
    ctx = assemble(
        session_id="s1", channel_scope=ChannelScope.CHAT,
        world_state=ws, memory=mem,
        task=t1,
        active_tasks=(t1, t2),
    )
    at_objectives = {s.content["objective"] for s in ctx.sections if s.kind == SectionKind.ACTIVE_TASKS}
    assert "task principale" not in at_objectives
    assert "task secondaire" in at_objectives
