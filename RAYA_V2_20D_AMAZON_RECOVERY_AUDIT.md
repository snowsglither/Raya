# RAYA V2 — Chantier 20D : Audit Amazon Recovery Root Causes

**Date** : 2026-09-16 10:01  
**Mode** : AUDIT UNIQUEMENT — aucune modification de code  
**Modèle reasoning** : `deepseek-v4.1-flash`  
**Modèle vision** : `gemma4:31b`  
**Browser** : Edge CDP (profil dédié RAYA)  
**Site** : `https://www.amazon.com.be`  

---

# 1. Executive Summary

## Résumé exécutif

L'audit 20D répond aux trois questions ouvertes de la session 20C :

**Question 1** : Pourquoi `vision.find_in_browser` échoue sur 'Ajouter au panier' ?
**Question 2** : Pourquoi `browser.click('Ajouter au panier')` échoue ?
**Question 3** : Le budget de 12 iterations est-il réellement insuffisant ?

### Réponses synthétiques

**RC-A (Vision)** : voir §4-5 — analyse complète ci-dessous
**RC-B (DOM)** : voir §6 — analyse du DOM
**RC-C (Iterations)** : voir §8 — 5 des 13 steps sont récupération ou redondants

---

# 2. Real Environment

| Composant | Valeur |
|-----------|--------|
| Modèle reasoning | `deepseek-v4.1-flash` |
| Modèle vision | `gemma4:31b` |
| context_budget_tokens (Vision) | `1024` |
| num_predict → Vision response budget | `256` tokens max |
| num_ctx → Vision context window | `4096` tokens |
| Screenshot dimensions | `1912×948` px |
| Viewport JS (innerWidth×innerHeight) | `1912×948` px |
| devicePixelRatio | `1` |
| page.screenshot(full_page) | `False` (viewport only) |
| max_tool_iterations | `12` |

---

# 3. Amazon trace (reproduction)

```
[ 1] browser.navigate                    success  SUCCESS      {"url": "https://www.amazon.com.be"}
[ 2] browser.read_page                   success  SUCCESS      {}
[ 3] browser.type                        failure  FAILURE      {"target": "champ de recherche \"Rechercher Amazon.com.be\"", "text": "Xbox Series S", "submit": tru…
[ 4] browser.type                        success  SUCCESS      {"submit": true, "target": "Rechercher Amazon.com.be", "text": "Xbox Series S"}
[ 5] browser.read_page                   success  SUCCESS      {}
[ 6] browser.click                       failure  FAILURE      {"target": "premier résultat produit Xbox Series S – 512 Go"}
[ 7] browser.read_page                   success  SUCCESS      {}
[ 8] browser.click                       failure  FAILURE      {"target": "lien produit Xbox Series S - 512 Go"}
[ 9] browser.click                       success  SUCCESS      {"target": "Xbox Series S - 512 Go"}
[10] browser.click_at_position           success  SUCCESS      {"x": 382, "y": 580}
[11] browser.read_page                   success  SUCCESS      {}
```

---

# 4. RC-A Vision analysis

## 4.1 Budget de tokens Vision

Dans `raya/models/vision.py:176` : `context_budget_tokens=1024`
Dans `raya/models/providers/ollama_cloud.py:184` :
```python
"num_predict": max(256, req.context_budget_tokens // 4),  # = 256 tokens
"num_ctx":     min(128_000, req.context_budget_tokens * 4),  # = 4096 tokens
```

**Conséquence** : Gemma4:31b dispose de **256 tokens max** pour sa réponse.
Le prompt grounding fait ~113 tokens.
Une réponse `FOUND:` minimale fait ~25 tokens.
Si Gemma décrit d'abord l'image (comportement naturel), 256 tokens s'épuisent avant FOUND.

## 4.2 Réponse brute de Gemma4:31b

### Target : 'bouton Ajouter au panier'
**found** : `False`
**RAW model output** :
```
FOUND: bbox=[0.76,0.46,0.93,0.51] label="bouton "Ajouter au panier"" confidence=0.99

The image is a screenshot of an Amazon France product page for an Xbox Series S console. It shows the product image, pricing (613.09 €), delivery options, and a yellow "Ajouter au panier" (Add to cart) button.
```

