"""Session Telegram (RAYA V2 Phase 9, consigne §7).

`telegram:<chat_id>` — jamais une session globale unique "telegram" (ce qui
mélangerait la conversation/mémoire/tâches de deux chats différents). Un
`chat_id` Telegram est stable pour la durée de vie d'une conversation privée
— suffisant comme clé d'isolation, exactement comme `cockpit-<random>` (Phase
6) ou `voice-<id>` (Phase 5)."""

from __future__ import annotations

_PREFIX = "telegram:"


def session_id_for_chat(chat_id: int) -> str:
    return f"{_PREFIX}{chat_id}"


def chat_id_from_session(session_id: str) -> int | None:
    if not session_id.startswith(_PREFIX):
        return None
    try:
        return int(session_id[len(_PREFIX):])
    except ValueError:
        return None
