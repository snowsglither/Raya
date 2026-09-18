# RAYA V2 — Chantier 20E : Grounding Parser Fix — Rapport d'Implémentation

**Date** : 2026-09-16 10:12  
**Mode** : IMPLEMENTATION + VALIDATION  
**Fichier modifié** : `raya/models/vision.py`  
**Tests ajoutés** : `tests/models/test_vision_model.py` (T1–T8, 8 nouveaux tests)  

---

# 1. Root cause

Le parser `_FOUND_PATTERN` dans `raya/models/vision.py` rejetait des réponses
VALIDES de Gemma4:31b. Deux cas précis observés en production (audit 20D) :

**CAS A — espaces dans bbox** :
```
FOUND: bbox=[0.35, 0.19, 0.51, 0.24] label="titre du produit Xbox Series S" confidence=0.9
```
Le pattern `([0-9.]+),([0-9.]+)` exigeait des virgules sans espace autour.

**CAS B — guillemets internes dans label** :
```
FOUND: bbox=[0.76,0.46,0.93,0.51] label="bouton "Ajouter au panier"" confidence=0.99
```
Le pattern `[^"]+` s'arrêtait au premier guillemet interne, brisant le match entier.

**Conséquence** : `found=False` alors que Gemma avait correctement localisé l'élément.
La perception Vision était correcte. Le parser était le problème.

---

# 2. Exact parser change

**Fichier** : `raya/models/vision.py` — `_FOUND_PATTERN` (ligne 73)

```python
# AVANT
_FOUND_PATTERN = re.compile(
    r"FOUND:\s*bbox=\[([0-9.]+),([0-9.]+),([0-9.]+),([0-9.]+)\]"
    r'\s+label="([^"]+)"\s+confidence=([0-9.]+)',
    re.IGNORECASE,
)

# APRÈS
_FOUND_PATTERN = re.compile(
    r'FOUND:\s*bbox=\[\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\]'
    r'\s+label="(.+?)"\s+confidence=([0-9.]+)',
    re.IGNORECASE,
)
```

**Changement 1** : `([0-9.]+),([0-9.]+)` → `([0-9.]+)\s*,\s*([0-9.]+)` — tolère les espaces autour des virgules.

**Changement 2** : `([^"]+)` → `(.+?)` — non-greedy avec terminateur `"\s+confidence=`.
Le quantificateur non-greedy `.+?` associé au terminateur `"\s+confidence=` retrouve
correctement le label complet même en présence de guillemets internes :
- `label="bouton "Ajouter au panier"" confidence=0.99`
  → `.+?` avance jusqu'à `"` suivi de `\s+confidence=`
  → match = `bouton "Ajouter au panier"` ✓

---

# 3. Before / After examples

| Input | AVANT | APRÈS |
|-------|-------|-------|
| `bbox=[0.1,0.2,0.3,0.4] label="test" confidence=0.9` | PASS | PASS |
| `bbox=[0.1, 0.2, 0.3, 0.4] label="test" confidence=0.9` | **FAIL** | PASS |
| `bbox=[0.76,0.46,0.93,0.51] label="bouton "Ajouter au panier"" confidence=0.99` | **FAIL** | PASS |
| `bbox=[0.35, 0.19, 0.51, 0.24] label="titre produit Xbox Series S" confidence=0.9` | **FAIL** | PASS |
| `bbox=[-0.1,0.2,0.8,0.9] label="x" confidence=0.8` | REJECTED | REJECTED |
| `bbox=[0.1,0.2,0.3] label="x" confidence=0.8` (3 coords) | REJECTED | REJECTED |

---

# 4. Tests

**22/22 PASS** — `tests/models/test_vision_model.py`  
**36/36 PASS** — suites visuelles adjacentes  

| Test | Description | Résultat |
|------|-------------|----------|
| T1 | bbox sans espaces (format nominal) | PASS |
| T2 | bbox avec espaces après virgules (observé sur Amazon) | PASS |
| T3 | label guillemets internes + bbox avec espaces | PASS |
| T4 | raw output Amazon exact audit 20D (bbox sans espaces, label avec guillemets) | PASS |
| T5 | raw output titre produit exact audit 20D (bbox avec espaces) | PASS |
| T6 | bbox dégénérée (x_min > x_max) — rejetée | PASS |
| T7 | nombre de coordonnées incorrect (3 au lieu de 4) — rejeté | PASS |
| T8 | confidence non numérique — rejetée | PASS |

Suites adjacentes (aucune régression) :
- `tests/contracts/test_visual_contracts.py` : 15/15 PASS
- `tests/tools/test_visual_catalog.py` : 15/15 PASS
- `tests/attention/test_visual_attention.py` : 6/6 PASS

---

# 5. Real E2E

## E2E #1 — vision.find_in_browser Wikipedia

**Site** : `https://fr.wikipedia.org/wiki/Python_(langage)`  
**Objectif** : `vision.find_in_browser` → `found=True` → bbox → screen coords

**Verdict** : **PASS ✓**  
**Elapsed** : 14.6s  
**Tool calls** : 2  
**vision.find_in_browser** : SUCCESS  
**Grounding exploitable** : True  

### Trace

```
  [ 1] browser.navigate          success  SUCCESS   {"url": "https://fr.wikipedia.org/wiki/Python_(langage)"}
  [ 2] vision.find_in_browser    success  SUCCESS   {"target": "titre principal de la page 'Python (langage)' (heading H1)"}
         evidence: FOUND: bbox=[0.25,0.216,0.395,0.259] label="titre principal..." confidence=0.99
```

### Réponse finale

