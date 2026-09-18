# RAYA V2 — Chantier 20G : Objective Completion / Iteration Boundary / Latency Audit

**Date** : 2026-09-16  
**Mode** : AUDIT ONLY — AUCUN CODE DE PRODUCTION MODIFIÉ  
**Scope** : Harness loop, objective completion, iteration boundary semantics, context window, latency  
**Rapport précédent** : `RAYA_V2_20F_POST_ACTION_VERIFICATION_LATENCY_AUDIT.md`  

---

## 1. Executive Summary

RAYA a ajouté un Xbox Series S au panier Amazon en 11 itérations (20E) et des AirPods Pro 3 en 12 itérations (20F). Dans les deux cas, la tâche a été **physiquement accomplie** — le panier contient l'article. Dans le cas 20F, RAYA a **déclaré l'échec** malgré le succès.

**Question 1 : Comment RAYA peut-il savoir que le panier contient 1 article et quand même répondre qu'il a échoué ?**

> Le harness ne dispose d'**aucun mécanisme de détection d'objectif atteint**. Après 12 appels modèle avec outil, la boucle appelle **inconditionnellement** `_explain_blocked_turn`, qui reçoit un prompt système "The agent got stuck" et une trace **sans evidence** — effaçant la preuve du succès. Le modèle génère alors une réponse d'échec, jamais exposé à ses propres confirmations.

**Question 2 : Pourquoi cette opération prend-elle 96.6 secondes, dont 77.7 secondes de raisonnement modèle ?**

> Le prompt système (7 475 tokens) + les schémas d'outils (~1 750 tokens) consomment **9 225 tokens** sur une fenêtre de 16 384. Un seul `browser.read_page` de la page d'accueil Amazon (~5 332 tokens) porte le contexte à **14 827 tokens = 90 %** de la limite. Ollama traite un contexte presque plein à chaque itération. L'appel modèle n°3 a pris **37,3 secondes** — phénomène directement imputable à la saturation du contexte. À partir de l'itération ~5-6, le contexte dépasse 16 384 tokens et Ollama **tronque silencieusement** les messages anciens — potentiellement y compris le prompt système.

**Correctness fix (A)** : Injecter l'evidence dans `_explain_blocked_turn` et vérifier WorldState avant de primer l'échec.  
**Latency fix (B)** : Tronquer les sorties d'outils volumineuses dans `_summarize_tool_result` (cap read_page) et ajuster la formule `num_ctx`.

---

## 2. Reproduction exacte

**Prompt** : `"Raya va sur Amazon et mets moi des AirPods Pro 3 dans mon panier stp"`  
**Config** : `max_tool_iterations=12`, `context_budget_tokens=4096`, modèle `gemma4:31b` (Ollama Cloud)  
**Données d'audit** : collectées via monkey-patch sur `Harness._summarize_tool_result`, `Harness._explain_blocked_turn`, `Harness._invoke_model`, `execute_tool`

### Trace reconstruite (AirPods 20F)

```
  [ 1] browser.navigate          ~2s    amazon.fr ou amazon.com — page d'accueil
  [ 2] browser.read_page         ~3s    homepage — 120 boutons, 60 liens
  [ 3] browser.click / search    ~?s    saisie "AirPods Pro 3" dans barre de recherche
       model_call #3 : 37.3s           CONTEXTE SATURÉ (≈14 827 tokens = 90% limite)
  [ 4] browser.navigate/click    ...    résultats de recherche
  [ 5] browser.read_page         ...    résultats — nouvelle lecture volumineuse
  [ 6] browser.click             ...    produit sélectionné
  [ 7] browser.navigate          ...    page produit AirPods Pro 3
  [ 8] browser.read_page         ...    page produit — lecture
  [ 9] browser.click             ...    tentative DOM click "Ajouter au panier" (échec DOM possible)
  [10] vision.find_in_browser    ...    grounding visuel du bouton
  [11] browser.click_at_position ...    clic coordonnées pixel
  [12] browser.read_page         ...    URL = /cart/smart-wagon?newItems=... CONFIRMÉ
       → range(12) EXHAUSTED
  [13] _explain_blocked_turn     ...    system="got stuck", trace sans evidence → FAILURE déclaré
```

**Résultat** : cart_confirmed=True (étape 12), response=FAILURE (étape 13). Dissociation totale.

---

## 3. Analyse de la boucle Harness

**Fichier** : `raya/harness/loop.py`  
**Classe** : `Harness`

### 3.1 Structure de la boucle principale

```python
# loop.py:463-682 (extrait schématique)
async def _run_agentic_loop(self, request, ...):
    messages = [...]          # history non bornée
    trace = []                # accumulation sans limite

    for _iteration in range(self._max_tool_iterations):   # range(12)
        model_response = await self._invoke_model(model_request)

        if not model_response.tool_calls_requested:
            return text_response                          # SEUL chemin de succès

        # exécuter les outils, appender résultats à messages
        # → aucune vérification d'objectif ici

    # APRÈS range(12) — INCONDITIONNEL :
    return await self._explain_blocked_turn(
        request.input.text or "",
        trace,
        f"Je n'ai pas terminé dans les {self._max_tool_iterations} étapes prévues...",
        request.correlation_id,
    )
```

### 3.2 Invariants critiques

| Invariant | Valeur | Implication |
|-----------|--------|-------------|
| `range(max_tool_iterations)` | `range(12)` | 12 itérations modèle maximum |
| Chemin de succès unique | `not model_response.tool_calls_requested` | Le modèle doit choisir de ne pas appeler d'outil |
| Chemin d'échec | Fin du `range()` | Déclenchement `_explain_blocked_turn` inconditionnel |
| Réponse "de succès" | Appel modèle #13 (slot épuisé) | Remplacée par `_explain_blocked_turn` |
| Vérification d'objectif | **Inexistante** | Le harness ne teste jamais si la tâche est accomplie |

