"""Context Engine — assemblage réel (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.7, §5,
§10 de la consigne Phase 1). Sélectionne, ne persiste jamais. Déterministe :
aucun appel modèle. Dépendances autorisées : memory/world_state/tasks (lecture),
tools.discovery (lecture seule des schémas — jamais tools.execution).
"""

from __future__ import annotations

from raya.contracts import (
    ChannelScope,
    Confidence,
    Context,
    ContextSection,
    FactStatus,
    Freshness,
    MemoryLayer,
    MemoryLifecycle,
    SectionKind,
    Task,
)
from raya.memory import MemoryStore
from raya.world_state import WorldStateStore

from .ranking import trim_to_budget
from .tokens import estimate_tokens

_CONFIDENCE_WEIGHT = {
    Confidence.KNOWN_FACT: 1.0,
    Confidence.INFERRED: 0.6,
    Confidence.HYPOTHESIS: 0.3,
}
_FRESHNESS_WEIGHT = {FactStatus.ACTIVE: 1.0, FactStatus.STALE: 0.4, FactStatus.SUPERSEDED: 0.0}
_LIFECYCLE_WEIGHT = {"confirmed": 1.0, "active": 0.8, "candidate": 0.5, "aging": 0.3, "obsolete": 0.0}

# Nom de l'assistant — donnée stable de branding, jamais une réponse figée à
# "quel est ton nom ?" (celle-ci reste composée par le modèle à partir de
# cette DONNÉE structurée, consigne stabilisation pré-Phase 7 §3).
_ASSISTANT_NAME = "RAYA"

# Provenance des faits d'identité "toujours disponibles" (voir
# _identity_baseline_sections) — décidée UNE FOIS au niveau de la catégorie
# de données (la section IDENTITÉ du profil), jamais par mot-clé de la
# question posée. scripts/ingest_profile.py écrit exactement cette provenance.
_IDENTITY_BASELINE_PROVENANCE = "profile_migration:identity"


def _system_rules_section(runtime_identity: dict | None) -> ContextSection:
    """runtime_context (consigne stabilisation pré-Phase 7 §3-4) : assistant
    identity + provider + modèle actif RÉELS, jamais inventés. `runtime_identity`
    vient de `Harness` (seul endroit qui a le droit d'importer raya.models) —
    context_engine ne route jamais lui-même vers un modèle."""
    runtime_identity = runtime_identity or {}
    return ContextSection(
        kind=SectionKind.SYSTEM_RULES,
        content={
            "assistant_identity": {"name": _ASSISTANT_NAME},
            "runtime": {
                "provider": runtime_identity.get("provider"),
                "model": runtime_identity.get("model"),
            },
        },
        provenance="context_engine:runtime_identity",
        rank_score=1.0,
    )


def _task_state_section(task: Task) -> ContextSection:
    return ContextSection(
        kind=SectionKind.TASK_STATE,
        content={
            "task_id": task.id,
            "objective": task.objective,
            "state": task.state.value,
            "progress": {"current_step": task.progress.current_step, "percent": task.progress.percent},
        },
        provenance="tasks:current",
        rank_score=1.0,
    )


# Chantier 16 : jamais mandatory (contrairement à TASK_STATE ci-dessus) — un
# utilisateur avec de nombreuses tâches actives ne doit pas voir TOUTES
# forcées dans le contexte peu importe le budget (consigne §22, "contexte
# pertinent et limité"). Priorité légèrement inférieure à l'historique de
# conversation (0.95) mais au-dessus du score par défaut des faits World
# State/Memory les moins pertinents.
_ACTIVE_TASK_RANK_SCORE = 0.8


