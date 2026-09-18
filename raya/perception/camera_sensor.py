"""CameraLightSensor — capteur webcam basé sur frame diff.

RÈGLE ABSOLUE : ce capteur ne fait AUCUN appel modèle. Il capte, compare,
et sauvegarde un artifact_ref local si changement détecté. L'analyse
visuelle est déclenchée en aval via tools/catalog/visual.py.

Dépendances : opencv-python (cv2) — OPTIONNEL. Si absent, sample() retourne
toujours None sans exception (dégradation gracieuse).

L'event publié est 'perception.visual.camera_observation' avec :
    domain="visual", key="camera_{camera_id}", confidence=INFERRED
    value = {change_detected, diff_score, artifact_ref, camera_id}

artifact_ref est vide si save_dir est None ou si la sauvegarde échoue.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

from raya.contracts import Confidence, Event, PerceptionObservation, new_id, to_dict

from .sensors import LightSensor

_DEFAULT_DIFF_THRESHOLD = 8.0   # MAD (mean absolute difference) en niveaux de gris
_DEFAULT_COOLDOWN_S = 5.0        # min secondes entre deux events publiés
_RESIZE_WIDTH = 320              # redimensionnement pour accélérer le diff


class CameraLightSensor(LightSensor):
    """Capteur webcam — publie un event uniquement lorsque la différence
    moyenne entre deux frames consécutives dépasse le seuil.

    Paramètres :
        camera_id     : index cv2.VideoCapture (0 = webcam principale)
        diff_threshold: seuil MAD (mean absolute difference en niveaux de gris)
        save_dir      : répertoire où sauvegarder les frames significatives
                        (None = pas de sauvegarde, artifact_ref = "")
        cooldown_s    : délai minimum entre deux events publiés (anti-spam)

    Dégradation gracieuse :
        - cv2 absent → sample() retourne toujours None
        - caméra indisponible → sample() retourne None, pas de crash
        - erreur de lecture → retourne None, tente de rouvrir au prochain sample
    """

    def __init__(
        self,
        camera_id: int = 0,
        diff_threshold: float = _DEFAULT_DIFF_THRESHOLD,
        save_dir: Path | None = None,
        cooldown_s: float = _DEFAULT_COOLDOWN_S,
    ) -> None:
        self._camera_id = camera_id
        self._threshold = diff_threshold
        self._save_dir = save_dir
        self._cooldown_s = cooldown_s
        self._cap = None
        self._previous_frame = None
        self._last_published: float = 0.0
        self._lock = threading.Lock()

    def _ensure_open(self) -> bool:
        """Ouvre la caméra si pas encore ouverte. Retourne False si cv2 absent
        ou si la caméra n'est pas disponible."""
        if self._cap is not None:
            return True
        try:
            import cv2  # type: ignore[import]

            cap = cv2.VideoCapture(self._camera_id)
            if cap.isOpened():
                self._cap = cap
                return True
            cap.release()
            return False
        except (ImportError, Exception):
            return False

    def sample(self) -> Event | None:
        if not self._ensure_open():
            return None

        try:
            import cv2  # type: ignore[import]
            import numpy as np  # type: ignore[import]
        except ImportError:
            return None

        ret, frame = self._cap.read()
        if not ret or frame is None:
            # Caméra déconnectée — réinitialiser pour retry au prochain sample
            self._cap.release()
            self._cap = None
            return None

        # Redimensionner + niveaux de gris pour le diff (moins cher)
        small = cv2.resize(frame, (_RESIZE_WIDTH, int(frame.shape[0] * _RESIZE_WIDTH / frame.shape[1])))
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        with self._lock:
            if self._previous_frame is None:
                self._previous_frame = gray
                return None

            diff = float(np.mean(np.abs(gray.astype(float) - self._previous_frame.astype(float))))

            if diff <= self._threshold:
                return None

            now = time.monotonic()
            if now - self._last_published < self._cooldown_s:
                self._previous_frame = gray
                return None

            self._previous_frame = gray
            self._last_published = now

        # Sauvegarder la frame originale (pas la redimensionnée) si save_dir fourni
        artifact_ref = ""
        if self._save_dir is not None:
            try:
                self._save_dir.mkdir(parents=True, exist_ok=True)
                filename = f"camera_{self._camera_id}_{new_id('frm')}.png"
                path = self._save_dir / filename
                cv2.imwrite(str(path), frame)
                artifact_ref = str(path)
            except Exception:
                pass  # indisponible — event publié quand même sans artifact

        obs = PerceptionObservation(
            domain="visual",
            key=f"camera_{self._camera_id}",
            value={
                "change_detected": True,
                "diff_score": round(diff, 2),
                "artifact_ref": artifact_ref,
                "camera_id": self._camera_id,
            },
            source=f"perception:camera_{self._camera_id}",
            confidence=Confidence.INFERRED,
            freshness_ttl_s=30,
        )
        return Event(
            type="perception.visual.camera_observation",
            source="perception",
            payload=to_dict(obs),
        )

    def release(self) -> None:
        """Libère la caméra proprement. Appelé à la fermeture du runtime."""
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

    def __del__(self) -> None:
        self.release()
