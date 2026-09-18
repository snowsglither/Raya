"""Chantier 18A — ACTIVE_TASKS enrichment (progress + steering_guidance).

Vérifie que _active_tasks_sections() inclut progress et steering_guidance
quand présents, et les EXCLUT quand absents — puis que render_system_prompt
les sérialise correctement. Ces champs permettent au modèle de rapporter
l'état réel d'une tâche de fond pendant une side question, sans accès à
TASK_STATE (injecté uniquement dans _run_long_horizon_step).
"""

from __future__ import annotations

from raya.context_engine.assembler import _active_tasks_sections
from raya.context_engine.render import render_system_prompt
from raya.contracts import (
    Context,
    ContextSection,
    SectionKind,
    Task,
    TaskOwner,
    TaskProgress,
    TaskState,
)


def _make_task(
    *,
    objective: str = "test task",
    state: TaskState = TaskState.RUNNING,
    progress: TaskProgress | None = None,
    checkpoint: dict | None = None,
    not_before: str | None = None,
) -> Task:
    return Task(
        objective=objective,
        owner=TaskOwner(channel="cli", session_id="s1"),
        correlation_id="corr-1",
        state=state,
        progress=progress or TaskProgress(),
        checkpoint=checkpoint,
        not_before=not_before,
    )


def _active_task_ctx(task: Task) -> Context:
    sections = _active_tasks_sections((task,), exclude_task_id=None)
    return Context(
        session_id="s1",
        budget_tokens=4096,
        sections=sections,
        used_tokens_estimate=0,
    )


# --- progress inclusion ---

def test_active_task_includes_progress_when_step_set():
    task = _make_task(progress=TaskProgress(current_step="slide 4 sur 10", percent=40.0))
    sections = _active_tasks_sections((task,), exclude_task_id=None)
    assert len(sections) == 1
    progress = sections[0].content.get("progress")
    assert progress is not None
    assert progress["current_step"] == "slide 4 sur 10"
    assert progress["percent"] == 40.0


def test_active_task_includes_progress_when_only_percent_set():
    task = _make_task(progress=TaskProgress(current_step="", percent=75.0))
    sections = _active_tasks_sections((task,), exclude_task_id=None)
    assert sections[0].content.get("progress") is not None
    assert sections[0].content["progress"]["percent"] == 75.0


def test_active_task_no_progress_key_when_empty():
    """TaskProgress par défaut (step="" et percent=None) → pas de clé 'progress'."""
    task = _make_task(progress=TaskProgress())
    sections = _active_tasks_sections((task,), exclude_task_id=None)
    assert "progress" not in sections[0].content


# --- steering_guidance inclusion ---

def test_active_task_includes_steering_guidance_when_set():
    task = _make_task(checkpoint={"steering_guidance": "do it in Dutch"})
    sections = _active_tasks_sections((task,), exclude_task_id=None)
    assert sections[0].content.get("steering_guidance") == "do it in Dutch"


def test_active_task_no_steering_key_when_checkpoint_none():
    task = _make_task(checkpoint=None)
    sections = _active_tasks_sections((task,), exclude_task_id=None)
    assert "steering_guidance" not in sections[0].content


def test_active_task_no_steering_key_when_checkpoint_has_no_guidance():
    task = _make_task(checkpoint={"plan": {}})
    sections = _active_tasks_sections((task,), exclude_task_id=None)
    assert "steering_guidance" not in sections[0].content


# --- render_system_prompt output ---

def test_active_task_progress_rendered_in_system_prompt():
    task = _make_task(
        objective="créer un rapport",
        progress=TaskProgress(current_step="slide 4 sur 10", percent=40.0),
    )
    rendered = render_system_prompt(_active_task_ctx(task))
    assert "Other active task" in rendered
    assert "créer un rapport" in rendered
    assert "slide 4 sur 10" in rendered
    assert "40%" in rendered


def test_active_task_steering_rendered_in_system_prompt():
    task = _make_task(checkpoint={"steering_guidance": "make it rhyme"})
    rendered = render_system_prompt(_active_task_ctx(task))
    assert "Active task directive" in rendered
    assert "make it rhyme" in rendered


def test_active_task_no_step_in_render_when_not_set():
    task = _make_task(progress=TaskProgress())
    rendered = render_system_prompt(_active_task_ctx(task))
    assert "step=" not in rendered


def test_active_task_no_steering_in_render_when_not_set():
    task = _make_task(checkpoint=None)
    rendered = render_system_prompt(_active_task_ctx(task))
    assert "Active task directive" not in rendered
