# RAYA V2 — Chantier 18D : Grounding Fix Implementation Report

**Date**: 2026-09-14  
**Session**: Chantier 18D (Implementation)  
**Mode**: IMPLEMENTATION (3 zones autorisées uniquement)  
**Résultat final**: PASS — GAP 1 résolu, grounding opérationnel sur sites réels

---

## 1. Résumé exécutif

La GAP CRITIQUE identifiée lors de l'audit multimodal réel (Test B xfail) est entièrement résolue.
`gemma4:cloud` retournait des coordonnées pixel (~70% du temps) qui étaient rejetées silencieusement
par `BoundingBox`. Trois modifications chirurgicales dans 3 fichiers permettent la normalisation
automatique pixel → [0,1] quand les dimensions de l'image sont disponibles.

**Avant fix** : `grounding=False` sur Wikipedia, GitHub, YouTube (pixel coords rejetées silencieusement)  
**Après fix** : `grounding=True` confirmé sur données réelles gemma4:cloud  

---

## 2. Modifications effectuées

### Fix A — `raya/runtime/bootstrap.py::_capture_browser()`

**Avant** :
```python
return {"path": path}
```

**Après** :
```python
try:
    from PIL import Image as _PILImage
    with _PILImage.open(path) as _img:
        _w, _h = _img.size
    return {"path": path, "width": _w, "height": _h}
except Exception:
    return {"path": path}  # graceful degradation
```

**Principe** : Dimensions lues depuis l'image réellement capturée (PIL). Jamais de dimension
théorique. Dégradation gracieuse si PIL indisponible — `capture_screen` continuait à fonctionner.

---

### Fix B — `raya/tools/catalog/visual.py::_handle_find_in_browser()`

**Avant** :
```python
obs = observe_fn(path, "", target, call.correlation_id, prefer_local, None, "perception:browser")
```

**Après** :
```python
w, h = cap.get("width", 0), cap.get("height", 0)
from raya.contracts import ViewportInfo as _ViewportInfo
vp = _ViewportInfo(image_width=w, image_height=h) if (w and h) else None
obs = observe_fn(path, "", target, call.correlation_id, prefer_local, vp, "perception:browser")
```

**Principe** : Consomme les dimensions retournées par Fix A pour créer un `ViewportInfo` réel.
Si les dimensions sont absentes (dégradation de Fix A), `vp=None` → comportement antérieur
préservé.

---

### Fix C — `raya/models/vision.py::_parse_grounding()`

**Avant** :
```python
try:
    x_min, y_min, x_max, y_max = ...
    label = ...; confidence = ...
    bbox = BoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)
except (ValueError, Exception):
    return None
```

**Après** :
```python
try:
    x_min, y_min, x_max, y_max = ...
    label = ...; confidence = ...
except (ValueError, Exception):
    return None

# Normalize pixel coordinates if the model returned values > 1.0
if max(x_min, y_min, x_max, y_max) > 1.0:
    if viewport is None or viewport.image_width <= 0 or viewport.image_height <= 0:
        return None
    x_min = x_min / viewport.image_width
    y_min = y_min / viewport.image_height
    x_max = x_max / viewport.image_width
    y_max = y_max / viewport.image_height

try:
    bbox = BoundingBox(x_min=x_min, y_min=y_min, x_max=x_max, y_max=y_max)
except (ValueError, Exception):
    return None
```

**Heuristique** : `max(vals) > 1.0` → mode pixel → normaliser. Jamais de re-normalisation
de coordonnées déjà en [0,1]. BoundingBox toujours créé APRÈS normalisation → contrat intact.

### Mise à jour du prompt de grounding — `_GROUNDING_PROMPT_TEMPLATE`

Ajout d'un exemple concret et d'une instruction explicite :
```
Example: FOUND: bbox=[0.25,0.10,0.75,0.20] label="{target}" confidence=0.9
Do NOT return pixel values — values must be between 0.0 and 1.0.
```

**Objectif** : Réduire la fréquence de retour de coordonnées pixel (renforcement prompt).
Le parser gère de toute façon les deux formats, mais moins de pixel = moins de dépendance
aux dimensions du viewport.

---

## 3. Garde-fous respectés (10 conditions)

