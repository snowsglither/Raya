"""Ranking déterministe + application du budget (RAYA_V2_MIGRATION_PLAN.md §10.1-10.2
de la consigne Phase 1). Aucun appel modèle ici — "Context Engine doit être
déterministe autant que possible".
"""

from __future__ import annotations

from raya.contracts import ContextSection, FactStatus, SectionKind

from .tokens import estimate_tokens

# Poids de fraîcheur pour le ranking des sections world_state — un fait stale
# reste inclus (jamais masqué) mais pèse moins dans le tri (RAYA_V2_TECHNICAL_ARCHITECTURE.md §5).
_FRESHNESS_WEIGHT = {
    FactStatus.ACTIVE: 1.0,
    FactStatus.STALE: 0.4,
    FactStatus.SUPERSEDED: 0.0,  # ne devrait jamais apparaître ici (exclu en amont)
}

_MANDATORY_KINDS = {SectionKind.SYSTEM_RULES, SectionKind.TASK_STATE}


def trim_to_budget(sections: list[ContextSection], budget_tokens: int) -> list[ContextSection]:
    """Trie par rank_score décroissant, inclut toujours les sections
    obligatoires (system_rules/task_state — supposées petites par nature),
    puis ajoute les sections optionnelles tant que le budget le permet.
    Ne dépasse JAMAIS arbitrairement le budget pour les sections optionnelles.
    """
    mandatory = [s for s in sections if s.kind in _MANDATORY_KINDS]
    optional = sorted(
        (s for s in sections if s.kind not in _MANDATORY_KINDS),
        key=lambda s: s.rank_score,
        reverse=True,
    )

    result: list[ContextSection] = []
    used = 0
    for section in mandatory:
        used += estimate_tokens(section.content)
        result.append(section)

    for section in optional:
        cost = estimate_tokens(section.content)
        if used + cost > budget_tokens:
            continue
        used += cost
        result.append(section)

    return result
