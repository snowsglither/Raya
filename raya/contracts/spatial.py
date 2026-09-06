"""Spatial Scene Model (RAYA V2 Phase 8 — Creative/Spatial Agent).

EXTRACT/ADAPT de `RAYA/modules/hologram/model.py` (V1, désigné `KEEP` par
`RAYA_V2_MIGRATION_PLAN.md`/`RAYA_V2_REPOSITORY_STRUCTURE.md` §11) : la forme
générale (objets id-stables en arbre, `Transform` position/rotation/scale,
géométrie/matériau optionnels, sérialisation JSON complète) était déjà
correcte et headless (aucune dépendance Three.js). ADAPTÉ, pas copié tel
quel : les champs propres à la maquette CAO/impression 3D de V1
(`dimension_confidence`, `exploded`/`explode_distance_mm`, `annotations`,
`dimensions`, `history_log`, `camera_view` restreint à 7 valeurs fixes) sont
abandonnés — hors scope d'un agent spatial générique (consigne §24 "NO
HARDCODED DEMOS", §6 "modèle propre et indépendant du renderer"). Un champ
générique `relationships` remplace la hiérarchie stricte pour les relations
non parent/enfant (ex: "connected_to" un serveur réseau, consigne §19).

Aucune de ces dataclasses n'importe Three.js ni aucun renderer — c'est la
frontière imposée par la consigne §6/§7."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ._base import new_id, utc_now_iso

_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")


class SpatialError(ValueError):
    """Erreur de validation/opération sur une scène spatiale."""


def slugify_object_id(label: str, existing: set[str] | None = None) -> str:
    """Transforme un label libre en identifiant stable (snake_case) — même
    principe que `slugify_id` de V1, sans dépendre du reste du module V1."""
    base = re.sub(r"[^a-z0-9]+", "_", (label or "object").strip().lower()).strip("_") or "object"
    base = base[:48]
    if not existing or base not in existing:
        return base
    i = 2
    while f"{base}_{i}" in existing:
        i += 1
    return f"{base}_{i}"


def validate_object_id(object_id: str) -> None:
    if not isinstance(object_id, str) or not _ID_RE.match(object_id):
        raise SpatialError(
            f"Identifiant d'objet spatial invalide : {object_id!r} "
            "(lettres/chiffres/_/- uniquement, 1-64 caractères)."
        )


@dataclass
class Vec3:
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


@dataclass
class Transform:
    position: Vec3 = field(default_factory=Vec3)
    rotation: Vec3 = field(default_factory=Vec3)  # degrés
    scale: Vec3 = field(default_factory=lambda: Vec3(1.0, 1.0, 1.0))


@dataclass
class Geometry:
    """Primitive générique — `kind` reste une chaîne libre (jamais un enum
    fermé) : un renderer qui ne reconnaît pas un `kind` donné doit pouvoir
    l'ignorer/le représenter honnêtement plutôt que RAYA n'invente une
    contrainte fermée que la consigne §24 interdit."""

    kind: str = "box"
    params: dict = field(default_factory=dict)


@dataclass
class Material:
    color: str = "#39a8ff"
    opacity: float = 1.0


@dataclass
class SpatialObject:
    id: str
    label: str
    object_type: str = "object"  # sémantique libre : "planet", "server", "cube"...
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    transform: Transform = field(default_factory=Transform)
    geometry: Geometry | None = None
    material: Material | None = None
    # Relations NOMMÉES autres que parent/enfant (consigne §19) — ex:
    # {"connected_to": "server01"}. Jamais interprétées par le Core, une
    # donnée sémantique pour le modèle/renderer, rien de plus.
    relationships: dict[str, str] = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)


@dataclass
class Scene:
    id: str = field(default_factory=lambda: new_id("scene"))
    label: str = "Scene"
    objects: dict[str, SpatialObject] = field(default_factory=dict)
    root_ids: list[str] = field(default_factory=list)
    camera: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)
    updated_at: str = field(default_factory=utc_now_iso)