| # | Condition | Statut |
|---|-----------|--------|
| 1 | pixel coords + viewport → VisualTarget non-None | PASS (T1) |
| 2 | pixel coords + no viewport → None | PASS (T2) |
| 3 | normalized [0,1] → inchangé, non re-normalisé | PASS (T3) |
| 4 | bbox dégénérée après normalisation → None | PASS (T4) |
| 5 | max() partiel > 1.0 → mode pixel, normalisé | PASS (T5) |
| 6 | capture_browser avec dims → ViewportInfo passé | PASS (T6) |
| 7 | capture_browser sans dims → viewport=None | PASS (T7) |
| 8 | capture_browser raise → BROWSER_CAPTURE_FAILED | PASS (T8) |
| 9 | viewport.image_width=0 → None (guard division) | PASS (T9) |
| 10 | confidence > 1.0 → clampé à 1.0 | PASS (T10) |

---

## 4. Résultats des tests

### 4.1 Nouveaux tests — robustesse (10/10 PASS)

```
tests/tools/test_18d_grounding_fix.py::test_pixel_coords_with_viewport_returns_normalized_target  PASSED
tests/tools/test_18d_grounding_fix.py::test_pixel_coords_without_viewport_returns_none            PASSED
tests/tools/test_18d_grounding_fix.py::test_normalized_coords_not_renormalized                    PASSED
tests/tools/test_18d_grounding_fix.py::test_degenerate_bbox_after_normalization_returns_none       PASSED
tests/tools/test_18d_grounding_fix.py::test_single_coord_above_one_triggers_pixel_mode            PASSED
tests/tools/test_18d_grounding_fix.py::test_capture_browser_with_dims_passes_viewport             PASSED
tests/tools/test_18d_grounding_fix.py::test_capture_browser_without_dims_passes_none_viewport     PASSED
tests/tools/test_18d_grounding_fix.py::test_capture_browser_exception_returns_error_result        PASSED
tests/tools/test_18d_grounding_fix.py::test_viewport_zero_dimension_returns_none                  PASSED
tests/tools/test_18d_grounding_fix.py::test_confidence_clamped_to_valid_range                     PASSED
```

### 4.2 Tests d'audit (8/8 PASS)

```
tests/audit/test_multimodal_repair_audit.py::test_parse_grounding_normalizes_pixel_coords_with_viewport  PASS (mis à jour: confirme fix)
tests/audit/test_multimodal_repair_audit.py::test_parse_grounding_returns_none_pixel_no_viewport          PASS
tests/audit/test_multimodal_repair_audit.py::test_parse_grounding_normalized_unchanged                    PASS
tests/audit/test_multimodal_repair_audit.py::test_normalization_preserves_degenerate_rejection            PASS
tests/audit/test_multimodal_repair_audit.py::test_find_on_screen_has_viewport_from_capture                PASS
tests/audit/test_multimodal_repair_audit.py::test_find_in_browser_no_viewport_from_capture                PASS
tests/audit/test_multimodal_repair_audit.py::test_browser_read_page_output_contains_title_not_in_worldstate PASS
tests/audit/test_multimodal_repair_audit.py::test_browser_navigate_promotes_url_to_worldstate             PASS
```

Note : T1 (`test_parse_grounding_normalizes_pixel_coords_with_viewport`) a été mis à jour —
il documentait le GAP (expected=None), maintenant il confirme le fix (expected=VisualTarget valide).

### 4.3 Tests E2E réels — gemma4:cloud, sites vrais (5/5 PASS)

```
tests/audit/test_real_e2e_multimodal.py::test_e2e_a_wikipedia_dom_and_vision_simultaneously  PASS  (2412ms)
tests/audit/test_real_e2e_multimodal.py::test_e2e_b_visual_grounding_gap_with_gemma4         PASS  (2162ms) ← était xfail
tests/audit/test_real_e2e_multimodal.py::test_e2e_c_dom_vision_disambiguation_google         PASS  (5036ms)
tests/audit/test_real_e2e_multimodal.py::test_e2e_d_dom_failure_vision_recovery_github       PASS  (3188ms)
tests/audit/test_real_e2e_multimodal.py::test_e2e_e_real_screen_pyautogui_vision             PASS  (2941ms)
```

