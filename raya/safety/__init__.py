"""Safety (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.12) — permissions, risque, STOP, audit.

Dépendances autorisées : persistence, observability, EventBus (abonnement).
Dépendances interdites : models, devices, interfaces.
"""

from .audit import AuditTrail
from .permissions import SafetyService
from .risk import classify_risk
from .stop import StopController

__all__ = ["AuditTrail", "SafetyService", "StopController", "classify_risk"]
