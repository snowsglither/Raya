"""MemoryStore — implémentation réelle et persistante (RAYA_V2_TECHNICAL_ARCHITECTURE.md
§1.8, RAYA_V2_CONTRACTS.md §3). Isolation stricte de canal, lifecycle, ranking
déterministe. Memory != World State (frontières distinctes, backends distincts
côté appelant même si le même SqliteBackend physique peut être partagé).
"""

from __future__ import annotations

import threading

from raya.contracts import (
    ChannelScope,
    Event,
    MemoryEntry,
    MemoryLifecycle,
    from_dict,
    parse_iso,
    to_dict,
    utc_now_iso,
)
from raya.event_bus import EventBus
from raya.persistence import PersistenceBackend

_COLLECTION = "memory_entries"

# Clé de bookkeeping INTERNE (jamais un champ de contrat `MemoryEntry`,
# jamais exposée hors de ce module) — RAYA_V2_PHASE11 (context continuité) :
# `utc_now_iso()` a une précision milliseconde, deux écritures rapprochées
# (ex: le tour utilisateur puis, quasi immédiatement, la réponse de RAYA
# dans le même `handle_request()`) peuvent partager EXACTEMENT le même
# horodatage. Le bonus de récence de `_score()` produit alors un score
# identique, et `list.sort()` (stable) retombe sur l'ordre de retour du
# backend — non garanti par SQL (pas de `ORDER BY` dans SqliteBackend.query),
# ce qui peut scrambler un transcript pourtant censé être chronologique.
# Un compteur monotone, écrit une fois par `write()`, sert de départage
# fiable et déterministe, indépendant du backend et de la granularité de
# l'horodatage — `from_dict` ignore silencieusement les clés inconnues,
# donc aucun changement de contrat n'est nécessaire.
_SEQ_KEY = "_seq"

# Mots de 3 lettres ou moins ignorés (déterministe, pas de NLP) — évite que
# "le"/"un"/"de" fassent matcher toute la base sur une requête en français.
_MIN_WORD_LEN = 4


def _significant_words(query: str) -> list[str]:
    return [w for w in query.lower().split() if len(w) >= _MIN_WORD_LEN]


# Poids déterministes du ranking (RAYA_V2_MIGRATION_PLAN.md §7.3 — "un ranking
# déterministe et explicable est préférable" à un système vectoriel).
_LIFECYCLE_WEIGHT = {
    MemoryLifecycle.CONFIRMED: 1.0,
    MemoryLifecycle.ACTIVE: 0.8,
    MemoryLifecycle.CANDIDATE: 0.5,
    MemoryLifecycle.AGING: 0.3,
    MemoryLifecycle.OBSOLETE: 0.0,
}


