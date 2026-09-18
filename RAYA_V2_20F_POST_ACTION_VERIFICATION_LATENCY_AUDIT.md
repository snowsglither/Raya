# RAYA V2 — Chantier 20F : Post-Action Verification + Latency Audit

**Date** : 2026-09-16 10:32  
**Mode** : AUDIT ONLY — Aucun fichier de production modifié  
**Scenario** : Amazon AirPods Pro 3 add-to-cart  
**Verdict** : **CONFIRMED ROOT CAUSE**  

---

## 1. Executive Summary

L'utilisateur a dit : *"Raya va sur Amazon et mets moi des AirPods Pro 3 dans mon panier stp"*  
Amazon a confirmé visuellement l'ajout. RAYA a répondu qu'elle n'avait pas réussi.

Deux hypothèses à vérifier :
- **Hypothèse A** : Après `browser.click_at_position` (succès), le modèle n'a pas de signal
  de confirmation dans le JSON qu'il reçoit (`observation=()` → aucun fait WorldState promu).
  Si `_explain_blocked_turn` est déclenché (max iterations ou boucle), le modèle est amorcé
  à rapporter un échec via le system prompt "The agent got stuck" — sans voir aucune preuve du panier.
- **Hypothèse B** : Trop d'itérations consommées en navigation → budget épuisé avant vérification.

**Verdict** : CONFIRMED ROOT CAUSE — RC-A: Cart was in browser.read_page output, but `_explain_blocked_turn` priming overrode evidence → failure reported.

---

## 2. Environment

| Paramètre | Valeur |
|-----------|--------|
| Site | amazon.com.be |
| Prompt exact | "Raya va sur Amazon et mets moi des AirPods Pro 3 dans mon panier stp" |
| max_tool_iterations | 12 |
| Total elapsed | 96.6s |
| Tool calls | 12/12 |
| Model calls | 13 |

---

## 3. Full Trace with Timing

```
  [##] elapsed   tool                                status    outcome      arguments
  [ 1] 3.918s   browser.navigate                    success   SUCCESS      {"url": "https://www.amazon.fr"}
              evidence: {"url": "https://www.amazon.fr/"}
  [ 2] 0.504s   browser.read_page                   success   SUCCESS      {}
              evidence: {"url": "https://www.amazon.fr/", "cookie_banner": false}
  [ 3] 0.124s   browser.type                        failure   FAILURE      {"target": "search box", "text": "AirPods Pro 3", "submit": true}
  [ 4] 0.537s   browser.type                        success   SUCCESS      {"target": "Rechercher Amazon.fr", "submit": true, "text": "AirPods Pro 3"}
              evidence: {"typed_text": "AirPods Pro 3", "typed_target": "Rechercher Amazon.fr"}
  [ 5] 0.403s   browser.read_page                   success   SUCCESS      {}
              evidence: {"url": "https://www.amazon.fr/s?k=AirPods+Pro+3&__mk_fr_FR=%C3%85M%C3%85%C5%BD%C3%95%C3%91&ref=nb_sb_noss", "cookie_banner": false}
  [ 6] 4.255s   browser.click                       failure   FAILURE      {"target": "Apple AirPods Pro 3 Écouteurs sans Fil, réduction Active du Bruit product link...
  [ 7] 1.696s   browser.click                       success   SUCCESS      {"target": "Apple AirPods Pro 3 Écouteurs sans Fil"}
              evidence: {"clicked_target": "Apple AirPods Pro 3 Écouteurs sans Fil", "url": "https://www.amazon.fr/Apple-%C3%89couteurs-r%C3%A9duction-d%C3%A9tection-Haute-fi...
  [ 8] 0.329s   browser.read_page                   success   SUCCESS      {}
              evidence: {"url": "https://www.amazon.fr/Apple-%C3%89couteurs-r%C3%A9duction-d%C3%A9tection-Haute-fid%C3%A9lit%C3%A9/dp/B0FQF32239/ref=sr_1_1?__mk_fr_FR=%C3%85M...
  [ 9] 2.309s   browser.screenshot                  success   SUCCESS      {}
  [10] 3.141s   vision.observe_image                success   SUCCESS      {"path": "C:\\Users\\ruben\\AppData\\Local\\Temp\\raya_20f__l2vxeh4\\screenshots\\browser_...
              evidence: {"visual_observation": {"description": "Dans la zone d'achat située à droite de l'image, il y a un bouton jaune intitulé **\"Ajouter au panier\"**.\n\...
  [11] 0.915s   browser.click                       success   SUCCESS      {"target": "Ajouter au panier"}
              evidence: {"clicked_target": "Ajouter au panier", "url": "https://www.amazon.fr/cart/add-to-cart/ref=dp_start-ubbf_1_glance"}
  [12] 0.159s   browser.read_page                   success   SUCCESS      {}
              evidence: {"url": "https://www.amazon.fr/cart/smart-wagon?newItems=091a4dbc-befe-443b-a090-b4e7d01e3406,1&ref_=sw_refresh", "cookie_banner": false}
```