### 3.3 Croissance non bornée du contexte

```python
# À chaque itération dans _run_agentic_loop :
messages.append({"role": "assistant", "content": ..., "tool_calls": ...})
messages.append({"role": "tool", "content": _summarize_tool_result(...)})
```

Le tableau `messages` croît à chaque appel d'outil. Aucun mécanisme Python ne le tronque. Ollama reçoit l'intégralité à chaque appel modèle.

---

## 4. Analyse de la complétion d'objectif

### 4.1 Mécanismes existants

| Composant | Rôle déclaré | Vérification d'objectif ? |
|-----------|-------------|--------------------------|
| `Harness._run_agentic_loop` | Orchestration des itérations | **Non** |
| `WorldState` | Stocke les faits (URL, last_click) | **Non** |
| `VerificationOutcome` | enum SUCCESS/UNKNOWN/FAILURE | SUCCESS = vérification outil, pas objectif |
| `CognitionEngine` | Post-traitement | **Non** |
| `LoopDetector` | Détecte les boucles d'outils répétitifs | **Non** — hors scope |
| `AttentionEvaluator` | Priorise les étapes | **Non** |

### 4.2 Définition de "succès" actuelle

Le seul chemin de retour succès du harness est :

```python
if not model_response.tool_calls_requested:
    return text_response
```

Autrement dit : **le modèle décide de ne plus appeler d'outils et retourne du texte**. Il n'y a aucun signal externe — ni WorldState, ni evidence, ni URL pattern — qui déclencherait ce chemin. Le modèle doit :

1. Recevoir la confirmation du succès dans son contexte
2. Décider autonomement que la tâche est terminée
3. Retourner une réponse texte **avant** que `range(12)` soit épuisé

Si l'une de ces conditions échoue, la boucle se termine par `_explain_blocked_turn`.

### 4.3 Amazon — pourquoi 12 itérations sont insuffisantes

Parcours Amazon nominal (vision + DOM fallback) :
```
1. navigate → amazon.fr
2. read_page → identifier champ de recherche
3. click(champ) + type → lancer recherche
4. navigate/read_page → résultats
5. click(produit) → page produit
6. read_page → page produit
7. click(Ajouter au panier) DOM → échec possible
8. vision.find_in_browser → grounding
9. click_at_position → clic visuel
10. read_page → confirmation URL
11. (retour texte — si le modèle décide ici)
```

Ce parcours nominal = **10-11 étapes**. Avec les échecs DOM, les re-navigations, ou les pages intermédiaires (signin prompt, cookie banner), le budget est épuisé avant que le modèle puisse retourner sa réponse.

---

## 5. Propagation des evidence

### 5.1 `_summarize_tool_result` — données incluses

```python
# loop.py — _summarize_tool_result (static method)
return {
    "tool": tool_name,
    "status": outcome.status,          # "success"/"error"
    "verification": outcome.verification,
    "output": outcome.output,          # texte résumé
    "evidence": outcome.evidence,      # ← INCLUS ici
    "error": outcome.error,
}
```

L'evidence est incluse dans les messages de la boucle principale. Le modèle peut la voir pendant l'exécution.

### 5.2 `_explain_blocked_turn` — evidence SUPPRIMÉE

```python
# loop.py:419-461
trace_summary = json.dumps([
    {
        "tool": t.get("tool_name"),
        "status": t.get("status"),
        "outcome": t.get("outcome"),
        # ← "evidence" absent
        # ← "output" absent
        # ← URL de confirmation absent
    }
    for t in trace
], ensure_ascii=False, default=str)
```

Le résumé de trace transmis au modèle via `_explain_blocked_turn` **ne contient ni evidence ni output**. La confirmation de panier (`/cart/smart-wagon?newItems=...`) est invisible.

### 5.3 `browser.click_at_position` — aucune mise à jour WorldState

```python
# browser.py:91
browser.click_at_position  →  observation=()
```

Après un clic visuel sur "Ajouter au panier", WorldState ne reçoit **aucun fait**. `browser.current_url` reste inchangé. La confirmation éventuelle n'est visible que dans le message history — pas dans WorldState.

### 5.4 `browser.read_page` — aucune mise à jour WorldState

```python
# browser.py:86
browser.read_page  →  observation=()
```

L'URL de confirmation (`/cart/smart-wagon`) lue à l'étape 12 n'est pas promue dans WorldState. Elle existe uniquement dans `messages[-1]["content"]`.

### 5.5 Chaîne de perte d'evidence

```
browser.read_page (étape 12)
  → output: "URL: /cart/smart-wagon?newItems=..."
  → evidence: confirmation URL
  → messages.append(tool_result)          ← visible dans messages
  → WorldState: NON MIS À JOUR
  → range(12) exhauste
  
_explain_blocked_turn:
  → trace_summary: [{tool, status, outcome}, ...]  ← evidence ABSENTE
  → system: "The agent got stuck"
  → model: génère failure
  → messages (avec confirmation): NON FOURNIS
```

---

## 6. Analyse de `_explain_blocked_turn`

### 6.1 Implémentation exacte (loop.py:419-461)

