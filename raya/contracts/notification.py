"""NotificationChannel (Chantier 12 §E) — canal d'ENVOI structuré (Telegram/
Email/Phone), DISTINCT de `ChannelScope` (memory.py, qui désigne l'INTERFACE
d'origine voix/chat/ios d'une conversation, jamais un canal de notification).
Vit dans contracts/ (toujours importable) car aussi bien `tools/catalog/`
(le Tool de préférence) que `harness/` en ont besoin."""

from __future__ import annotations

import enum


class NotificationChannel(str, enum.Enum):
    TELEGRAM = "telegram"
    EMAIL = "email"
    PHONE = "phone"
