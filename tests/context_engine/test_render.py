"""render_system_prompt() — sérialisation du Context assemblé en texte réel
transmis au Model Layer (le fix du bug central : Context calculé mais jamais
envoyé). Ne doit jamais ajouter d'information absente de context.sections."""

from __future__ import annotations

from raya.context_engine.render import render_system_prompt
from raya.contracts import Context, ContextSection, FactStatus, Freshness, SectionKind


def _ctx(*sections: ContextSection) -> Context:
    return Context(session_id="s1", budget_tokens=4096, sections=list(sections), used_tokens_estimate=0)


def test_render_includes_assistant_name():
    ctx = _ctx(ContextSection(
        kind=SectionKind.SYSTEM_RULES,
        content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
        provenance="context_engine:runtime_identity",
    ))
    assert "You are RAYA" in render_system_prompt(ctx)


def test_render_includes_runtime_model_when_present():
    ctx = _ctx(ContextSection(
        kind=SectionKind.SYSTEM_RULES,
        content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": "ollama_cloud", "model": "deepseek-v4-flash:cloud"}},
        provenance="context_engine:runtime_identity",
    ))
    rendered = render_system_prompt(ctx)
    assert "ollama_cloud" in rendered
    assert "deepseek-v4-flash:cloud" in rendered


def test_render_omits_runtime_line_when_model_unknown():
    """Jamais une ligne 'Runtime: provider=unknown, model=unknown' quand rien
    n'est enregistré — omission complète plutôt qu'une fausse impression de savoir."""
    ctx = _ctx(ContextSection(
        kind=SectionKind.SYSTEM_RULES,
        content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
        provenance="context_engine:runtime_identity",
    ))
    assert "Runtime:" not in render_system_prompt(ctx)


def test_render_includes_memory_fact():
    ctx = _ctx(ContextSection(
        kind=SectionKind.MEMORY, content={"id": "m1", "type": "fact", "content": "Nom complet : Ruben Lukusa"},
        provenance="profile_migration:identity",
    ))
    assert "Ruben Lukusa" in render_system_prompt(ctx)


def test_render_includes_world_state_with_freshness():
    ctx = _ctx(ContextSection(
        kind=SectionKind.WORLD_STATE, content={"domain": "pc", "key": "active_window", "value": "Notepad"},
        provenance="pc:observation", freshness=Freshness(status=FactStatus.ACTIVE, as_of="2026-01-01T00:00:00.000Z"),
    ))
    rendered = render_system_prompt(ctx)
    assert "Notepad" in rendered and "active" in rendered


def test_render_includes_task_state():
    ctx = _ctx(ContextSection(
        kind=SectionKind.TASK_STATE,
        content={"task_id": "t1", "objective": "faire X", "state": "RUNNING", "progress": {"current_step": "step_1", "percent": 20.0}},
        provenance="tasks:current",
    ))
    rendered = render_system_prompt(ctx)
    assert "faire X" in rendered and "RUNNING" in rendered


def test_render_includes_active_tasks_distinctly_from_task_state():
    """Chantier 16 : ACTIVE_TASKS doit être rendu (visible même sans un
    TASK_STATE 'courant' dans le même Context) et se distinguer clairement
    de TASK_STATE dans le texte produit."""
    ctx = _ctx(ContextSection(
        kind=SectionKind.ACTIVE_TASKS,
        content={"task_id": "t2", "objective": "rappel dans 5 min", "state": "PENDING", "not_before": "2026-01-01T00:05:00.000Z"},
        provenance="tasks:active",
    ))
    rendered = render_system_prompt(ctx)
    assert "rappel dans 5 min" in rendered
    assert "Other active task" in rendered
    assert "Current background task" not in rendered


def test_render_includes_conversation_history():
    ctx = _ctx(ContextSection(
        kind=SectionKind.CONVERSATION_HISTORY, content={"recent": [{"id": "m1", "content": "salut"}]},
        provenance="memory:conversation",
    ))
    assert "salut" in render_system_prompt(ctx)


def test_render_skips_tool_schemas_already_sent_via_available_tools():
    ctx = _ctx(ContextSection(
        kind=SectionKind.TOOL_SCHEMAS, content={"tools": [{"name": "pc.window.list", "description": "liste les fenêtres"}]},
        provenance="tools:discovery",
    ))
    rendered = render_system_prompt(ctx)
    assert "pc.window.list" not in rendered


def test_render_empty_context_and_no_fabrication():
    """Context vide -> chaîne vide, jamais un crash. Une section SYSTEM_RULES
    sans section MEMORY -> aucune mention de fait utilisateur, jamais une
    donnée par défaut/inventée."""
    assert render_system_prompt(_ctx()) == ""

    ctx = _ctx(ContextSection(
        kind=SectionKind.SYSTEM_RULES,
        content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
        provenance="context_engine:runtime_identity",
    ))
    assert "Ruben" not in render_system_prompt(ctx)