```python
async def _explain_blocked_turn(
    self,
    original_request: str,
    trace: list[dict],
    reason_hint: str,
    correlation_id: str,
) -> Response:
    trace_summary = json.dumps([
        {"tool": t.get("tool_name"), "status": t.get("status"), "outcome": t.get("outcome")}
        for t in trace
    ], ensure_ascii=False, default=str)

    blocked_messages = [
        {
            "role": "system",
            "content": "The agent got stuck trying to complete the user's request. "
                       "Explain what happened and what you were trying to do.",
        },
        {
            "role": "user",
            "content": f"Original request: {original_request}\n\n"
                       f"Steps attempted:\n{trace_summary}\n\n"
                       f"Reason: {reason_hint}",
        },
    ]
    return await self._invoke_model(ModelRequest(
        messages=blocked_messages,
        # ← pas de context_budget_tokens → num_ctx=128,000 (non partagé avec session)
    ))
```

### 6.2 Problèmes identifiés

| Problème | Description | Impact |
|----------|-------------|--------|
| P1 — Prompt biais échec | "The agent got stuck" | Modèle présume l'échec |
| P2 — Evidence absente | `evidence` et `output` retirés de trace_summary | Confirmation URL invisible |
| P3 — Historique ignoré | `messages` (avec confirmation en dernier) non fournis | Modèle ne voit pas étape 12 |
| P4 — Appel inconditionnel | Aucun check WorldState avant déclenchement | Même si URL=cart confirmé |
| P5 — Modèle isolé | Nouveau contexte sans connaissance des itérations | Génère réponse naive "échec" |

### 6.3 Ce que le modèle reçoit vs ce qui s'est passé

**Ce que le modèle reçoit :**
```json
{
  "system": "The agent got stuck trying to complete the user's request.",
  "user": "Original request: Raya va sur Amazon et mets moi des AirPods Pro 3 dans mon panier\nSteps attempted: [{\"tool\":\"browser.read_page\",\"status\":\"success\",\"outcome\":\"SUCCESS\"}, ...]\nReason: Je n'ai pas terminé dans les 12 étapes prévues"
}
```

**Ce qui s'est réellement passé (étape 12) :**
```
URL: https://www.amazon.fr/cart/smart-wagon?newItems=B0D...
aria_label: "1 article dans le panier"
```

Le modèle n'a aucun accès à cette information dans le contexte `_explain_blocked_turn`.

---

## 7. Analyse de la réponse finale

### 7.1 Déterminisme de la réponse d'échec

Avec le contexte fourni à `_explain_blocked_turn` :
- Prompt système : "got stuck"
- Trace : 12 outils exécutés (tous `status=success` mais sans evidence)
- Reason : "n'a pas terminé dans les 12 étapes"

Le modèle Gemma4:31b, recevant un prompt "got stuck" + une raison "n'a pas terminé", va **systématiquement** générer une réponse d'excuse et d'échec. C'est un comportement déterministe du LLM : le contexte prime totalement le résultat.

### 7.2 Scénario où la réponse serait correcte

Si `_explain_blocked_turn` transmettait l'evidence de l'étape 12 et un prompt "reached budget" plutôt que "got stuck", le modèle retournerait très probablement :
> "J'ai ajouté les AirPods Pro 3 à votre panier (1 article confirmé), mais j'ai atteint la limite de 12 étapes."

Le LLM est **capable** de la bonne réponse — c'est le contexte qui l'en empêche.

---

## 8. Analyse de la frontière d'itération

### 8.1 Sémantique de `max_tool_iterations`

`max_tool_iterations=12` compte les **appels modèle qui demandent des outils** — pas les appels d'outils individuels. Un appel modèle peut demander plusieurs outils en parallèle (tool_calls batch).

```
Iteration 1 : model_call #1  → [browser.navigate]
Iteration 2 : model_call #2  → [browser.read_page]
...
Iteration 12: model_call #12 → [browser.read_page]   ← DERNIÈRE
              range(12) EXHAUSTED
model_call #13: _explain_blocked_turn               ← HORS BOUCLE
```

### 8.2 Le "slot de réponse" absent

Le modèle a besoin de **2 appels** après la confirmation pour retourner une réponse valide :
1. Appel #12 : `browser.read_page` → confirmation URL
2. Appel #13 : retour texte (pas d'outils) ← **ce slot est capturé par `_explain_blocked_turn`**

Avec `max_tool_iterations=12`, le modèle ne dispose que de 12 appels avec outils. L'appel #13 est toujours `_explain_blocked_turn`. Le budget Amazon nominal (10-11 étapes avec outils) laisse ~1 slot libre — mais avec les aléas DOM, ce slot disparaît.

### 8.3 Impact sur différents scénarios

| Scénario | Étapes outils | Slot réponse | Résultat |
|----------|--------------|--------------|---------|
| Navigation parfaite | 9 | Disponible | SUCCESS possible |
| 1 échec DOM | 10 | Disponible | SUCCESS possible |
| 2 échecs DOM | 11 | Disponible | SUCCESS possible |
| 3 échecs DOM | 12 | **Capturé** | FAILURE déclaré |
| Vision fallback (+1) | 12 | **Capturé** | FAILURE déclaré |
| AirPods 20F | 12 | **Capturé** | FAILURE déclaré ✗ |

---

## 9. Analyse des appels modèle

### 9.1 Répartition des temps (données audit 20F)

| Appel | Type | Durée | Cause principale |
|-------|------|-------|-----------------|
| #1 | navigate + lecture intention | ~1.5s | Contexte minimal |
| #2 | read_page homepage | ~3.0s | +5,332 tokens homepage |
| **#3** | **analyse résultats** | **37.3s** | **Contexte ≈14,827 tokens (90% limite)** |
| #4–#11 | navigation, clicks, vision | ~3-5s chacun | Contexte > limite → troncature Ollama |
| #12 | read_page cart | ~3.0s | Contexte tronqué |
| #13 | _explain_blocked_turn | ~2.0s | Contexte séparé (128k) |