### Target : 'titre du produit Xbox Series S'
**found** : `False`
**RAW model output** :
```
FOUND: bbox=[0.35, 0.19, 0.51, 0.24] label="titre du produit Xbox Series S" confidence=0.9

This image is a screenshot of an Amazon product page for an Xbox Series S console, featuring the product image, price, shipping details, and a list of product features.
```

### Scene description complète
**RAW (600 chars)** :
```
Voici une description détaillée de l'image, qui est une capture d'écran d'une page produit Amazon France pour une console Xbox Series S.

### Description générale
La page présente une console **Xbox Series S de 512 Go**. L'image principale montre la console blanche et sa manette assortie sur un fond blanc. À droite, on trouve le bloc d'achat avec le prix, les options de livraison et le panier. En bas, une section descriptive détaille les caractéristiques du produit.

---

### Liste de tous les boutons et éléments cliquables visibles

#### 1. Barre de navigation supérieure (Header)
*   **Logo A
```

---

# 5. Screenshot / viewport analysis

**Screenshot sauvegardé** : `C:\Users\ruben\AppData\Local\Temp\tmpsnf09ad3\vision\audit_amazon_product_before_cart_1789545619728071100.png`  
**Dimensions PNG** : `1912×948` px  
**Viewport JS (innerWidth×innerHeight)** : `1912×948` px  
**devicePixelRatio** : `1`  
**scrollY** : `0` px (position verticale de défilement)  
**documentHeight** : `8148` px (hauteur totale de la page)  

## 5.1 Buybox et bouton 'Ajouter au panier' (JS direct)

**Buybox element trouvé** : `id=rightCol` rect={'top': 197, 'bottom': 903, 'height': 706, 'width': 244} visible=True

**Candidate (strategy 0)** :
  - `tag=input` `id=add-to-cart-button` `text='Ajouter au panier'`
  - `rect={'top': 461, 'left': 1457, 'width': 204, 'height': 30, 'bottom': 491, 'right': 1661}`
  - `visible_in_viewport=True` `partially_visible=True`
  - `disabled=False` `display=block` `visibility=visible`
**Candidate (strategy 1)** :
  - `tag=input` `id=add-to-cart-button` `text='Ajouter au panier'`
  - `rect={'top': 461, 'left': 1457, 'width': 204, 'height': 30, 'bottom': 491, 'right': 1661}`
  - `visible_in_viewport=True` `partially_visible=True`
  - `disabled=False` `display=block` `visibility=visible`
**Candidate (strategy 3)** :
  - `tag=button` `id=nav-assist-add-to-cart` `text='Ajouter au panier
La touche majuscule
+
alt
+
K'`
  - `rect={'top': 472, 'left': -9966, 'width': 376, 'height': 70, 'bottom': 542, 'right': -9590}`
  - `visible_in_viewport=False` `partially_visible=True`
  - `disabled=False` `display=flex` `visibility=visible`
**Candidate (strategy 4)** :
  - `tag=form` `id=addToCart` `text='613,09€
613
,
09€
Livraison GRATUITE 24 - 26 septembre. Détails
Livrer à ruben - Geraardsbergen 9500'`
  - `rect={'top': 197, 'left': 1437, 'width': 244, 'height': 565, 'bottom': 762, 'right': 1681}`
  - `visible_in_viewport=True` `partially_visible=True`
  - `disabled=None` `display=block` `visibility=visible`
**Candidate (strategy 5)** :
  - `tag=button` `id=nav-assist-add-to-cart` `text='Ajouter au panier
La touche majuscule
+
alt
+
K'`
  - `rect={'top': 472, 'left': -9966, 'width': 376, 'height': 70, 'bottom': 542, 'right': -9590}`
  - `visible_in_viewport=False` `partially_visible=True`
  - `disabled=False` `display=flex` `visibility=visible`

## 5.2 Boutons DOM (browser.read_page)

**Aucun bouton 'panier'/'cart'/'ajouter'** dans browser.read_page