def _active_tasks_sections(active_tasks: tuple[Task, ...], exclude_task_id: str | None) -> list[ContextSection]:
    return [
        ContextSection(
            kind=SectionKind.ACTIVE_TASKS,
            content={"task_id": t.id, "objective": t.objective, "state": t.state.value, "not_before": t.not_before},
            provenance="tasks:active",
            rank_score=_ACTIVE_TASK_RANK_SCORE,
        )
        for t in active_tasks
        if t.id != exclude_task_id
    ]


def _world_state_sections(world_state: WorldStateStore, domains: tuple[str, ...]) -> list[ContextSection]:
    facts = world_state.retrieve_relevant(domains)
    sections = []
    for fact in facts:
        score = _CONFIDENCE_WEIGHT.get(fact.confidence, 0.5) * _FRESHNESS_WEIGHT.get(fact.status, 0.2)
        sections.append(
            ContextSection(
                kind=SectionKind.WORLD_STATE,
                content={"domain": fact.domain, "key": fact.key, "value": fact.value},
                provenance=fact.source,
                rank_score=score,
                freshness=Freshness(status=fact.status, as_of=fact.timestamp),
            )
        )
    return sections


def _memory_sections(
    memory: MemoryStore, channel_scope: ChannelScope, query_text: str, exclude_ids: frozenset[str] = frozenset()
) -> list[ContextSection]:
    hits = memory.search(query=query_text, channel_scope=channel_scope)
    sections = []
    for entry in hits:
        if entry.layer == MemoryLayer.CONVERSATION:
            continue  # historique de conversation traité séparément
        if entry.id in exclude_ids:
            continue  # déjà inclus par _identity_baseline_sections
        score = _LIFECYCLE_WEIGHT.get(entry.lifecycle.value, 0.5) * _CONFIDENCE_WEIGHT.get(entry.confidence, 0.5)
        sections.append(
            ContextSection(
                kind=SectionKind.MEMORY,
                content={"id": entry.id, "type": entry.type.value, "content": entry.content},
                provenance=entry.provenance,
                rank_score=score,
            )
        )
    return sections


def _identity_baseline_sections(memory: MemoryStore, channel_scope: ChannelScope) -> list[ContextSection]:
    """Faits d'identité CONFIRMÉS toujours disponibles, comme system_rules —
    JAMAIS filtrés par correspondance de mots-clés avec la question posée.

    Cause exacte diagnostiquée (stabilisation pré-Phase 7) : "Qui suis-je ?"
    ne partage lexicalement AUCUN mot de ≥4 lettres avec "Nom complet : Ruben
    Lukusa" — un filtrage par pertinence textuelle (memory.search) échoue
    donc systématiquement sur ce cas précis, qui est pourtant l'exemple
    canonique de "identité pertinente". Bornée à la seule section IDENTITÉ du
    profil migré (nom, préférence de tutoiement/adressage, localisation —
    ex: "Réside à Evere, Bruxelles, Belgique") — jamais tout le profil
    (guitare/jeux vidéo/famille restent uniquement récupérables par
    pertinence textuelle via _memory_sections, jamais injectés par défaut —
    consigne "ne pas créer un énorme prompt statique").

    BUG CORRIGÉ (passe "Targeted Fix" — Sujet 1, confirmé empiriquement
    contre la vraie base) : `memory.search(..., limit=50)` reste un tri par
    SCORE (lifecycle + petit bonus de récence, jamais par mot-clé ici
    puisque query="") — silencieusement, un fait d'identité migré une seule
    fois en début d'usage (ex: la localisation) finit par sortir des 50
    entrées les mieux scorées à mesure que de nouvelles entrées CONVERSATION
    (plus récentes) s'accumulent, AVANT même que le filtre de provenance
    ci-dessous ne s'applique — le fait n'est alors JAMAIS vu, quel que soit
    le budget de contexte disponible (vérifié : 837/4096 tokens utilisés,
    section absente). Un plafond nettement plus généreux (jamais atteint en
    pratique par le nombre de faits d'identité réels, qui reste de l'ordre
    de la dizaine) réalise fidèlement l'intention déjà documentée
    ci-dessus : "toujours disponibles", jamais évincés par le volume de
    conversation accumulé."""
    hits = memory.search(query="", channel_scope=channel_scope, limit=2000)
    baseline = [
        e for e in hits
        if e.layer == MemoryLayer.PERSONAL and e.provenance == _IDENTITY_BASELINE_PROVENANCE
    ]
    return [
        ContextSection(
            kind=SectionKind.MEMORY,
            content={"id": entry.id, "type": entry.type.value, "content": entry.content},
            provenance=entry.provenance,
            rank_score=1.0,  # priorité maximale, comme system_rules — jamais évincé par le budget en premier
        )
        for entry in baseline
    ]