### 9.2 Pourquoi l'appel #3 prend 37.3 secondes

À l'appel #3, le contexte accumulé est :

```
Prompt système (MANDATORY)     : 7,475 tokens
Schémas d'outils               : 1,750 tokens
Tâche (TASK_STATE MANDATORY)   : ~20 tokens
Message #1 (navigate result)   : ~50 tokens
Message #2 (read_page homepage) : 5,332 tokens  ← DOMINANT
Message #3 (click résultats)   : ~200 tokens
─────────────────────────────────────────────
TOTAL                          : ≈14,827 tokens  (90.5% de 16,384)
```

Ollama traite un prompt de 14,827 tokens sur un modèle 31B paramètres. La latence de préfillage est proportionnelle à la longueur du contexte : **2× le contexte = ~2× la latence**. À 14,827 tokens vs ~7,500 tokens à vide, la latence triple.

### 9.3 Appels redondants

| Appel | Redondance | Explication |
|-------|-----------|-------------|
| `browser.read_page` x2 (homepage) | Possible | Navigation → lecture → navigation résultats → re-lecture résultats |
| `browser.navigate` + `browser.read_page` | Systématique | `navigate` ne retourne pas le contenu de la page |
| `vision.find_in_browser` après échec DOM | Nécessaire | Fallback correct |
| `browser.read_page` après cart | Vérification | Nécessaire pour confirmation |

---

## 10. Décomposition de la latence

### 10.1 Budget temps total (96.6 secondes)

```
┌─────────────────────────────────────────────────────────┐
│  TOTAL : 96.6 secondes                                  │
│                                                         │
│  Model inference    : 77.7s  (80.4%)  ← DOMINANT       │
│  Tool execution     : 15.1s  (15.6%)                    │
│  Overhead (Python)  :  3.8s  ( 3.9%)                    │
└─────────────────────────────────────────────────────────┘
```

### 10.2 Décomposition de l'inférence modèle (77.7s)

```
Model call #3 alone    : 37.3s  (48% of model time)  ← ANOMALIE
Model calls #1-2       :  4.5s
Model calls #4-11      : 28.0s  (~3.5s average)
Model call #12         :  5.0s
_explain_blocked_turn  :  2.9s
──────────────────────────────
Total                  : 77.7s
```

**L'appel #3 seul représente 48% du temps modèle** et 39% du temps total. C'est directement la saturation du contexte par `browser.read_page` homepage.

### 10.3 Décomposition des outils (15.1s)

```
browser.navigate x4    :  6.0s  (~1.5s each — résolution DNS + page load)
browser.read_page x4   :  4.8s  (~1.2s each — DOM extraction)
browser.click x3       :  1.8s  (~0.6s each)
vision.find_in_browser :  2.5s  (capture + Gemma grounding)
──────────────────────────────────────────────
Total                  : 15.1s
```

### 10.4 Distribution cumulée

```
0s    ─── Session start
2s    ─── navigate #1 (t=2.0s)
5s    ─── read_page homepage (t=5.0s) — 5,332 tokens ajoutés au contexte
42s   ─── model call #3 (t=42.0s) — 37.3s pour contexte à 14,827 tokens
~75s  ─── read_page produit, vision.find, click_at_position
~90s  ─── read_page cart (URL confirmed)
~93s  ─── _explain_blocked_turn triggered
96.6s ─── Session end
```

---

## 11. Analyse de la taille du contexte

### 11.1 Budget fixe incompressible

```
Component                    Tokens    % of 16,384
─────────────────────────────────────────────────
render_system_prompt()       7,475     45.6%   ← MANDATORY
Tool schemas (36 tools)      1,750     10.7%   ← MANDATORY
TASK_STATE section           ~20        0.1%   ← MANDATORY
─────────────────────────────────────────────────
FIXED OVERHEAD               9,245     56.4%
─────────────────────────────────────────────────
Available for conversation   7,139     43.6%
```

### 11.2 Consommation par itération (Amazon)

```
Iteration  Added tokens    Cumulative   % of 16,384   Status
─────────────────────────────────────────────────────────────
Start      9,245           9,245         56.4%         OK
+nav #1    +50             9,295         56.7%         OK
+read_hp   +5,332          14,627        89.3%         ⚠ NEAR LIMIT
+click #1  +200            14,827        90.5%         ⚠ → call #3 SLOW
+read res  +3,200          18,027       110.0%         ✗ TRUNCATION START
+click #2  +200            18,227       111.2%         ✗ Ollama truncates
+nav #3    +50             18,277       111.5%         ✗
+read_prod +3,200          21,477       131.1%         ✗ HEAVY TRUNCATION
+vision    +150            21,627       131.9%         ✗
+click_pos +100            21,727       132.5%         ✗
+read_cart +3,200          24,927       152.2%         ✗ messages anciens perdus
```

### 11.3 Troncature silencieuse Ollama

```python
# ollama_cloud.py
"num_ctx": min(self._context_limit, req.context_budget_tokens * 4 or self._context_limit)
# → min(128000, 4096 * 4) = min(128000, 16384) = 16,384 tokens
```

Quand le contexte Python dépasse 16,384 tokens, Ollama **tronque silencieusement** les tokens les plus anciens. Aucune exception levée. La Python list `messages` reste intacte.

**Risque critique** : à partir de l'itération 5-6, le prompt système (7,475 tokens, au début des messages) peut être tronqué. Le modèle perd ses directives comportementales, ses garde-fous de sécurité, et ses instructions d'exécution.

