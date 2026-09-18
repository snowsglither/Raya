"""Directives système Chantier 18 (Agentic Interaction) — présence
structurelle des textes injectés par `render_system_prompt()` et rendu
du steering_guidance dans TASK_STATE. Même style que
test_chantier16_directives.py."""

from __future__ import annotations

from raya.context_engine.render import render_system_prompt
from raya.contracts import Context, ContextSection, SectionKind


def _system_rules_ctx() -> Context:
    return Context(
        session_id="s1",
        budget_tokens=4096,
        sections=[ContextSection(
            kind=SectionKind.SYSTEM_RULES,
            content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
            provenance="context_engine:runtime_identity",
        )],
        used_tokens_estimate=0,
    )


def _task_state_ctx(steering_guidance: str | None = None) -> Context:
    content: dict = {
        "task_id": "task-123",
        "objective": "test objective",
        "state": "RUNNING",
        "progress": {"current_step": "step 1", "percent": 50.0},
    }
    if steering_guidance is not None:
        content["steering_guidance"] = steering_guidance
    return Context(
        session_id="s1",
        budget_tokens=4096,
        sections=[ContextSection(
            kind=SectionKind.TASK_STATE,
            content=content,
            provenance="tasks:current",
            rank_score=1.0,
        )],
        used_tokens_estimate=0,
    )


# --- §A Side Questions ---

def test_side_questions_directive_present():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "Parallel threads" in rendered


def test_side_questions_directive_mentions_independent_threads():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "task continues from its last checkpoint" in rendered
    assert "two separate concerns" in rendered


# --- §B Communication Policy ---

def test_communication_policy_directive_present():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "Communication policy" in rendered


def test_communication_policy_directive_mentions_silent_execution():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "silent execution" in rendered
    assert "do NOT narrate every micro-step" in rendered


# --- §C Human Judgment ---

def test_human_judgment_directive_present():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "Human judgment" in rendered


def test_human_judgment_directive_requires_recommendation():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "always include" in rendered
    assert "recommendation" in rendered


# --- §D Task Steering ---

def test_task_steering_directive_present():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "Task steering" in rendered


def test_task_steering_directive_mentions_updated_directive_marker():
    rendered = render_system_prompt(_system_rules_ctx())
    assert "Updated user directive" in rendered
    assert "Preserve all work already verified" in rendered


# --- TASK_STATE steering_guidance rendering ---

def test_task_state_renders_without_steering_guidance_normally():
    rendered = render_system_prompt(_task_state_ctx())
    assert "Current background task" in rendered
    assert "test objective" in rendered
    assert "Updated user directive for this task" not in rendered


def test_task_state_renders_steering_guidance_when_present():
    rendered = render_system_prompt(_task_state_ctx(steering_guidance="do it in Dutch"))
    assert "Updated user directive for this task" in rendered
    assert "do it in Dutch" in rendered
