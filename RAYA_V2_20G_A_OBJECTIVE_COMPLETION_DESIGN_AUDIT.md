# RAYA V2 — Chantier 20G-A : Objective Completion / Design Audit

**Date** : 2026-09-16  
**Mode** : AUDIT ONLY — AUCUN CODE MODIFIÉ  
**Scope** : Architecture decision — finalization semantics, evidence propagation, context reduction, num_ctx  
**Précédent** : `RAYA_V2_20G_OBJECTIVE_COMPLETION_LATENCY_AUDIT.md`

---

## 1. Executive Summary

Le chantier 20G a confirmé deux problèmes distincts. Cet audit détermine l'architecture correcte avant toute implémentation.

**Problème 1 — Objective completion** : Le harness alloue 12 slots à des itérations d'outils. Le 13ème appel modèle est inconditionnellement `_explain_blocked_turn`, qui reçoit un prompt "got stuck" et une trace sans evidence. Le modèle produit un rapport d'échec même si la dernière action était une confirmation de succès.

**Problème 2 — Latence/contexte** : `num_ctx=16,384` est saturé dès l'appel #3 sur Amazon. La vraie cause n'est pas `num_ctx` trop bas — c'est la sortie de `browser.read_page` qui inclut jusqu'à 120 boutons et 60 liens dont une large fraction est inutile pour l'action en cours.

**Conclusion architecturale principale** :

La sémantique correcte de `max_tool_iterations` est l'option **B** : budget d'itérations d'outils, avec un dernier appel de finalisation garanti et sans outils disponibles. Ce dernier appel reçoit evidence + résultats récents et produit une réponse honnête. Le mécanisme existe déjà (`_invoke_model` sans `available_tools`). Il s'agit d'un ajout d'une méthode `_finalize_turn` dans `Harness`, sans nouveau composant.

**Verdict** : **GO IMPLEMENTATION** sous conditions (§17)

---

## 2. Sémantiques actuelles

### 2.1 `max_tool_iterations` — sémantique actuelle

```python
# loop.py:501-682
for _iteration in range(self._max_tool_iterations):   # range(12)
    model_response = self._invoke_model(...)
    if not model_response.tool_calls_requested:
        return text_response                          # SUCCÈS
    # ... exécution outils ...

# APRÈS range(12) — INCONDITIONNEL :
return self._explain_blocked_turn(...)
```

**Sémantique actuelle** : `max_tool_iterations` = nombre maximum d'appels modèle DEMANDANT des outils, y compris le dernier appel de réponse finale. L'appel modèle #13 n'est pas dans `range(12)` — il est consommé par `_explain_blocked_turn`.

**Conséquence** : Le modèle a besoin de N appels avec outils **+ 1 appel texte sans outil**. Pour Amazon nominal (9-11 appels outils), le budget de 12 laisse 1-3 slots libres. Avec des aléas DOM (+1 à +3), ce dernier slot disparaît.

### 2.2 `_explain_blocked_turn` — deux rôles actuels confondus

```python
# Appelé pour deux raisons distinctes :

# Raison 1 : VRAI BLOCAGE (LoopDetector.ESCALATE)
if recovery_action == RecoveryAction.ESCALATE:
    escalation_text = self._explain_blocked_turn(...)  # ligne 634, 662

# Raison 2 : BUDGET ÉPUISÉ (fin de range())
return self._explain_blocked_turn(...)  # ligne 677 — inconditionnel
```

Ces deux raisons sont sémantiquement différentes :
- **Vrai blocage** : le modèle tourne en boucle sur des échecs — escalade justifiée, signal "got stuck" correct
- **Budget épuisé** : le modèle a progressé normalement mais manque du slot de réponse — signal "got stuck" INCORRECT

### 2.3 Ce que contient `trace` à l'appel #13

```python
trace.append({
    "tool_name": requested.tool_name,
    "arguments": requested.arguments,
    "status": tool_result.status.value,
    "outcome": outcome.value,
    "evidence": tool_result.evidence,    # ← PRÉSENT ici
})
```

L'evidence EST dans `trace`. Mais `_explain_blocked_turn` la supprime :

```python
trace_summary = json.dumps([
    {"tool": t.get("tool_name"), "status": t.get("status"), "outcome": t.get("outcome")}
    # ← "evidence" absent
], ...)
```

**L'information n'est pas perdue dans l'architecture — elle est délibérément filtrée par `_explain_blocked_turn`.**

---

## 3. Alternatives de sémantique pour `max_tool_iterations`

### Option A — Budget de model calls total (sémantique actuelle)
```
max=12 : range(12) calls avec outils possibles
         → dernier call (13e) = _explain_blocked_turn
```
- Slot de réponse finale non garanti
- 12 itérations outils → zéro slot réponse
- **REJETÉ** : comportement actuel démontré défaillant

### Option B — Budget d'itérations d'outils + finalisation garantie
```
max=12 : range(12) calls avec outils
         → call #13 = finalisation SANS outils (model_request sans available_tools)
```
- Slot de réponse finale TOUJOURS garanti
- Le modèle reçoit evidence, ne peut pas appeler d'outils, produit sa réponse
- Compatible avec les contrats actuels : `ModelRequest(available_tools=None)`
- **RECOMMANDÉ**

### Option C — Budget adaptatif basé sur l'état
```
boucle continue jusqu'à signal structurel de complétion
```
- Nécessite un ObjectiveManager ou un signal structurel des outils
- Risque de boucle infinie
- Viole la contrainte "pas d'ObjectiveManager"
- **REJETÉ**

### Analyse de l'option B