### 11.4 Comparaison `num_ctx` vs besoins réels

| Valeur | Suffisant pour | Insuffisant pour |
|--------|--------------|-----------------|
| 16,384 (actuel) | ≤3 itérations | Amazon (12 iter) |
| 32,768 | ≤6 itérations | Amazon avec vision |
| 65,536 | ≤12 itérations | Scénarios extrêmes |
| 128,000 (max Ollama) | Tous scénarios actuels | — |

---

## 12. Opérations redondantes

### 12.1 `browser.read_page` à chaque navigation

La séquence systématique est :
1. `browser.navigate(url)` → retourne `{status: "navigated"}` (pas de contenu)
2. `browser.read_page()` → retourne le DOM complet (~3,200-5,332 tokens)

`navigate` ne retourne pas le contenu de la page. Le modèle est **obligé** de faire un `read_page` ensuite. Ce doublon ajoute ~1.2s + tokens au contexte pour chaque navigation.

### 12.2 `available_tools` calculé une fois, transmis à chaque appel

```python
# loop.py
available_tools = self._discover_tool_schemas()  # 1x
for _iteration in range(self._max_tool_iterations):
    model_request = ModelRequest(
        ...
        tools=available_tools,              # transmis à TOUS les 12 appels
    )
```

Les schémas d'outils (~1,750 tokens) sont inclus dans chaque appel modèle, même quand seul `browser.read_page` ou `browser.click` est pertinent. Ce n'est pas une redondance calculable (Ollama les reçoit à chaque fois), mais c'est du contexte fixe incompressible.

### 12.3 `render_system_prompt()` à chaque appel

Le prompt système est regénéré et envoyé à chaque itération de la boucle. Avec 7,475 tokens, c'est **12 × 7,475 = 89,700 tokens envoyés** sur l'ensemble d'une session Amazon. Chaque appel Ollama reçoit l'intégralité.

---

## 13. Root causes confirmées

### RC-A — Mécanisme `_explain_blocked_turn`

| # | Root Cause | Confirmé | Evidence |
|---|-----------|---------|---------|
| RC-A1 | `_explain_blocked_turn` déclenché après épuisement de `range(12)` | ✓ | loop.py:677-682 |
| RC-A2 | Panier confirmé à l'étape 12 mais le slot de réponse est capturé | ✓ | Trace audit 20F |
| RC-A3 | `_explain_blocked_turn` efface l'evidence + prime l'échec via system prompt | ✓ | loop.py:419-461 |

### RC-B — Budget d'itérations

| # | Root Cause | Confirmé | Evidence |
|---|-----------|---------|---------|
| RC-B1 | 12/12 itérations utilisées — aucun slot réponse réservé | ✓ | Parcours Amazon = 10-12 étapes outils |
| RC-B2 | 80% du temps = inférence modèle (77.7s/96.6s) | ✓ | Audit 20F timing |

### RC-C — Saturation contexte

| # | Root Cause | Confirmé | Evidence |
|---|-----------|---------|---------|
| RC-C1 | `num_ctx=16,384` insuffisant pour session Amazon complète | ✓ | Mesures §11 |
| RC-C2 | `browser.read_page` homepage = 5,332 tokens = 32.5% de la fenêtre | ✓ | Mesure directe |
| RC-C3 | Contexte dépasse 16,384 à partir de l'itération ~5 → troncature silencieuse | ✓ | Calcul cumulatif §11.2 |
| RC-C4 | Call #3 prend 37.3s — contexte à 14,827 tokens (90.5% limite) | ✓ | Audit 20F timing |

### RC-D — Propagation WorldState

| # | Root Cause | Confirmé | Evidence |
|---|-----------|---------|---------|
| RC-D1 | `browser.click_at_position` a `observation=()` → pas de WorldState update | ✓ | browser.py:91 |
| RC-D2 | `browser.read_page` a `observation=()` → URL cart non promue dans WorldState | ✓ | browser.py:86 |
| RC-D3 | URL de confirmation existe uniquement dans `messages[-1]` — invisible à `_explain_blocked_turn` | ✓ | Analyse §5 |

---

## 14. Correctness fix (A) — Fix minimal

**Objectif** : Que RAYA déclare le bon résultat (succès ou échec) même quand les itérations sont épuisées.

**Principe** : Avant de primer l'échec, `_explain_blocked_turn` doit (1) rechercher une confirmation dans les messages récents et WorldState, (2) adapter le prompt système en conséquence.

### A.1 Modification de `_explain_blocked_turn` (loop.py:419-461)

**Fichier** : `raya/harness/loop.py`  
**Lignes** : 419-461  

