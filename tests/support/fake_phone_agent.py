"""FakePhoneDeviceAgent (Chantier 13C, Safety Test Isolation) — double de
test STRUCTURELLEMENT incapable d'atteindre le vrai mécanisme Phone Link :
n'importe jamais `raya.devices.ios.mechanisms.phone_link`, ne construit
jamais de vrai `PhoneLinkDeviceAgent`. Contrairement à un monkeypatch des
fonctions du module réel (raya/devices/ios/agent.py::phone_link.*, toujours
correct mais qui laisserait passer un appel réel si une nouvelle fonction
était ajoutée à l'agent sans être repatché dans le test), cette classe ne
peut structurellement jamais exécuter d'action téléphonique réelle, quel
que soit le chemin emprunté.

Root cause Chantier 13C (voir RAYA_V2_CHANTIER13C_REPORT) : un script de
vérification AD HOC (pas un test pytest committé) a appelé
`execute(..., user_confirmed=True)` contre un `bootstrap()` réel (avec le
vrai `PhoneLinkDeviceAgent` enregistré, car `RuntimeConfig.
enable_phone_device` par défaut à True) pour observer le comportement du
chemin confirmé — plaçant un vrai appel téléphonique. La suite pytest
committée était déjà correctement isolée par monkeypatch ; ce fichier
fournit en plus l'outil canonique, structurellement sûr, pour TOUT test qui
vérifie le comportement Safety (SENSITIVE -> confirmation requise) des
Tools téléphoniques — jamais `PhoneLinkDeviceAgent` réel dans un tel test."""

from __future__ import annotations

from dataclasses import dataclass, field

from raya.contracts import Capability, Command, CommandStatus, DeviceStatus, ErrorInfo, Health, Result
from raya.devices import DeviceAgent, ShouldStop


def _never_stop() -> bool:
    return False


@dataclass
class FakePhoneDeviceAgent(DeviceAgent):
    """Chaque appel à `execute()` est enregistré dans `calls` (jamais
    d'action réelle) — les tests affirment `calls == []` (rien exécuté) ou
    `len(calls) == 1` (exactement une exécution, après confirmation),
    jamais en observant un effet de bord réel externe."""

    device_id: str = "fake_phone_agent"
    calls: list[tuple[str, dict]] = field(default_factory=list)
    responses: dict[str, dict] = field(default_factory=dict)

    def list_capabilities(self) -> list[Capability]:
        return []

    def health(self) -> Health:
        return Health(device_id=self.device_id, status=DeviceStatus.ONLINE)

    def execute(self, command: Command, should_stop: ShouldStop = _never_stop) -> Result:
        if should_stop():
            return Result(command_id=command.id, status=CommandStatus.CANCELLED,
                           error=ErrorInfo(code="STOP_ACTIVE", message="Interrompu par STOP avant exécution", retryable=False))
        self.calls.append((command.capability_name, dict(command.arguments)))
        output = self.responses.get(command.capability_name, {"status": "ok", "fake": True})
        return Result(command_id=command.id, status=CommandStatus.SUCCESS, output=output, mechanism_used="fake_phone_agent")