`_invoke_model` avec `available_tools=None` produit un appel modèle où Ollama n'expose aucune fonction. Le modèle **ne peut pas** retourner `tool_calls` — son `finish_reason` sera toujours `COMPLETED` ou `ERROR`, jamais `TOOL_CALL_PENDING`. C'est une contrainte structurelle, pas une instruction textuelle.

```python
# Finalization turn — comportement garanti :
response = self._invoke_model(ModelRequest(
    messages=finalization_messages,
    available_tools=None,              # ← outils structurellement absents
    context_budget_tokens=context_budget,
))
# response.tool_calls_requested = None — toujours
# response.finish_reason = COMPLETED ou ERROR — jamais TOOL_CALL_PENDING
```

---

## 4. Design de la finalisation

### 4.1 Principe

La `_finalize_turn` reçoit :
1. **Requête originale** — ce que l'utilisateur a demandé
2. **Derniers N résultats d'outils avec evidence** — les 3-5 derniers entrées de `trace`, fields complets
3. **WorldState pertinent** — faits du domaine `browser` (current_url, last_clicked_target)
4. **Contrainte structurelle** — `available_tools=None` → le modèle ne peut pas demander d'autres outils

La `_finalize_turn` NE reçoit PAS :
- Un prompt "got stuck"
- Un prompt "you failed"
- Une heuristique lexicale pour détecter le succès

### 4.2 Prompt de finalisation

```python
# Prompt système adapté — jamais "got stuck"
FINALIZATION_SYSTEM = (
    "You have reached the end of your action budget while working on the user's request. "
    "Review the tool call trace and evidence below, and report to the user HONESTLY: "
    "(1) what was actually accomplished (based only on the evidence), "
    "(2) what may still be pending, if anything. "
    "Do NOT say you failed if the evidence shows success. "
    "Do NOT claim success without evidence. "
    "Reply in the user's language. Never mention internal tools, JSON fields, or technical details."
)
```

### 4.3 Construction du contexte de finalisation

```python
# Extraire les N derniers résultats pertinents (jamais toute la trace)
recent_trace = trace[-5:]
trace_with_evidence = json.dumps([
    {
        "tool": t.get("tool_name"),
        "status": t.get("status"),
        "outcome": t.get("outcome"),
        "evidence": t.get("evidence"),    # ← INCLUS
        "output": t.get("output"),         # ← INCLUS si présent dans trace
    }
    for t in recent_trace
], ensure_ascii=False, default=str)

# WorldState browser — derniers faits pertinents
browser_facts = self._world_state.retrieve_relevant(("browser",))
ws_summary = {f.key: f.value for f in browser_facts if f.key in ("current_url", "last_clicked_target")}
```

### 4.4 Compatibilité avec les contrats actuels

| Contrat | Compatible ? | Note |
|---------|-------------|------|
| `ModelRequest(available_tools=None)` | ✓ | Chemin déjà utilisé par `_explain_blocked_turn` |
| `trace` contient `evidence` | ✓ | Ligne 624 de loop.py — déjà présent |
| `VerificationOutcome` | ✓ | Pas modifié |
| `HarnessState` | ✓ | Pas modifié |
| Retour `str` de `_run_agentic_loop` | ✓ | `_finalize_turn` retourne `str` |

---

## 5. Propagation des evidence

### 5.1 Ce qui existe déjà dans `trace`

```python
# loop.py:622-624 — déjà présent pour CHAQUE appel d'outil
trace.append({
    "tool_name": requested.tool_name,
    "arguments": requested.arguments,
    "status": tool_result.status.value,
    "outcome": outcome.value,
    "evidence": tool_result.evidence,    # ← exemple: {"url": "/cart/smart-wagon", ...}
})
```

Chaque entrée de `trace` contient l'evidence réelle de l'outil. **Aucun nouveau mécanisme de collecte n'est nécessaire.**

### 5.2 Ce qui manque dans le chemin actuel

`_explain_blocked_turn` reconstruit son propre `trace_summary` en filtrant délibérément les champs `evidence` et `output`. Cette reconstruction est l'unique source du problème.

### 5.3 Résolution minimale

Supprimer le filtrage dans la construction du résumé de trace pour `_finalize_turn` :

```python
# AVANT (_explain_blocked_turn) :
trace_summary = json.dumps([
    {"tool": t.get("tool_name"), "status": t.get("status"), "outcome": t.get("outcome")}
    for t in trace
], ...)

# APRÈS (_finalize_turn) :
trace_summary = json.dumps([
    {"tool": t.get("tool_name"), "status": t.get("status"),
     "outcome": t.get("outcome"), "evidence": t.get("evidence")}
    for t in trace[-5:]    # les 5 derniers — contexte borné
], ...)
```

### 5.4 WorldState — apport additionnel

`browser.current_url` est mis à jour par `browser.navigate` et `browser.click` (via `_CURRENT_URL_OBSERVATION` et `_LAST_CLICKED_OBSERVATION`). Il n'est PAS mis à jour par `browser.read_page` ni `browser.click_at_position`.

Pour Amazon : après `click_at_position` → `read_page` (URL cart), WorldState `browser.current_url` contient l'URL navigée antérieurement mais PAS l'URL du cart. Le cart URL est uniquement dans `trace[-1].evidence` (depuis le `read_page` cart).

**Concluxion** : Pour la finalisation, `trace[-1:5].evidence` est plus fiable que WorldState pour les confirmations récentes. Les deux sont complémentaires — inclure les deux dans la finalisation.

---

## 6. Options de redesign de `_explain_blocked_turn`

### Option 1 — `_explain_blocked_turn` conservée pour vrai blocage uniquement

