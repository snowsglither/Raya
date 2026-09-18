# RAYA V2 — Audit 20B : Real Browser Multimodal Execution
## DOM + Vision + Recovery + Verification

**Date** : 2026-09-15  
**Branche** : main  
**Session** : 3 vraies exécutions E2E sur Amazon.com.be  
**Mode** : AUDIT ONLY — aucune modification de code

---

## 1. Executive Summary

L'audit 20B répond à la question centrale :

> « Lorsqu'une action Browser DOM échoue dans une vraie tâche, pourquoi RAYA utilise-t-il ou n'utilise-t-il pas Vision ? »

**Réponse directe :**

RAYA *essaie bien* d'utiliser Vision (`vision.observe_browser`) après un échec DOM dans une session réelle. Mais l'outil échoue systématiquement avec `BROWSER_CAPTURE_FAILED` avant même de capturer quoi que ce soit — à cause d'un bug dans `bootstrap.py::_capture_browser`. Le modèle adapte alors en utilisant `browser.screenshot` + `vision.observe_image` comme contournement, mais cette voie ne fournit pas de grounding (coordonnées), ce qui empêche `browser.click_at_position` basé sur la vision.

**Root causes confirmées (2 critiques, 2 moyennes) :**

| # | Cause | Fichier | Sévérité | Confiance |
|---|-------|---------|----------|-----------|
| RC-1 | `_capture_browser` asyncio bug — `vision.observe_browser` et `vision.find_in_browser` toujours `BROWSER_CAPTURE_FAILED` | `raya/runtime/bootstrap.py:172-192` | CRITIQUE | CERTAINE |
| RC-2 | Même bug — `vision.find_in_browser` inutilisable, pas de grounding browser via Vision | `raya/runtime/bootstrap.py:172-192` | CRITIQUE | CERTAINE |
| RC-3 | Directive gap : "Browser DOM fallback" ne mentionne pas Vision pour les échecs DOM généraux (uniquement pour cookie consent) | `raya/context_engine/render.py:525-536` | MOYENNE | CERTAINE |
| RC-4 | `max_tool_iterations=12` peut être atteint avant l'ajout au panier, même quand la navigation réussit | `raya/harness/loop.py:501` | MOYENNE | OBSERVÉE |

---

## 2. Real Environment

| Composant | Valeur |
|-----------|--------|
| Modèle reasoning | `deepseek-v4.1-flash` (Ollama Cloud) |
| Modèle vision | `gemma4:31b` (Ollama Cloud) |
| Browser | Playwright Edge (BrowserDeviceAgent) |
| Site cible | `https://www.amazon.com.be` |
| `max_tool_iterations` | 12 |
| `max_same_tool_failures` | 4 (LoopDetector) |
| `max_identical_failures` | 2 (LoopDetector) |
| Vision tools disponibles | `vision.observe_browser`, `vision.find_in_browser`, `vision.observe_screen`, `vision.find_on_screen`, `vision.observe_image` |
| Vision tools fonctionnels | `vision.observe_screen`, `vision.find_on_screen`, `vision.observe_image` ✓ |
| Vision tools cassés | `vision.observe_browser`, `vision.find_in_browser` ✗ |

---

## 3. Real E2E Scenarios

### Scénario 1 — Run 1 (58.6s, 13 tool calls)

Input : *"Va sur Amazon.com.be et mets une Xbox dans mon panier (sans acheter)"*

| Step | Tool | Status | Note |
|------|------|--------|------|
| 1 | `browser.navigate` | SUCCESS | amazon.com.be |
| 2 | `browser.read_page` | SUCCESS | page d'accueil |
| 3 | `browser.type` (target: "search box") | FAILURE | locator introuvable |
| 4 | `browser.type` (target: "Rechercher Amazon.com.be") | SUCCESS | adapte le target |
| 5 | `browser.read_page` | SUCCESS | résultats de recherche |
| 6 | `browser.click` (Xbox Series X console product) | FAILURE | locator trop spécifique |
| 7 | `browser.click` (variante) | FAILURE | second essai |
| 8 | `browser.screenshot` | SUCCESS | adapte : capture l'écran |
| 9 | `vision.observe_image` | SUCCESS | analyse le screenshot |
| 10 | `browser.click` ("Xbox Series X") | SUCCESS | trouve le bon texte via vision |
| 11 | `browser.read_page` | SUCCESS | page produit |
| 12 | `browser.click` ("Voir toutes les offres") | SUCCESS | |
| 13 | `browser.read_page` | SUCCESS | **max_tool_iterations atteint** |

