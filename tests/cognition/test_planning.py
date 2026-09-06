"""build_plan / replan_step (RAYA V2 Phase 10, consigne §3/§6) — Cognition
PRODUIT un plan, ne l'exécute jamais. Réutilise le Model Layer existant
(ModelCapability.PLANNING, déjà définie Phase 0, jamais utilisée avant
cette phase) — aucun provider spécifique Long-Horizon (consigne §16)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from support.fake_provider import FakeScriptedProvider  # noqa: E402

from raya.cognition import build_plan, replan_step  # noqa: E402
from raya.contracts import ContentPart, FinishReason, ModelCapability, ModelResponse, PlanStep  # noqa: E402
from raya.models import ModelRegistry  # noqa: E402
from raya.models.providers.null_provider import NullProvider  # noqa: E402


def _text_response(text: str) -> ModelResponse:
    return ModelResponse(request_id="", provider_used="fake", content=[ContentPart(type="text", value=text)], finish_reason=FinishReason.COMPLETED)


def _planning_registry(script) -> ModelRegistry:
    reg = ModelRegistry()
    reg.register(NullProvider())  # toujours présent, toujours en erreur -> route() retombe sur le fake
    reg.register(FakeScriptedProvider(script, capabilities=[ModelCapability.PLANNING]))
    return reg


def test_build_plan_parses_a_clean_json_array():
    reg = _planning_registry([_text_response('["Ouvrir le navigateur", "Chercher la météo", "Résumer le résultat"]')])
    plan = build_plan("Donne-moi la météo du jour", reg, correlation_id="c1")
    assert [s.objective for s in plan.steps] == ["Ouvrir le navigateur", "Chercher la météo", "Résumer le résultat"]


def test_build_plan_parses_json_surrounded_by_prose():
    reg = _planning_registry([_text_response('Voici le plan :\n["Étape unique"]\nFin.')])
    plan = build_plan("objectif", reg, correlation_id="c1")
    assert [s.objective for s in plan.steps] == ["Étape unique"]


def test_build_plan_falls_back_to_single_step_on_unparseable_response():
    reg = _planning_registry([_text_response("je ne peux pas répondre en JSON")])
    plan = build_plan("mon objectif brut", reg, correlation_id="c1")
    assert [s.objective for s in plan.steps] == ["mon objectif brut"]


def test_build_plan_falls_back_honestly_when_no_model_available():
    reg = ModelRegistry()  # aucun provider du tout
    plan = build_plan("objectif sans modèle", reg, correlation_id="c1")
    assert [s.objective for s in plan.steps] == ["objectif sans modèle"]


def test_build_plan_steps_are_sequentially_dependent_by_default():
    reg = _planning_registry([_text_response('["A", "B", "C"]')])
    plan = build_plan("obj", reg, correlation_id="c1")
    assert plan.steps[0].dependencies == []
    assert plan.steps[1].dependencies == [plan.steps[0].id]
    assert plan.steps[2].dependencies == [plan.steps[1].id]


def test_build_plan_caps_at_five_steps():
    steps = [f"step {i}" for i in range(10)]
    reg = _planning_registry([_text_response(str(steps).replace("'", '"'))])
    plan = build_plan("obj", reg, correlation_id="c1")
    assert len(plan.steps) == 5


def test_build_plan_first_step_is_current():
    reg = _planning_registry([_text_response('["A", "B"]')])
    plan = build_plan("obj", reg, correlation_id="c1")
    assert plan.current_step_id == plan.steps[0].id


def test_replan_step_returns_alternative_plan_step():
    reg = _planning_registry([_text_response("Essayer une source alternative pour la météo")])
    failed = PlanStep(id="step_1", objective="Chercher la météo")
    alt = replan_step("Donne-moi la météo", failed, {"error": "timeout"}, reg, correlation_id="c1")
    assert alt is not None
    assert alt.id != failed.id
    assert "alternative" in alt.objective.lower()


def test_replan_step_returns_none_when_model_says_none():
    reg = _planning_registry([_text_response("NONE")])
    failed = PlanStep(id="step_1", objective="objectif impossible")
    alt = replan_step("obj", failed, None, reg, correlation_id="c1")
    assert alt is None


def test_replan_step_returns_none_honestly_when_no_model_available():
    reg = ModelRegistry()
    failed = PlanStep(id="step_1", objective="x")
    assert replan_step("obj", failed, None, reg, correlation_id="c1") is None


# --- Chantier 14 : available_tools transmis à build_plan() ---
#
# Constat réel (validation E2E) : sans ceci, un objectif "envoie-moi ça sur
# Telegram" pouvait être planifié comme une procédure manuelle de contrôle
# du téléphone (le modèle de planification ignorant tout de
# telegram.send_message) au lieu d'un plan à une seule étape appelant
# directement ce Tool — bloquant la tâche sur sa première étape et faisant
# boucler des envois Telegram réels et répétés à chaque nouvelle tentative.

def test_build_plan_forwards_available_tools_to_the_model_request():
    provider = FakeScriptedProvider([_text_response('["Envoyer le message via le Tool"]')], capabilities=[ModelCapability.PLANNING])
    reg = ModelRegistry()
    reg.register(NullProvider())
    reg.register(provider)
    tools = [{"name": "telegram.send_message", "description": "Envoie un message Telegram."}]

    build_plan("Envoie-moi un message Telegram", reg, correlation_id="c1", available_tools=tools)

    assert provider.calls[-1].available_tools == tools


def test_build_plan_without_available_tools_still_degrades_honestly():
    """Rétrocompatibilité (consigne §2) : un appelant qui ne fournit pas
    `available_tools` (défaut `None`) garde exactement le comportement
    précédent — jamais un plan à moitié construit."""
    reg = _planning_registry([_text_response('["Étape unique"]')])
    plan = build_plan("objectif", reg, correlation_id="c1")
    assert [s.objective for s in plan.steps] == ["Étape unique"]
