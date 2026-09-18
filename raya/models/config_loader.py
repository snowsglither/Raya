"""Charge le catalogue de modèles depuis catalog.toml (Chantier 2).

tomllib est disponible en stdlib depuis Python 3.11 (Python 3.14 confirmé
dans ce projet — aucune dépendance externe nécessaire).
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from raya.contracts import (
    CapabilityEvidence,
    CapabilityRecord,
    CostProfile,
    LatencyProfile,
    ModelCapability,
    StaticModelEntry,
)

_DEFAULT_CATALOG_PATH = Path(__file__).parent / "catalog.toml"


def load_model_catalog(path: Path | None = None) -> list[StaticModelEntry]:
    """Charge et parse le catalogue TOML. path=None → catalog.toml dans raya/models/."""
    catalog_path = path or _DEFAULT_CATALOG_PATH
    with open(catalog_path, "rb") as f:
        data = tomllib.load(f)

    entries: list[StaticModelEntry] = []
    for m in data.get("models", []):
        evidence = CapabilityEvidence(m.get("capability_evidence", "declared"))
        capability_records = [
            CapabilityRecord(capability=ModelCapability(cap), evidence=evidence)
            for cap in m.get("capabilities", [])
            if cap in ModelCapability._value2member_map_
        ]
        entries.append(StaticModelEntry(
            model_id=m["model_id"],
            display_name=m.get("display_name", m["model_id"]),
            provider=m.get("provider", "ollama_cloud"),
            capability_records=capability_records,
            context_limit=int(m.get("context_limit", 128_000)),
            cost_profile=CostProfile(),
            latency_profile=LatencyProfile(),
            supports_tool_calls=bool(m.get("supports_tool_calls", True)),
            supports_vision=bool(m.get("supports_vision", False)),
        ))
    return entries
