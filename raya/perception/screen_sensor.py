"""ScreenLightSensor — capteur de changement d'écran basé sur hash perceptuel.

RÈGLE ABSOLUE : ce capteur ne fait AUCUN appel modèle. Il capte et compare
uniquement. L'analyse visuelle du contenu est déclenchée en aval par les
tools/catalog/visual.py via le Model Layer.

Algorithme : dhash (difference hash) sur miniature 9×8 — compare chaque pixel
à son voisin de droite. Hamming distance entre deux hashs = nombre de bits
différents = mesure de la différence visuelle. Coût < 50ms par sample.

Dépendances : pyautogui (déjà requis par devices/windows/mechanisms/screen.py),
Pillow (dépendance transitive de pyautogui).

Dégradation gracieuse : si pyautogui/PIL absent (plateforme non-Windows),
sample() retourne toujours None sans exception.
"""

from __future__ import annotations

import threading
import time

from raya.contracts import Confidence, Event, PerceptionObservation, to_dict

from .sensors import LightSensor

_HASH_SIZE = 8          # dhash produit hash_size * hash_size bits
_THUMBNAIL_W = _HASH_SIZE + 1
_THUMBNAIL_H = _HASH_SIZE
_DEFAULT_THRESHOLD = 10  # hamming distance — 10/64 bits = ~15% de différence


def _compute_dhash(img) -> bytes:
    """Difference hash (dhash) — compare chaque pixel à son voisin de droite.
    Retourne un objet bytes de longueur hash_size²/8 (8 bytes pour hash_size=8).
    Implémentation pure Pillow — aucune dépendance externe (imagehash, etc.)."""
    from PIL import Image  # type: ignore[import]

    small = img.resize((_THUMBNAIL_W, _THUMBNAIL_H), Image.LANCZOS).convert("L")
    pixels = list(small.getdata())
    bits: list[int] = []
    for row in range(_THUMBNAIL_H):
        for col in range(_HASH_SIZE):
            left = pixels[row * _THUMBNAIL_W + col]
            right = pixels[row * _THUMBNAIL_W + col + 1]
            bits.append(1 if left > right else 0)
    # Packer les bits en octets (LSB first)
    result = bytearray()
    for i in range(0, len(bits), 8):
        byte = 0
        for j, bit in enumerate(bits[i : i + 8]):
            byte |= bit << j
        result.append(byte)
    return bytes(result)


def _hamming(a: bytes, b: bytes) -> int:
    """Hamming distance entre deux séquences de bytes."""
    count = 0
    for x, y in zip(a, b):
        diff = x ^ y
        while diff:
            count += diff & 1
            diff >>= 1
    return count


class ScreenLightSensor(LightSensor):
    """Capteur de changement d'écran — publie un event uniquement lorsque
    le hash perceptuel de l'écran diffère significativement du précédent.

    L'event publié est 'perception.visual.screen_changed' avec :
        domain="visual", key="screen_state", confidence=INFERRED
        value = {change_detected, diff_score, threshold}

    NE stocke PAS de screenshots dans WorldState.
    NE transmet PAS d'images au modèle.
    """

    def __init__(
        self,
        diff_threshold: int = _DEFAULT_THRESHOLD,
        cooldown_s: float = 2.0,
    ) -> None:
        self._threshold = diff_threshold
        self._cooldown_s = cooldown_s
        self._previous_hash: bytes | None = None
        self._last_published: float = 0.0
        self._lock = threading.Lock()

    def sample(self) -> Event | None:
        try:
            import pyautogui  # type: ignore[import]
        except ImportError:
            return None

        try:
            img = pyautogui.screenshot()
        except Exception:
            return None

        try:
            current_hash = _compute_dhash(img)
        except Exception:
            return None

        with self._lock:
            if self._previous_hash is None:
                self._previous_hash = current_hash
                return None

            diff = _hamming(current_hash, self._previous_hash)
            if diff <= self._threshold:
                return None

            now = time.monotonic()
            if now - self._last_published < self._cooldown_s:
                # Changement détecté mais cooldown actif — met à jour le hash
                # pour ne pas s'emballer lors du prochain sample, sans publier.
                self._previous_hash = current_hash
                return None

            self._previous_hash = current_hash
            self._last_published = now

        obs = PerceptionObservation(
            domain="visual",
            key="screen_state",
            value={
                "change_detected": True,
                "diff_score": diff,
                "threshold": self._threshold,
            },
            source="perception:screen_dhash",
            confidence=Confidence.INFERRED,
            freshness_ttl_s=15,
        )
        return Event(
            type="perception.visual.screen_changed",
            source="perception",
            payload=to_dict(obs),
        )