```python
# _explain_blocked_turn : sémantique inchangée, appelée UNIQUEMENT par LoopDetector.ESCALATE
# Nouveau : _finalize_turn pour la fin de range()

# Dans _run_agentic_loop :
if recovery_action == RecoveryAction.ESCALATE:
    return self._explain_blocked_turn(...)   # vrai blocage → "got stuck" approprié

# Après range(12) :
return self._finalize_turn(...)             # budget épuisé → honnêteté basée sur evidence
```

### Option 2 — `_explain_blocked_turn` devient générique

Modifier `_explain_blocked_turn` pour accepter un paramètre `reason_type` (BLOCKED vs BUDGET_EXHAUSTED) et adapter son prompt.

Problème : un paramètre `reason_type` bool/enum est une mauvaise architecture — deux chemins sémantiquement distincts dans la même fonction avec conditionnelle. Moins lisible, moins testable.

### Option 3 — Séparation claire (RECOMMANDÉ)

```python
# Deux méthodes, deux sémantiques :
# _explain_blocked_turn(trace) : ESCALATE — vrai blocage, "got stuck", trace sans evidence ok
# _finalize_turn(trace)        : BUDGET — fin de budget, evidence incluse, prompt neutre

# _run_agentic_loop :
# ESCALATE → _explain_blocked_turn (inchangé)
# range() fin → _finalize_turn (nouveau)
```

**Avantages** :
- `_explain_blocked_turn` reste inchangée — aucun risque de régression sur les 3 points d'escalade existants
- `_finalize_turn` est une nouvelle méthode, testable indépendamment
- Chaque méthode a une sémantique claire et un seul rôle

**Invariant préservé** : BUDGET EXHAUSTED ≠ TASK FAILED. L'une appelle `_finalize_turn`, l'autre appelle `_explain_blocked_turn`.

---

## 7. Analyse des faux positifs

### 7.1 Heuristique lexicale (écartée)

La proposition 20G d'une heuristique `cart/panier/added/order/commande` présente des faux positifs documentés :

| Scénario | Evidence contient | Résultat heuristique | Résultat correct |
|----------|------------------|---------------------|-----------------|
| Page produit avec "Ajouter au panier" visible | "bouton Ajouter au panier" | **FAUX POSITIF** | pas encore ajouté |
| Page résultats Amazon avec ads "Add to Cart" | "add to cart" | **FAUX POSITIF** | sur page de liste |
| Page panier confirmée, URL /cart/smart-wagon | "1 article dans le panier" | TRUE POSITIVE | ajouté |
| Recherche "paniers en osier" | "panier" | **FAUX POSITIF** | hors scope |

**Conclusion** : L'heuristique lexicale produit des faux positifs sur des cas courants. Elle est **écartée** conformément à la spec.

### 7.2 Approche evidence-based (retenue)

La `_finalize_turn` transmet l'evidence structurée au modèle et **lui laisse le soin de déterminer si l'objectif est atteint**. Le modèle LLM (Gemma4:31b) dispose des capacités de raisonnement pour distinguer :
- `evidence: {"url": "/cart/smart-wagon?newItems=..."}` → panier confirmé
- `evidence: {"url": "/s?k=AirPods", "buttons": [...]}` → page de résultats, pas encore ajouté

**Faux positifs de l'approche evidence-based** :
- **Faux positif** : Amazon retourne une page panier avec un article déjà présent (pas celui demandé) → modèle peut confondre. ATTENUATION : l'evidence inclut le titre du produit dans le `output` de `read_page`.
- **Faux négatif** : Evidence absente (click_at_position a `observation=()`, pas de WorldState update) → modèle voit `evidence: null` pour l'appel click et doit inférer depuis le `read_page` suivant. ATTENUATION : inclure les 5 derniers appels dans la trace, pas seulement le dernier.

### 7.3 Taux d'erreur comparé

| Méthode | Faux positifs | Faux négatifs | Complexité |
|---------|-------------|--------------|-----------|
| Lexical (cart/panier) | ÉLEVÉS | faibles | Faible |
| Evidence-based (trace[-5]) | Faibles | Rares | Nul — model fait le travail |
| Signal structurel du tool | Nuls | Nuls | Fort — nécessite contrats modifiés |
| Cognition layer | Faibles | Faibles | Fort — nouveau composant |

**Evidence-based est le meilleur compromis** : fiabilité haute, complexité nulle, compatible avec les contrats actuels.

---

## 8. Analyse du contexte

### 8.1 Structure exacte de `browser.read_page`

Depuis `_STRUCT_JS` (controller.py:73-152), le retour de `read_page` contient :

```javascript
{
    url: string,           // ~50 chars — CRITIQUE pour toute navigation
    title: string,         // ~50 chars — UTILE pour identification page
    cookie_banner: bool,   // ~25 chars — UTILE pour dismiss_overlay
    buttons: [{            // jusqu'à 120 items
        kind: "button",
        text: string,      // max 90 chars (sliced at 90)
        tag: string,       // "button" ou "a" ou "input"
        left: int,         // coordonnée viewport
        top: int,          // coordonnée viewport
        aria_label?: str,
    }],
    links: [{              // jusqu'à 60 items
        kind: "link",
        text: string,      // max 90 chars
        tag: "a",
        left: int,
        top: int,
    }],
    inputs: [{             // jusqu'à 20 items
        kind: "input",
        text: string,
        placeholder?: str,
        role?: str,
        input_type?: str,
        left: int, top: int,
    }]
}
```

### 8.2 Ordre de priorité dans `_STRUCT_JS`

**Boutons — tri par priorité explicite** (controller.py:116-131) :

