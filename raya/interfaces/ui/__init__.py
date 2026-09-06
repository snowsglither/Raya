"""Interface Cockpit (RAYA V2 Phase 6 — ADAPTIVE USER INTERFACE).

Client mince du Harness, exactement au même titre que `interfaces/cli` et
`interfaces/voice` (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.14) : traduit une
intention utilisateur en `HarnessRequest`, lit l'état via l'API publique du
Harness, ne détient JAMAIS lui-même la vérité (tasks/world_state/safety
restent authoritatives côté Core). Ne construit AUCUNE nouvelle boucle
agentique, n'importe jamais cognition/tools/devices/models/tasks/safety/
attention directement (vérifié par tests/architecture/test_ui_architecture_proof.py).
"""

from __future__ import annotations

from .channel import UIChannel
from .events import UIEventBridge

__all__ = ["UIChannel", "UIEventBridge"]
