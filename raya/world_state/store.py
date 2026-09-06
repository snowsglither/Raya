"""WorldStateStore — implémentation réelle et persistante (RAYA_V2_TECHNICAL_ARCHITECTURE.md
§1.2, §6). Clé composite (domain, key) — une nouvelle valeur REMPLACE l'ancienne
(status -> superseded), ce n'est jamais un journal (RAYA_V2_CONTRACTS.md §2).

Traçabilité (§6.3) : chaque écriture publie `world_state.updated` sur l'EventBus
— l'historique détaillé vit dans l'Event Repository (observability/event_store.py),
pas dans la table de faits elle-même (qui reste "état courant", pas un journal).
"""

from __future__ import annotations

import threading

from raya.contracts import Confidence, Event, FactStatus, WorldStateFact, from_dict, to_dict
from raya.event_bus import EventBus
from raya.persistence import PersistenceBackend

_COLLECTION = "world_state_facts"


def _row_id(domain: str, key: str) -> str:
    return f"{domain}:{key}"


class WorldStateStore:
    def __init__(self, backend: PersistenceBackend, bus: EventBus | None = None) -> None:
        self._backend = backend
        self._bus = bus
        self._lock = threading.Lock()
        # RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.2 : "Events consommés : tout
        # event perception.*" — c'est ICI, et seulement ici, qu'un
        # PerceptionObservation devient un WorldStateFact réel. `perception/`
        # ne dépend jamais de `world_state/` directement (voir
        # raya/perception/runtime.py) : le découplage passe entièrement par
        # l'EventBus, comme StopController/PresenceTracker ailleurs.
        if bus is not None:
            bus.subscribe("perception.*", self._on_perception_event, subscriber="world_state.perception")

    def _on_perception_event(self, event: Event) -> None:
        payload = event.payload if isinstance(event.payload, dict) else to_dict(event.payload)
        if not isinstance(payload, dict):
            return
        try:
            fact = WorldStateFact(
                domain=payload["domain"], key=payload["key"], value=payload["value"],
                source=payload["source"],
                confidence=Confidence(payload.get("confidence", Confidence.KNOWN_FACT.value)),
                freshness_ttl_s=payload.get("freshness_ttl_s"),
            )
        except (KeyError, ValueError):
            # Payload malformé (event mal formé, mauvaise version d'un capteur)
            # — jamais un crash du store, leçon du bug Phase 2 TaskEvent.payload.
            return
        self.apply_update(fact)

    def _publish(self, fact: WorldStateFact, action: str) -> None:
        if self._bus is None:
            return
        self._bus.publish(
            Event(
                type="world_state.updated",
                source="world_state",
                payload={"domain": fact.domain, "key": fact.key, "action": action, "status": fact.status.value},
            )
        )

    def create_fact(self, fact: WorldStateFact) -> WorldStateFact:
        return self.apply_update(fact)

    def apply_update(self, fact: WorldStateFact) -> WorldStateFact:
        """Une nouvelle valeur pour (domain, key) REMPLACE l'ancienne (upsert)."""
        with self._lock:
            self._backend.save(_COLLECTION, _row_id(fact.domain, fact.key), to_dict(fact))
        self._publish(fact, action="updated")
        return fact

    def update_fact(self, domain: str, key: str, value: object, source: str) -> WorldStateFact | None:
        existing = self.retrieve_fact(domain, key)
        if existing is None:
            return None
        existing.value = value
        existing.source = source
        existing.status = FactStatus.ACTIVE
        from raya.contracts import utc_now_iso

        existing.timestamp = utc_now_iso()
        return self.apply_update(existing)

    def invalidate_fact(self, domain: str, key: str) -> WorldStateFact | None:
        """Marque explicitement le fait courant comme superseded, sans remplacement
        immédiat (ex: 'Chrome fermé' sans nouveau fait 'app ouverte')."""
        fact = self.retrieve_fact(domain, key)
        if fact is None:
            return None
        fact.status = FactStatus.SUPERSEDED
        with self._lock:
            self._backend.save(_COLLECTION, _row_id(domain, key), to_dict(fact))
        self._publish(fact, action="invalidated")
        return fact

    def expire_fact(self, domain: str, key: str) -> WorldStateFact | None:
        """Force explicitement le passage à stale (au-delà du TTL paresseux)."""
        fact = self.retrieve_fact(domain, key, apply_lazy_staleness=False)
        if fact is None:
            return None
        fact.status = FactStatus.STALE
        with self._lock:
            self._backend.save(_COLLECTION, _row_id(domain, key), to_dict(fact))
        self._publish(fact, action="expired")
        return fact

    def retrieve_fact(self, domain: str, key: str, apply_lazy_staleness: bool = True) -> WorldStateFact | None:
        with self._lock:
            raw = self._backend.load(_COLLECTION, _row_id(domain, key))
        if raw is None:
            return None
        fact = from_dict(WorldStateFact, raw)
        if apply_lazy_staleness and fact.status == FactStatus.ACTIVE and fact.is_expired():
            fact.status = FactStatus.STALE
        return fact

    # Alias conforme au vocabulaire du contrat (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.2)
    get_fact = retrieve_fact

    def retrieve_by_domain(self, domain: str) -> list[WorldStateFact]:
        with self._lock:
            rows = self._backend.query(_COLLECTION, domain=domain)
        facts = [from_dict(WorldStateFact, r) for r in rows]
        for f in facts:
            if f.status == FactStatus.ACTIVE and f.is_expired():
                f.status = FactStatus.STALE
        return facts

    query = retrieve_by_domain

    def retrieve_relevant(self, domains: tuple[str, ...] = ()) -> list[WorldStateFact]:
        """Faits actifs/stale (jamais superseded) pour les domaines demandés,
        ou tous les domaines connus si aucun n'est précisé."""
        if domains:
            facts = [f for d in domains for f in self.retrieve_by_domain(d)]
        else:
            with self._lock:
                rows = self._backend.query(_COLLECTION)
            facts = [from_dict(WorldStateFact, r) for r in rows]
            for f in facts:
                if f.status == FactStatus.ACTIVE and f.is_expired():
                    f.status = FactStatus.STALE
        return [f for f in facts if f.status != FactStatus.SUPERSEDED]

    def all(self) -> list[WorldStateFact]:
        with self._lock:
            rows = self._backend.query(_COLLECTION)
        return [from_dict(WorldStateFact, r) for r in rows]