> Titre localisé sur la page « Python (langage) » :
>
> - **screen_x** : 616  
> - **screen_y** : 225  
> - **Bounding box normalisée** : x_min = 0.25, y_min = 0.216, x_max = 0.395, y_max = 0.259  
> - Confiance : 0.99
>
> Ces coordonnées sont bien issues d'une observation visuelle réelle, pas estimées.

**Confirmation** : le parser accepte le format réel de Gemma et retourne des coordonnées pixel exploitables.

---

# 6. Amazon result

## E2E #2 — Amazon Xbox add-to-cart

**Site** : `https://www.amazon.com.be`  
**Objectif** : `vision.find_in_browser("bouton Ajouter au panier")` → `found=True` → `click_at_position` → panier confirmé

### Run 1 (ACHIEVED)

**Verdict** : **ACHIEVED ✓**

| Métrique | Valeur |
|----------|--------|
| Total iterations | 11 / 12 |
| DOM failures | 2 |
| vision.find_in_browser | success=1, failure=0 |
| click_at_position | 1 |
| Grounding obtained | True |
| Cart confirmed | True — "1 article dans le panier" |

**Étapes clés** :
```
  [ 8] browser.read_page         success  SUCCESS   (page produit Xbox Series S chargée)
  [ 9] vision.find_in_browser    success  SUCCESS   target="bouton 'Ajouter au panier' (Add to cart)"
         evidence: FOUND: bbox=[0.758,0.465,0.937,0.511] label="bouton 'Ajouter au panier'..." confidence=0.99
  [10] browser.click_at_position success  SUCCESS   {"x": 1620, "y": 462}
  [11] browser.read_page         success  SUCCESS   url=.../cart/add-to-cart/...
```

**Réponse finale** :
> Le panier est passé à **1 article** (`aria_label: "1 article dans le panier"`).
> **Xbox Series S – 512 Go** (B0DGXTKZ42, 613,09 €) ajouté. Aucun achat effectué.

### Run 2 (navigation non-déterministe)

**Verdict** : NON_ATTEINT — modèle bloqué en navigation sur les résultats de recherche (7 steps)  
**Lien avec le parser** : aucun — `vision.find_in_browser` n'a pas été appelé (page produit non atteinte).  
**Cause** : comportement non-déterministe du modèle de raisonnement, hors scope 20E.

---

# 7. Number of tool iterations

**Run 1 (ACHIEVED)** : 11 / 12 max — dans le budget  
**Run 2** : 7 / 12 — bloqué en navigation

**Conclusion RC-C** : avec le parser fixé et une navigation nominale, le parcours Amazon tient dans les 12 iterations (11 observées). `max_tool_iterations=12` est suffisant.

---

# 8. Regression

| Suite | Résultat | Statut |
|-------|----------|--------|
| `test_vision_model.py` (22 tests, dont 8 nouveaux T1–T8) | 22/22 PASS | ✓ NO REGRESSION |
| `test_visual_contracts.py` (15 tests) | 15/15 PASS | ✓ NO REGRESSION |
| `test_visual_catalog.py` (15 tests) | 15/15 PASS | ✓ NO REGRESSION |
| `test_visual_attention.py` (6 tests) | 6/6 PASS | ✓ NO REGRESSION |

Aucune régression introduite.

---

# 9. V1 integrity

Seul `raya/models/vision.py` a été modifié (1 expression régulière `_FOUND_PATTERN`, ligne 73).  
RAYA V1 n'utilise pas ce fichier. **V1 INTACTE.**

---

# 10. Limitations

- **RC-B résiduel** : `browser.click('Ajouter au panier')` peut encore échouer si le bouton
  n'est pas exposé par `browser.read_page` (DOM ne traverse pas les frames). Hors scope 20E.
  Le Run 1 l'a contourné via `vision.find_in_browser` → `click_at_position`.
- **context_budget_tokens=1024** non modifié (conformément à spec 20E §7).
  Non nécessaire : `FOUND:` était déjà présent dans le output Gemma — c'est le parser qui échouait.
- **Navigation non-déterministe** : Amazon Run 2 montre un blocage en navigation résultats.
  Hors scope 20E — ne concerne pas le parser.
- **Pixel coordinates** : si Gemma retourne des valeurs > 1.0 sans viewport,
  `_parse_grounding` retourne None (comportement inchangé et correct).

---

# 11. Final verdict

| Composant | Statut |
|-----------|--------|
| Parser fix `_FOUND_PATTERN` | ✓ IMPLÉMENTÉ |
| T1–T8 parser tests | ✓ 8/8 PASS |
| Suites visuelles adjacentes | ✓ 58/58 NO REGRESSION |
| E2E #1 Wikipedia grounding | ✓ PASS |
| E2E #2 Amazon Run 1 | ✓ ACHIEVED — 11/12 iter, cart confirmé "1 article" |
| E2E #2 Amazon Run 2 | ⚠ NON_ATTEINT — navigation non-déterministe (hors scope) |

**Root cause RC-A résolue.**  
Gemma4:31b écrivait correctement `FOUND: bbox=...` — le parser rejetait la réponse
à cause de guillemets internes dans le label et d'espaces dans les coordonnées bbox.  
Après la correction, `vision.find_in_browser` retourne `found=True` avec des coordonnées
exploitables. `browser.click_at_position` peut les utiliser directement.  
La mission Amazon (Run 1) a été accomplie en **11 itérations sur 12 autorisées**.

---

*Rapport généré par `scripts/validate_20e_grounding_parser.py`*  
*Modification code source : `raya/models/vision.py` uniquement (`_FOUND_PATTERN`)*  
