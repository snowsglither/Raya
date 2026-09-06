"""Device Agent iOS (Chantier 13, Phone Integration MVP) — voir agent.py
pour le contexte architectural complet (pourquoi `ios/` et pas `phone/`,
pourquoi Phone Link plutôt qu'une API privée)."""

from .agent import DEVICE_ID, PhoneLinkDeviceAgent

__all__ = ["DEVICE_ID", "PhoneLinkDeviceAgent"]
