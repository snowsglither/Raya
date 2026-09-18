"""ExternalInteraction — primitive de données pour les interactions externes
continues (Chantier 20). Stockée comme WorldStateFact (domain='interaction',
key=interaction_id) — jamais un manager, jamais un second orchestrateur.

Le modèle peut suivre qu'une interaction externe est AWAITING_EXTERNAL_REPLY
et résoudre les pronoms/référents quand la réponse arrivera dans un tour futur.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from ._base import new_id, utc_now_iso


class ExternalInteractionState(str, enum.Enum):
    AWAITING_EXTERNAL_REPLY = "AWAITING_EXTERNAL_REPLY"
    REPLIED = "REPLIED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


ACTIVE_INTERACTION_STATES: frozenset[ExternalInteractionState] = frozenset(
    {ExternalInteractionState.AWAITING_EXTERNAL_REPLY}
)


@dataclass
class ExternalInteraction:
    """Représente une interaction externe continue (ex: message envoyé, réponse attendue).

    Stockée dans WorldState (domain='interaction') → persistée SQLite, visible
    depuis n'importe quelle interface (Cockpit/Telegram/voix), et assemblée
    automatiquement dans le contexte du modèle (WORLD_STATE section).
    """
    interlocutor: str
    channel: str
    outgoing_message: str
    id: str = field(default_factory=lambda: new_id("inter"))
    state: str = ExternalInteractionState.AWAITING_EXTERNAL_REPLY.value
    original_request: str = ""
    source_language: str = ""
    target_language: str = ""
    expected_reply: str = ""
    reply_text: str = ""
    created_at: str = field(default_factory=utc_now_iso)
    last_activity: str = field(default_factory=utc_now_iso)
    continuation_context: dict = field(default_factory=dict)
    owner_session_id: str = ""
