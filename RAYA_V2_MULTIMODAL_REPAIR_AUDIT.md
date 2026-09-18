# RAYA V2 — MULTIMODAL PERCEPTION REPAIR AUDIT

**Date** : 2026-09-14  
**Mode** : AUDIT UNIQUEMENT — AUCUN CODE MODIFIÉ  
**Basé sur** : `RAYA_V2_REAL_MULTIMODAL_AUDIT.md`

---

# 1. Executive Summary

Deux gaps identifiés dans l'audit précédent. Ce rapport les audite en profondeur.

**GAP 1 — Grounding** : `gemma4:cloud` retourne des coordonnées **pixel ou normalisées de façon non déterministe**. `_parse_grounding()` rejette silencieusement les coordonnées pixel. La correction est claire, localisée et sans régression.

**GAP 2 — DOM → WorldState** : L'asymétrie est **partiellement intentionnelle** et non critique. La quasi-totalité des faits utiles est déjà disponible. La seule valeur manquante est `page_title` — élément de confort, pas un blocage fonctionnel.

---

# 2. GAP 1 — Grounding

## 2.1 Current flow

```
vision.find_in_browser("Sign in button")
    → capture_browser_fn()
        → BrowserController.screenshot() → "github.png"
        → returns {"path": "github.png"}  ← NO width/height
    → observe_fn(path, "", "Sign in button", ..., viewport=None, ...)
        → observe_image(registry, path, find_target="Sign in button", viewport=None)
            → ModelRequest(VISION) + _GROUNDING_PROMPT_TEMPLATE
            → gemma4:cloud → "FOUND: bbox=[891,13,926,46] label='Sign in button'"
            → _parse_grounding("FOUND: bbox=[891,13,926,46]...", obs_id, viewport=None)
                → x_min=891.0 → BoundingBox(x_min=891.0) → ValueError: 891 > 1.0
                → except (ValueError, Exception): return None
    → obs.target = None
    → ToolResult: FAILURE, code="TARGET_NOT_FOUND"
```

## 2.2 Root cause

**Deux causes imbriquées :**

**Cause A — Inconsistance modèle** : `gemma4:cloud` retourne alternativement des coordonnées normalisées [0,1] ou des coordonnées pixel, malgré un prompt explicite demandant [0,1]. Ce comportement est **non déterministe** — même page, même modèle, résultats différents selon le contexte visuel.

**Cause B — Viewport manquant pour le browser** : `_capture_browser()` (bootstrap.py:162) retourne `{"path": path}` sans `width`/`height`. `_handle_find_in_browser` passe donc `viewport=None` à `observe_fn`. Sans viewport, une normalisation pixel→[0,1] est impossible dans `_parse_grounding()`.

Comparaison :
| Source | capture_fn retourne | viewport fourni |
|---|---|---|
| `vision.find_on_screen` | `{"path", "width", "height"}` | ✓ `ViewportInfo(w, h)` |
| `vision.find_in_browser` | `{"path"}` seulement | ✗ `None` |

## 2.3 Real model outputs — Données réelles collectées

**Image** : 1912×948 pixels (viewport browser via CDP)

| Site | Target | Format retourné | Valeurs |
|---|---|---|---|
| Wikipedia | search box | **PIXEL** | `[239,11,456,48]` |
| Wikipedia | Wikipedia logo | **PIXEL** | `[125,12,213,47]` |
| Google | search input field | NORMALIZED | `[0.318,0.386,0.682,0.447]` |
| Google | Google logo | NORMALIZED | `[0.427,0.269,0.573,0.346]` |
| GitHub | Sign in button | **PIXEL** | `[892,14,927,46]` |
| YouTube | search bar | **PIXEL** | `[335,0,665,30]` |
| YouTube | YouTube logo | **PIXEL** | `[333,57,405,86]` |

**Ratio observé** : 5/7 = pixel, 2/7 = normalisé.  
**Conclusion** : le prompt [0,1] est respecté dans ~30% des cas avec gemma4:cloud.