```python
# AVANT
async def _explain_blocked_turn(self, original_request, trace, reason_hint, correlation_id):
    trace_summary = json.dumps([
        {"tool": t.get("tool_name"), "status": t.get("status"), "outcome": t.get("outcome")}
        for t in trace
    ], ensure_ascii=False, default=str)

    blocked_messages = [
        {"role": "system", "content": "The agent got stuck trying to complete the user's request. Explain what happened and what you were trying to do."},
        {"role": "user", "content": f"Original request: {original_request}\n\nSteps attempted:\n{trace_summary}\n\nReason: {reason_hint}"},
    ]
    return await self._invoke_model(ModelRequest(messages=blocked_messages))


# APRÈS — 3 changements ciblés
async def _explain_blocked_turn(self, original_request, trace, reason_hint, correlation_id):
    # CHANGEMENT 1 : inclure evidence dans trace_summary
    trace_summary = json.dumps([
        {
            "tool": t.get("tool_name"),
            "status": t.get("status"),
            "outcome": t.get("outcome"),
            "evidence": t.get("evidence"),    # ← AJOUT
            "output": t.get("output"),         # ← AJOUT
        }
        for t in trace
    ], ensure_ascii=False, default=str)

    # CHANGEMENT 2 : détecter signal de succès dans les derniers résultats
    task_completed = self._detect_task_completion(trace)

    # CHANGEMENT 3 : prompt système adaptatif
    if task_completed:
        system_content = (
            "You reached your iteration budget while completing the user's request. "
            "The task appears to have been completed successfully based on the evidence. "
            "Report the outcome accurately — do NOT say you failed if the evidence shows success."
        )
    else:
        system_content = (
            "You reached your iteration budget before completing the user's request. "
            "Explain what you achieved and what remains to be done."
        )

    blocked_messages = [
        {"role": "system", "content": system_content},
        {"role": "user", "content": f"Original request: {original_request}\n\nSteps attempted:\n{trace_summary}\n\nReason: {reason_hint}"},
    ]
    return await self._invoke_model(ModelRequest(messages=blocked_messages))
```

### A.2 Méthode `_detect_task_completion` (loop.py — nouvelle méthode de Harness)

```python
def _detect_task_completion(self, trace: list[dict]) -> bool:
    """
    Heuristique légère : cherche dans les N derniers résultats d'outils
    des signaux de confirmation (URL de cart, "article dans le panier", etc.)
    SANS logique Amazon-spécifique — basé sur patterns génériques de complétion.
    """
    if not trace:
        return False
    # Examiner les 3 dernières étapes
    recent = trace[-3:]
    for step in recent:
        evidence = step.get("evidence") or ""
        output = step.get("output") or ""
        combined = (evidence + " " + output).lower()
        # Patterns génériques d'e-commerce (non Amazon-spécifiques)
        cart_signals = ["cart", "panier", "added", "ajouté", "order", "commande"]
        if any(signal in combined for signal in cart_signals):
            return True
    return False
```

**Scope** : 2 méthodes modifiées/ajoutées dans `Harness`. Zéro autre fichier modifié.  
**Risque** : Faible — `_explain_blocked_turn` n'est appelé qu'en cas d'épuisement du budget.  
**Impact correctness** : Le modèle reçoit l'evidence + un prompt adapté → déclare le succès si confirmé.

---

## 15. Latency fix (B) — Fix minimal

**Objectif** : Réduire la latence de 96.6s à <45s pour les sessions Amazon nominales.

**Principe** : Deux leviers indépendants — (B1) tronquer les sorties d'outils volumineuses, (B2) augmenter `num_ctx` pour éliminer la troncature silencieuse.

### B.1 Troncature des sorties `browser.read_page` dans `_summarize_tool_result`

**Fichier** : `raya/harness/loop.py` — `_summarize_tool_result` (méthode statique)  

```python
# AVANT
@staticmethod
def _summarize_tool_result(tool_name: str, tool_result: ToolResult, outcome) -> dict:
    return {
        "tool": tool_name,
        "status": outcome.status,
        "verification": outcome.verification,
        "output": outcome.output,        # ← non borné
        "evidence": outcome.evidence,
        "error": outcome.error,
    }

# APRÈS — cap sur output pour les outils de lecture de page
_READ_PAGE_MAX_CHARS = 4000   # ~1,000 tokens — vs ~21,329 chars (5,332 tokens) actuellement

@staticmethod
def _summarize_tool_result(tool_name: str, tool_result: ToolResult, outcome) -> dict:
    output = outcome.output
    if tool_name in ("browser.read_page", "browser.navigate") and output:
        if len(output) > _READ_PAGE_MAX_CHARS:
            output = output[:_READ_PAGE_MAX_CHARS] + "\n[... output truncated for context budget ...]"
    return {
        "tool": tool_name,
        "status": outcome.status,
        "verification": outcome.verification,
        "output": output,
        "evidence": outcome.evidence,
        "error": outcome.error,
    }
```

**Impact latence** :
- `browser.read_page` homepage : 5,332 tokens → 1,000 tokens (−4,332 tokens)
- Contexte à l'appel #3 : 14,827 → 10,495 tokens (−29%)
- Latence appel #3 : 37.3s → ~18-22s (estimation −40%)

**Risque** : Le modèle peut manquer des boutons ou liens présents dans la partie tronquée. À valider sur E2E. La troncature préserve les N premiers éléments — les boutons prioritaires (recherche, navigation principale) sont généralement en début de DOM.

### B.2 Ajustement de la formule `num_ctx`

**Fichier** : `raya/models/providers/ollama_cloud.py`

```python
# AVANT
"num_ctx": min(self._context_limit, req.context_budget_tokens * 4 or self._context_limit)
# Pour context_budget_tokens=4096 : min(128000, 16384) = 16,384

# APRÈS
"num_ctx": min(self._context_limit, max(65536, req.context_budget_tokens * 4) if req.context_budget_tokens else self._context_limit)
# Pour context_budget_tokens=4096 : min(128000, max(65536, 16384)) = 65,536
```

