"""Interface Telegram (RAYA V2 Phase 9) — un client mince du Harness, comme
Cockpit (Phase 6) et Voice (Phase 5). Voir `channel.py` pour l'invariant
d'architecture (Interface -> Harness uniquement)."""

from .auth import TelegramAuthDecision, TelegramAuthorizer
from .channel import TelegramChannel
from .chunker import TELEGRAM_MESSAGE_LIMIT, chunk_message
from .client import TelegramClient, redact_token
from .factory import DEVICE_ID, build_real_telegram_runtime
from .runtime import TelegramRuntime
from .session import chat_id_from_session, session_id_for_chat

__all__ = [
    "TelegramAuthDecision",
    "TelegramAuthorizer",
    "TelegramChannel",
    "TelegramClient",
    "TelegramRuntime",
    "TELEGRAM_MESSAGE_LIMIT",
    "chunk_message",
    "redact_token",
    "session_id_for_chat",
    "chat_id_from_session",
    "build_real_telegram_runtime",
    "DEVICE_ID",
]