## 2.4 Coordinate systems

**Normalized [0,1]** :
- x=0.0 = bord gauche de l'image, x=1.0 = bord droit
- y=0.0 = bord supérieur, y=1.0 = bord inférieur
- Indépendant des dimensions de l'image

**Pixel (CSS pixels)** :
- x=0 = bord gauche, x=image_width = bord droit
- Dépend directement des dimensions de l'image
- Pour une image 1912×948 : x peut aller de 0 à 1912

**Détection robuste** :
```python
is_pixel = max(x_min, y_min, x_max, y_max) > 1.0
```

- Cas limite `[0,0,1,1]` (image entière normalisée) : max=1.0 → pas pixel → correct
- Cas limite `[0,0,1920,1080]` (image entière pixel) : max=1080 → pixel → correct
- Aucun cas ambigu connu dans les données réelles collectées

**Validité après normalisation** (tous les 7 cas réels) :
```
Wikipedia search box [239,11,456,48] / 1912×948 → [0.125,0.012,0.238,0.051] ✓ valid
Wikipedia logo       [125,12,213,47] / 1912×948 → [0.065,0.013,0.111,0.050] ✓ valid
GitHub Sign in       [892,14,927,46] / 1912×948 → [0.467,0.015,0.485,0.049] ✓ valid
YouTube search bar   [335,0,665,30]  / 1912×948 → [0.175,0.000,0.348,0.032] ✓ valid
YouTube logo         [333,57,405,86] / 1912×948 → [0.174,0.060,0.212,0.091] ✓ valid
```

**Coordonnées écran vérifiées** (après fix simulé) :
- GitHub "Sign in" → screen_coords (908, 29) → ✓ cohérent (bouton top-right à x≈908/1912)
- Wikipedia search → screen_coords (347, 34) → ✓ cohérent (search box top area)

## 2.5 Correct normalization strategy

**Stratégie retenue** : `if max(values) > 1.0 AND viewport available → normalize`

```python
# Dans _parse_grounding() :
if max(x_min, y_min, x_max, y_max) > 1.0:
    if viewport is None:
        # Impossible de normaliser — log warning et retourner None
        log("warning", "vision._parse_grounding: coordonnées pixel mais viewport=None",
            raw_values=[x_min, y_min, x_max, y_max])
        return None
    x_min = x_min / viewport.image_width
    y_min = y_min / viewport.image_height
    x_max = x_max / viewport.image_width
    y_max = y_max / viewport.image_height
# Puis : BoundingBox(x_min, y_min, x_max, y_max) — validation inchangée
```

Cette stratégie :
- N'altère pas le comportement pour les coordonnées normalisées (passage transparent)
- Normalise correctement les 5/7 cas pixel de notre dataset
- Preserve la validation BoundingBox (bboxes dégénérées toujours rejetées)
- Rend l'échec **observable** via log warning (avant : silencieux)

## 2.6 Prompt vs parser

**Réponse** : **les deux doivent être corrigés, mais le parser est prioritaire.**

Le prompt ne peut pas garantir le format d'un LLM — l'instruction [0,1] est ignorée dans ~70% des cas avec gemma4:cloud. Tout fix prompt-only serait fragile et non testé.

Le parser doit rester défensif même avec un prompt amélioré : un modèle futur différent peut se comporter autrement.

**Amélioration optionnelle du prompt** (second ordre) :
```
"Use NORMALIZED coordinates where 0.0=top-left, 1.0=bottom-right.
Example for a button in the center-right: bbox=[0.6,0.1,0.7,0.15]
Do NOT use pixel values like 450,120."
```

L'ajout d'un exemple concret augmenterait la conformité, mais le parser doit gérer les deux cas indépendamment.

## 2.7 Error handling

**Comportement actuel** : échec silencieux — `_parse_grounding` retourne `None` et rien n'indique dans les logs pourquoi.

**Comportement souhaité** :