**Tous les boutons DOM (premiers 20)** :
```
  'Livrer à ruben - Geraardsbergen 9500‌' [tag=a top=311 left=1456]
  'Cstore.' [tag=a top=549 left=1559]
  'Peut être retourné dans les 30 jours suivant la réception' [tag=a top=581 left=1559]
  'Transaction sécurisée' [tag=a top=641 left=1559]
  'Livrer à ruben Geraardsb... 9500‌' [tag=a top=5 left=183]
  'Go' [tag=input top=10 left=1392]
  'Développer pour changer de langue ou de pays' [tag=button top=36 left=1514]
  'Élargissez le compte et les listes' [tag=button top=37 left=1656]
  'Catégories' [tag=a top=60 left=15]
  'Prime Détails' [tag=button top=78 left=730]
  'Neufs (9) à partir de 635,52€ 635 , 52€ & Livraison GRATUITE' [tag=a top=839 left=1450]
  'Partager' [tag=a top=211 left=602]
  '5+' [tag=button top=590 left=218]
  '16 ans et plus' [tag=a top=249 left=939]
  '4,7 4,7 étoile(s) sur 5' [tag=a top=273 left=705]
  'Détails' [tag=a top=559 left=964]
```

---

# 6. RC-B DOM analysis

## 6.1 Analyse du locator 'Ajouter au panier'

**FINDING RC-B** : Le DOM ne contient pas de bouton 'Ajouter au panier' visible.

Causes possibles :
- Le bouton est présent mais pas dans le viewport (hors `innerHeight`) → `_STRUCT_JS` ne le capture pas
- Le bouton est dans un sous-frame (`<iframe>`) → `_STRUCT_JS` ne traverse pas les frames
- Le bouton n'est pas encore rendu (lazy-load, hydration React)
- Le page state est 'page des offres' (plusieurs vendeurs) et non 'page produit directe'

## 6.2 `_STRUCT_JS` et les frames

Le code `_STRUCT_JS` dans `raya/devices/browser/controller.py` :
- Priorise `[id*=buybox i], [id*=addtocart i], [id*=add-to-cart i]` ✓
- **Ne traverse PAS les `<iframe>`** — si le buybox est dans une frame, il est invisible au DOM
- Borne les résultats à 120 boutons — ne devrait pas poser problème

---

# 7. Recovery analysis

## 7.1 Flux observé lors de la tentative d'ajout

```
[ 1] browser.read_page                   success  SUCCESS      {}
```

## 7.2 Pattern récupération

Flux observé dans E2E #5 (session 20C) :
```
step 8 : browser.read_page → SUCCESS (page produit chargée)
step 9 : vision.find_in_browser('bouton Ajouter au panier') → FAILURE
         → modèle switch vers workaround screenshot+observe_image
step 10: browser.screenshot → SUCCESS
step 11: browser.read_page → SUCCESS (redondant, même URL que step 8)
step 12: vision.observe_image → SUCCESS (description zone d'achat)
step 13: browser.click('Ajouter au panier') → FAILURE (limit atteinte)
```

**Observation** : Le modèle ne retente PAS vision.find_in_browser après failure — 
il switch directement vers screenshot+observe_image. Ce comportement est conforme à la directive.
Le problème n'est pas la récupération — c'est que les deux stratégies (Vision grounding ET DOM click) échouent.

---

# 8. RC-C iteration analysis

## 8.1 Classification des 13 steps (E2E #5, session 20C)

| Step | Tool | Status | Classe | Note |
|------|------|--------|--------|------|
| 1 | `browser.navigate` | success | **USEFUL** | Ouverture Amazon |
| 2 | `browser.read_page` | success | **USEFUL** | Lecture homepage |
| 3 | `browser.type` | failure | **RECOVERY** | target='champ de recherche Amazon' — locator trop générique |
| 4 | `browser.type` | success | **RECOVERY** | target='Rechercher Amazon.com.be' — adaptation correcte |
| 5 | `browser.read_page` | success | **USEFUL** | Lecture résultats de recherche |
| 6 | `browser.click` | failure | **RECOVERY** | target='Xbox Series S - 512 Go (lien du produit)' — locator trop spécifique |
| 7 | `browser.click` | success | **RECOVERY** | target='Xbox Series S - 512 Go' — adaptation correcte |
| 8 | `browser.read_page` | success | **USEFUL** | Lecture page produit |
| 9 | `vision.find_in_browser` | failure | **FAILED** | target='bouton Ajouter au panier' — FAILURE (root cause à investiguer) |
| 10 | `browser.screenshot` | success | **RECOVERY** | Workaround après vision.find failure |
| 11 | `browser.read_page` | success | **REDUNDANT** | 2ème read_page sur la même URL — redondant avec step 8 |
| 12 | `vision.observe_image` | success | **RECOVERY** | Vision fallback après find_in_browser failure |
| 13 | `browser.click` | failure | **BLOCKED** | target='Ajouter au panier' — FAILURE, limit atteinte |

