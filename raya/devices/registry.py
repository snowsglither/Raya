"""DeviceRegistry (Phase 4) — résout `Command.device_id` vers un `DeviceAgent`
réel. Absente des Phases 0-3 (aucun Device Agent concret n'existait) ; suit
exactement le pattern déjà établi par `ToolRegistry`/`ModelRegistry` —
`RAYA_V2_REPOSITORY_STRUCTURE.md` §14 ne l'énumère pas fichier par fichier
mais `Command.device_id` (RAYA_V2_CONTRACTS.md §16) implique nécessairement
un point de résolution ; ajout non prévu explicitement mais non contradictoire.

Phase 9 (§16, Mobile/Telegram/Devices) : donne enfin vie au contrat `Device`
(identity/status/capabilities/platform/metadata/last_seen,
RAYA_V2_ARCHITECTURAL_BLUEPRINT.md §19), resté vestigial depuis la Phase 0 —
AUCUN appelant ne construisait `Device(...)` avant cette phase. Deux formes
de présence, un seul registre (jamais un second système) :
  - un DEVICE AGENT exécutable (Windows/Browser, Phase 4) — `register()`,
    inchangé dans sa signature minimale, `describe()` synthétise un `Device`
    à partir de `agent.list_capabilities()`/`agent.health()` en temps réel.
  - un DEVICE INFORMATIONNEL, jamais exécutable (ex: le téléphone de Ruben,
    connu via l'Interface Telegram, §17 : jamais transformé en agent
    autonome) — `register_info()`/`touch()` stockent un `Device` fourni
    directement, jamais synthétisé depuis un `DeviceAgent` inexistant.
"""

from __future__ import annotations

import threading

from raya.contracts import Capability, Device, DeviceStatus, DeviceType, Health, utc_now_iso

from .base import DeviceAgent


class DeviceRegistry:
    def __init__(self) -> None:
        self._devices: dict[str, DeviceAgent] = {}
        self._agent_types: dict[str, DeviceType] = {}
        self._info: dict[str, Device] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Device Agents exécutables (Windows/Browser, Phase 4) — inchangé
    # ------------------------------------------------------------------

    def register(self, device_id: str, agent: DeviceAgent, *, device_type: DeviceType | None = None) -> None:
        """`device_type` est un paramètre ADDITIF optionnel (Phase 9) — les
        appels Phase 4 existants (`devices.register(_WIN_ID, windows_agent)`)
        restent valides tels quels, `describe()` retombe alors sur
        `DeviceType.FUTURE` plutôt que de deviner."""
        self._devices[device_id] = agent
        if device_type is not None:
            self._agent_types[device_id] = device_type

    def get(self, device_id: str) -> DeviceAgent | None:
        return self._devices.get(device_id)

    def all_ids(self) -> list[str]:
        return sorted(self._devices)

    def health_all(self) -> dict[str, Health]:
        return {device_id: agent.health() for device_id, agent in self._devices.items()}

    # ------------------------------------------------------------------
    # Devices informationnels (Phase 9) — jamais exécutables, jamais un
    # DeviceAgent : le Registry sert ICI uniquement à SAVOIR qu'un device
    # existe, pas à lui envoyer des Command (§16 : "connaître les
    # devices/capabilities disponibles", pas les piloter).
    # ------------------------------------------------------------------

    def register_info(
        self, device_id: str, device_type: DeviceType, *,
        platform: str | None = None, capabilities: tuple[Capability, ...] = (), metadata: dict | None = None,
    ) -> Device:
        with self._lock:
            device = Device(
                id=device_id, type=device_type, capabilities=list(capabilities),
                status=DeviceStatus.ONLINE, platform=platform, metadata=dict(metadata or {}),
            )
            self._info[device_id] = device
            return device

    def touch(self, device_id: str, *, metadata: dict | None = None) -> None:
        """Preuve de vie (ex: message Telegram reçu) — met à jour `last_seen`
        SANS republier tout le device (mutation en place, même style que
        `WorldStateStore`/`SceneStore` : un seul point d'écriture).

        `metadata` (Phase 11, additif) : fusionné (jamais remplacé) dans le
        `metadata` existant du device — permet à `TelegramChannel` d'y noter
        `last_chat_id` à chaque message reçu, seule info nécessaire pour que
        la capacité `telegram.send_message` (tools/catalog/notify.py) sache
        à QUI répondre sans que Telegram devienne un second système."""
        with self._lock:
            device = self._info.get(device_id)
            if device is not None:
                device.last_seen = utc_now_iso()
                device.status = DeviceStatus.ONLINE
                if metadata:
                    device.metadata.update(metadata)

    def mark_offline(self, device_id: str) -> None:
        with self._lock:
            device = self._info.get(device_id)
            if device is not None:
                device.status = DeviceStatus.OFFLINE

    def unregister_info(self, device_id: str) -> None:
        with self._lock:
            self._info.pop(device_id, None)

    # ------------------------------------------------------------------
    # Lecture unifiée — agents exécutables ET devices informationnels
    # ------------------------------------------------------------------

    def describe(self, device_id: str) -> Device | None:
        agent = self._devices.get(device_id)
        if agent is not None:
            health = agent.health()
            return Device(
                id=device_id, type=self._agent_types.get(device_id, DeviceType.FUTURE),
                capabilities=agent.list_capabilities(), status=health.status, last_seen=health.checked_at,
            )
        with self._lock:
            return self._info.get(device_id)

    def list_devices(self) -> list[Device]:
        ids = set(self._devices) | set(self._info)
        devices = [self.describe(device_id) for device_id in sorted(ids)]
        return [d for d in devices if d is not None]