```javascript
// PRIORITÉ 1 : boutons dans les buybox/add-to-cart roots
const priorityRoots = [...document.querySelectorAll(
    '[id*=buybox i], [id*=addtocart i], [id*=add-to-cart i], [class*=buybox i]'
)];
// Extraits en premier → placés au début du tableau buttons[]

// PRIORITÉ 2 : tous les autres boutons (filtrés avec seen set)
const buttons = priorityButtons.concat(
    grab(BTN_SEL, 'button', 100).filter(...)
).slice(0, 120);
```

**Conséquence critique** : Les boutons "Ajouter au panier" sont **toujours en tête** du tableau `buttons[]` sur une page produit Amazon. Ils ne seront jamais tronqués si on cap à ≥ 5 boutons.

**Liens** : pas de priorité explicite — extraits dans l'ordre DOM. Sur la homepage Amazon, les 60 premiers liens sont navigation principale + catégories. Peu utiles pour une tâche d'achat ciblée.

**Inputs** : maximum 20, incluent la barre de recherche. Critiques pour les pages avec formulaire. Volume faible (~40 tokens total).

### 8.3 Éléments nécessaires vs superflus

| Élément | Nécessaire pour | Volume sur homepage | Action |
|---------|----------------|--------------------|----|
| `url` | Navigation/vérification | ~12 tokens | GARDER |
| `title` | Identification page | ~8 tokens | GARDER |
| `cookie_banner` | Gestion overlay | ~6 tokens | GARDER |
| `inputs` (20 max) | Saisie recherche | ~40 tokens | GARDER |
| `buttons[0:15]` | Add-to-cart, nav principale | ~180 tokens | GARDER |
| `buttons[15:120]` | Nav secondaire, ads, footer | ~1,260 tokens | **TRONQUER** |
| `links[0:20]` | Catégories, navigation | ~200 tokens | GARDER |
| `links[20:60]` | Liens répétés, footer | ~400 tokens | **TRONQUER** |

**Réduction estimée** : de 5,332 tokens (120 btns + 60 links) à ~446 tokens (15 btns + 20 links + url/title/inputs).

### 8.4 Approche de réduction — dans `_summarize_tool_result` vs dans `_STRUCT_JS`

**Option 1 : Cap dans `_summarize_tool_result` (harness)**
- Tronque la sortie JSON dans le résumé envoyé au modèle
- La sortie complète reste dans `tool_result.output`
- Risque : le modèle perd des boutons présents après le cap
- Avantage : aucun changement au Device Agent

**Option 2 : Cap dans `_STRUCT_JS` ou `BrowserController.read_page`**
- La donnée n'est jamais collectée
- Serait un changement dans `raya/devices/browser/controller.py`
- Hors scope de la modification minimale

**Option 3 : Cap structurel dans `_summarize_tool_result` basé sur les clés JSON** (RECOMMANDÉ)

```python
# Pas output[:4000] — mais un cap structurel sur les arrays
# Préserve les éléments prioritaires (buybox buttons sont déjà en tête)

def _cap_read_page_output(output: dict | str | None) -> str | None:
    """Cap structurel : préserve url/title/inputs/cookie_banner intégralement,
    tronque buttons et links après les N premiers."""
    if not isinstance(output, (dict, str)):
        return output
    if isinstance(output, str):
        # tenter un parse JSON pour cap structurel
        try:
            data = json.loads(output)
        except (json.JSONDecodeError, ValueError):
            return output[:6000] + "[truncated]" if len(output) > 6000 else output
    else:
        data = output
    
    _BUTTONS_CAP = 20     # les 20 premiers boutons — buybox buttons en tête
    _LINKS_CAP = 25       # les 25 premiers liens
    
    capped = {
        "url": data.get("url"),
        "title": data.get("title"),
        "cookie_banner": data.get("cookie_banner"),
        "inputs": data.get("inputs", []),
        "buttons": (data.get("buttons") or [])[:_BUTTONS_CAP],
        "links": (data.get("links") or [])[:_LINKS_CAP],
    }
    if len(data.get("buttons") or []) > _BUTTONS_CAP:
        capped["buttons_truncated"] = len(data.get("buttons", [])) - _BUTTONS_CAP
    return json.dumps(capped, ensure_ascii=False)
```

**Avantage** : Préserve toujours les `_BUTTONS_CAP` premiers boutons (buybox/add-to-cart en tête). Aucune perte d'information critique pour l'add-to-cart.

### 8.5 Éléments déjà connus / dupliqués

- `url` après `browser.navigate` est déjà dans WorldState (`browser.current_url`). Incluse dans `read_page` pour confirmation — acceptable (12 tokens).
- `title` change à chaque page — inclure.
- `buttons[]` sur la homepage : ~70 boutons de navigation répétitive (catégories, publicités, footer). Le modèle navigue vers une URL cible et n'a pas besoin de tous les boutons de navigation une fois qu'il a localisé la barre de recherche ou le bouton add-to-cart.

---

## 9. Analyse de la latence

### 9.1 Appels modèle réellement nécessaires pour Amazon

**Parcours minimal théorique** :
```
1. navigate(amazon.fr)
2. read_page → identifier champ recherche + inputs
3. type(recherche + submit=True)
4. read_page → identifier produit dans résultats
5. click(produit)
6. read_page → page produit, identifier bouton add-to-cart
7. click(add-to-cart) — ou vision.find + click_at_position si DOM échoue
8. read_page → vérification URL cart
```
= **8 appels modèle avec outils**, puis 1 appel de réponse finale = **9 appels total**

Avec aléas :
- Cookie banner : +1 (`dismiss_overlay`)
- Échec DOM click : +1-2 (fallback visuel)
- Re-navigation : +1
= **9-12 appels total dans le cas nominal avec aléas**

### 9.2 Étapes redondantes identifiées