---

## 4. Step Classification

| Step | Classification | Tool | Status/Outcome |
|------|---------------|------|----------------|
|  1 | USEFUL | `browser.navigate` | success/SUCCESS |
|  2 | USEFUL | `browser.read_page` | success/SUCCESS |
|  3 | FAILED | `browser.type` | failure/FAILURE |
|  4 | USEFUL | `browser.type` | success/SUCCESS |
|  5 | REDUNDANT | `browser.read_page` | success/SUCCESS |
|  6 | FAILED | `browser.click` | failure/FAILURE |
|  7 | USEFUL | `browser.click` | success/SUCCESS |
|  8 | REDUNDANT | `browser.read_page` | success/SUCCESS |
|  9 | USEFUL | `browser.screenshot` | success/SUCCESS |
| 10 | USEFUL | `vision.observe_image` | success/SUCCESS |
| 11 | USEFUL | `browser.click` | success/SUCCESS |
| 12 | REDUNDANT | `browser.read_page` | success/SUCCESS |

**Legend** : USEFUL = moved toward goal, RECOVERY = error handling, REDUNDANT = repeated success, FAILED = tool returned failure, BLOCKED = exceeded loop limit

---

## 5. Tool Execution Timing

| Tool | elapsed_s | t_start | t_end |
|------|-----------|---------|-------|
| `browser.navigate` | 3.918s | 1.573s | 5.491s |
| `browser.read_page` | 0.504s | 7.11s | 7.614s |
| `browser.type` | 0.124s | 45.009s | 45.132s |
| `browser.type` | 0.537s | 46.743s | 47.28s |
| `browser.read_page` | 0.403s | 49.302s | 49.705s |
| `browser.click` | 4.255s | 51.611s | 55.866s |
| `browser.click` | 1.696s | 57.586s | 59.282s |
| `browser.read_page` | 0.329s | 61.294s | 61.623s |
| `browser.screenshot` | 2.309s | 63.886s | 66.195s |
| `vision.observe_image` | 3.141s | 68.469s | 71.61s |
| `browser.click` | 0.915s | 73.36s | 74.276s |
| `browser.read_page` | 0.159s | 76.327s | 76.487s |

**Summary:**
- Total tool execution : **18.3s**
- Total model inference : **77.7s**
- Total elapsed        : **96.6s**

---

## 6. Model Inference Timing

| # | Capability | elapsed_s | t_start | t_end |
|---|-----------|-----------|---------|-------|
| 1 | reasoning | 1.531s | 0.028s | 1.559s |
| 2 | reasoning | 1.577s | 5.519s | 7.096s |
| 3 | reasoning | 37.325s | 7.669s | 44.993s |
| 4 | reasoning | 1.556s | 45.161s | 46.717s |
| 5 | reasoning | 1.918s | 47.362s | 49.28s |
| 6 | reasoning | 1.873s | 49.721s | 51.593s |
| 7 | reasoning | 1.69s | 55.883s | 57.573s |
| 8 | reasoning | 1.948s | 59.333s | 61.281s |
| 9 | reasoning | 2.239s | 61.636s | 63.875s |
| 10 | reasoning | 2.243s | 66.212s | 68.455s |
| 11 | reasoning | 1.707s | 71.637s | 73.344s |
| 12 | reasoning | 1.983s | 74.33s | 76.313s |
| 13 | reasoning | 20.108s | 76.501s | 96.609s |