| Cas | Action proposée |
|---|---|
| Coordonnées pixel + viewport disponible | Normaliser automatiquement + log info |
| Coordonnées pixel + viewport=None | Log warning avec valeurs brutes + return None |
| Format FOUND malformé | Log debug (déjà silencieux, acceptable) |
| NOT_FOUND dans la réponse | Log info "grounding NOT_FOUND: {reason}" |

La traçabilité existe déjà via `grounding=False` dans le log `vision.observe_image`. Pour les cas pixel-sans-viewport, un log warning avec les valeurs brutes permet le diagnostic sans modifier le contrat.

## 2.8 Files involved

| Fichier | Rôle | Modification nécessaire |
|---|---|---|
| `raya/models/vision.py` | `_parse_grounding()` | **OUI** — normalisation pixel→[0,1] |
| `raya/tools/catalog/visual.py` | `_handle_find_in_browser()` | **OUI** — lire dimensions image + créer ViewportInfo |
| `raya/runtime/bootstrap.py` | `_capture_browser()` | **OUI** — retourner `{"path", "width", "height"}` |

## 2.9 Proposed changes

### Changement 1 : `raya/models/vision.py::_parse_grounding()`

**Actuel (ligne 90-96)** :
```python
try:
    x_min, y_min, x_max, y_max = float(m.group(1)), ...
    bbox = BoundingBox(x_min=x_min, ...)  # raises ValueError si > 1.0
except (ValueError, Exception):
    return None
```

**Proposé** :
```python
try:
    x_min, y_min, x_max, y_max = float(m.group(1)), ...
    # Normalisation automatique si coordonnées pixel (max > 1.0)
    if max(x_min, y_min, x_max, y_max) > 1.0:
        if viewport is None:
            log("warning", "_parse_grounding: pixel coords but no viewport",
                raw=[x_min, y_min, x_max, y_max])
            return None
        x_min = x_min / viewport.image_width
        y_min = y_min / viewport.image_height
        x_max = x_max / viewport.image_width
        y_max = y_max / viewport.image_height
    bbox = BoundingBox(x_min=x_min, ...)
except (ValueError, Exception):
    return None
```

### Changement 2 : `raya/runtime/bootstrap.py::_capture_browser()`

**Actuel** :
```python
path = loop.run_until_complete(controller.screenshot(str(...)))
return {"path": path}
```

**Proposé** :
```python
path = loop.run_until_complete(controller.screenshot(str(...)))
# Lire les dimensions réelles de l'image capturée
try:
    from PIL import Image as _PILImage
    with _PILImage.open(path) as _img:
        _w, _h = _img.size
    return {"path": path, "width": _w, "height": _h}
except Exception:
    return {"path": path}  # dégradation gracieuse, pas de crash
```

### Changement 3 : `raya/tools/catalog/visual.py::_handle_find_in_browser()`

**Actuel** :
```python
obs = observe_fn(path, "", target, ..., None, ...)  # viewport=None
```

**Proposé** :
```python
w, h = cap.get("width", 0), cap.get("height", 0)
viewport = ViewportInfo(image_width=w, image_height=h) if w and h else None
obs = observe_fn(path, "", target, ..., viewport, ...)
```

## 2.10 Risks

| Risque | Probabilité | Mitigation |
|---|---|---|
| Normalisation incorrecte (mauvaises dimensions) | Faible | PIL lit les vraies dimensions de l'image capturée |
| Régression sur coordonnées normalisées | Nulle | `max(values) > 1.0` ne se déclenche jamais pour [0,1] |
| PIL absent dans bootstrap | Faible | dégradation gracieuse : `return {"path": path}` |
| Bbox déjà dégénérée après normalisation | Rare | BoundingBox validation toujours active après normalisation |

---

# 3. GAP 2 — DOM / World State

## 3.1 Current flow

