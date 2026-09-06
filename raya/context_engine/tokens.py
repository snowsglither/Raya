"""Estimateur de tokens (RAYA_V2_MIGRATION_PLAN.md §10.2 de la consigne Phase 1).

"Pas besoin d'un tokenizer parfait : une estimation documentée est acceptable
pour Phase 1, mais l'abstraction doit permettre de remplacer l'estimateur
plus tard." — heuristique ~4 caractères/token (approximation standard pour
l'anglais/français en usage courant), isolée dans cette seule fonction.
"""

from __future__ import annotations

import json

_CHARS_PER_TOKEN = 4


def estimate_tokens(content: object) -> int:
    if isinstance(content, str):
        text = content
    else:
        text = json.dumps(content, ensure_ascii=False, default=str)
    return max(1, len(text) // _CHARS_PER_TOKEN)