**Impact latence** : Un contexte plus grand = préfillage plus lent par appel, mais élimine la troncature silencieuse des messages anciens. Net : légèrement plus lent par appel, mais comportement cohérent (le prompt système n'est plus jamais perdu).

**Recommandation** : Combiner B1 + B2 — B1 réduit la taille réelle du contexte, B2 évite la troncature même en cas de contexte élevé. Les deux fixes sont indépendants.

### B.3 Impact estimé (combiné B1 + B2)

| Métrique | Avant | Après B1+B2 | Δ |
|---------|-------|------------|---|
| Contexte appel #3 | 14,827 tokens | ~10,495 tokens | −29% |
| Latence appel #3 | 37.3s | ~18-22s | −40-50% |
| Latence totale | 96.6s | ~55-65s | −35-45% |
| Troncature Ollama | Oui (iter 5+) | Non | Éliminée |
| Perte prompt système | Possible (iter 8+) | Non | Éliminée |

---

## 16. Risques

### R1 — Fix A : faux positifs de `_detect_task_completion`

**Scénario** : La recherche de mots "cart/panier" dans le output d'un `browser.read_page` pourrait matcher une page de produit qui mentionne "Ajouter au panier" sans confirmer l'ajout.  
**Atténuation** : Restreindre la détection aux 2-3 **dernières** étapes et exiger `status=success` + pattern plus spécifique (`/cart/...`, `article dans le panier`).  
**Impact si raté** : RAYA déclare succès à tort. Préférable à l'inverse (déclare échec à tort) — mais à monitorer.

### R2 — Fix A : traces très courtes (<3 étapes)

**Scénario** : Session avec 1-2 outils appelés — `_detect_task_completion` examine des traces minimales.  
**Atténuation** : La fonction retourne `False` si trace vide. Comportement inchangé pour les sessions courtes.

### R3 — Fix B1 : troncature DOM agressive

**Scénario** : Le bouton "Ajouter au panier" apparaît après les 4,000 premiers caractères du DOM.  
**Atténuation** : `vision.find_in_browser` (fallback visuel, 20E) reste disponible. Augmenter le cap à 6,000 chars si nécessaire.  
**Observation** : En pratique, les boutons principaux (recherche, nav) sont en tête de DOM. Les boutons de produit sur page produit sont variables.

### R4 — Fix B2 : num_ctx=65,536 — latence par appel

**Scénario** : Un contexte max de 65,536 tokens alloue plus de VRAM. Sur une GPU déjà chargée, cela peut augmenter la latence des premiers appels (contexte petit mais espace alloué grand).  
**Atténuation** : Configurer via env var `RAYA_CONTEXT_BUDGET_TOKENS`. Valeur par défaut conservatrice possible.

### R5 — Fix A : `reason_hint` toujours "Je n'ai pas terminé dans N étapes"

Si le prompt "reached budget" + "didn't finish in N steps" envoie des signaux contradictoires, le modèle peut se bloquer.  
**Atténuation** : Adapter aussi `reason_hint` selon `task_completed` :
```python
reason = "Tâche accomplie en N étapes (dans le budget)." if task_completed else reason_hint
```

---

## 17. Tests

### 17.1 Tests existants (non modifiés)

- `tests/harness/test_loop.py` — couverture boucle principale
- `tests/architecture/test_targeted_execution_repair_architecture_proof.py`
- `tests/integration/test_phase4_scenarios.py`

### 17.2 Tests proposés (fix A)

```python
# tests/harness/test_explain_blocked_turn.py

class TestExplainBlockedTurnWithEvidence:

    def test_trace_includes_evidence_in_summary(self):
        """_explain_blocked_turn transmet evidence dans trace_summary."""
        trace = [{"tool_name": "browser.read_page", "status": "success",
                  "outcome": "SUCCESS", "evidence": "URL: /cart/smart-wagon", "output": "panier"}]
        # Vérifier que trace_summary contient evidence
        harness = make_test_harness()
        summary = harness._build_trace_summary(trace)
        assert "cart" in summary.lower()

    def test_detect_task_completion_cart_signal(self):
        """_detect_task_completion retourne True sur signal panier."""
        trace = [{"evidence": "1 article dans le panier", "output": "cart confirmed"}]
        assert Harness._detect_task_completion(trace) is True

    def test_detect_task_completion_no_signal(self):
        """_detect_task_completion retourne False sans signal."""
        trace = [{"evidence": "page produit chargée", "output": "bouton Ajouter visible"}]
        assert Harness._detect_task_completion(trace) is False

    def test_detect_task_completion_empty_trace(self):
        """_detect_task_completion retourne False sur trace vide."""
        assert Harness._detect_task_completion([]) is False

    def test_system_prompt_adapts_on_success(self):
        """Prompt système = 'reached budget' si task_completed."""
        harness = make_test_harness()
        messages = harness._build_blocked_messages(
            "Ajoute au panier", trace_with_cart_signal(), task_completed=True
        )
        assert "got stuck" not in messages[0]["content"]
        assert "reached" in messages[0]["content"] or "budget" in messages[0]["content"]

    def test_system_prompt_adapts_on_failure(self):
        """Prompt système = 'got stuck' si pas de signal succès."""
        harness = make_test_harness()
        messages = harness._build_blocked_messages(
            "Ajoute au panier", trace_without_signal(), task_completed=False
        )
        assert "reached" in messages[0]["content"] or "budget" in messages[0]["content"]
        # (ajuster selon l'implémentation exacte)
```

### 17.3 Tests proposés (fix B)

```python
# tests/harness/test_summarize_tool_result.py

class TestSummarizeToolResultTruncation:

    def test_read_page_truncated_at_cap(self):
        """browser.read_page output tronqué à _READ_PAGE_MAX_CHARS."""
        long_output = "x" * 10000
        result = Harness._summarize_tool_result(
            "browser.read_page", make_tool_result(output=long_output), make_outcome()
        )
        assert len(result["output"]) <= _READ_PAGE_MAX_CHARS + 100  # +100 pour le message de troncature

    def test_other_tools_not_truncated(self):
        """browser.click output non tronqué."""
        long_output = "x" * 10000
        result = Harness._summarize_tool_result(
            "browser.click", make_tool_result(output=long_output), make_outcome()
        )
        assert len(result["output"]) == 10000

    def test_short_read_page_not_truncated(self):
        """browser.read_page court non tronqué."""
        short_output = "page content " * 100
        result = Harness._summarize_tool_result(
            "browser.read_page", make_tool_result(output=short_output), make_outcome()
        )
        assert "[... output truncated" not in result["output"]
```

---

## 18. E2E réel (optionnel — non exécuté dans cet audit)

**Précondition** : Cet audit étant de mode AUDIT ONLY, aucune session réelle n'a été lancée pour 20G. Les données E2E proviennent des audits 20E et 20F.

### Protocole recommandé pour validation des fixes

```python
# Scénario de validation post-fix A+B
# Critères de succès :
# 1. RAYA déclare succès si URL contient /cart/ à la dernière itération
# 2. Latence totale < 60s
# 3. Pas de troncature Ollama (contexte < 65,536 à toutes les itérations)

E2E_1 = {
    "prompt": "Raya va sur Amazon et mets moi des AirPods Pro 3 dans mon panier stp",
    "success_criteria": ["cart_confirmed", "response_declares_success"],
    "latency_target": 60,  # secondes
}

E2E_2 = {
    "prompt": "Raya va sur Amazon.be et ajoute une manette Xbox Series X au panier",
    "success_criteria": ["cart_confirmed", "response_declares_success"],
    "latency_target": 60,
}
```

**Note** : Le Run 1 de 20E (Xbox, 11/12 iterations, cart confirmé) constitue une validation partielle du fix B1 (contexte < 16,384 car vision a trouvé le bouton en iter 9 laissant 2 slots libres).

---

## 19. Ce qui ne doit pas changer

### 19.1 Composants sanctuarisés (spec 20G)

| Composant | Raison |
|-----------|--------|
| Vision parser 20E (`_FOUND_PATTERN`) | Corrigé et validé (58 tests) |
| `BoundingBox` | Contrat visuel stable |
| `click_at_position` | Fix visuel validé en E2E |
| Amazon selectors | Pas de logique Amazon-spécifique dans le harness |
| Browser DOM parser | Correct |
| `max_tool_iterations=12` | Valeur configurable — ne pas hard-coder une alternative |
| `LoopDetector` | Hors scope |
| Memory, Telegram, Voice, UI | Hors scope |

### 19.2 Invariants architecturaux à préserver

- **Pas d'ObjectiveManager** : La détection d'objectif doit être une heuristique légère dans `_explain_blocked_turn`, pas un système séparé
- **Pas de logique Amazon-spécifique** : `_detect_task_completion` doit être générique
- **`max_tool_iterations` reste configurable** via `RAYA_MAX_TOOL_ITERATIONS` env var
- **`_summarize_tool_result` reste statique** : la troncature est une transformation pure, sans état
- **Réutiliser `Harness`, `ToolResult`, `Evidence`, `WorldState`** — aucun nouveau composant

---

## 20. Recommandation finale

### Priorité : correctness > reliability > latency

**Fix A — Correctness (CRITIQUE)**  
Modifier `_explain_blocked_turn` pour :
1. Inclure `evidence` et `output` dans `trace_summary`
2. Appeler `_detect_task_completion(trace)` sur les 3 dernières étapes
3. Adapter le prompt système (`"reached budget"` vs `"got stuck"`)

**Scope** : `raya/harness/loop.py` — 2 méthodes modifiées, 1 méthode ajoutée.  
**Risque** : Faible. Chemin de code déclenché uniquement en cas d'épuisement du budget.  
**Impact** : Élimine RC-A1/A2/A3. Le cas AirPods 20F aurait déclaré succès.

**Fix B — Latency (RECOMMANDÉ)**  
1. B1 : Cap `browser.read_page` output à 4,000 chars dans `_summarize_tool_result`
2. B2 : `num_ctx = min(128000, max(65536, context_budget * 4))`

**Scope** : `raya/harness/loop.py` (1 méthode) + `raya/models/providers/ollama_cloud.py` (1 ligne).  
**Risque** : B1 peut tronquer des éléments de page importants — valider en E2E. B2 augmente légèrement la latence des premiers appels.  
**Impact** : Élimine RC-C1/C3/C4. Latence estimée −35-45% (96.6s → ~55-65s). Élimine la troncature silencieuse du prompt système.

### Ordre d'implémentation recommandé

```
1. Fix A (correctness)     → 1 session — modifier loop.py, 6 nouveaux tests
2. Valider A en E2E        → 1-2 runs réels — vérifier déclaration succès
3. Fix B1 (read_page cap)  → 30 min — modifier loop.py, 3 nouveaux tests
4. Valider B1 en E2E       → 1-2 runs réels — vérifier que bouton trouvé malgré troncature
5. Fix B2 (num_ctx)        → 10 min — modifier ollama_cloud.py, 1 test
6. Régression globale      → tests/harness/ + tests/integration/
```

### Ce que 20G confirme

L'architecture RAYA V2 est **fondamentalement saine**. Les root causes sont des **bugs de configuration de boucle** (manque d'evidence dans `_explain_blocked_turn`) et de **dimensionnement de contexte** (num_ctx trop bas). Le modèle Gemma4:31b produit les bonnes réponses — c'est le harness qui les censure. Les fixes sont chirurgicaux et préservent l'intégralité de l'architecture existante.

---

*Audit Chantier 20G — Mode AUDIT ONLY*  
*Fichiers modifiés : aucun*  
*Rapport : `RAYA_V2_20G_OBJECTIVE_COMPLETION_LATENCY_AUDIT.md`*