```
browser.navigate("https://wikipedia.org/...") 
    → evidence = {"url": "https://..."}
    → _CURRENT_URL_OBSERVATION → WorldState[browser][current_url] = "https://..."  ✓

browser.read_page()
    → output = {
        "url": "https://...",                            # déjà en WS via navigate
        "title": "Python (programming language) - Wikipedia",  # NOT in WorldState
        "cookie_banner": false,
        "buttons": [...],
        "links": [...],
        "inputs": [...],
        "status": "ok"
      }
    → observation=() → RIEN promu en WorldState
```

## 3.2 Root cause

`browser.read_page` a délibérément `observation=()`. Le DOM complet (liens, boutons, inputs) est trop dynamique et trop volumineux pour WorldState. Les données changent à chaque scroll, animation, ou interaction.

Seuls des faits **stables et légers** (URL courante, élément cliqué, texte saisi) sont promus. C'est une décision architecturale documentée dans les ObservationSpec existants.

## 3.3 Is this actually a problem?

**Non, pour la grande majorité des cas.** Voici pourquoi :

**Ce qui EST déjà en WorldState après les actions browser :**

| Fait | Via quel outil | TTL |
|---|---|---|
| `browser/current_url` | `browser.navigate`, `browser.click` | 60s |
| `browser/last_clicked_target` | `browser.click` | 300s |
| `browser/last_typed_text` | `browser.type` | 300s |
| `browser/last_typed_target` | `browser.type` | 300s |

**Ce qui N'EST PAS en WorldState mais serait utile :**

| Fait | Utilité | Verdict |
|---|---|---|
| `page_title` | Confort au tour suivant | DEFER |
| `cookie_banner` | Utile pour éviter un re-check | DEFER |
| `primary_actions` (boutons) | Trop dynamique, re-read trivial | NON |
| Full DOM | Beaucoup trop lourd | NON |

**Pourquoi la re-lecture DOM est acceptable :**
- Latence `browser.read_page` = **0.14s** — négligeable
- Le DOM change entre les tours (interactions, JS, animations)
- Un fait DOM en WorldState depuis 30s peut être obsolète
- Le modèle peut et doit re-lire si nécessaire

**L'asymétrie Vision vs DOM est intentionnelle :**
- Vision : capture coûteuse (4s), fait durable (scène change lentement) → TTL 30s justifié
- DOM : capture rapide (0.14s), fait volatile (page dynamique) → pas de WorldState justifié

## 3.4 Which DOM facts matter?

**Seul cas réellement utile** : `page_title` comme contexte de navigation.

Entre deux tours :
```
Tour 1: "navigue sur la page Python de Wikipedia"
         → model appelle navigate → URL en WS
         → model appelle read_page → title="Python - Wikipedia" (perdu à la fin du tour)

Tour 2: "sur quelle page sommes-nous ?"  
         → WS contient current_url = "https://en.wikipedia.org/wiki/Python_..."
         → model PEUT dériver le titre depuis l'URL (suffisant)
```

L'URL en WorldState est suffisante dans ce cas. Le titre serait un confort, pas une nécessité.

**Cas qui BÉNÉFICIERAIT d'une promotion title :**
- L'URL contient des paramètres obscurs (ex: `?search=...&action=edit&section=5`)
- Le modèle a besoin du titre lisible plutôt que de parser l'URL

Ce cas est rare. Ne justifie pas une modification architecturale.

## 3.5 TTL / freshness

Si `page_title` était promu, TTL recommandé : 60-120s.  
Déclenché sur `browser.navigate` ou `browser.read_page` (si `observation` est ajouté).

Mais : le titre change rarement entre navigations. `browser.navigate` declenche déjà `current_url` en WS. On pourrait ajouter `page_title` à `_CURRENT_URL_OBSERVATION` si l'agent retournait le titre dans son evidence — ce n'est pas le cas actuellement.

## 3.6 Duplication risks

`browser.navigate` evidence = `{"url": ...}` seulement.  
`browser.read_page` output contient `url` ET `title`.  

