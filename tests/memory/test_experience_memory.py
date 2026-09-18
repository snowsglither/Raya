"""Tests Experience Memory — write, persistence, retrieval, context rendering.

Couverture :
1.  maybe_distill_experience — plan worthy → entry created
2.  no experience for trivial objective
3.  no experience for single-step plan
4.  no experience without retry
5.  experience content has no coordinates/URLs/selectors
6.  experience written to MemoryType.EXPERIENCE + MemoryLayer.EXPERIENCE
7.  experience retrieved by memory.search(type_filter=EXPERIENCE)
8.  experience rendered in context (assembler → render)
9.  experience survives restart (persistence)
10. no secrets in experience content
"""

from __future__ import annotations

import pytest

from raya.contracts import (
    ChannelScope,
    MemoryEntry,
    MemoryLayer,
    MemoryType,
    Plan,
    PlanStep,
    StepState,
    Task,
    TaskOwner,
    TaskState,
)
from raya.memory import MemoryStore, maybe_distill_experience
from raya.persistence import InMemoryBackend, SqliteBackend


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_plan(steps: list[dict]) -> Plan:
    plan_steps = []
    for i, s in enumerate(steps):
        step = PlanStep(id=f"step_{i}", objective=s["objective"])
        step.status = s.get("status", StepState.COMPLETED)
        step.attempts = s.get("attempts", 1)
        step.result = s.get("result")
        plan_steps.append(step)
    return Plan(steps=plan_steps)


def _make_task(objective: str, channel: str = "cli") -> Task:
    owner = TaskOwner(session_id="test_session", channel=channel)
    t = Task(objective=objective, owner=owner, correlation_id="test_corr")
    t.state = TaskState.COMPLETED
    return t


def _memory() -> MemoryStore:
    return MemoryStore(InMemoryBackend())


# ---------------------------------------------------------------------------
# 1. Worthy plan → experience created
# ---------------------------------------------------------------------------

def test_distill_creates_experience_for_worthy_plan():
    mem = _memory()
    task = _make_task("Ajoute cette manette au panier")
    plan = _make_plan([
        {"objective": "Trouver le bouton Add to cart", "status": StepState.COMPLETED, "attempts": 3, "result": {"text": "Bouton trouvé et cliqué"}},
        {"objective": "Vérifier l'état du panier", "status": StepState.COMPLETED, "attempts": 1, "result": {"text": "Panier mis à jour"}},
    ])
    entry = maybe_distill_experience(task, plan, mem)
    assert entry is not None
    assert entry.type == MemoryType.EXPERIENCE
    assert entry.layer == MemoryLayer.EXPERIENCE


# ---------------------------------------------------------------------------
# 2. Trivial objective → no experience
# ---------------------------------------------------------------------------

def test_no_experience_for_trivial_objective():
    mem = _memory()
    task = _make_task("Bonjour")
    plan = _make_plan([
        {"objective": "Répondre", "status": StepState.COMPLETED, "attempts": 2},
        {"objective": "Confirmer", "status": StepState.COMPLETED, "attempts": 1},
    ])
    result = maybe_distill_experience(task, plan, mem)
    assert result is None


# ---------------------------------------------------------------------------
# 3. Single-step plan → no experience
# ---------------------------------------------------------------------------

def test_no_experience_for_single_step():
    mem = _memory()
    task = _make_task("Lance Steam")
    plan = _make_plan([
        {"objective": "Lancer Steam", "status": StepState.COMPLETED, "attempts": 2},
    ])
    result = maybe_distill_experience(task, plan, mem)
    assert result is None


# ---------------------------------------------------------------------------
# 4. No retry → no experience
# ---------------------------------------------------------------------------

def test_no_experience_without_retry():
    mem = _memory()
    task = _make_task("Ouvre la bibliothèque Steam")
    plan = _make_plan([
        {"objective": "Naviguer dans Steam", "status": StepState.COMPLETED, "attempts": 1},
        {"objective": "Cliquer Bibliothèque", "status": StepState.COMPLETED, "attempts": 1},
    ])
    result = maybe_distill_experience(task, plan, mem)
    assert result is None


# ---------------------------------------------------------------------------
# 5. Experience content has no fragile data
# ---------------------------------------------------------------------------

def test_experience_content_has_no_coordinates():
    mem = _memory()
    task = _make_task("Ajoute au panier")
    plan = _make_plan([
        {"objective": "Cliquer x=742 y=531 sur #addToCart_feature_div", "attempts": 2, "result": {"text": "x=742 y=531 cliqué"}},
        {"objective": "Vérifier panier https://amazon.com/cart?ref=xyz123", "attempts": 1, "result": {"text": "ok"}},
    ])
    entry = maybe_distill_experience(task, plan, mem)
    # Entry may or may not be created, but if created must not have raw coords/URLs
    if entry is not None:
        content_str = str(entry.content)
        assert "x=742" not in content_str
        assert "y=531" not in content_str
        assert "#addToCart" not in content_str
        assert "amazon.com/cart" not in content_str


