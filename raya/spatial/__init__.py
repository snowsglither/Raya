"""Spatial (RAYA V2 Phase 8 — Creative/Spatial Agent).

Sous-système FEUILLE (comme `perception`) : ne dépend que de
`contracts`/`observability`, jamais de `models`/`memory`/`harness`/
`interfaces`/`tools`/`devices` (RAYA_V2_ARCHITECTURAL_INVARIANTS.md,
consigne Phase 8 §29). `SceneStore` est le seul point d'écriture d'une
scène — `tools/catalog/spatial.py` (mutation, piloté par le modèle via
Harness) et `raya/harness/loop.py` (lecture seule, exposition UI) sont les
deux seuls appelants légitimes."""

from __future__ import annotations

from .renderer.base import RendererAdapter
from .renderer.threejs_adapter import ThreeJSAdapter
from .store import SceneStore

__all__ = ["SceneStore", "RendererAdapter", "ThreeJSAdapter"]