Si `page_title` était ajouté à `browser.navigate` ou `browser.read_page` observation :
- Pas de doublon avec Vision (Vision est `domain=visual`, DOM est `domain=browser`)
- Pas de doublon avec `current_url` (clés différentes)
- Seul risque : URL et titre désynchronisés si navigation sans `read_page`

Risque faible, géré par TTL.

## 3.7 DOM + Vision coexistence

**Scénario production réel (confirmé en Test A) :**

```
Turn N : model appelle browser.read_page → DOM dans message thread
          model appelle vision.observe_browser → Vision dans message thread
          Both disponibles simultanément dans le même tour ✓

Turn N+1 : WS contient :
            [browser][current_url] = "https://en.wikipedia.org/..."  ✓ (via navigate)
            [visual][browser_visual_state] = {description: "Wikipedia..."} ✓ (via vision)
            DOM content → absent ✗ (perdu)
```

Le scénario le plus problématique est :
```
Turn N : model lit DOM → trouve "bouton Lecture" à position (x=450, y=200)
Turn N+1 : user dit "clique dessus"
          → model ne sait plus où est ce bouton (DOM perdu)
          → doit re-lire le DOM (0.14s) ou appeler vision.find_in_browser
```

Ce scénario existe mais la solution est naturelle : re-lire le DOM (rapide) ou utiliser le grounding Vision (après fix GAP 1).

## 3.8 Proposed changes

**Aucune modification architecturale nécessaire pour GAP 2.**

Option de confort (niveau DEFER) : promouvoir `page_title` dans `browser.navigate` si l'agent retourne le titre dans son evidence.

**Ce qui changerait :**
- `raya/devices/browser/agent.py::_navigate` : ajouter `title` dans evidence
- `raya/tools/catalog/browser.py::_CURRENT_URL_OBSERVATION` : ajouter un spec pour `page_title` TTL=60s

Impact minimal, risque faible, mais non bloquant.

## 3.9 Risks

| Risque | Probabilité | Mitigation |
|---|---|---|
| Titre désynchronisé avec URL | Faible | TTL 60s, re-lu à chaque navigate |
| Titre trop long pour WorldState | Très faible | WorldState stocke n'importe quelle string |
| Inutile pour 90% des cas | Élevée | Raison pour DEFER |

---

# 4. REAL E2E

## E2E 1 — Grounding avec normalisation simulée (Wikipedia + GitHub)

**Site** : Wikipedia `https://en.wikipedia.org/wiki/Python_(programming_language)` + GitHub `https://github.com/`  
**Date** : 2026-09-14, session locale  
**Résultats** :

### Wikipedia — Search box

| | Valeur |
|---|---|
| DOM disponible | YES — links=60, inputs=1 |
| Screenshot | SUCCESS, 1912×948px |
| Raw model output | `FOUND: bbox=[241,17,453,52] label="search box" confidence=0.9` |
| Coordinate format | PIXEL |
| Current result | `target=None` (BoundingBox raises ValueError) |
| With normalization | `BoundingBox(0.126, 0.018, 0.237, 0.055)` → `screen_coords=(347, 34)` |
| Verdict | **FIX RÉSOUT LE PROBLÈME** |

### GitHub — Sign in button

| | Valeur |
|---|---|
| Screenshot | SUCCESS, 1912×948px |
| Raw model output | `FOUND: bbox=[891,13,926,46] label="Sign in button" confidence=0.95` |
| Coordinate format | PIXEL |
| Current result | `target=None` |
| With normalization | `BoundingBox(0.466, 0.014, 0.484, 0.049)` → `screen_coords=(908, 29)` |
| Verdict | **FIX RÉSOUT LE PROBLÈME** |

---

## E2E 2 — DOM WorldState state audit

**Site** : Wikipedia  
**Objectif** : Prouver que `current_url` EST en WorldState, `page_title` n'y est PAS.