def _personal_context_sections(
    memory: MemoryStore,
    channel_scope: ChannelScope,
    exclude_ids: frozenset[str] = frozenset(),
    limit: int = 8,
) -> list[ContextSection]:
    """Contexte personnel CONFIRMED/ACTIVE — JAMAIS keyword-filtré.

    F1 root cause (20A) : les préférences / hobbies / style de communication
    stockés en MemoryLayer.PERSONAL (ex: "ÉCHECS : joue le dimanche",
    "Tutoiement, ton direct") sont invisibles pour des requêtes sans
    chevauchement lexical direct ("mes activités ?", "comment tu me parles ?")
    parce que _memory_sections() passe par memory.search(query=query_text)
    dont le filtre _significant_words(≥4 chars) exclut ces entrées en amont
    du scoring. Même discipline que _identity_baseline_sections() : query=""
    pour récupérer TOUTES les entrées, puis filtre par layer + lifecycle.

    Invariants :
    - CONFIRMED et ACTIVE seulement — CANDIDATE/AGING jamais injectés
    - exclut l'identity baseline (déjà dans _identity_baseline_sections)
    - limit=8 : protection contre un profil très grand
    - exclude_ids : évite les doublons avec les sections déjà assemblées"""
    hits = memory.search(query="", channel_scope=channel_scope, limit=500)
    personal = [
        e for e in hits
        if e.layer == MemoryLayer.PERSONAL
        and e.provenance != _IDENTITY_BASELINE_PROVENANCE
        and e.id not in exclude_ids
        and e.lifecycle in (MemoryLifecycle.CONFIRMED, MemoryLifecycle.ACTIVE)
    ]
    personal.sort(
        key=lambda e: _LIFECYCLE_WEIGHT.get(e.lifecycle.value, 0.5)
                      * _CONFIDENCE_WEIGHT.get(e.confidence, 0.5),
        reverse=True,
    )
    return [
        ContextSection(
            kind=SectionKind.MEMORY,
            content={"id": entry.id, "type": entry.type.value, "content": entry.content},
            provenance=entry.provenance,
            rank_score=_LIFECYCLE_WEIGHT.get(entry.lifecycle.value, 0.5),
        )
        for entry in personal[:limit]
    ]


_ASSISTANT_PROVENANCE_SUFFIX = ":assistant"


def _entry_role(entry) -> str:
    """RAYA_V2_PHASE11 fix (context continuity) : `Harness.handle_request()`
    écrit désormais AUSSI la réponse de RAYA en mémoire CONVERSATION (avant
    cette phase, seul le message utilisateur l'était — le modèle ne
    "voyait" jamais ses propres tours précédents, rendant impossible toute
    résolution de référent portant sur sa propre réponse, ex: "Oui lance-la"
    après que RAYA ait dit "je peux lancer la calculatrice"). Le rôle est
    distingué par un simple suffixe de provenance, jamais un nouveau champ
    de contrat ni un second système de mémoire."""
    return "assistant" if str(entry.provenance).endswith(_ASSISTANT_PROVENANCE_SUFFIX) else "user"


