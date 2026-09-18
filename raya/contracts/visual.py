"""VisualObservation, BoundingBox, VisualTarget, ViewportInfo, VisualArtifact.

RÈGLE DE CONFIANCE (RAYA_V2_ARCHITECTURAL_INVARIANTS.md) :
  - confidence = INFERRED obligatoire pour toute VisualObservation.
  - Un modèle vision produit des INFÉRENCES, jamais des KNOWN_FACT
    (réservés aux capteurs OS vérifiables — win32gui, UIA, etc.).
  - artifact_ref est un chemin de fichier LOCAL temporaire — jamais
    stocké dans WorldState, jamais transmis cross-session.

RÈGLE DE PROVENANCE :
  - Chaque VisualTarget est lié à un observation_id et un ViewportInfo.
  - Jamais de coordonnées "nues" sans provenance — toujours
    traceable jusqu'à l'observation qui les a produites.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ._base import new_id, utc_now_iso
from .world_state import Confidence


@dataclass
class BoundingBox:
    """Boîte englobante normalisée [0.0, 1.0] relative aux dimensions
    de l'image source.

    IMPORTANT : ce sont des coordonnées normalisées, PAS des pixels absolus.
    Appeler to_pixel() ou center_pixel() pour la transformation."""

    x_min: float
    y_min: float
    x_max: float
    y_max: float

    def __post_init__(self) -> None:
        for name, val in (("x_min", self.x_min), ("y_min", self.y_min),
                           ("x_max", self.x_max), ("y_max", self.y_max)):
            if not (0.0 <= val <= 1.0):
                raise ValueError(f"BoundingBox.{name}={val} hors [0.0, 1.0]")
        if self.x_min >= self.x_max or self.y_min >= self.y_max:
            raise ValueError(f"BoundingBox dégénérée : [{self.x_min},{self.y_min},{self.x_max},{self.y_max}]")

    @property
    def center_x(self) -> float:
        return (self.x_min + self.x_max) / 2.0

    @property
    def center_y(self) -> float:
        return (self.y_min + self.y_max) / 2.0

    def to_pixel(self, width: int, height: int) -> tuple[int, int, int, int]:
        """(left, top, right, bottom) en pixels absolus."""
        return (
            int(self.x_min * width),
            int(self.y_min * height),
            int(self.x_max * width),
            int(self.y_max * height),
        )

    def center_pixel(self, width: int, height: int) -> tuple[int, int]:
        """Centre en pixels absolus."""
        return (int(self.center_x * width), int(self.center_y * height))


@dataclass
class ViewportInfo:
    """Système de coordonnées de la capture — indispensable pour transformer
    des bbox normalisées en coordonnées souris/écran.

    Tous les champs ont des défauts raisonnables (pas de HiDPI, pas de zoom,
    pas de scroll) pour simplifier les cas simples tout en permettant la
    transformation correcte dans les cas complexes."""

    image_width: int
    image_height: int
    window_left: int = 0             # position de la fenêtre dans l'écran (px)
    window_top: int = 0
    device_pixel_ratio: float = 1.0  # rapport pixel physique / pixel CSS
    browser_zoom: float = 1.0        # zoom navigateur (0.9, 1.0, 1.25...)
    scroll_x: int = 0                # scroll courant du document
    scroll_y: int = 0


@dataclass
class VisualTarget:
    """Cible visuellement identifiée dans une image — toujours liée à une
    observation (observation_id) pour garantir la traçabilité des coordonnées.

    screen_coordinates() retourne les coordonnées PHYSIQUES utilisables
    directement par pc.mouse.click ou browser.click_at_position."""

    label: str
    bbox: BoundingBox
    viewport: ViewportInfo
    observation_id: str
    confidence: float = 0.0  # [0.0, 1.0]

    def screen_coordinates(self) -> tuple[int, int]:
        """Coordonnées physiques écran — transformation complète appliquée.

        Pour un screenshot plein écran : window_left/top = 0, device_pixel_ratio
        et browser_zoom s'annulent si l'image a la résolution physique réelle.
        Pour un screenshot browser : appliquer la position de fenêtre Edge."""
        cx, cy = self.bbox.center_pixel(self.viewport.image_width, self.viewport.image_height)
        screen_x = self.viewport.window_left + cx
        screen_y = self.viewport.window_top + cy
        return (screen_x, screen_y)


@dataclass
class VisualArtifact:
    """Représentation d'une image capturée AVANT l'analyse vision.

    artifact_ref est un chemin LOCAL — temporaire, jamais stocké en WS,
    nettoyé après utilisation (voir raya/models/vision.py cleanup)."""

    artifact_id: str = field(default_factory=lambda: new_id("art"))
    path: str = ""
    width: int = 0
    height: int = 0
    source: str = ""      # "screen" | "browser" | "camera:{id}"
    timestamp: str = field(default_factory=utc_now_iso)
    metadata: dict = field(default_factory=dict)


@dataclass
class VisualObservation:
    """Résultat structuré d'une analyse visuelle par un modèle Vision.

    RÈGLES ABSOLUES :
    - confidence = INFERRED TOUJOURS (jamais KNOWN_FACT)
    - source doit commencer par "perception:"
    - artifact_ref = chemin local TEMPORAIRE (jamais en WorldState)
    - to_world_state_value() retourne les faits dérivés uniquement

    PROVENANCE : chaque observation est identifiable via observation_id,
    model_used, et timestamp pour audit/debugging."""

    source: str              # "perception:screen" | "perception:browser" | "perception:camera"
    description: str
    artifact_ref: str = ""   # chemin fichier local temporaire
    semantic_entities: list[str] = field(default_factory=list)
    target: VisualTarget | None = None
    raw_model_output: str = ""    # sortie brute du modèle pour debug
    model_used: str = ""          # "provider:model_id"
    confidence: Confidence = Confidence.INFERRED
    freshness_ttl_s: int = 30
    observation_id: str = field(default_factory=lambda: new_id("vobs"))
    timestamp: str = field(default_factory=utc_now_iso)
    viewport: ViewportInfo | None = None

    def __post_init__(self) -> None:
        if self.confidence != Confidence.INFERRED:
            raise ValueError(
                "VisualObservation.confidence doit être INFERRED — la vision produit "
                "des inférences, jamais des faits vérifiés (RAYA_V2_ARCHITECTURAL_INVARIANTS.md)."
            )
        if not self.source.startswith("perception:"):
            raise ValueError(
                f"VisualObservation.source doit suivre le format 'perception:<mechanism>' "
                f"(reçu: {self.source!r})"
            )

    def to_world_state_value(self) -> dict:
        """Représentation WorldState — JAMAIS artifact_ref, JAMAIS raw_model_output.
        Seuls les faits dérivés : description, entités, observation_id, modèle."""
        return {
            "description": self.description,
            "semantic_entities": self.semantic_entities,
            "observation_id": self.observation_id,
            "model_used": self.model_used,
            "timestamp": self.timestamp,
        }
