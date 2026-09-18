# RAYA V2 — Vision Foundation + Real Vision E2E
## Implementation Report

**Date** : 2026-09-14  
**Status** : COMPLETE  
**Tests** : 78 new tests — 0 failures

---

## Résumé exécutif

La Vision Foundation implémente le pipeline perception visuelle complet de RAYA V2 :
capture d'écran → hash diff → event → outil vision → modèle Vision → WorldState.
Tous les contrats architecturaux sont respectés :
- Sensors = capture only, zero LLM call
- Tools = injection pattern (pas d'import direct `raya.models`)
- Coordinates = normalisées [0,1] + transform via ViewportInfo
- Privacy = artifact_ref jamais en WorldState
- Attention = PERCEPTION ≠ INTERRUPTION (screen_changed → IGNORE, observation → BACKGROUND)

---

## Phases complétées

### Phase 1 — Contrats visuels (`raya/contracts/visual.py` + `__init__.py`)

**Nouveaux contrats :**

| Contrat | Description |
|---|---|
| `BoundingBox` | Coordonnées normalisées [0,1], validation stricte (rejet hors-range, dégénéré, inversé), `to_pixel()`, `center_pixel()` |
| `ViewportInfo` | Contexte de transformation : image_width/height, window_left/top, device_pixel_ratio, browser_zoom, scroll_x/y |
| `VisualTarget` | Élément grounded — label, bbox, viewport, confidence. `screen_coordinates()` → (x, y) pixel réels |
| `VisualArtifact` | Référence d'image sauvegardée localement (path local, jamais cloud) |
| `VisualObservation` | Résultat d'analyse Vision. `confidence` = TOUJOURS `INFERRED` (enforced `__post_init__`). `source` doit commencer par `perception:`. `to_world_state_value()` exclut `artifact_ref` et `raw_model_output` |

**Exports ajoutés à `raya/contracts/__init__.py`** : BoundingBox, ViewportInfo, VisualArtifact, VisualObservation, VisualTarget

---

### Phase 2 — Fix transport d'images Ollama (`raya/models/providers/ollama_cloud.py`)

**Bug corrigé** : `ContentPart(type="image_ref")` était silencieusement ignoré par `_messages_to_ollama()`. Les images n'atteignaient jamais le modèle Vision.

**Fix** :
```python
# Nouvelle fonction
def _encode_image_ref(path: str) -> str | None:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")

# Dans _messages_to_ollama()
image_paths = [p.value for p in m.content if p.type == "image_ref"]
if image_paths:
    encoded = [enc for p in image_paths if (enc := _encode_image_ref(p)) is not None]
    if encoded:
        entry["images"] = encoded
```

Format Ollama : `images` = liste de base64 pur (sans préfixe `data:image/...`).  
Dégradation gracieuse : images illisibles ignorées silencieusement.

---

### Phase 3 — ScreenLightSensor (`raya/perception/screen_sensor.py`)

**Algorithme** : dhash (difference hash) 9×8 — compare les pixels adjacents.

```
Écran → PIL screenshot → resize 9×8 grayscale → compare col[n] vs col[n+1] → 64 bits → bytes
Hamming(hash_actuel, hash_précédent) > threshold → Event publié
```

**Paramètres** : `diff_threshold=10` (10/64 bits ≈ 15%), `cooldown_s=2.0`

**Event publié** : `perception.visual.screen_changed`
```json
{
  "domain": "visual", "key": "screen_state", "confidence": "inferred",
  "value": {"change_detected": true, "diff_score": 14, "threshold": 10}
}
```

**Règle absolue** : zéro appel modèle. Vérifiable statiquement (test `test_no_model_call_in_sensor`).

---

### Phase 4 — CameraLightSensor (`raya/perception/camera_sensor.py`)

**Algorithme** : MAD (mean absolute difference) sur frame redimensionnée 320px grayscale.

**Dépendance** : `opencv-python` (optionnel — dégradation gracieuse si absent).

**Event publié** : `perception.visual.camera_observation`
```json
{
  "domain": "visual", "key": "camera_0", "confidence": "inferred",
  "value": {"change_detected": true, "diff_score": 12.4, "artifact_ref": "/path/to/frame.png", "camera_id": 0}
}
```

**Note** : `artifact_ref` est dans `value` (local), jamais promu en WorldState.

---

### Phase 5 — `raya/perception/__init__.py`

Exports ajoutés : `CameraLightSensor`, `ScreenLightSensor`.

---

### Phase 6 — Attention visuelle (`raya/attention/evaluator.py`)

Nouveaux event types gérés dans `_decide_perception_event()` :

| Event | Décision | Raison |
|---|---|---|
| `perception.visual.screen_changed` | IGNORE | Signal capteur léger (hash diff), pas d'analyse LLM |
| `perception.visual.camera_observation` | IGNORE | Signal capteur léger (frame diff) |
| `perception.visual.screen_observation` | BACKGROUND | Observation Vision riche — disponible en contexte |
| `perception.visual.browser_observation` | BACKGROUND | Observation Vision riche — disponible en contexte |

**Règle PERCEPTION ≠ INTERRUPTION** : aucun event visuel ne produit INTERRUPT. Jamais.

---

### Phase 7 — Pipeline Vision (`raya/models/vision.py`)

Fonction principale : `observe_image(model_registry, image_path, prompt, find_target, ...)`

**Mode observation générale** : `_SCENE_PROMPT` — décrit la scène en 2-4 phrases.

**Mode grounding** : `_GROUNDING_PROMPT_TEMPLATE` — demande au modèle :
```
FOUND: bbox=[x_min,y_min,x_max,y_max] label="..." confidence=0.N
```
Parsing robuste via regex — retourne `None` pour :
- `NOT_FOUND`
- Format malformé
- Coordonnées hors [0,1]
- BBox dégénérée (x_min >= x_max)

**Entités sémantiques** : extraction légère (mots capitalisés + liste `_COMMON_UI_ENTITIES`), bornée à 20.

**Contrats de robustesse** :
- Fichier inexistant → `None` (log warning)
- Modèle indisponible → `VisualObservation(description="")` avec `raw_model_output="ERROR:CODE"`
- Jamais un crash, jamais une exception non gérée

---

### Phase 8 — Visual Tools (`raya/tools/catalog/visual.py`)

5 tools enregistrés via `register_visual_tools(registry, observe_fn, capture_screen_fn, capture_browser_fn, prefer_local)`.

**Pattern architectural** : injection de callables (identique à `register_task_control_tools`). Jamais d'import `raya.models` dans ce fichier.

| Tool | Tags | ObservationSpec |
|---|---|---|
| `vision.observe_screen` | `vision`, `pc.read` | `visual/screen_state`, TTL=30s |
| `vision.find_on_screen` | `vision`, `pc.read` | `visual/screen_state`, TTL=30s |
| `vision.observe_browser` | `vision`, `browser.read` | `visual/browser_visual_state`, TTL=30s |
| `vision.find_in_browser` | `vision`, `browser.read` | `visual/browser_visual_state`, TTL=30s |
| `vision.observe_image` | `vision`, `pc.read` | `visual/last_image_observation`, TTL=60s |

**Codes d'erreur explicites** : `SCREEN_CAPTURE_FAILED`, `BROWSER_CAPTURE_FAILED`, `VISION_UNAVAILABLE`, `VISION_ERROR`, `TARGET_NOT_FOUND`, `MISSING_TARGET`, `MISSING_PATH`.

**Privacy** : `_obs_to_evidence()` appelle `to_world_state_value()` → artifact_ref jamais en WorldState. `_obs_to_output()` ne contient jamais le chemin fichier brut.

**DOM-FIRST** : descriptions des tools `observe_browser`/`find_in_browser` documentent explicitement que ce sont des FALLBACKS après `browser.read_page`/`browser.click`.

---

### Phase 9 — Risk table (`raya/safety/risk.py`)

Ajout : `"vision": PermissionLevel.SAFE` — même profil que `pc.read`/`browser.read` (lecture pure de l'état visuel, jamais une mutation).

---

### Phase 10 — Tool catalog export (`raya/tools/catalog/__init__.py`)

Ajout : `from .visual import register_visual_tools` + `"register_visual_tools"` dans `__all__`.

---

### Phase 11 — Config vision (`raya/runtime/config.py`)

Nouveaux champs `RuntimeConfig` :

| Champ | Défaut | Description |
|---|---|---|
| `enable_screen_sensor` | `False` | Active ScreenLightSensor (dhash) |
| `enable_camera_sensor` | `False` | Active CameraLightSensor (cv2) |
| `vision_prefer_local` | `False` | Préfère modèle Vision local (privacy) |
| `vision_screenshot_dir` | `<data_dir>/vision_captures` | Répertoire captures temporaires |

Variables d'environnement : `RAYA_ENABLE_SCREEN_SENSOR`, `RAYA_ENABLE_CAMERA_SENSOR`, `RAYA_VISION_PREFER_LOCAL`, `RAYA_VISION_SCREENSHOT_DIR`.

---

### Phase 12 — Bootstrap wiring (`raya/runtime/bootstrap.py`)

**`_register_visual_tools()`** — câble les 5 tools Vision avec injection :
- `capture_screen_fn` : `pyautogui.screenshot()` → PNG dans `vision_screenshot_dir`
- `capture_browser_fn` : `BrowserController.screenshot()` via BrowserDeviceAgent
- `observe_fn` : wraps `raya.models.vision.observe_image(models, ...)`

Best-effort : exception → `log("warning", ...)`, aucun tool enregistré, jamais un crash.

**`_start_perception()` mis à jour** :
```python
sensors = [ActiveWindowSensor(), PhoneCallActivitySensor(), IncomingCallNotificationSensor()]
if config.enable_screen_sensor:
    sensors.append(ScreenLightSensor())
if config.enable_camera_sensor:
    sensors.append(CameraLightSensor(save_dir=config.vision_screenshot_dir))
```

Imports ajoutés : `ScreenLightSensor`, `register_visual_tools`.

---

### Phase 13 — `.env.example` mis à jour

Section ajoutée :
```
# Vision Perception — capteurs visuels + tools vision.*
RAYA_ENABLE_SCREEN_SENSOR=false
RAYA_ENABLE_CAMERA_SENSOR=false
RAYA_VISION_PREFER_LOCAL=false
RAYA_VISION_SCREENSHOT_DIR=
```

---

## Tests créés (78 tests, 0 failures)

### `tests/contracts/test_visual_contracts.py` (16 tests)
- BoundingBox : validation (hors-range, inversé, dégénéré), to_pixel, center_pixel
- ViewportInfo : champs par défaut
- VisualTarget : screen_coordinates sans/avec offset
- VisualObservation : source enforcement, confidence INFERRED, to_world_state_value (privacy)

### `tests/perception/test_screen_sensor.py` (12 tests)
- `_compute_dhash` : longueur, hash identique, hash différent
- `_hamming` : 0 pour identiques, 8 pour `\xff`/`\x00`
- ScreenLightSensor : baseline None, frames identiques, changement détecté, payload structure, cooldown, ImportError, static lint LLM-free

### `tests/perception/test_camera_sensor.py` (6 tests)
- cv2 absent → None, caméra indisponible → None, first frame baseline → None
- release idempotent, static lint LLM-free, confidence=INFERRED

### `tests/models/test_vision_model.py` (11 tests)
- `_parse_grounding` : format valide, NOT_FOUND, malformé, bbox dégénérée, hors-range, confidence clamp, sans viewport
- `observe_image` : fichier manquant, succès, source enforcement, grounding found/not found, erreur modèle, entités

### `tests/models/test_ollama_image_transport.py` (10 tests)
- `_encode_image_ref` : base64 valide, fichier manquant → None, pas de data URI prefix, ASCII
- `_messages_to_ollama` : texte seul, avec image_ref, fichier manquant ignoré, 3 images, texte + image coexistent, régression texte pur

### `tests/tools/test_visual_catalog.py` (17 tests)
- observe_screen : succès, capture failure, VISION_UNAVAILABLE, description vide
- find_on_screen : target trouvé (coordonnées), target manquant arg, TARGET_NOT_FOUND
- observe_browser : succès, BROWSER_CAPTURE_FAILED
- find_in_browser : trouvé, non trouvé
- observe_image : succès, MISSING_PATH
- Evidence/Privacy : artifact_ref absent, chemin fichier absent de l'output

### `tests/attention/test_visual_attention.py` (6 tests)
- screen_changed → IGNORE
- camera_observation → IGNORE
- screen_observation → BACKGROUND
- browser_observation → BACKGROUND
- Aucun event visuel → INTERRUPT (règle architecturale)
- screen_changed/camera_observation → pas PROCESS_NOW

---

## Invariants architecturaux vérifiés

| Règle | Vérification |
|---|---|
| Sensors sans LLM | Tests statiques AST dans `test_screen_sensor.py` et `test_camera_sensor.py` |
| confidence = INFERRED | `__post_init__` raise sur tout autre valeur |
| source = perception:* | `__post_init__` raise si non conforme |
| artifact_ref absent de WorldState | `to_world_state_value()` + test dédié |
| PERCEPTION ≠ INTERRUPTION | `test_visual_events_never_interrupt()` |
| Injection (pas import direct models) | `register_visual_tools` reçoit `observe_fn: Callable` |
| vision tag = SAFE | `_RISK_BY_TAG["vision"] = PermissionLevel.SAFE` |

---

## Régressions

**Aucune régression** : 473 tests existants continuent de passer.  
Le seul test qui échoue (`test_chantier17_portability.py::test_config_root_is_derived_from_file_location_not_hardcoded`) est un test d'environnement pre-existant lié à une variable `RAYA_DATA_DIR` définie localement sur la machine — non lié à ces changements.

---

## Fichiers modifiés

| Fichier | Nature |
|---|---|
| `raya/contracts/visual.py` | NOUVEAU |
| `raya/contracts/__init__.py` | MODIFIÉ (exports) |
| `raya/models/providers/ollama_cloud.py` | MODIFIÉ (fix image transport) |
| `raya/models/vision.py` | NOUVEAU |
| `raya/perception/screen_sensor.py` | NOUVEAU |
| `raya/perception/camera_sensor.py` | NOUVEAU |
| `raya/perception/__init__.py` | MODIFIÉ (exports) |
| `raya/attention/evaluator.py` | MODIFIÉ (événements visuels) |
| `raya/tools/catalog/visual.py` | NOUVEAU |
| `raya/tools/catalog/__init__.py` | MODIFIÉ (export) |
| `raya/safety/risk.py` | MODIFIÉ (tag vision=SAFE) |
| `raya/runtime/config.py` | MODIFIÉ (champs vision) |
| `raya/runtime/bootstrap.py` | MODIFIÉ (wiring) |
| `.env.example` | MODIFIÉ (documentation) |

---

## Tests créés

| Fichier | Tests |
|---|---|
| `tests/contracts/test_visual_contracts.py` | 16 |
| `tests/perception/test_screen_sensor.py` | 12 |
| `tests/perception/test_camera_sensor.py` | 6 |
| `tests/models/test_vision_model.py` | 11 |
| `tests/models/test_ollama_image_transport.py` | 10 |
| `tests/tools/test_visual_catalog.py` | 17 |
| `tests/attention/test_visual_attention.py` | 6 |
| **TOTAL** | **78** |

---

*Rapport généré automatiquement — RAYA V2 Vision Foundation Implementation*
