"""DeviceAgent — interface générique (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.13, §9.1).

Phase 0 : squelette (aucune implémentation concrète). Phase 4 : premiers
Device Agents réels (Windows, Browser) — extension additive de l'ABC, voir
`should_stop` ci-dessous. Règle absolue vérifiée par le lint architectural :
cette interface, ni aucune implémentation, n'importe JAMAIS raya.models — un
Device Agent n'a pas de cerveau (RAYA_V2_ARCHITECTURAL_INVARIANTS.md #5).

`should_stop` (Phase 4, ajouté à `execute()`) : un Device Agent doit pouvoir
s'interrompre coopérativement en cours d'exécution longue (invariant #6 —
STOP vérifié à intervalles courts). Injecté en PARAMÈTRE plutôt qu'importé
depuis `raya.safety` — `devices/` n'a le droit d'importer que des
bibliothèques bas niveau + `observability` (RAYA_V2_TECHNICAL_ARCHITECTURE.md
§1.13 « Dépendances autorisées »), jamais `safety/`. C'est `tools/catalog/`
(qui, lui, a accès à `safety`) qui referme la boucle en passant
`safety.should_stop` comme callable au moment d'appeler `device.execute()`.
Paramètre à valeur par défaut : extension rétrocompatible, aucun appelant
Phase 0-3 n'existait encore pour cette méthode.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from raya.contracts import Capability, Command, Health, Result

ShouldStop = Callable[[], bool]


def _never_stop() -> bool:
    return False


class DeviceAgent(ABC):
    @abstractmethod
    def list_capabilities(self) -> list[Capability]: ...

    @abstractmethod
    def execute(self, command: Command, should_stop: ShouldStop = _never_stop) -> Result: ...

    @abstractmethod
    def health(self) -> Health: ...