def _conversation_history_section(
    memory: MemoryStore, channel_scope: ChannelScope, limit: int = 5
) -> ContextSection | None:
    hits = memory.search(query="", channel_scope=channel_scope, type_filter=None, limit=50)
    # `search()` classe par score décroissant (le plus RÉCENT en tête) —
    # `[:limit]` sélectionne donc bien les `limit` derniers tours, mais dans
    # l'ordre récent -> ancien. Un transcript doit se lire chronologiquement
    # (ancien -> récent, comme une vraie conversation) : `[::-1]` inverse
    # UNIQUEMENT l'ordre d'affichage, jamais la sélection elle-même.
    conv = [e for e in hits if e.layer == MemoryLayer.CONVERSATION][:limit][::-1]
    if not conv:
        return None
    return ContextSection(
        kind=SectionKind.CONVERSATION_HISTORY,
        content={"recent": [{"id": e.id, "content": e.content, "role": _entry_role(e)} for e in conv]},
        provenance="memory:conversation",
        rank_score=0.95,
    )


def _tool_schema_section(tools_registry, capability_tags: tuple[str, ...]) -> ContextSection | None:
    if tools_registry is None or not capability_tags:
        return None
    from raya.tools import discover  # lecture seule (discovery), jamais tools.execution

    tools = discover(tools_registry, list(capability_tags))
    if not tools:
        return None
    return ContextSection(
        kind=SectionKind.TOOL_SCHEMAS,
        content={"tools": [{"name": t.name, "description": t.description} for t in tools]},
        provenance="tools:discovery",
        rank_score=0.9,
    )


def assemble(
    session_id: str,
    channel_scope: ChannelScope,
    world_state: WorldStateStore,
    memory: MemoryStore,
    budget_tokens: int = 4096,
    world_state_domains: tuple[str, ...] = (),
    task: Task | None = None,
    active_tasks: tuple[Task, ...] = (),
    query_text: str = "",
    tools_registry: object | None = None,
    capability_tags: tuple[str, ...] = (),
    runtime_identity: dict | None = None,
) -> Context:
    candidates: list[ContextSection] = [_system_rules_section(runtime_identity)]

    if task is not None:
        candidates.append(_task_state_section(task))

    candidates.extend(_active_tasks_sections(active_tasks, exclude_task_id=task.id if task is not None else None))

    conv_section = _conversation_history_section(memory, channel_scope)
    if conv_section is not None:
        candidates.append(conv_section)

    candidates.extend(_world_state_sections(world_state, world_state_domains))

    identity_sections = _identity_baseline_sections(memory, channel_scope)
    identity_ids = frozenset(s.content["id"] for s in identity_sections)
    candidates.extend(identity_sections)

    personal_sections = _personal_context_sections(memory, channel_scope, exclude_ids=identity_ids)
    personal_ids = frozenset(s.content["id"] for s in personal_sections)
    candidates.extend(personal_sections)

    candidates.extend(_memory_sections(memory, channel_scope, query_text, exclude_ids=identity_ids | personal_ids))

    tool_section = _tool_schema_section(tools_registry, capability_tags)
    if tool_section is not None:
        candidates.append(tool_section)

    trimmed = trim_to_budget(candidates, budget_tokens)
    used = sum(estimate_tokens(s.content) for s in trimmed)
    # Si `used` dépasse budget_tokens ici, c'est que les sections OBLIGATOIRES
    # (system_rules/task_state, jamais rognées) seules dépassent déjà le budget
    # demandé — Context.__post_init__ lève alors une erreur explicite plutôt
    # que de mentir sur la consommation réelle (RAYA_V2_MIGRATION_PLAN.md §22,
    # "no silent failures"). trim_to_budget() garantit que les sections
    # OPTIONNELLES, elles, ne font jamais dépasser le budget.

    return Context(
        session_id=session_id,
        budget_tokens=budget_tokens,
        sections=trimmed,
        task_id=task.id if task is not None else None,
        used_tokens_estimate=used,
    )