class MemoryStore:
    def __init__(self, backend: PersistenceBackend, bus: EventBus | None = None) -> None:
        self._backend = backend
        self._bus = bus
        self._lock = threading.Lock()
        self._seq_counter = 0

    def _publish(self, entry: MemoryEntry, event_type: str) -> None:
        if self._bus is None:
            return
        self._bus.publish(
            Event(
                type=event_type,
                source="memory",
                payload={"entry_id": entry.id, "layer": entry.layer.value, "channel_scope": entry.channel_scope.value},
            )
        )

    def write(self, entry: MemoryEntry) -> MemoryEntry:
        with self._lock:
            self._seq_counter += 1
            payload = to_dict(entry)
            payload[_SEQ_KEY] = self._seq_counter
            self._backend.save(_COLLECTION, entry.id, payload)
        self._publish(entry, "memory.entry_written")
        return entry

    def get(self, entry_id: str) -> MemoryEntry | None:
        raw = self._backend.load(_COLLECTION, entry_id)
        return from_dict(MemoryEntry, raw) if raw else None

    def update_lifecycle(self, entry_id: str, lifecycle: MemoryLifecycle) -> MemoryEntry | None:
        entry = self.get(entry_id)
        if entry is None:
            return None
        entry.lifecycle = lifecycle
        entry.updated_at = utc_now_iso()
        with self._lock:
            # Préserve le `_seq` déjà attribué (jamais régénéré ici) — un
            # changement de lifecycle ne doit pas faire perdre le départage
            # chronologique déterministe de l'entrée.
            existing = self._backend.load(_COLLECTION, entry.id) or {}
            payload = to_dict(entry)
            payload[_SEQ_KEY] = existing.get(_SEQ_KEY, 0)
            self._backend.save(_COLLECTION, entry.id, payload)
        self._publish(entry, "memory.entry_updated")
        return entry

    def correct(self, entry_id: str, new_content: object, provenance: str) -> MemoryEntry | None:
        """Une correction utilisateur a priorité absolue (RAYA_V2_CONTRACTS.md §3) :
        l'ancienne entrée passe obsolete, une nouvelle la remplace via `supersedes`."""
        old = self.get(entry_id)
        if old is None:
            return None
        self.update_lifecycle(entry_id, MemoryLifecycle.OBSOLETE)
        new_entry = MemoryEntry(
            type=old.type,
            layer=old.layer,
            channel_scope=old.channel_scope,
            content=new_content,
            provenance=provenance,
            lifecycle=MemoryLifecycle.CONFIRMED,
            supersedes=old.id,
        )
        return self.write(new_entry)

    def search(
        self,
        query: str,
        channel_scope: ChannelScope,
        *,
        type_filter: object | None = None,
        lifecycle_filter: object | None = None,
        confidence_filter: object | None = None,
        limit: int = 20,
    ) -> list[MemoryEntry]:
        """Isolation stricte de canal (RAYA_V2_CONTRACTS.md §3) : seules les
        entrées `shared` ou du canal demandé sont visibles — jamais une fuite
        cross-canal, même en cas de correspondance textuelle forte."""
        with self._lock:
            raw_entries = self._backend.query(_COLLECTION)
        # Départage déterministe (voir note `_SEQ_KEY` en tête de fichier) —
        # extrait AVANT la conversion en `MemoryEntry` (jamais un champ du
        # contrat lui-même).
        seq_by_id = {r.get("id"): r.get(_SEQ_KEY, 0) for r in raw_entries}
        entries = [from_dict(MemoryEntry, r) for r in raw_entries]

        entries = [e for e in entries if e.channel_scope in (ChannelScope.SHARED, channel_scope)]
        if type_filter is not None:
            entries = [e for e in entries if e.type == type_filter]
        if lifecycle_filter is not None:
            entries = [e for e in entries if e.lifecycle == lifecycle_filter]
        if confidence_filter is not None:
            entries = [e for e in entries if e.confidence == confidence_filter]
        entries = [e for e in entries if e.lifecycle != MemoryLifecycle.OBSOLETE]

        words = _significant_words(query)
        if words:
            entries = [e for e in entries if any(w in str(e.content).lower() for w in words)]

        # `(score, seq)` : à score de récence identique (deux écritures dans
        # la même milliseconde, cf. note `_SEQ_KEY`), l'entrée écrite en
        # dernier (seq le plus grand) doit rester la plus "récente" —
        # jamais un ordre arbitraire dépendant du backend.
        entries.sort(key=lambda e: (self._score(e, words), seq_by_id.get(e.id, 0)), reverse=True)
        return entries[:limit]

    @staticmethod
    def _score(entry: MemoryEntry, words: list[str]) -> float:
        """Ranking déterministe : lifecycle + correspondance texte + récence.
        Documenté et remplaçable — pas de système vectoriel (RAYA_V2_MIGRATION_PLAN.md §7.3)."""
        score = _LIFECYCLE_WEIGHT.get(entry.lifecycle, 0.5)
        if words:
            content = str(entry.content).lower()
            occurrences = sum(content.count(w) for w in words)
            score += min(occurrences, 5) * 0.1
        try:
            hours_elapsed = (
                parse_iso(utc_now_iso()) - parse_iso(entry.updated_at)
            ).total_seconds() / 3600.0
            score += max(0.0, 0.2 - hours_elapsed * 0.001)  # récence : petit bonus, décroît avec le temps
        except ValueError:
            pass
        return score