---

## 7. What Model Received After browser.click_at_position


_browser.click_at_position was not called in this run._


**Architectural explanation** : `browser.click_at_position` has `observation=()` in
`raya/tools/catalog/browser.py:91`. This means:
- No WorldState fact is promoted after a successful click
- `_summarize_tool_result` returns only `{tool, status, verification, output: {x, y}, evidence: null}`
- The model has NO cart confirmation signal from this tool alone
- The model MUST explicitly call `browser.read_page` to verify the cart state

---

## 8. browser.read_page After Click (Cart Signal Analysis)


**t=7.7s** — **CART SIGNAL PRESENT**
Keywords found: `['dans le panier', 'panier', 'cart', 'article']`
JSON preview:
```json
{"tool": "browser.read_page", "status": "success", "verification": "SUCCESS", "output": {"url": "https://www.amazon.fr/", "title": "Amazon.fr : livres, DVD, jeux vidéo, musique, high-tech, informatique, jouets, vêtements, chaussures, sport, bricolage, maison, beauté, puériculture, épicerie et plus encore !", "cookie_banner": false, "buttons": [{"kind": "button", "text": "Votre adresse de livraison: Belgique", "tag": "a", "left": 149, "top": 5}, {"kind": "button", "text": "Go", "tag": "input", "l
```

**t=49.7s** — **CART SIGNAL PRESENT**
Keywords found: `['dans le panier', 'panier', 'cart', 'article']`
JSON preview:
```json
{"tool": "browser.read_page", "status": "success", "verification": "SUCCESS", "output": {"url": "https://www.amazon.fr/s?k=AirPods+Pro+3&__mk_fr_FR=%C3%85M%C3%85%C5%BD%C3%95%C3%91&ref=nb_sb_noss", "title": "Amazon.fr : AirPods Pro 3", "cookie_banner": false, "buttons": [{"kind": "button", "text": "Votre adresse de livraison: Belgique", "tag": "a", "left": 149, "top": 5}, {"kind": "button", "text": "Go", "tag": "input", "left": 1369, "top": 10}, {"kind": "button", "text": "Toutes", "tag": "a", "l
```

**t=61.6s** — **CART SIGNAL PRESENT**
Keywords found: `['dans le panier', 'panier', 'cart', 'article']`
JSON preview:
```json
{"tool": "browser.read_page", "status": "success", "verification": "SUCCESS", "output": {"url": "https://www.amazon.fr/Apple-%C3%89couteurs-r%C3%A9duction-d%C3%A9tection-Haute-fid%C3%A9lit%C3%A9/dp/B0FQF32239/ref=sr_1_1?__mk_fr_FR=%C3%85M%C3%85%C5%BD%C3%95%C3%91&dib=eyJ2IjoiMSJ9.D4cDARTAvHZ6RYEz8OrzJcxSDWo6kEMS_DSplPA9BuIdxrYe2kjIIcN0UlrLn1bVVaY2DDA88AAoqf1dKcchafXqclt-ZJGYwoDB2d_uxNLK-9bhvrwBKGlk3Cz1aWkxwF5oc90OT49igOQ2ehGF_ctdx0FAyfT3HDP1ciwtkzJfW2VFKBGCSj6uy9idGWuaEbsBpZomKl4Ni83MNX_owBSmt-TE
```

**t=76.5s** — **CART SIGNAL PRESENT**
Keywords found: `['dans le panier', 'panier', 'cart', 'article']`
JSON preview:
```json
{"tool": "browser.read_page", "status": "success", "verification": "SUCCESS", "output": {"url": "https://www.amazon.fr/cart/smart-wagon?newItems=091a4dbc-befe-443b-a090-b4e7d01e3406,1&ref_=sw_refresh", "title": "Amazon.fr Panier", "cookie_banner": false, "buttons": [{"kind": "button", "text": "Votre adresse de livraison: Belgique", "tag": "a", "left": 149, "top": 5}, {"kind": "button", "text": "Go", "tag": "input", "left": 1239, "top": 10}, {"kind": "button", "text": "Élargissez le compte et les
```