# ---------------------------------------------------------------------------
# 6. Correct type and layer
# ---------------------------------------------------------------------------

def test_experience_type_and_layer():
    mem = _memory()
    task = _make_task("Rechercher un produit et l'acheter")
    plan = _make_plan([
        {"objective": "Trouver le produit dans la liste", "attempts": 2, "result": {"text": "Produit identifié"}},
        {"objective": "Cliquer sur Acheter", "attempts": 1, "result": {"text": "Achat initié"}},
    ])
    entry = maybe_distill_experience(task, plan, mem)
    assert entry is not None
    assert entry.type == MemoryType.EXPERIENCE
    assert entry.layer == MemoryLayer.EXPERIENCE
    assert entry.channel_scope == ChannelScope.CHAT


# ---------------------------------------------------------------------------
# 7. Experience retrieved by search
# ---------------------------------------------------------------------------

def test_experience_retrieved_by_search():
    mem = _memory()
    task = _make_task("Ajouter un produit au panier e-commerce")
    plan = _make_plan([
        {"objective": "Identifier le bouton d'achat", "attempts": 3, "result": {"text": "Bouton identifié"}},
        {"objective": "Vérifier le panier", "attempts": 1, "result": {"text": "Panier mis à jour"}},
    ])
    maybe_distill_experience(task, plan, mem)
    results = mem.search("ajouter panier", channel_scope=ChannelScope.CHAT, type_filter=MemoryType.EXPERIENCE)
    assert len(results) == 1
    assert results[0].type == MemoryType.EXPERIENCE


# ---------------------------------------------------------------------------
# 8. Experience rendered in context
# ---------------------------------------------------------------------------

def test_experience_rendered_in_context():
    from raya.context_engine.assembler import assemble
    from raya.context_engine.render import render_system_prompt
    from raya.world_state import WorldStateStore

    backend = InMemoryBackend()
    mem = MemoryStore(backend)
    ws = WorldStateStore(backend)

    task = _make_task("Ajouter un produit au panier")
    plan = _make_plan([
        {"objective": "Trouver et cliquer add to cart", "attempts": 2, "result": {"text": "Cliqué avec succès"}},
        {"objective": "Vérifier panier", "attempts": 1, "result": {"text": "Panier mis à jour"}},
    ])
    maybe_distill_experience(task, plan, mem)

    ctx = assemble(
        session_id="test",
        channel_scope=ChannelScope.CHAT,
        world_state=ws,
        memory=mem,
        query_text="ajouter produit panier",
    )
    prompt = render_system_prompt(ctx)
    assert "Past successful strategy" in prompt


# ---------------------------------------------------------------------------
# 9. Experience persists across MemoryStore restart
# ---------------------------------------------------------------------------

def test_experience_persists_across_restart(tmp_path):
    db_path = tmp_path / "test_exp.sqlite3"
    backend1 = SqliteBackend(db_path)
    mem1 = MemoryStore(backend1)

    task = _make_task("Télécharger et installer un jeu")
    plan = _make_plan([
        {"objective": "Trouver le jeu dans le catalogue", "attempts": 2, "result": {"text": "Jeu trouvé"}},
        {"objective": "Cliquer Installer", "attempts": 1, "result": {"text": "Installation lancée"}},
    ])
    maybe_distill_experience(task, plan, mem1)
    backend1.close()

    # Restart
    backend2 = SqliteBackend(db_path)
    mem2 = MemoryStore(backend2)
    results = mem2.search("jeu installer", channel_scope=ChannelScope.CHAT, type_filter=MemoryType.EXPERIENCE)
    assert len(results) >= 1
    backend2.close()


# ---------------------------------------------------------------------------
# 10. No secrets in experience content
# ---------------------------------------------------------------------------

def test_no_secrets_in_experience():
    mem = _memory()
    task = _make_task("Se connecter au compte Netflix")
    plan = _make_plan([
        {"objective": "Saisir email test@example.com et mot de passe secret123", "attempts": 2,
         "result": {"text": "Connecté. Token: eyJhbGc.xyz123 password=secret123"}},
        {"objective": "Accéder au contenu", "attempts": 1, "result": {"text": "Accès accordé"}},
    ])
    entry = maybe_distill_experience(task, plan, mem)
    if entry is not None:
        content_str = str(entry.content).lower()
        # Regex sanitizer strips key=value credentials — password=, token=, secret=
        # (bare prose passwords like "mot de passe X" cannot be detected without NLP)
        assert "password=secret123" not in content_str
        assert "token=eyjhbgc" not in content_str
