"""RAYA_V2_PHASE11 (§5, Root Causes) : `RAYA_MAX_TOOL_ITERATIONS` — 4
suffisait pour prouver le pipeline (Phase 3) mais coupait artificiellement
tôt un scénario de Computer Use réel à plusieurs étapes (Netflix : naviguer
-> profil -> contenu -> lecture = ~7 tool calls). Relevé à 12 (~2x le besoin
observé), jamais un budget "illimité" arbitraire — la protection contre les
boucles/répétitions reste `LoopDetector`, inchangée, jamais désactivée par
ce réglage."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raya.runtime.config import RuntimeConfig, load_config  # noqa: E402


def test_default_max_tool_iterations_is_twelve_not_four(tmp_path, monkeypatch):
    monkeypatch.delenv("RAYA_MAX_TOOL_ITERATIONS", raising=False)
    cfg = load_config(root=tmp_path)
    assert cfg.max_tool_iterations == 12


def test_dataclass_field_default_matches_env_parsing_default():
    """Non-régression : le défaut du champ dataclass et le défaut de repli
    du parsing d'environnement doivent rester synchronisés (source du bug
    corrigé cette phase : le champ avait été relevé à 12 sans mettre à jour
    la chaîne de repli `merged.get("RAYA_MAX_TOOL_ITERATIONS", "4")`)."""
    import inspect

    default_field_value = RuntimeConfig.__dataclass_fields__["max_tool_iterations"].default
    source = inspect.getsource(load_config)
    assert f'"RAYA_MAX_TOOL_ITERATIONS", "{default_field_value}"' in source


def test_max_tool_iterations_still_configurable_via_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RAYA_MAX_TOOL_ITERATIONS", "20")
    cfg = load_config(root=tmp_path)
    assert cfg.max_tool_iterations == 20
    monkeypatch.delenv("RAYA_MAX_TOOL_ITERATIONS", raising=False)


def test_max_tool_iterations_is_not_used_by_long_horizon_tasks():
    """Consigne §5 : distinguer conversation synchrone (bornée par ce budget)
    et Task Long-Horizon (Phase 10, non bornée par cette valeur — un tick =
    un appel modèle, la complétion/l'échec/le replanning sont les seules
    conditions d'arrêt naturelles)."""
    import inspect

    from raya.harness import Harness

    source = inspect.getsource(Harness._run_long_horizon_step)
    assert "max_tool_iterations" not in source