---

## 9. Vision Tools


**vision.observe_image** at t=71.6s:
```json
{"tool": "vision.observe_image", "status": "success", "verification": "SUCCESS", "output": {"observation_id": "vobs_01M2MNGD7FH1P852SDN5FC1PPP", "description": "Dans la zone d'achat située à droite de l'image, il y a un bouton jaune intitulé **\"Ajouter au panier\"**.\n\nIl se trouve dans la colonne de droite, vers le milieu de la page. Le prix affiché juste au-dessus de la section de sélection du vendeur est de **184,35 €**.", "semantic_entities": ["Dans"], "model_used": "ollama_cloud:gemma4:31b", "confidence": "inferred"}, "evidence": {"visual_observation": {"description": "Dans la zone d'ac
```


---

## 10. _explain_blocked_turn Analysis


**TRIGGERED** at t=76.5s after 12 tool calls.

**Reason hint passed to model:**
> Je n'ai pas terminé cette demande dans les 12 étapes prévues (12 action(s) réelle(s) tentée(s)).

**Stripped trace passed to model (no evidence, no evidence of cart success):**
| # | Tool | Status | Outcome |
|---|------|--------|---------|
| 1 | `browser.navigate` | success | SUCCESS |
| 2 | `browser.read_page` | success | SUCCESS |
| 3 | `browser.type` | failure | FAILURE |
| 4 | `browser.type` | success | SUCCESS |
| 5 | `browser.read_page` | success | SUCCESS |
| 6 | `browser.click` | failure | FAILURE |
| 7 | `browser.click` | success | SUCCESS |
| 8 | `browser.read_page` | success | SUCCESS |
| 9 | `browser.screenshot` | success | SUCCESS |
| 10 | `vision.observe_image` | success | SUCCESS |
| 11 | `browser.click` | success | SUCCESS |
| 12 | `browser.read_page` | success | SUCCESS |

**Effect**: The model receives: system prompt "The agent got stuck trying to complete the user's
request" + stripped trace + reason hint. This primes the model to report failure, regardless of
whether the cart action actually succeeded.


---

## 11. Exact Failure Mechanism

The failure mechanism is **precisely reconstructed** from the trace + instrumentation:

```
Iteration 11 (model call #11):
  Model sees result of step 10 (vision.observe_image: "bouton Ajouter au panier" visible)
  → model calls browser.click("Ajouter au panier")
  → click SUCCEEDS: url = /cart/add-to-cart/ref=dp_start-ubbf_1_glance

Iteration 12 (model call #12):
  Model sees result of step 11 (click success + cart URL)
  → model calls browser.read_page to verify
  → read_page SUCCEEDS: url = /cart/smart-wagon?newItems=..., title = "Amazon.fr Panier"
  → cart IS confirmed in model's message history

>>> Loop range(12) is now exhausted — no iteration 13 available <<<

_explain_blocked_turn (model call #13):
  System: "The agent got stuck trying to complete the user's request"
  Reason: "Je n'ai pas terminé cette demande dans les 12 étapes prévues"
  Trace: 12 entries stripped of ALL evidence — no cart URL, no cart count
  → Model generates: "Je n'ai pas réussi à mettre l'article dans le panier..."
```

**The model's iteration 12 correctly received cart confirmation. But the response slot
was taken by `_explain_blocked_turn`, not by the model itself. The model never got to
say "success" — the loop ended and a failure narrative was injected.**

---

## 12. Root Cause Analysis

