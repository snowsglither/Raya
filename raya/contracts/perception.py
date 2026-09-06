"""PerceptionObservation (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.3, §11 ;
RAYA_V2_ARCHITECTURAL_INVARIANTS.md, format de provenance "<subsystem>:<mechanism>").

Payload TYPÉ des events `perception.*` publiés sur l'EventBus — même principe
que `TaskEventPayload` (`contracts/task.py`) : un `Event` générique porte un
payload dict libre, mais un event STRUCTURÉ mérite un contrat explicite,
round-trip-testable, plutôt que des clés de dict devinées à chaque site
d'appel. `world_state/store.py` est le seul lecteur (s'abonne à `perception.*`
et convertit ce contrat en `WorldStateFact`)."""

from __future__ import annotations

from dataclasses import dataclass

from .world_state import Confidence


@dataclass
class PerceptionObservation:
    domain: str
    key: str
    value: object
    source: str  # format "perception:<mechanism>", ex: "perception:foreground_window"
    confidence: Confidence = Confidence.KNOWN_FACT
    freshness_ttl_s: int | None = None

    def __post_init__(self) -> None:
        if self.confidence == Confidence.HYPOTHESIS:
            raise ValueError(
                "PerceptionObservation: confidence=hypothesis interdit — la perception observe "
                "des faits ou des inférences immédiates, jamais une hypothèse non fondée "
                "(RAYA_V2_ARCHITECTURAL_INVARIANTS.md)."
            )
        if not self.source.startswith("perception:"):
            raise ValueError(
                f"PerceptionObservation.source doit suivre le format 'perception:<mechanism>' (reçu: {self.source!r})"
            )