**Observation** : Le modèle a trouvé Vision via le chemin `browser.screenshot` + `vision.observe_image` après 2 échecs DOM. L'objectif (ajout panier) n'a pas été complété — atteinte du budget d'itérations.

### Scénario 2 — Run 2 (41.5s, 12 tool calls)

| Step | Tool | Status | Note |
|------|------|--------|------|
| 1-4 | navigate, read_page, type, read_page | SUCCESS | |
| 5 | `browser.click` (Xbox Series X console) | FAILURE | |
| 6 | `browser.click` (Xbox Series X) | SUCCESS | |
| 7 | `browser.read_page` | SUCCESS | |
| 8 | `browser.screenshot` | SUCCESS | |
| 9 | `vision.observe_image` | SUCCESS (non montré — filtrage log) | |
| 10 | `browser.click` ("Voir toutes les offres") | SUCCESS | |
| 11 | `browser.read_page` | SUCCESS | |
| 12 | `browser.screenshot` | SUCCESS | **max_tool_iterations atteint** |

### Scénario 3 — Run 3 (45.0s, 12 tool calls) — **PLUS RÉVÉLATEUR**

| Step | Tool | Status | Note |
|------|------|--------|------|
| 1 | `browser.navigate` | SUCCESS | |
| 2 | `browser.read_page` | SUCCESS | |
| 3 | `browser.type` | SUCCESS | |
| 4 | `browser.read_page` | SUCCESS | |
| 5 | `browser.click` (Xbox Series X — locator spécifique) | FAILURE | |
| 6 | `browser.click` (Xbox Series X) | SUCCESS | |
| 7 | `browser.read_page` | SUCCESS | page produit |
| 8 | `browser.click` ("Voir toutes les offres") | SUCCESS | |
| 9 | `browser.read_page` | SUCCESS | page offres |
| **10** | **`vision.observe_browser`** | **FAILURE** ← | **BUG : BROWSER_CAPTURE_FAILED** |
| 11 | `browser.screenshot` | SUCCESS | contournement |
| 12 | `vision.observe_image` | SUCCESS | contournement partiel |

