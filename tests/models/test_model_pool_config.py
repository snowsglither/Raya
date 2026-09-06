"""Pool de modèles multi-fournisseur — DONNÉE de config, pas de règle métier
gravée (consigne Phase 3 §6)."""

from __future__ import annotations

from raya.contracts import ModelCapability
from raya.runtime.config import parse_model_pool


def test_parses_multiple_models_with_multiple_capabilities():
    pool = parse_model_pool("deepseek-v4-flash:cloud:reasoning|planning,kimi-k2.7-code:cloud:coding")
    assert len(pool) == 2
    assert pool[0] == ("deepseek-v4-flash:cloud", [ModelCapability.REASONING, ModelCapability.PLANNING])
    assert pool[1] == ("kimi-k2.7-code:cloud", [ModelCapability.CODING])


def test_model_id_containing_colon_parsed_correctly():
    """Les IDs de modèles Ollama contiennent eux-mêmes un ':' (ex: ':cloud') —
    rpartition sur le DERNIER ':' doit rester correct."""
    pool = parse_model_pool("gemma4:cloud:vision")
    assert pool[0][0] == "gemma4:cloud"


def test_unknown_capability_ignored_not_crash():
    pool = parse_model_pool("model:reasoning|not_a_real_capability")
    assert pool[0][1] == [ModelCapability.REASONING]


def test_empty_spec_returns_empty_pool():
    assert parse_model_pool("") == []


def test_entry_without_valid_capability_dropped_entirely():
    pool = parse_model_pool("model:bogus_cap_only")
    assert pool == []


def test_whitespace_around_entries_tolerated():
    pool = parse_model_pool(" model:cloud:reasoning , other:cloud:coding ")
    assert len(pool) == 2