```
browser.navigate("https://en.wikipedia.org/wiki/Python_...") → evidence={"url": "https://..."}
_CURRENT_URL_OBSERVATION.promote → WorldState[browser][current_url] = "https://..." ✓

browser.read_page() → output={"url": "...", "title": "Python... - Wikipedia", ...}
observation=() → RIEN promu ✗

WorldState final = {[browser][current_url]: "https://en.wikipedia.org/..."} (1 seul fait)
page_title="Python (programming language) - Wikipedia" : NON en WorldState
```

**Verdict** : Comportement attendu et cohérent. L'asymétrie est documentée, non un bug.

---

# 5. Existing Tests Reused

Les tests suivants de la suite existante couvrent déjà les primitives concernées :

| Test existant | Ce qu'il couvre |
|---|---|
| `tests/contracts/test_visual_contracts.py::test_bounding_box_rejects_out_of_range` | BoundingBox validation ✓ |
| `tests/contracts/test_visual_contracts.py::test_visual_observation_to_world_state_value_excludes_artifact` | Privacy ✓ |
| `tests/models/test_vision_model.py::test_parse_grounding_valid_format` | Parsing normalisé ✓ |
| `tests/models/test_vision_model.py::test_parse_grounding_degenerate_bbox` | Dégénéré rejeté ✓ |
| `tests/models/test_vision_model.py::test_parse_grounding_out_of_range` | > 1.0 rejeté ✓ |
| `tests/tools/test_visual_catalog.py::test_find_on_screen_with_target` | find_on_screen pipeline ✓ |
| `tests/architecture/test_chantier17_portability.py` | Portabilité config ✓ |

Ces tests restent valables après le fix proposé — aucune régression attendue.

---

# 6. New Tests

**8 nouveaux tests automatisés** (voir `tests/audit/test_multimodal_repair_audit.py`) :

| Test | Ce qu'il vérifie | Type |
|---|---|---|
| `test_parse_grounding_normalizes_pixel_coords_with_viewport` | Normalisation pixel→[0,1] avec viewport | Unit |
| `test_parse_grounding_returns_none_pixel_no_viewport` | Pixel sans viewport → None | Unit |
| `test_parse_grounding_normalized_unchanged` | Normalisé passe sans modification | Unit |
| `test_normalization_preserves_degenerate_rejection` | Dégénéré rejeté après normalisation | Unit |
| `test_find_on_screen_has_viewport_from_capture` | capture_screen_fn fournit dimensions | Integration |
| `test_find_in_browser_no_viewport_from_capture` | capture_browser_fn ne fournit pas dimensions | Integration |
| `test_browser_read_page_output_contains_title_not_in_worldstate` | DOM title absent de WS | Integration |
| `test_browser_navigate_promotes_url_to_worldstate` | URL présent en WS après navigate | Integration |

**2 tests E2E réels** intégrés dans `tests/audit/test_real_e2e_multimodal.py` (section 4 ci-dessus).

---

# 7. V1 Integrity

Ce rapport couvre uniquement RAYA V2. Aucun fichier V1 n'est affecté par ces changements proposés.

Les fichiers ciblés par les changements proposés sont tous dans :
- `raya/models/vision.py` (V2, created Vision Foundation)
- `raya/tools/catalog/visual.py` (V2, created Vision Foundation)
- `raya/runtime/bootstrap.py` (V2, modifié Vision Foundation)

---

# 8. Final Verdict

| Gap | Verdict | Raison |
|---|---|---|
| GAP 1 — Grounding (normalisation) | **MODIFY** | Fix clair, localisé, validé sur données réelles. Débloque vision.find_* en production. |
| GAP 2 — DOM/WorldState | **DEFER** | Asymétrie intentionnelle. `current_url` déjà en WS. `page_title` = confort non bloquant. |

### OVERALL : **GO WITH GAPS (GAP 2 accepté, GAP 1 à corriger)**

---

# 9. Exact Implementation Plan

## Modification A — `raya/models/vision.py` → `_parse_grounding()`