| Étape | Redondance | Économie possible |
|-------|-----------|------------------|
| `navigate` + `read_page` systématique | `navigate` ne retourne pas le DOM — obligatoire | Non |
| `read_page` homepage (5,332 tokens) | Boutons 15-120 inutiles pour recherche | Oui — cap à 20 |
| `read_page` résultats (3,200 tokens) | Liens navigation répétitifs | Oui — cap à 25 |
| `read_page` produit (3,200 tokens) | Navigation répétitive — boutons buybox CRITIQUES | Cap prudent à 25 |
| `available_tools` à chaque itération | 1,750 tokens × 12 appels = 21,000 tokens envoyés | Non modifiable sans refactor |

### 9.3 Gain réaliste avec cap structurel (8.4)

| Itération | Tokens avant cap | Tokens après cap | Économie |
|-----------|-----------------|-----------------|---------|
| read_page homepage | 5,332 | ~500 | −4,832 |
| read_page résultats | 3,200 | ~600 | −2,600 |
| read_page produit | 3,200 | ~650 | −2,550 |
| read_page cart | ~800 | ~800 | 0 (page simple) |

**Réduction du contexte à l'appel #3** :
- Avant : 7,475 (sysprompt) + 1,750 (tools) + 5,332 (homepage) + 200 (navigate) = **14,757 tokens**
- Après : 7,475 + 1,750 + 500 + 200 = **9,925 tokens** (67% de la limite actuelle)
- Latence appel #3 : 37.3s → ~12-15s (estimation −60%)

**Total session** :
- Avant : ~96.6s
- Après : ~40-50s (estimation)

### 9.4 Priorité confirmée

```
CORRECTNESS > RELIABILITY > LATENCY
```

Le cap read_page améliore la latence et n'impacte pas la correctness si les boutons prioritaires (buybox, add-to-cart) sont préservés en tête. L'analyse de `_STRUCT_JS` confirme que c'est le cas.

---

## 10. Analyse de `num_ctx`

### 10.1 Formule actuelle

```python
# ollama_cloud.py:185
"num_ctx": min(self._context_limit, req.context_budget_tokens * 4 or self._context_limit)
# Pour context_budget_tokens=4096 : min(128000, 16384) = 16,384
```

### 10.2 La vraie question — deux problèmes distincts

**Problème A : contexte réel > num_ctx → troncature silencieuse**
Cause : context_budget_tokens×4 = 16,384 < contexte réel (~14,800-24,000 tokens)
Solution : augmenter num_ctx OU réduire le contexte

**Problème B : contexte réel élevé → latence de préfillage élevée**
Cause : 14,800 tokens prend 37.3s sur Ollama
Solution : réduire le contexte réel

Ces deux problèmes sont liés mais distincts. Augmenter `num_ctx` seul résout A mais aggrave B (plus de contexte à préfiller). La vraie solution est **réduire le contexte réel** (fix read_page), et utiliser `num_ctx` comme garde-fou de sécurité.

### 10.3 Impact de `num_ctx=65536` sans cap read_page

| Itération | Contexte réel | num_ctx=16384 | num_ctx=65536 |
|-----------|--------------|---------------|---------------|
| Appel #3 | 14,827 tokens | OK (90%) | OK (23%) |
| Appel #5 | ~19,000 tokens | TRONQUÉ | OK (29%) |
| Appel #8 | ~24,000 tokens | TRONQUÉ | OK (37%) |
| Appel #12 | ~30,000 tokens | FORTEMENT TRONQUÉ | OK (46%) |
| Latence appel #3 | 37.3s | — | ~40s (pire, contexte max plus grand) |

**Conclusion** : `num_ctx=65536` élimine la troncature silencieuse mais n'améliore pas la latence (légèrement pire si Ollama alloue de la VRAM pour la fenêtre complète dès le départ).

### 10.4 Recommandation pour `num_ctx`

