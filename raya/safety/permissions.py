"""check_permission() (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.12, §10.2).

Phase 0 : politique minimale honnête — safe=allowed, sensitive/destructive=
requires_confirmation par défaut (jamais d'auto-approbation silencieuse).
La politique de confirmation riche (§10.2) arrive avec de vraies capacités
en Phase 3/4. Toute décision est auditée.
"""

from __future__ import annotations

from raya.contracts import GrantedBy, Permission, PermissionDecision, PermissionLevel

from .audit import AuditTrail
from .risk import classify_risk
from .stop import StopController


class SafetyService:
    def __init__(self, stop: StopController, audit: AuditTrail) -> None:
        self._stop = stop
        self._audit = audit

    def should_stop(self) -> bool:
        return self._stop.should_stop()

    def request_stop(self, source: str) -> None:
        self._stop.request_stop(source)

    def last_stop_source(self) -> str | None:
        return self._stop.last_source()

    def check_permission(
        self,
        action_ref: str,
        capability_tags: list[str],
        *,
        arguments: dict | None = None,
        user_confirmed: bool = False,
        physical_presence_verified: bool = False,
    ) -> Permission:
        risk = classify_risk(capability_tags, arguments)

        if risk == PermissionLevel.SAFE:
            perm = Permission(
                action_ref=action_ref,
                risk_level=risk,
                decision=PermissionDecision.ALLOWED,
                reason="risque=safe, faible friction",
                granted_by=GrantedBy.POLICY,
            )
        elif user_confirmed:
            perm = Permission(
                action_ref=action_ref,
                risk_level=risk,
                decision=PermissionDecision.ALLOWED,
                reason="confirmation utilisateur explicite",
                granted_by=GrantedBy.USER_CONFIRMATION,
            )
        elif physical_presence_verified and risk != PermissionLevel.DESTRUCTIVE:
            perm = Permission(
                action_ref=action_ref,
                risk_level=risk,
                decision=PermissionDecision.ALLOWED,
                reason="présence physique vérifiée",
                granted_by=GrantedBy.PHYSICAL_PRESENCE,
            )
        else:
            perm = Permission(
                action_ref=action_ref,
                risk_level=risk,
                decision=PermissionDecision.REQUIRES_CONFIRMATION,
                reason=f"risque={risk.value}, confirmation requise (Phase 0 : jamais d'auto-approbation)",
            )

        self._audit.record(perm)
        return perm
