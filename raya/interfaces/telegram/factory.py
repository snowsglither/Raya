"""Câblage de la chaîne Telegram RÉELLE — délibérément SÉPARÉ de
`raya/runtime/bootstrap.py` (RAYA V2 Phase 9), même principe que
`raya/interfaces/voice/factory.py` (Phase 5) : Telegram est opt-in
(RAYA_ENABLE_TELEGRAM), un token/allowlist mal configuré ou l'absence de
réseau ne doit jamais empêcher le reste de RAYA de démarrer.

Composition-root miniature, symétrique à `voice/factory.py` : câblage pur,
aucune logique métier ici."""

from __future__ import annotations

from typing import Protocol

from raya.contracts import DeviceType
from raya.event_bus import EventBus
from raya.harness import Harness

from .auth import TelegramAuthorizer
from .channel import TelegramChannel
from .client import TelegramClient
from .runtime import TelegramRuntime

DEVICE_ID = "telegram-mobile"


class _RuntimeHandlesLike(Protocol):
    """Duck-typing local (même raison que `voice/factory.py`) : `interfaces/`
    ne dépend jamais de `runtime/` (RAYA_V2_REPOSITORY_STRUCTURE.md §20)."""

    harness: Harness
    bus: EventBus


def build_real_telegram_runtime(
    handles: _RuntimeHandlesLike, *, token: str, allowed_user_ids: tuple[int, ...] = (),
    poll_timeout_s: int = 25,
) -> tuple[TelegramChannel, TelegramRuntime]:
    """Construit une chaîne Telegram RÉELLE branchée sur un runtime déjà
    démarré (`bootstrap()`). Ne démarre RIEN elle-même — l'appelant décide
    quand `runtime.start()` (même contrat que `build_real_voice_runtime`)."""
    client = TelegramClient(token)
    channel = TelegramChannel(handles.harness, handles.bus, client.send_message, device_id=DEVICE_ID,
                               allowed_user_ids=allowed_user_ids)
    authorizer = TelegramAuthorizer(allowed_user_ids)
    handles.harness.register_device_info(
        DEVICE_ID, DeviceType.MOBILE, platform="telegram", metadata={"interface": "telegram"},
    )
    runtime = TelegramRuntime(client, channel, authorizer, handles.harness, poll_timeout_s=poll_timeout_s)
    return channel, runtime
