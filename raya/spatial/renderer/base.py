"""RendererAdapter — frontière Data/Rendering (RAYA V2 Phase 8 consigne §7).

`Scene`/`SpatialObject` (raya.contracts.spatial) restent la source de
vérité, indépendante de tout renderer. Un `RendererAdapter` ne fait QUE
transformer une `Scene` en un payload JSON-safe consommé par UN renderer
particulier (ici Three.js côté Cockpit) — il ne stocke rien, ne décide
rien, peut être remplacé ou absent sans jamais affecter `SceneStore`."""

from __future__ import annotations

from abc import ABC, abstractmethod

from raya.contracts import Scene


class RendererAdapter(ABC):
    @abstractmethod
    def to_payload(self, scene: Scene) -> dict: ...