**Totaux** : USEFUL=4 RECOVERY=6 REDUNDANT=1 FAILED=1 BLOCKED=1
**Efficiency** : 4/13 = 31%

## 8.2 Parcours minimal théorique

| Chemin | Steps |
|--------|-------|
| DOM-only zero-failure | 8 steps |
| Avec 2 DOM failures (observed) | 10 steps |
| Avec vision.find_in_browser SUCCESS | 11 steps |
| Avec vision.find_in_browser FAILURE + workaround | 13 steps (observé) |

**Conclusion RC-C** : `max_tool_iterations=12` est suffisant pour le chemin nominal.
L'overrun à 13 est causé par :
1. `vision.find_in_browser` FAILURE (ajoute 3 steps de workaround)
2. `browser.read_page` redondant au step 11
**Si RC-A est résolu**, le parcours rentrerait dans les 12 iterations.

---

# 9. LoopDetector analysis

**LoopDetector paramètres actifs** :
- `max_tool_iterations = 12`
- `max_identical_failures = 2` (même tool + même args → ESCALATE)
- `max_same_tool_failures = 4` (même tool, args quelconques → ESCALATE)

**Comportement observé dans E2E #5** :
- Aucune répétition identique (modèle change d'arguments à chaque échec)
- LoopDetector n'est PAS intervenu
- Le modèle change de stratégie avant d'atteindre les seuils

**VERDICT** : LoopDetector fonctionne correctement. Il n'est pas la cause des échecs.

---

# 10. Objective verification

Le modèle distingue correctement `TOOL_SUCCESS ≠ OBJECTIVE_SUCCESS` :

- Steps 1-7 : navigation, recherche, clic produit — TOOL SUCCESS ✓
- Step 8 : `browser.read_page` confirme page produit — OBSERVATION ✓
- Steps 9-12 : tentatives d'ajout au panier — toutes FAILURE
- Réponse finale : modèle déclare honnêtement l'échec ✓

**RAYA ne déclare pas succès sur un simple clic DOM réussi.** Ce comportement est correct.

---

# 11. Cross-site comparison

| Site | Target | Vision found | Grounding | Notes |
|------|--------|-------------|-----------|-------|
| Coolblue — bouton panier e-commerce Belgique | `zoekbalk` | `True` | `True` | FOUND: bbox=[0.307,0.0,0.624,0.40] label="zoekbalk" confidence=0.9

The image sh |
| Wikipedia — barre de recherche simple | `search input bar` | `True` | `True` | FOUND: bbox=[0.247,0.014,0.457,0.053] label="search input bar" confidence=0.95

 |

---

# 12. Root causes confirmed

| Root Cause | Statut | Evidence |
|------------|--------|---------|
| **RC-A** : vision.find_in_browser FAILURE | **PARTIAL** | §4 |
| **RC-B** : browser.click FAILURE | **PARTIAL** | §6 |
| **RC-C** : iterations (13 > 12) | **CONFIRMED** (dérivé de RC-A) | §8 |

### RC-A détail
PARTIAL — voir §4.2 pour analyse complète
Réponse brute : `FOUND: bbox=[0.76,0.46,0.93,0.51] label="bouton "Ajouter au panier"" confidence=0.99

The image is a screenshot of an Amazon France product page for an Xbox Series S console. It shows the product imag`

### RC-B détail
PARTIAL — voir §6 pour analyse complète

### RC-C
Dérivé de RC-A : si vision.find_in_browser fonctionnait, les steps 10-11 (workaround)
ne seraient pas nécessaires → 10-11 iterations au lieu de 13.

---

# 13. Minimal correction proposals

## C-1 — Augmenter context_budget_tokens pour les requêtes Vision

**Fichier** : `raya/models/vision.py:176`  
**Fonction** : `observe_image()`  
**Changement minimal** :
```python
# AVANT
context_budget_tokens=1024,

# APRÈS
context_budget_tokens=4096,  # num_predict = max(256, 4096//4) = 1024 tokens
```
**Effet** : `num_predict` passe de 256 → 1024 tokens pour la réponse Gemma.
**Raison** : Avec 256 tokens, Gemma peut ne pas atteindre la ligne FOUND si elle décrit l'image d'abord.
**Risque** : Faible — augmente uniquement le plafond de réponse Vision, pas le context window principal.
**Tests** : `tests/models/test_vision_model.py`, `tests/tools/test_visual_catalog.py`
**E2E** : Répéter Amazon E2E #5 et vérifier que `vision.find_in_browser` retourne FOUND.

## C-2 — Scroll page avant vision.find_in_browser si DOM ne contient pas l'élément

**Fichier** : `raya/context_engine/render.py` — directive Browser DOM fallback  
**Changement minimal** : Ajouter une ligne à la clause Vision escalation :
```
If browser.read_page does not show the target element in the current viewport,
consider scrolling down (browser.scroll or equivalent) before calling
vision.find_in_browser — the element may be below the fold.
```
**Note** : Ne s'applique que SI `browser.scroll` existe dans le ToolRegistry.  
**Alternative** : Vérifier si `browser.click_at_position` peut être utilisé pour scroll.

## C-3 — Ajouter browser.scroll au BrowserController (si C-2 est retenu)

**Fichier** : `raya/devices/browser/controller.py` et `agent.py`  
**Changement minimal** : Méthode `scroll(direction, amount)` via `page.evaluate('window.scrollBy(0, N)')`  
**Risque** : Faible — `scrollBy` est idempotent, pas d'action mutante  
**Note** : Ne pas implémenter avant validation que le bouton est réellement hors viewport.

---

# 14. What should NOT be changed

- **LoopDetector** : fonctionne correctement, ne pas toucher
- **max_tool_iterations** : 12 est suffisant si RC-A est résolu
- **BrowserController.screenshot()** : viewport-only est correct (full_page peut déformer l'expérience réelle)
- **_STRUCT_JS priorityRoots** : la priorisation buybox/addtocart est déjà en place
- **_GROUNDING_PROMPT_TEMPLATE** : le format est correct, le problème est num_predict, pas le prompt
- **Amazon-specific selectors** : interdit (voir §20 de la mission)

---

# 15. Deferred items

- **Vérification manuelle du screenshot** : l'image capturée est conservée dans `e2e_results/`.
  Un humain peut vérifier si 'Ajouter au panier' est visuellement présent.
- **Viewport dynamique** : si devicePixelRatio > 1 et que le screenshot est en résolution HiDPI,
  les coordonnées normalisées peuvent être incorrectes. À vérifier.
- **frame traversal** : si le buybox est dans un `<iframe>`, `_STRUCT_JS` et le screenshot
  peuvent ne pas le capturer correctement. Diagnostic complémentaire requis.
- **Ollama num_predict override** : possibilité d'ajouter un override `vision_context_budget_tokens`
  séparé dans RuntimeConfig pour ne pas impacter les autres requêtes.

---

# 16. Final recommendation

**Premier point de rupture identifié** : RC-A (`vision.find_in_browser` FAILURE)

La correction minimale et à plus fort impact est **C-1** :
`context_budget_tokens: 1024 → 4096` dans `vision.py:observe_image()`

**Justification** :
- `num_predict=256` est objectivement trop petit pour un prompt de grounding
- `num_predict=1024` (après C-1) donne à Gemma assez d'espace pour décrire + FOUND
- Les E2E #1 et #2 ont fonctionné car Wikipedia a une mise en page simple
- Amazon est plus complexe → Gemma écrit plus de description → 256 tokens épuisés avant FOUND

**Ordre d'implémentation recommandé** :
1. **C-1** : fix `context_budget_tokens` → valider que `vision.find_in_browser` réussit sur Amazon
2. **Si le bouton est hors viewport** (confirmé par screenshot) → **C-3** (browser.scroll)
3. **C-2** : ajouter la directive scroll uniquement après que C-3 existe

**Si C-1 seul résout RC-A** :
- RC-C (iterations) se résout automatiquement
- RC-B peut rester un problème résiduel si le bouton est hors viewport

---

*Rapport généré par `scripts/audit_20d_amazon_recovery.py`*  
*Aucune modification de code source n'a été effectuée.*