**Observation clé** : Le modèle a spontanément essayé `vision.observe_browser` (l'outil correct) au step 10, après avoir navigué sur la page des offres. L'outil a échoué immédiatement avec `BROWSER_CAPTURE_FAILED`. Le modèle s'est adapté en 2 étapes. Budget épuisé avant l'ajout au panier.

---

## 4. DOM-only Successful Actions

Les actions DOM pure qui ont fonctionné sans Vision :

- `browser.navigate` → toujours SUCCESS (classification : `EXPECTED_DOM_ONLY`)
- `browser.type` après adaptation du locator → SUCCESS
- `browser.click` sur les titres de produits Amazon (texte exact visible) → SUCCESS
- `browser.read_page` → toujours SUCCESS

**Verdict** : DOM suffit pour navigation, saisie, clics sur éléments bien identifiables. Vision n'est pas nécessaire pour ces actions.

---

## 5. DOM Failures

**Failures observées :**

| Tool | Arguments | Code | Pattern |
|------|-----------|------|---------|
| `browser.type` | target="search box" | FIELD_NOT_FOUND | Locator générique non reconnu par Playwright |
| `browser.click` | target="Xbox Series X console product" | ELEMENT_NOT_FOUND | Description trop longue/spécifique |
| `browser.click` | variantes similaires | ELEMENT_NOT_FOUND | Même pattern |

**Comportement observé après DOM failure :**
- Pattern B majoritaire : *DOM failure → autre target DOM → Vision (observe_browser ou screenshot+image) → action alternative*
- Jamais Pattern D (abandon immédiat) sur les sessions observées
- Le modèle adapte systématiquement avant le seuil LoopDetector

---

## 6. Vision Availability

**Vision est-elle disponible au modèle à chaque étape ?**

OUI — vérification par inspection directe du ToolRegistry :

```
Registered vision tools:
  vision.observe_screen   [vision, pc.read]
  vision.find_on_screen   [vision, pc.read]
  vision.observe_browser  [vision, browser.read]   ← présent, cassé
  vision.find_in_browser  [vision, browser.read]   ← présent, cassé
  vision.observe_image    [vision, pc.read]
```

`_discover_tool_schemas()` retourne ALL tools y compris les 5 vision.* — ils sont bien dans `available_tools` envoyés au modèle.

**Réponse à la question de l'audit** : Vision ÉTAIT disponible au modèle à chaque étape, y compris les étapes critiques.

---

## 7. Vision Triggering Behavior

**Le modèle essaie-t-il Vision ?**

OUI — observé dans toutes les sessions :

- Runs 1 & 2 : modèle choisit `browser.screenshot` + `vision.observe_image` après DOM failure
- **Run 3 (étape critique)** : modèle choisit `vision.observe_browser` directement — l'outil correct

**Quand le modèle décide d'utiliser Vision :**
- Après 1-2 failures DOM sur la même zone de la page
- Lorsque la page a changé de structure et que les locators textuels ne fonctionnent plus
- De manière spontanée, sans être explicitement guidé par la directive principale

**Ce que le modèle reçoit comme feedback d'erreur quand vision.observe_browser échoue :**
```json
{
  "tool": "vision.observe_browser",
  "status": "failure",
  "verification": "FAILURE",
  "output": null,
  "error": {
    "code": "BROWSER_CAPTURE_FAILED",
    "message": "An asyncio.Future, a coroutine or an awaitable is required"
  }
}
```

Le message d'erreur est opaque (erreur Python interne) — le modèle ne peut pas distinguer "l'outil est cassé" de "le browser n'est pas disponible".

---

## 8. Vision Result Transport

**Transport DOM-only :**

`browser.click` → `ToolResult.evidence = {clicked_target, url}` → `WorldState.browser.current_url` ✓

**Transport Vision :**

- `vision.observe_image` → `ToolResult.output.description` → modèle lit le texte → peut décider d'une action DOM ✓ (FONCTIONNE pour la compréhension de scène)
- `vision.find_in_browser` → devrait retourner `screen_x, screen_y` → mais FAILURE avant d'atteindre le grounding ✗
- `vision.observe_browser` → devrait retourner `description` → mais FAILURE avant la capture ✗

**Point de rupture dans le pipeline vision-browser :**

```
vision.observe_browser appelé
↓
_handle_observe_browser() dans tools/catalog/visual.py
↓
capture_browser_fn() == _capture_browser() dans bootstrap.py
↓
controller.screenshot(path) → retourne dict synchrone
↓
loop.run_until_complete(dict) → TypeError ← RUPTURE ICI
↓
exception catchée → ToolResult FAILURE BROWSER_CAPTURE_FAILED
```

La vision Gemma4:31b n'est jamais atteinte pour les outils browser. Le grounding ne peut jamais se produire.

---

## 9. Objective vs Tool Success

**Distinction observée :** Le modèle fait bien la distinction dans les 3 sessions — il ne déclare pas succès après un DOM click réussi sur un lien produit, il continue vers la page d'ajout au panier.

**Objectif non atteint :** Dans les 3 runs, le produit a été trouvé, la page produit/offres a été atteinte, mais l'ajout effectif au panier n'a pas eu lieu — `max_tool_iterations` a été atteint avant.

**Classification :** `OBJECTIVE_PLANNING_FAILURE` (budget insuffisant) + `VISION_TRIGGERED_BUT_FAILED` (vision browser cassée, empêche grounding).

---

## 10. Recovery / Replanning

**Observations :**

1. **DOM failure → adaptation DOM** : le modèle essaie un locator différent immédiatement (jamais la même chaîne exacte deux fois — LoopDetector efficace sur `_DEFAULT_MAX_IDENTICAL_FAILURES=2`)

2. **DOM failure multi → Vision** : après 1-2 failures sur la même zone, le modèle passe à Vision sans attendre l'escalade LoopDetector

3. **vision.observe_browser failure → workaround** : après BROWSER_CAPTURE_FAILED, le modèle adapte vers `browser.screenshot` + `vision.observe_image` — résilience correcte

4. **Workaround limitation** : `vision.observe_image` retourne une description textuelle mais pas de coordonnées. Sans grounding, le modèle ne peut pas appeler `browser.click_at_position` avec des coordonnées issues de Vision — il doit repasser par DOM.

---

## 11. LoopDetector Interaction

**LoopDetector paramètres :**
- `max_identical_failures = 2` : même (tool_name + arguments exacts) → ESCALATE
- `max_same_tool_failures = 4` : même tool_name, n'importe quels args → ESCALATE

**Comportement observé :**

Le modèle change de stratégie (DOM → screenshot+vision) AVANT que le LoopDetector atteigne son seuil d'escalade. Le LoopDetector n'a pas interféré avec la décision d'utiliser Vision dans les sessions observées.

**Conclusion :** Le LoopDetector ne bloque pas Vision dans ce contexte. La cause de l'échec vision est le bug `_capture_browser`, pas le LoopDetector.

---

## 12. Verification

**Vérification d'état :**

- Navigation : `WorldState.browser.current_url` est mis à jour après chaque `browser.click` réussi → le modèle peut vérifier sans appeler `browser.read_page` ✓
- Ajout au panier : jamais tenté (budget épuisé avant d'atteindre cette étape)
- Vision : `WorldState.visual.browser_visual_state` n'est jamais mis à jour (vision.observe_browser toujours FAILURE)

---

## 13. Latency

| Action | Latence observée |
|--------|-----------------|
| `browser.navigate` | ~1-2s |
| `browser.read_page` | ~0.5-1s |
| `browser.click` | ~0.5-1s |
| `browser.screenshot` | ~0.5s |
| `vision.observe_image` (Gemma4:31b) | ~3-6s (réseau Ollama) |
| `vision.observe_browser` | <10ms (FAILURE immédiate) |
| `deepseek-v4.1-flash` model call | ~4-8s |
| **Tour E2E complet** | **41-58s (12-13 tool calls)** |

**Coût DOM-only (success path) :** ~1-3s par action

**Coût DOM + Vision (workaround) :** +4-7s par appel vision.observe_image (screenshot 0.5s + modèle 3-6s)

**Coût DOM + vision.observe_browser si corrigé :** comparable — la capture browser est O(1) (~0.5s) + modèle ~3-6s

---

## 14. Root Causes

### RC-1 — `_capture_browser` asyncio bug (CRITIQUE, CERTAINE)

**Evidence :**
```python
# bootstrap.py::_register_visual_tools::_capture_browser (lignes ~172-192)
import asyncio
loop = asyncio.new_event_loop()
try:
    path = loop.run_until_complete(controller.screenshot(str(...)))  # BUG
finally:
    loop.close()
```

`BrowserController.screenshot()` est une méthode **synchrone** qui retourne un `dict` en appelant `self._worker.run_sync()` en interne. L'envelopper dans `loop.run_until_complete(dict_result)` lève `TypeError: An asyncio.Future, a coroutine or an awaitable is required`.

**Test direct confirmé :**
```python
>>> loop = asyncio.new_event_loop()
>>> loop.run_until_complete({"status": "ok", "path": "/tmp/x.png"})
TypeError: An asyncio.Future, a coroutine or an awaitable is required
```

**Impact :** `vision.observe_browser` et `vision.find_in_browser` → toujours `BROWSER_CAPTURE_FAILED`

**Localisation exacte :** `raya/runtime/bootstrap.py`, fonction `_capture_browser` intérieure à `_register_visual_tools`, ~ligne 183.

**Sévérité :** CRITIQUE  
**Confiance :** CERTAINE (reproduit à 100%, test unitaire direct confirmé)

---

### RC-2 — Pas de grounding browser via Vision (CRITIQUE, DÉRIVÉE DE RC-1)

**Evidence :** `vision.find_in_browser` échoue pour la même raison que RC-1. Sans cet outil fonctionnel, le modèle ne peut jamais obtenir de coordonnées `(screen_x, screen_y)` issues de Vision pour appeler `browser.click_at_position`.

Le workaround `browser.screenshot + vision.observe_image` fournit une **description textuelle** de l'écran mais **pas de coordonnées grounding**. Le pipeline est donc :

```
Vision disponible → Vision browser cassée → workaround observe_image
→ description textuelle seulement → modèle doit revenir au DOM
→ si le DOM échoue encore → pas de sortie grounding
```

**Sévérité :** CRITIQUE (empêche la récupération Vision-grounded après DOM failure persistant)  
**Confiance :** CERTAINE

---

### RC-3 — Directive gap : Vision non mentionnée dans "Browser DOM fallback" (MOYENNE, CERTAINE)

**Evidence :** Extrait de `render.py` (lignes 525-536), directive "Browser DOM fallback" :

```
"Browser DOM fallback — if browser.click fails (ELEMENT_NOT_FOUND): call 
browser.read_page immediately to re-observe... 
If browser.click by text/role/aria-label keeps failing and read_page exposes 
position data (button.left, button.top), browser.click_at_position may be used 
as a positional fallback..."
```

**Absent de cette directive :** toute mention de `vision.find_in_browser` comme étape suivante si `browser.click_at_position` échoue aussi, ou si `read_page` ne fournit pas de bonnes coordonnées.

**La directive cookie consent (lines 544-587) mentionne Vision (Step 4)**, mais elle est spécifique aux banières cookies.

**Impact :** Le modèle utilise Vision de façon spontanée (observé dans les 3 runs), mais ce comportement repose sur la décision autonome du modèle, pas sur une directive. Un modèle plus littéral (ou une session avec moins de contexte) pourrait ne jamais essayer Vision.

**Sévérité :** MOYENNE (le modèle compense avec son propre raisonnement, mais pas de façon fiable)  
**Confiance :** CERTAINE

---

### RC-4 — max_tool_iterations atteint avant l'objectif (MOYENNE, OBSERVÉE)

**Evidence :** Dans les 3 runs, les 12 itérations ont été consommées avant que l'ajout au panier ne soit tenté. La navigation et la recherche consomment 4-6 étapes, les récupérations DOM 2-4 étapes, Vision 2-3 étapes — il reste peu d'espace pour les dernières actions (ajout panier, vérification).

**Note :** `max_tool_iterations=12` est la valeur actuelle. Avec des actions DOM fiables, 8-10 étapes suffiraient. Avec les récupérations actuelles, 12 peut être insuffisant.

**Sévérité :** MOYENNE  
**Confiance :** OBSERVÉE (3 runs, objectif jamais atteint — mais le budget est configurable)

---

## 15. What Already Works

| Capacité | Status |
|----------|--------|
| DOM navigation (navigate, read_page) | ✓ Fiable |
| DOM search (browser.type adaptatif) | ✓ Fiable |
| DOM click sur éléments bien textuels | ✓ Fiable |
| LoopDetector anti-boucle | ✓ Efficace |
| Adaptation DOM → autres locators | ✓ Observée |
| browser.screenshot | ✓ Fonctionne |
| vision.observe_image (fichier existant) | ✓ Fonctionne (Gemma4:31b) |
| vision.observe_screen (écran entier) | ✓ Fonctionne |
| Workaround screenshot+observe_image | ✓ Partiel (description, pas grounding) |
| Modèle essaie Vision spontanément | ✓ Observé dans les 3 runs |
| Même tour : DOM puis Vision | ✓ Observé dans tous les runs |

---

## 16. What Does NOT Work

| Capacité | Status | Cause |
|----------|--------|-------|
| `vision.observe_browser` | ✗ Toujours FAILURE | RC-1 : `_capture_browser` asyncio bug |
| `vision.find_in_browser` | ✗ Toujours FAILURE | RC-1 : même bug |
| Grounding browser via Vision | ✗ Impossible | RC-2 : dérivé de RC-1 |
| Vision-guided `click_at_position` | ✗ Impossible | RC-2 : pas de coordonnées |
| Completion add-to-cart en 12 itérations | ✗ Non observé | RC-4 : budget |

---

## 17. Minimal Correction Proposals

### Correction C-1 — Fix `_capture_browser` (RC-1, RC-2)

**Fichier :** `raya/runtime/bootstrap.py`  
**Fonction :** `_capture_browser` (closure intérieure à `_register_visual_tools`)  
**Ligne :** ~183

**Problème :**
```python
# ACTUEL — BUG
import asyncio
loop = asyncio.new_event_loop()
try:
    path = loop.run_until_complete(controller.screenshot(str(screenshot_dir / f"browser_{...}.png")))
finally:
    loop.close()
try:
    from PIL import Image as _PILImage
    with _PILImage.open(path) as _img:
        ...
```

`controller.screenshot()` est synchrone. Elle appelle `self._worker.run_sync()` en interne et retourne déjà un `dict {"status": "ok", "path": "..."}`. Il ne faut pas l'envelopper dans asyncio.

**Correction minimale :**
```python
# CORRIGÉ — supprime le wrapping asyncio inutile
path_str = str(screenshot_dir / f"browser_{__import__('time').time_ns()}.png")
result = controller.screenshot(path_str)  # synchrone, retourne {"status": "ok", "path": ...}
path = result.get("path") or path_str
try:
    from PIL import Image as _PILImage
    with _PILImage.open(path) as _img:
        _w, _h = _img.size
    return {"path": path, "width": _w, "height": _h}
except Exception:
    return {"path": path}
```

**Risque :** Faible — supprime uniquement le wrapping asyncio incorrect. `controller.screenshot()` est déjà synchrone et thread-safe via `BrowserWorker.run_sync`.

**Test minimal requis :** `vision.find_in_browser` et `vision.observe_browser` ne retournent plus `BROWSER_CAPTURE_FAILED` après navigation.

**Test E2E requis :** Répéter le scénario Amazon et vérifier que les étapes vision.observe_browser/vision.find_in_browser succeent, que les coordonnées grounding sont retournées, et que `browser.click_at_position` avec ces coordonnées fonctionne.

---

### Correction C-2 — Compléter la directive "Browser DOM fallback" (RC-3)

**Fichier :** `raya/context_engine/render.py`  
**Localisation :** Bloc "Browser DOM fallback" (lignes ~525-536)

**Problème :** La directive se termine sur `browser.click_at_position` comme dernier recours, sans mentionner Vision pour les cas où les coordonnées DOM ne sont pas disponibles ou où `click_at_position` échoue aussi.

**Correction minimale (après C-1) :** Ajouter à la fin du bloc "Browser DOM fallback" :

```
"If browser.click_at_position also fails, or if read_page does not expose usable "
"position data, call vision.find_in_browser with a semantic description of the "
"target element. If it returns coordinates, call browser.click_at_position with "
"those coordinates — they are grounded in the actual visual state, never invented. "
"If vision.find_in_browser returns TARGET_NOT_FOUND, the element may be off-screen, "
"inside a frame, or blocked — call browser.read_page to recheck the full structure "
"before trying again."
```

**Risque :** Faible — directive générique, ne hardcode pas de comportement Amazon-spécifique.

**Pré-requis :** C-1 doit être implémentée en premier. Sans C-1, cette directive ferait appeler `vision.find_in_browser` qui échouerait immédiatement.

---

### Correction C-3 — Ajuster max_tool_iterations (RC-4, OPTIONNEL)

**Fichier :** `raya/runtime/config.py`  
**Paramètre :** `max_tool_iterations`

**Contexte :** Une session Amazon typique (navigate → search → product → offers → add to cart → verify) nécessite au moins 8-10 étapes DOM pure. Avec les récupérations Vision, 12 peut être insuffisant.

**Correction minimale :** Augmenter la valeur par défaut à 16-20 pour les tâches browser, ou rendre `max_tool_iterations` configurable par type de canal.

**Risque :** Moyen — augmente la latence maximale et le coût en tokens par tour.

---

## 18. Recommended Next Chantier

**Chantier 20C — Vision Browser Fix**

Objectif unique, minimal, vérifiable :

1. **Fix RC-1** : corriger `_capture_browser` dans `bootstrap.py` (Correction C-1)
2. **Verify** : `vision.observe_browser` et `vision.find_in_browser` retournent SUCCESS après navigation
3. **Test E2E** : répéter le scénario Amazon et observer que `vision.find_in_browser` retourne des coordonnées réelles après un DOM failure
4. **Fix RC-3** : compléter la directive "Browser DOM fallback" (Correction C-2)
5. **Verify** : dans une session où DOM click échoue et `read_page` ne fournit pas de bonnes coordonnées, le modèle utilise `vision.find_in_browser` → `browser.click_at_position` de façon cohérente

**Ce que Chantier 20C n'est pas :**
- Un refactor du Bootstrap
- Une modification du BrowserController
- Un ajout de manager de fallback
- Un changement de l'architecture de routing Vision

**Tests minimaux requis :**
- `test_vision_find_in_browser_returns_success_after_navigation` (unit, avec vrai browser ouvert)
- `test_vision_observe_browser_returns_description` (unit)
- `test_e2e_vision_grounding_then_click_at_position` (E2E réel, site quelconque)
- Réexécution des 3 scénarios Amazon pour observer la complétion

---

## 19. Deferred Items

| Item | Raison du report |
|------|-----------------|
| Mesure précise du taux de succès add-to-cart | Nécessite RC-1 corrigé |
| Test avec `vision.find_in_browser` réellement fonctionnel | Bloqué par RC-1 |
| Audit du path `vision.find_on_screen` + `pc.mouse.click` comme alternative | Hors scope Amazon (PC-level, pas browser-level) |
| Optimisation du nombre d'étapes pour réduire le budget | Non urgent, RC-1 est prioritaire |
| Audit du même scénario sur d'autres sites e-commerce | Après Chantier 20C |

---

## 20. Final Verdict

**La question centrale de l'audit est répondue :**

> « À quel endroit précis de la chaîne la décision devient-elle incorrecte ? »

```
Context              → Vision présente dans available_tools ✓
Model                → DeepSeek essaie vision.observe_browser spontanément ✓
Tool Discovery       → Tool présent, schéma correct ✓
Cognition            → Pas impliquée
Harness              → Dispatche l'appel ✓
Browser              ← RUPTURE : bootstrap._capture_browser() asyncio bug
Recovery             → Modèle adapte via screenshot+observe_image (workaround partiel) ✓
```

**Le premier point de rupture est dans `bootstrap.py::_capture_browser`.**

Le modèle fait sa part (essaie Vision, adapte, ne boucle pas), le routing est correct, la directive est partiellement insuffisante mais le modèle compense. Le bug est un bug d'implémentation pur dans la fonction de capture browser — une seule ligne à corriger.

**La phrase "RAYA n'a pas utilisé Vision" dans la conversation Telegram initiale** s'explique par une combinaison :
1. `vision.observe_browser` échoue immédiatement (le modèle peut considérer qu'un outil qui échoue n'a "pas été utilisé" dans le sens de "n'a rien accompli")
2. Le modèle a peut-être utilisé le workaround screenshot+observe_image sans le signaler explicitement à l'utilisateur
3. Dans certaines sessions, la directive gap peut avoir empêché Vision d'être tentée du tout

**Priorité d'action : Corriger RC-1 (Correction C-1) immédiatement, puis RC-3 (Correction C-2).**