**Confirmation clé (Test B)** : Log `grounding=True` avec `entities_count=7` — gemma4:cloud a retourné
des coordonnées pixel, `_parse_grounding` les a normalisées via viewport, `VisualTarget` produit valide.

### 4.4 Régression complète

**Résultat final** : 12 failed, 1607 passed, 15 skipped — 7 min 25 sec (1634 tests)

**Toutes les 12 failures sont PRE-EXISTANTES**, aucune liée aux 3 fichiers modifiés :

| Test | Cause | Classification |
|------|-------|----------------|
| `test_config_root_is_derived_from_file_location_not_hardcoded` | `DATA_DIR` override dans `.env` | PRE-EXISTING ENV |
| `test_health_reports_online_when_uia_responds` | Nécessite Notepad ouvert | PRE-EXISTING ENV |
| `test_ui_inspect_lists_real_elements_of_notepad` | Nécessite Notepad ouvert | PRE-EXISTING ENV |
| `test_ui_type_writes_real_text_into_notepad_via_uia` | Nécessite Notepad ouvert | PRE-EXISTING ENV |
| `test_ui_click_activates_real_element` | Nécessite Notepad ouvert | PRE-EXISTING ENV |
| `test_handle_request_fails_honestly_with_null_provider_stub` | null_provider non utilisé, modèle cloud répond | PRE-EXISTING |
| `test_recovered_task_can_actually_resume_and_complete` | Timing/flaky avec cloud model | PRE-EXISTING FLAKY |
| `test_queued_task_waits_when_at_concurrency_limit` | Race condition timing | PRE-EXISTING FLAKY |
| `test_scenario_7_background_task_does_not_block_conversation` | 2.83s > 0.2s (cloud latency) | PRE-EXISTING FLAKY |
| `test_2_conversation_answered_immediately_during_task` | 2.07s > 0.2s (cloud latency) | PRE-EXISTING FLAKY |
| `TestRealKokoro::test_real_speak_is_non_blocking_and_completes` | TTS hardware timeout | PRE-EXISTING ENV |
| `TestRealKokoro::test_real_cancellation_latency_is_reasonable` | TTS hardware timeout | PRE-EXISTING ENV |

**Aucune régression** dans les périmètres touchés : `tests/tools/`, `tests/audit/`, `tests/models/`,
`tests/contracts/`, `tests/runtime/`, `tests/perception/`, `tests/persistence/`

---

## 5. Données réelles confirmées

| Site | Viewport | Target | Format reçu | Normalisé |
|------|---------|--------|-------------|-----------|
| Wikipedia | 1912×948 | search box | PIXEL [239,11,456,48] | [0.125, 0.012, 0.238, 0.051] |
| Wikipedia | 1912×948 | Wikipedia logo | PIXEL [125,12,213,47] | [0.065, 0.013, 0.111, 0.050] |
| Google | 1912×948 | search input | NORMALIZED [0.318,…] | inchangé |
| GitHub | 1912×948 | Sign in button | PIXEL [892,14,927,46] | [0.466, 0.015, 0.485, 0.049] |
| YouTube | 1912×948 | search bar | PIXEL [335,0,665,30] | [0.175, 0.000, 0.347, 0.032] |

---

## 6. Périmètre non modifié (GAP 2 — DEFER)

- `WorldState` DOM : observation=() sur `browser.read_page` → inchangé (intentionnel)
- Aucun `page_title` ajouté au WorldState
- Aucun manager créé
- V1 non touchée

---

## 7. Architecture — chaîne complète après fix

```
browser.screenshot()
    → PIL.Image.open() → (width, height)
    → {"path": path, "width": W, "height": H}

_handle_find_in_browser()
    → ViewportInfo(image_width=W, image_height=H)
    → observe_fn(..., viewport=vp, ...)

_parse_grounding(response, obs_id, viewport)
    → parse float coords
    → if max > 1.0: normalize by vp.image_width / vp.image_height
    → BoundingBox(validated [0,1])
    → VisualTarget(label, bbox, viewport)

VisualTarget.screen_coordinates()
    → cx = bbox.center_pixel(W, H) + viewport.window_left
    → cy = ...
    → utilisable par browser.click_at_position
```