**Avec cap read_page** : contexte max réel ≈ 9,925 tokens (appel #3). La formule actuelle (`num_ctx=16,384`) est alors suffisante avec marge de sécurité (9,925 / 16,384 = 60.6%).

**Sans cap read_page** (si fix B1 non implémenté) : `num_ctx=32768` minimum pour éviter la troncature dès l'appel #5. `num_ctx=65536` pour sécurité totale.

**Formule recommandée** (avec cap read_page implémenté) :

```python
# La formule actuelle devient suffisante :
"num_ctx": min(self._context_limit, req.context_budget_tokens * 4 or self._context_limit)
# = 16,384 pour budget=4096 — suffisant si read_page est cappé

# Si le cap read_page n'est PAS implémenté simultanément :
"num_ctx": min(self._context_limit, max(32768, req.context_budget_tokens * 4) if req.context_budget_tokens else self._context_limit)
```

**Il ne faut pas résoudre le vrai problème (contexte trop large) en augmentant num_ctx.**

---

## 11. Architecture minimale recommandée

### 11.1 Schéma de la modification

```
Avant :
┌──────────────────────────────────────────────────────────────────┐
│ range(12)                                                        │
│   iteration N : invoke_model(tools=available) → execute_tool    │
│   iteration 12 : invoke_model(tools=available) → execute_tool   │
│ fin range() → _explain_blocked_turn()  ← MÊME fonction que ESCALATE
└──────────────────────────────────────────────────────────────────┘

Après :
┌──────────────────────────────────────────────────────────────────┐
│ range(12)                                                        │
│   iteration N : invoke_model(tools=available) → execute_tool    │
│   ESCALATE → _explain_blocked_turn()  ← INCHANGÉ               │
│ fin range() → _finalize_turn(trace, request)  ← NOUVEAU        │
│   → invoke_model(tools=None, messages=finalization_messages)    │
│   → retour texte basé sur evidence                              │
└──────────────────────────────────────────────────────────────────┘
```

### 11.2 Nouvelle méthode `_finalize_turn`

```python
def _finalize_turn(self, objective_text: str, trace: list[dict], 
                   correlation_id: str) -> str:
    """Budget d'itérations épuisé — NE PAS confondre avec _explain_blocked_turn
    (vrai blocage détecté par LoopDetector). Ici le modèle a travaillé
    normalement jusqu'à la fin de son budget. Il reçoit les derniers
    résultats avec evidence complète et produit une réponse honnête.
    
    JAMAIS available_tools : le modèle ne peut structurellement pas demander
    un 13e appel d'outil — sa réponse sera text-only, toujours."""
    recent_trace = trace[-5:]
    trace_with_evidence = json.dumps([
        {
            "tool": t.get("tool_name"),
            "status": t.get("status"),
            "outcome": t.get("outcome"),
            "evidence": t.get("evidence"),
        }
        for t in recent_trace
    ], ensure_ascii=False, default=str)
    
    browser_facts = self._world_state.retrieve_relevant(("browser",))
    ws_summary = {f.key: f.value for f in browser_facts
                  if f.key in ("current_url", "last_clicked_target")}
    
    messages = [
        Message(role="system", content=[ContentPart(type="text", value=(
            "You have used your full action budget working on the user's request. "
            "Review the last tool results and evidence below. Report to the user "
            "HONESTLY what was accomplished based ONLY on the evidence shown. "
            "If evidence shows success, report it as success. If evidence is "
            "absent or inconclusive, say so honestly. Never claim success without "
            "evidence. Never claim failure if evidence shows success. "
            "Reply in the user's language. Do not mention tool names or JSON."
        ))]),
        Message(role="user", content=[ContentPart(type="text", value=(
            f"User request: {objective_text}\n"
            f"Last actions and evidence:\n{trace_with_evidence}\n"
            f"Current browser state: {json.dumps(ws_summary, ensure_ascii=False)}"
        ))]),
    ]
    response = self._invoke_model(ModelRequest(
        capability=ModelCapability.REASONING,
        messages=messages,
        correlation_id=correlation_id,
        available_tools=None,              # ← structurellement sans outils
        context_budget_tokens=self._context_budget_tokens,
    ))
    if response.finish_reason != FinishReason.ERROR:
        text = "".join(p.value for p in response.content if p.type == "text").strip()
        if text:
            return text
    attempted = ", ".join(sorted({t.get("tool_name", "?") for t in trace})) or "aucune action"
    return f"J'ai atteint ma limite d'actions ({len(trace)} tentée(s) : {attempted}). Je préfère le signaler honnêtement."
```

### 11.3 Modification dans `_run_agentic_loop`

```python
# LIGNE UNIQUE À CHANGER — fin de la boucle (loop.py:676-682)

# AVANT :
self._last_tool_trace[request.session_id] = trace
return self._explain_blocked_turn(
    request.input.text or "", trace,
    f"Je n'ai pas terminé cette demande dans les {self._max_tool_iterations} étapes prévues "
    f"({len(trace)} action(s) réelle(s) tentée(s)).",
    request.correlation_id,
)

# APRÈS :
self._last_tool_trace[request.session_id] = trace
return self._finalize_turn(
    request.input.text or "", trace, request.correlation_id,
)
```

`_explain_blocked_turn` reste inchangée et continue d'être appelée par les trois points d'escalade existants (LoopDetector ESCALATE : lignes 634, 662).

---

## 12. Fichiers qui nécessiteraient modification

| Fichier | Modification | Lignes concernées |
|---------|-------------|-----------------|
| `raya/harness/loop.py` | Ajouter `_finalize_turn` | Après ligne 461 |
| `raya/harness/loop.py` | Remplacer l'appel fin de range() | Lignes 676-682 |
| `raya/harness/loop.py` | `_summarize_tool_result` : cap structurel browser.read_page | Lignes 404-417 |
| `raya/models/providers/ollama_cloud.py` | Ajuster formule `num_ctx` (optionnel si cap implémenté) | Ligne 185 |

**Total : 1 fichier principal (`loop.py`) + 1 fichier optionnel (`ollama_cloud.py`).**  
**Aucun nouveau module, aucune nouvelle classe, aucune modification de contrats.**

### 12.1 Fichiers que l'on NE modifie PAS

| Fichier | Raison |
|---------|--------|
| `raya/devices/browser/controller.py` | `_STRUCT_JS` intact — le cap est en aval dans loop.py |
| `raya/cognition/verification.py` | VerificationOutcome inchangé |
| `raya/contracts/__init__.py` | Aucun nouveau contrat |
| `raya/tools/catalog/browser.py` | `observation=()` sur click_at_position/read_page — inchangé |
| `raya/cognition/recovery.py` | LoopDetector inchangé |
| `raya/context_engine/` | Inchangé |

---

## 13. Tests proposés

**Maximum 8 tests — couvrant les cas critiques.**

### T1 — Budget épuisé + dernière action SUCCESS

```python
def test_finalize_turn_with_success_evidence():
    """_finalize_turn appelée avec evidence de succès → texte ne déclare pas l'échec."""
    trace = [
        {"tool_name": "browser.read_page", "status": "success", "outcome": "SUCCESS",
         "evidence": {"url": "https://amazon.fr/cart/smart-wagon?newItems=B0D..."}}
    ]
    harness = make_test_harness()
    # Mock _invoke_model pour retourner un texte de succès
    # Vérifier que _finalize_turn transmet l'evidence dans ses messages
    messages_sent = capture_invoke_model_messages(harness, "_finalize_turn", trace)
    assert "url" in str(messages_sent)
    assert "smart-wagon" in str(messages_sent)
    assert "got stuck" not in str(messages_sent[0].content)
```

### T2 — Budget épuisé + aucune preuve de completion

```python
def test_finalize_turn_without_evidence():
    """_finalize_turn sans evidence → pas de faux positif de succès déclaré."""
    trace = [
        {"tool_name": "browser.navigate", "status": "success", "outcome": "SUCCESS",
         "evidence": {"url": "https://amazon.fr"}},
    ]
    harness = make_test_harness()
    # _finalize_turn : le modèle reçoit evidence sans confirmation cart
    # → message système = "evidence shows success" uniquement si evidence le dit
    messages_sent = capture_invoke_model_messages(harness, "_finalize_turn", trace)
    assert "got stuck" not in str(messages_sent[0].content)
    assert "budget" in str(messages_sent[0].content).lower()
```

### T3 — Finalisation sans tool_calls structurellement

```python
def test_finalize_turn_cannot_call_tools():
    """_finalize_turn passe available_tools=None → tool_calls_requested = None."""
    model_request_captured = []
    original_invoke = Harness._invoke_model
    def capture(self, req):
        model_request_captured.append(req)
        return make_text_response("fait")
    harness = make_test_harness_with_mock(capture)
    harness._finalize_turn("test", [], "corr-1")
    assert model_request_captured[0].available_tools is None
```

### T4 — Evidence conservée dans la trace de finalisation

```python
def test_finalize_turn_trace_includes_evidence():
    """_finalize_turn : les 5 derniers items de trace incluent evidence dans le message."""
    trace = [
        {"tool_name": "browser.read_page", "status": "success", "outcome": "SUCCESS",
         "evidence": {"url": "/cart/add-to-cart/", "title": "Panier"}},
    ]
    harness = make_test_harness()
    messages = build_finalization_messages(harness, trace)
    user_msg = messages[1].content[0].value
    assert "/cart/add-to-cart/" in user_msg
```

### T5 — BLOCKED ≠ COMPLETED : chemins séparés

```python
def test_escalate_calls_explain_blocked_not_finalize():
    """LoopDetector.ESCALATE → _explain_blocked_turn, jamais _finalize_turn."""
    harness = make_test_harness()
    finalize_called = []
    explain_called = []
    harness._finalize_turn = lambda *a, **k: finalize_called.append(True) or "fin"
    harness._explain_blocked_turn = lambda *a, **k: explain_called.append(True) or "bloqué"
    
    # Provoquer un ESCALATE via LoopDetector
    run_harness_with_repeated_tool_failure(harness)
    
    assert len(explain_called) > 0
    assert len(finalize_called) == 0
```

### T6 — Contexte de finalisation compact

```python
def test_finalize_turn_uses_last_5_trace_only():
    """_finalize_turn : seulement les 5 derniers items de trace dans le message."""
    trace = [{"tool_name": f"t{i}", "status": "success", "outcome": "SUCCESS", "evidence": {}}
             for i in range(12)]
    harness = make_test_harness()
    messages = build_finalization_messages(harness, trace)
    user_msg = messages[1].content[0].value
    # t0 à t6 ne doivent pas apparaître, t7 à t11 oui
    assert "t0" not in user_msg
    assert "t11" in user_msg
```

### T7 — Cap structurel read_page préserve boutons prioritaires

```python
def test_cap_read_page_preserves_priority_buttons():
    """_cap_read_page_output : les 20 premiers boutons (buybox) sont préservés."""
    data = {
        "url": "https://amazon.fr/dp/B0D",
        "title": "AirPods Pro 3",
        "cookie_banner": False,
        "buttons": [{"text": f"btn{i}", "tag": "button"} for i in range(120)],
        "links": [{"text": f"link{i}", "tag": "a"} for i in range(60)],
        "inputs": [],
    }
    result = json.loads(_cap_read_page_output(data))
    assert len(result["buttons"]) == 20
    assert result["buttons"][0]["text"] == "btn0"   # prioritaire conservé
    assert result["buttons_truncated"] == 100
    assert len(result["links"]) == 25
```

### T8 — Pas de faux positif lexical par absence d'heuristique

```python
def test_finalize_turn_no_lexical_cart_detection():
    """Pas d'heuristique lexicale dans _finalize_turn — le modèle décide."""
    # Trace avec "Ajouter au panier" dans le output (page produit, pas confirmé)
    trace = [
        {"tool_name": "browser.read_page", "status": "success", "outcome": "SUCCESS",
         "evidence": {"url": "https://amazon.fr/dp/B0D", "buttons": [{"text": "Ajouter au panier"}]}},
    ]
    harness = make_test_harness()
    # _finalize_turn ne doit PAS contenir de logique "panier in evidence → success"
    # Elle transmet l'evidence au modèle sans modification sémantique
    import inspect
    source = inspect.getsource(Harness._finalize_turn)
    assert "panier" not in source.lower()
    assert "cart" not in source.lower()
    assert "added" not in source.lower()
```

---

## 14. Observations E2E réelles

*Cet audit étant AUDIT ONLY, aucune session réelle n'a été exécutée en 20G-A.*

**Données E2E disponibles depuis 20E/20F** :

### Observation #1 — Amazon Xbox (20E, Run 1 — ACHIEVED)

Trace étape 12 :
```
[11] browser.read_page → url=.../cart/add-to-cart/, output contient "1 article dans le panier"
     evidence: {"url": ".../cart/add-to-cart/..."}
```

Avec `_finalize_turn` :
- Modèle reçoit `evidence: {"url": "...cart/add-to-cart..."}` dans les messages
- Modèle ne peut pas appeler d'outils (`available_tools=None`)
- Modèle a toutes les preuves pour déclarer le succès
- **Résultat attendu : succès correctement déclaré**

### Observation #2 — Amazon AirPods (20F — FAILURE incorrect)

Trace étape 12 :
```
[12] browser.read_page → url=/cart/smart-wagon?newItems=..., output confirme panier
     evidence: {"url": "/cart/smart-wagon?newItems=..."}
```

Avec `_finalize_turn` vs `_explain_blocked_turn` actuel :

| | `_explain_blocked_turn` actuel | `_finalize_turn` proposé |
|-|-------------------------------|--------------------------|
| Prompt système | "got stuck" | "used full action budget" |
| Evidence dans contexte | Non | Oui — URL cart visible |
| Tools disponibles | Non (nouveau contexte) | Non (structurel) |
| Résultat attendu | FAILURE (déclaré) | SUCCESS (correctement) |

---

## 15. Risques

### R1 — Modèle mal calibré en finalisation

**Scénario** : Gemma4:31b voit evidence de panier confirmé mais reste incertain et dit "je ne suis pas sûr".  
**Probabilité** : Faible — le modèle reçoit une URL explicite `/cart/smart-wagon?newItems=...`  
**Atténuation** : Tester le prompt de finalisation sur les cas connus (20E Run 1, 20F). Ajuster le wording si nécessaire.

### R2 — Finalization prompt trop large

**Scénario** : `trace[-5:]` avec evidence lourde (5 × 500 tokens) + WorldState dépasse `num_ctx` pour `_finalize_turn`.  
**Impact** : Faible — la finalisation reçoit les 5 derniers appels comprimés, pas 120 boutons.  
**Atténuation** : `context_budget_tokens=self._context_budget_tokens` est passé à `_finalize_turn` → `num_ctx` = 16,384, suffisant pour une trace de 5 items.

### R3 — Cap read_page trop agressif (20 boutons)

**Scénario** : Le bouton add-to-cart ne figure pas dans les 20 premiers sur certaines pages.  
**Analyse** : `_STRUCT_JS` prioritize explicitement les buybox roots en tête. Sur une page produit Amazon, les boutons buybox sont les premiers extraits. Le cap à 20 est sûr si les priority roots existent.  
**Atténuation** : `vision.find_in_browser` reste disponible comme fallback si `browser.click` échoue. Le cap à 20 peut être augmenté à 30 sans coût significatif.

### R4 — `_explain_blocked_turn` toujours appelée pour vrais blocages

**Scénario** : LoopDetector.ESCALATE continue de déclencher `_explain_blocked_turn` (inchangé).  
**Impact** : Aucun — chemin existant, non modifié.

### R5 — Perte de contexte conversationnel dans `_finalize_turn`

**Scénario** : La finalisation ouvre un nouveau contexte (pas les messages historiques).  
**Analyse** : C'est le comportement actuel d'`_explain_blocked_turn`. Le modèle ne voit pas l'historique de la conversation. Il voit uniquement les derniers résultats d'outils + evidence.  
**Impact** : Acceptable — le modèle dispose de la requête originale + evidence récente, suffisant pour une réponse honnête.

---

## 16. Ce qui ne doit pas changer

| Composant | Raison |
|-----------|--------|
| `_explain_blocked_turn` | Inchangée — 3 points d'appel existants pour vrais blocages |
| `LoopDetector` | Mécanisme correct, hors scope |
| `VerificationOutcome` | Contrat stable |
| `browser.click_at_position` `observation=()` | Hors scope |
| `browser.read_page` `observation=()` | Hors scope — le cap est en aval |
| `_STRUCT_JS` (controller.py) | Hors scope — logic de priorité correcte |
| `max_tool_iterations=12` | Valeur configurable via env, inchangée |
| Vision parser (`_FOUND_PATTERN`) | Corrigé en 20E |
| BoundingBox | Contrat stable |
| Memory, Telegram, Voice, UI | Hors scope |

---

## 17. Recommandation finale

### Verdict : **GO IMPLEMENTATION** sous conditions

**Condition 1 (obligatoire)** : Implémenter `_finalize_turn` dans `raya/harness/loop.py` avec les caractéristiques décrites en §4 et §11. Remplacer l'appel ligne 677-682 par `_finalize_turn`. **Ne pas modifier `_explain_blocked_turn`.**

**Condition 2 (obligatoire)** : Ajouter cap structurel `browser.read_page` dans `_summarize_tool_result` (§8.4) — 20 boutons, 25 liens. Jamais `output[:4000]` — cap sur les arrays JSON pour préserver les éléments prioritaires (buybox buttons en tête).

**Condition 3 (optionnelle)** : Ajuster `num_ctx` uniquement si le cap read_page n'est pas implémenté simultanément, ou comme garde-fou additionnel. La formule actuelle est suffisante après le cap.

**Ce qui est garanti par cette architecture** :
- `BUDGET EXHAUSTED ≠ TASK FAILED` — chemins structurellement séparés
- Evidence transmise au modèle — pas de filtrage
- Pas d'heuristique lexicale — le modèle décide
- Pas de faux positifs structurels — `available_tools=None` empêche tout 13e appel d'outil
- Pas de nouveau composant — 1 nouvelle méthode dans `Harness`, 1 modification de méthode

**Scope final de l'implémentation** :
- `raya/harness/loop.py` : +`_finalize_turn`, modif `_summarize_tool_result`, 1 ligne changée
- `raya/models/providers/ollama_cloud.py` : 1 ligne optionnelle

---

*Audit 20G-A — Mode AUDIT ONLY — Aucun code modifié*  
*Rapport : `RAYA_V2_20G_A_OBJECTIVE_COMPLETION_DESIGN_AUDIT.md`*