- **RC-A1 CONFIRMED**: `_explain_blocked_turn` was triggered — model was primed to report failure.
- **RC-A2 CONFIRMED**: `browser.read_page` WAS called after click AND showed cart URL — but model call #12 never got a response slot; `_explain_blocked_turn` generated the response instead.
- **RC-A3 CONFIRMED**: Cart signal WAS in browser.read_page output at t=76.5s (`/cart/smart-wagon?newItems=...`), yet model reported failure because `_explain_blocked_turn` stripped ALL evidence and primed failure.
- **RC-B CONFIRMED**: Max iterations reached (12/12) — the 13th slot (model's success response) was replaced by `_explain_blocked_turn`.
- **RC-B2 OBSERVED**: Model call #3 took 37.3s (anomalous, likely due to large homepage read_page output). 80.4% of total time is model inference (77.7s / 96.6s). Each individual model call consumes the iteration budget.

---

## 13. Architecture Gap — Post-Action Verification

### Gap A1 — No observation after click_at_position

`browser.click_at_position` (`raya/tools/catalog/browser.py:91`) :
```python
("browser.click_at_position", ..., observation=()),
```

After a successful position-click, the JSON the model sees is:
```json
{"tool": "browser.click_at_position", "status": "success", "verification": "success",
 "output": {"x": X, "y": Y}, "evidence": null}
```

There is NO cart confirmation, no URL change, no count update. The model must infer
that it should call `browser.read_page` next — but this is a reasoning step that
may not happen if the model has other priorities or if iterations are exhausted.

### Gap A2 — _explain_blocked_turn strips all evidence

When `_explain_blocked_turn` is called (`raya/harness/loop.py:419-461`), it passes
to the model:
1. System prompt: **"The agent got stuck trying to complete the user's request"** — primes failure
2. Stripped trace: `{tool, status, outcome}` only — NO evidence fields, NO cart count
3. Reason hint: e.g. `"Je n'ai pas terminé cette demande dans les 12 étapes prévues"`

Even if the cart was added (visible in browser), the model has NO signal of this
in the `_explain_blocked_turn` context, and is positively prompted to report failure.

### Gap B — Latency budget consumed before verification

An Amazon add-to-cart task requires approximately:
- Navigate to site (1-2 steps)
- Handle cookie banner (1-2 steps)
- Search product (1-2 steps)
- Navigate to product page (1-2 steps)
- Find add-to-cart button (1-2 steps, possibly vision)
- Click + verify (2 steps)

Minimum: ~8 steps. With any navigation uncertainty, 12 is tight.
If verification (step N+1 after click) is step 13, `_explain_blocked_turn` fires.

---

## 14. RAYA Response

```
Voici où on en est, honnêtement :

1. **Ce qui a été fait :** J'ai bien ouvert Amazon, puis j'ai fait des recherches et navigué sur des pages pour tenter de trouver les AirPods Pro 3.
2. **Ce qui a été tenté :** J'ai essayé plusieurs fois de saisir du texte et de cliquer sur des éléments, et j'ai aussi pris une capture d'écran pour analyser la page.
3. **Ce qui bloque :** Je n'ai pas réussi à mettre l'article dans le panier dans le nombre d'étapes prévu (12 actions). Le suivi ne montre pas clairement la cause exacte — je ne peux donc pas affirmer pourquoi précisément ça n'a pas abouti (page différente, produit introuvable, ou bouton qui n'a pas réagi).
4. **Ce que tu peux faire :** Si tu veux, je peux réessayer. Sinon, tu peux vérifier toi-même sur Amazon si les AirPods Pro 3 sont disponibles à l'achat (il est possible que le produit ne soit pas encore en vente ou porte un nom légèrement différent), ou me préciser la bonne référence exacte pour que je recommence.
```

---

## 15. Final Verdict

| Root Cause | Status |
|------------|--------|
| RC-A1: `_explain_blocked_turn` primes failure | **CONFIRMED** |
| RC-A2: Cart confirmed in step 12, but loop ended before model could respond | **CONFIRMED** |
| RC-A3: `_explain_blocked_turn` stripped cart evidence from model context | **CONFIRMED** |
| RC-B: 12/12 iterations exhausted — success response slot hijacked | **CONFIRMED** |
| RC-B2: Model inference 77.7s / 96.6s total (80%), 37s anomaly on call #3 | **OBSERVED** |

**Final verdict : CONFIRMED ROOT CAUSE**  
RC-A: Cart was in browser.read_page output, but `_explain_blocked_turn` priming overrode evidence → failure reported.

---

*Rapport généré par `scripts/audit_20f_post_action_verification.py`*  
*Aucun fichier de production modifié.*  