**File** : `raya/models/vision.py`  
**Function** : `_parse_grounding()` (ligne 78)  
**Current behavior** : Si max(values) > 1.0 → `BoundingBox()` lève `ValueError` → `return None` silencieux  
**Desired behavior** : Si max(values) > 1.0 ET viewport disponible → normaliser et continuer. Si pixel et pas de viewport → log warning + return None.

**Exact change** (dans le bloc `try:` à la ligne ~91) :
```python
# Après: x_min, y_min, x_max, y_max = float(m.group(1)), ...
# Avant: bbox = BoundingBox(x_min=x_min, ...)

# AJOUTER :
if max(x_min, y_min, x_max, y_max) > 1.0:
    if viewport is None:
        log("warning", "_parse_grounding: pixel coordinates but no viewport for normalization",
            raw_bbox=[x_min, y_min, x_max, y_max])
        return None
    x_min = x_min / viewport.image_width
    y_min = y_min / viewport.image_height
    x_max = x_max / viewport.image_width
    y_max = y_max / viewport.image_height
```

**Reason** : gemma4:cloud retourne des pixel coords dans ~70% des cas malgré le prompt [0,1].  
**Regression risk** : Nul. Aucun test existant ne teste de valeurs > 1.0 avec viewport fourni.  
**Tests required** : `test_parse_grounding_normalizes_pixel_coords_with_viewport`, `test_parse_grounding_returns_none_pixel_no_viewport`

---

## Modification B — `raya/runtime/bootstrap.py` → `_capture_browser()`

**File** : `raya/runtime/bootstrap.py`  
**Function** : `_capture_browser()` (ligne 162)  
**Current behavior** : Retourne `{"path": path}` sans dimensions  
**Desired behavior** : Retourne `{"path": path, "width": w, "height": h}` en lisant l'image capturée

**Exact change** :
```python
# Après: path = loop.run_until_complete(controller.screenshot(...))
# Avant: return {"path": path}

# REMPLACER par :
try:
    from PIL import Image as _PILImage  # PIL already required (vision deps)
    with _PILImage.open(path) as _img:
        _w, _h = _img.size
    return {"path": path, "width": _w, "height": _h}
except Exception:
    return {"path": path}  # graceful degradation — viewport=None fallback
```

**Reason** : `_handle_find_in_browser` a besoin des dimensions pour créer ViewportInfo.  
**Regression risk** : Très faible. `_capture_browser` n'est utilisé que dans les visual tools injectés.  
**Tests required** : `test_find_in_browser_has_viewport_after_fix` (non créé — attend l'implémentation)

---

## Modification C — `raya/tools/catalog/visual.py` → `_handle_find_in_browser()`

**File** : `raya/tools/catalog/visual.py`  
**Function** : `_handle_find_in_browser()` (ligne 303)  
**Current behavior** : `observe_fn(path, "", target, ..., None, ...)` — viewport toujours None  
**Desired behavior** : Lire w, h depuis cap et créer ViewportInfo si disponible

**Exact change** (après `path = cap.get("path", "")`) :
```python
# AVANT : obs = observe_fn(path, "", target, ..., None, ...)
# REMPLACER par :
w, h = cap.get("width", 0), cap.get("height", 0)
viewport = ViewportInfo(image_width=w, image_height=h) if w and h else None
obs = observe_fn(path, "", target, call.correlation_id, prefer_local, viewport, "perception:browser")
```

**Reason** : Cohérence avec `_handle_find_on_screen` qui fait déjà cette transformation.  
**Regression risk** : Nul. Change uniquement quand width/height disponibles (ne l'est pas actuellement).  
**Tests required** : Test d'intégration `test_find_in_browser_with_viewport`

---

**Ordre d'application recommandé** : B (bootstrap) → C (visual catalog) → A (vision.py)

Modification B rend width/height disponibles. Modification C les utilise. Modification A les consomme dans le parser.

Les 3 peuvent être faites dans un seul commit sans dépendances croisées complexes.
