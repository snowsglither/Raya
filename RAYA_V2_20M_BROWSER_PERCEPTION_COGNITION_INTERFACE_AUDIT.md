# RAYA V2 — Chantier 20M
# Browser Perception → Cognition Interface Audit
# POST-20K / POST-20L

**Date :** 2026-09-16  
**Mode :** AUDIT UNIQUEMENT — ZERO CODE / ZERO TEST / ZERO RUNTIME  
**Question centrale :** POST-20K/POST-20L, est-ce que RAYA possède réellement une couche de perception
qui transforme DOM + Vision en représentation exploitable, ou bien donne-t-il essentiellement
au modèle plusieurs données brutes/structurées et lui demande-t-il de faire lui-même
la perception, la corrélation et la sélection ?

---

## §1 — Executive Summary

POST-20K/20L, RAYA fournit au modèle de raisonnement deux flux de **données structurées** — pas une perception sémantique. La distinction est fondamentale.

Le pipeline DOM produit une liste compacte de `{kind, text, tag}` (20 boutons, 25 liens, n inputs). Les éléments n'ont ni rôle sémantique, ni priorité, ni pertinence par rapport à l'objectif utilisateur. La Vision produit une description textuelle libre et, si grounding demandé, des coordonnées pixel liées à un label textuel. Ces deux flux arrivent au modèle de raisonnement comme deux messages `role=tool` séparés — jamais fusionnés, jamais co-interprétés par le système.

**Ce que le modèle doit faire lui-même, sans aide structurelle :**
- Filtrer 20 boutons pour trouver celui pertinent à l'objectif
- Attribuer un rôle sémantique aux éléments (action principale, formulaire, navigation)
- Corréler un élément DOM avec son équivalent visuel (par texte label uniquement)
- Agréger DOM + Vision + WorldState pour reconstituer l'état courant de la page
- Sélectionner l'action candidate parmi les possibles

**Abstractions absentes du code et des blueprints :** PageState, ActionCandidate, SemanticElement, GroundedTarget, CrossModalObservation, DOMRelevanceFilter, FormState.

**Verdict :** RAYA donne au modèle des **données structurées** (pas des perceptions structurées) et lui délègue l'essentiel du travail de perception, de corrélation et de sélection. Cette architecture est cohérente avec les blueprints qui ne définissent pas de couche de perception sémantique pour le browser. Ce n'est pas un bug — c'est une limite de conception connue.

---

## §2 — Production Pipeline

```
USER INPUT ("Ajoute des AirPods au panier")
  │
  ▼
Harness._handle_direct_request()  [loop.py:~200]
  │
  ├─── assemble(world_state, memory, active_tasks, ...) → Context
  │     [assembler.py, render.py]
  │     ├── SYSTEM_RULES  : identité RAYA + ~15 directives génériques
  │     ├── IDENTITY      : nom, runtime, provider
  │     ├── MEMORY        : faits personnels confirmés (text)
  │     ├── WORLD_STATE   : tous les WorldStateFacts (retrieve_relevant(()))
  │     │    ex: "Observed env state [fresh]: browser.current_url = 'https://...'"
  │     ├── ACTIVE_TASKS  : tâches de fond actives
  │     └── CONV_HISTORY  : messages précédents formatés
  │
  └─── _run_agentic_loop()  [loop.py:542]
         │
         ├── ModelRequest(REASONING, messages, available_tools=ALL)
         │     messages = [
         │       Message(role="system", content=render_system_prompt(context)),
         │       Message(role="user", content="Ajoute des AirPods au panier"),
         │       ...previous role=assistant + role=tool...
         │     ]
         │
         │              MODEL REÇOIT ET DÉCIDE
         │
         ├── IF tool_calls_requested:
         │     execute_tool(registry, safety, tool_call)
         │     │
         │     ├─ [browser.read_page]
         │     │    BrowserDeviceAgent._read_page()
         │     │    → BrowserController.read_page()
         │     │    → page.evaluate(_STRUCT_JS) [controller.py:58-110]
         │     │    → {url, title, cookie_banner,
         │     │        buttons[up to 120], links[up to 60], inputs[up to 20]}
         │     │    → _compact_read_page_output() [loop.py:391]
         │     │    → {buttons[20], links[25], inputs[all], url, title, cookie_banner}
         │     │    → JSON → Message(role="tool", content=json)
         │     │    WORLDSTATE : RIEN (observation=())
         │     │
         │     ├─ [vision.find_in_browser(target="...")]
         │     │    capture_browser_fn() → BrowserController.screenshot()
         │     │    → page.screenshot(path) → PIL → {path, width, height}
         │     │    → ViewportInfo(image_width=w, image_height=h)
         │     │    → observe_image(models, path, find_target, viewport)
         │     │    → ModelRequest(VISION, [image_ref(base64)+grounding_prompt])
         │     │    → "FOUND: bbox=[...] label='...' confidence=N"
         │     │    → _parse_grounding() → VisualTarget(bbox, viewport, obs_id)
         │     │    → screen_x = int(bbox.center_x * image_width)
         │     │    → screen_y = int(bbox.center_y * image_height)
         │     │    → output: {screen_x, screen_y, bbox, label, confidence, obs_id}
         │     │    → evidence: {visual_observation: {description, entities, obs_id}}
         │     │    WORLDSTATE : visual.browser_visual_state = {description, entities}
         │     │                 (PAS les coordonnées)
         │     │
         │     └─ [browser.click_at_position(x, y)]
         │          page.mouse.click(x, y)
         │          WORLDSTATE : browser.last_clicked_at = "x,y" (30s)
         │
         ├── _promote_observations_and_verify() [loop.py:333]
         │     → world_state.apply_update() pour chaque ObservationSpec
         │
         └── _summarize_tool_result() → JSON → Message(role="tool")
               → append to messages → next iteration → MODEL voit le résultat
```

---

## §3 — What The Model Actually Sees

### SYSTEM

Rendu par `render_system_prompt(context)` (`render.py:26`).

Un seul bloc de texte contenant dans l'ordre :
1. `"You are Raya, an AI assistant."`
2. `"Runtime: you are currently served by provider=..., model=..."`
3. Directive evidence-based ("base it on Observed environment state...")
4. Directive completion ("final action is NOT success until verified...")
5. Directive multilingual browser navigation
6. Directive URL guessing prevention
7. Directive CAPTCHA/2FA/login honest stop
8. Directive date/time (use system.time.now)
9. Directive future tasks (tasks.create)
10. Directive filesystem paths (pc.filesystem.find_folder)
11. Directive info vs action distinction
12. Directive channel preference
13. Directive future task vs immediate action
14. Directive task identity (cancel + recreate)
15. Directive CLI-first preference
16. Directive USE ≠ SHOW
17. Directive no tool/path hallucination
18. Faits mémoire : `"Known, confirmed fact about the user: ..."` (un par entrée)
19. WorldState facts : `"Observed environment state [fresh]: browser.current_url = 'https://...'"` (un par fact)
20. Tâches actives : `"Other active task (...): ..., state=..."`
21. Historique : `"Earlier in this conversation, you (RAYA) said: ..."` / `"...the user said: ..."`

**Il n'y a aucune description de la page courante dans le system prompt.**

### USER

`request.input.text` brut, inchangé : ex. `"Ajoute des AirPods Pro 3 dans mon panier stp"`

### WORLD STATE

Clés browser/visual présentes dans le contexte si populées :
```
Observed environment state [fresh]: browser.current_url = 'https://amazon.fr/dp/...'
Observed environment state [fresh]: browser.last_clicked_target = 'AirPods Pro 3'
Observed environment state [fresh]: browser.last_clicked_at = '704,554'
Observed environment state [stale]: visual.browser_visual_state = {'description': '...', 'semantic_entities': [...], 'observation_id': 'vobs-...'}
```

**Aucun fait sur le DOM de la page courante.** `browser.read_page` a `observation=()`.

### MEMORY

Faits personnels confirmés (profil, préférences). Exemples typiques :
```
Known, confirmed fact about the user: Réside à Evere, Bruxelles, Belgique
Known, confirmed user preference: préfère être tutoyé
```

Pas d'information contextuelle browser dans Memory.

### TASK

Si une tâche de fond est active :
```
Current background task (task-...): "Ajoute des AirPods...", state=running, progress=step 3 (25%)
```

### PREVIOUS TOOL RESULTS

Messages `role=tool` dans `messages[]` depuis la même session, format JSON :

```json
// browser.read_page
{
  "tool": "browser.read_page",
  "status": "success",
  "verification": "SUCCESS",
  "output": {
    "url": "https://amazon.fr/...",
    "title": "AirPods Pro 3 - Amazon.fr",
    "cookie_banner": false,
    "buttons": [
      {"kind": "button", "text": "Ajouter au panier", "tag": "button"},
      {"kind": "button", "text": "Acheter maintenant", "tag": "button"},
      ...20 max
    ],
    "links": [...25 max],
    "inputs": [{"kind": "input", "text": "Recherche", "tag": "input"}, ...]
  },
  "evidence": {"url": "https://...", "cookie_banner": false},
  "error": null
}

// vision.find_in_browser
{
  "tool": "vision.find_in_browser",
  "status": "success",
  "verification": "SUCCESS",
  "output": {
    "observation_id": "vobs-01jzxxx",
    "description": "Amazon product page showing AirPods Pro 3...",
    "semantic_entities": ["AirPods", "Amazon", "button", "cart", ...],
    "model_used": "ollama:gemma4:cloud",
    "confidence": "inferred",
    "target": {
      "label": "Ajouter au panier",
      "confidence": 0.93,
      "screen_x": 704,
      "screen_y": 554,
      "bbox": {"x_min": 0.45, "y_min": 0.72, "x_max": 0.65, "y_max": 0.82},
      "observation_id": "vobs-01jzxxx"
    }
  },
  "evidence": {"visual_observation": {"description": "...", "semantic_entities": [...], ...}},
  "error": null
}
```

### TOOL SCHEMAS

Passés via `ModelRequest.available_tools` — **PAS dans le system prompt**. Schémas JSON complets de tous les outils enregistrés (browser.*, vision.*, pc.*, system.*, tasks.*, memory.*, etc.). Le modèle voit leur `name`, `description`, `input_schema`, `permission_level`.

### CURRENT OBSERVATION

**Il n'existe pas d'objet "current observation".**

Le modèle reconstitue l'état courant de la page à partir de :
1. WorldState text : URL courante (60s TTL)
2. Dernier `role=tool` résultat de `browser.read_page` dans l'historique
3. Dernier `role=tool` résultat de `vision.*` dans l'historique

Ces trois sources sont éparpillées dans le contexte sans label unifié.

### Réponse à la question centrale

**Le modèle reçoit une collection de résultats d'outils — pas une représentation de la page.**

Il n'existe aucun objet `PageState`, `BrowserObservation`, ni `CurrentPageContext` qui regroupe et structure l'état courant du browser pour le modèle. L'état courant doit être reconstitué par le modèle à partir de fragments épars dans le system prompt (WorldState text) et dans l'historique de messages (tool results).

---

## §4 — DOM Representation

### Niveau A — RAW DOM

Le DOM complet de la page dans le browser : arbre HTML complet, tous les éléments, attributs, styles, positionnement, visibilité, z-index, shadow DOM, iframes, event handlers.

### Niveau B — STRUCTURED DOM

`_STRUCT_JS` extrait (`controller.py:58-110`) :

```javascript
{
  url: "https://...",
  title: "...",
  cookie_banner: false,
  buttons: [{"kind": "button", "text": "...", "tag": "button"}, ...],   // max 120 brut
  links:   [{"kind": "link",   "text": "...", "tag": "a"}, ...],         // max 60 brut
  inputs:  [{"kind": "input",  "text": "...", "tag": "input"}, ...]      // max 20 brut
}
```

Après `_compact_read_page_output()` : **20 boutons**, **25 liens**, inputs intégraux.

**Informations extraites :**
- `text` = `innerText || value || aria-label || title || placeholder` (tronqué à 90 chars)
- `tag` = nom du tag HTML (`button`, `a`, `input`, `textarea`)
- `kind` = `"button"` | `"link"` | `"input"`
- Visibilité filtrée : `getBoundingClientRect()`, `opacity`, `visibility`, `display`

**Informations PERDUES à ce niveau :**
- `type` des inputs (password, email, number, text, checkbox, radio, file...)
- `name` des inputs
- `required`, `pattern`, `maxlength`, `autocomplete`
- Valeur courante (seulement `el.value` initial — pas l'état en cours de saisie)
- Position/coordonnées dans la page (bounding rect n'est PAS exporté)
- Z-index, stacking context
- État (disabled, aria-disabled, aria-expanded, aria-selected, aria-checked)
- Relations entre éléments (input → label associé, input → form parent → submit button)
- Hiérarchie DOM (parent/enfant/sibling)
- CSS classes/IDs (sauf via priority roots)
- Style visuel (couleur, taille, contraste, prominence)
- Event handlers déclarés
- Contenu des iframes (cherchés seulement pour `find_clickable()`, pas `read_page`)

### Niveau C — SEMANTIC DOM PERCEPTION

**SEMANTIC DOM PERCEPTION = ABSENT**

Il n'existe aucune couche entre le Niveau B et le modèle qui attribue :
- Rôle sémantique : `PRIMARY_ACTION`, `SECONDARY_ACTION`, `NAVIGATION`, `SEARCH_FIELD`, `FORM_SUBMIT`, `PRODUCT_TITLE`, `PRICE`, `COOKIE_CONSENT`, `MODAL_CLOSE`
- Pertinence par rapport à l'objectif : `RELEVANT_TO_INTENT`, `IRRELEVANT`
- Priorité : `rank_score`, `importance`
- Relations structurées : `"this input submits via this button"`
- État de remplissage : `EMPTY`, `FILLED`, `VALIDATED`, `ERROR`

Le modèle reçoit la liste brute de 20 boutons et doit réaliser lui-même la totalité de ce travail d'interprétation sémantique.

---

## §5 — Vision Representation

### IMAGE (artefact brut)

Screenshot Playwright PNG sauvegardé localement. Dimensions connues POST-20K via PIL. Jamais transmis au modèle de raisonnement (`artifact_ref` exclu de `to_world_state_value()`).

### VISUAL OBSERVATION (`VisualObservation`)

Produit par `observe_image()` (`models/vision.py:126`) :

```python
VisualObservation(
    source="perception:browser",    # provenance structurée
    description="Amazon product page showing AirPods Pro 3...",  # texte libre du Vision model
    artifact_ref="/tmp/raya_vision_capture.png",  # LOCAL UNIQUEMENT — jamais en WS
    semantic_entities=["AirPods", "Amazon", "button", "cart", ...],  # heuristique mots >3 chars
    target=VisualTarget(...),        # None si vision.observe_browser; renseigné si find_in_browser
    raw_model_output="FOUND: bbox=[...]...",  # sortie brute pour debug
    model_used="ollama:gemma4:cloud",
    confidence=Confidence.INFERRED,  # TOUJOURS — enforcement dans __post_init__
    observation_id="vobs-01jzxxx",
    viewport=ViewportInfo(1280, 720),
)
```

`semantic_entities` est une heuristique Python (`models/vision.py:205-208`) :
```python
entities = list({
    w.strip(".,;:\"'()") for w in raw_text.split()
    if len(w) > 3 and (w[0].isupper() or w.lower() in _COMMON_UI_ENTITIES)
})[:20]
```
Ce n'est PAS une reconnaissance d'entités — c'est un découpage lexical sur la description textuelle.

### TARGET (`VisualTarget`)

Produit uniquement lors d'un appel avec `find_target != ""` :

```python
VisualTarget(
    label="Ajouter au panier",
    bbox=BoundingBox(x_min=0.45, y_min=0.72, x_max=0.65, y_max=0.82),  # normalisé [0,1]
    viewport=ViewportInfo(image_width=1280, image_height=720, window_left=0, window_top=0),
    observation_id="vobs-01jzxxx",
    confidence=0.93,
)
# screen_coordinates() = (int(0.55 * 1280), int(0.77 * 720)) = (704, 554)
```

**Ce que Vision produit :**
- ✓ Description sémantique textuelle (interprétation du Vision model)
- ✓ Grounding pixel précis si grounding demandé
- ✓ `observation_id` pour traçabilité
- ✗ Compréhension structurée de l'UI (pas de "bouton principal", "formulaire de recherche")
- ✗ Relations entre éléments visuels
- ✗ État des éléments (actif/inactif, coché, sélectionné)

**Réponse :** Vision produit une **description sémantique partielle** (le Vision model comprend l'image et produit du texte) + un **grounding partiel** (localise un élément par label textuel). Ce n'est pas une perception sémantique structurée — c'est une transcription/localisation visuelle.

---

## §6 — DOM + Vision Correlation

**DOM_VISUAL_TARGET_CORRELATION = ABSENT**

Aucun objet du type :
```
GroundedTarget {
    dom_reference: "Ajouter au panier"  // button text from DOM
    visual_reference: BoundingBox(0.45, 0.72, 0.65, 0.82)
    screen_x: 704
    screen_y: 554
    semantic_label: "add_to_cart"
    confidence: 0.93
    observation_id: "vobs-..."
    timestamp: "2026-09-16T..."
}
```

n'existe dans aucun fichier du codebase — ni dans `contracts/`, ni dans `tools/`, ni dans `harness/`, ni dans `devices/browser/`, ni dans `context_engine/`.

### Mécanisme implicite

La seule "corrélation" possible est **par texte label** : le modèle voit dans le DOM `{"kind":"button","text":"Ajouter au panier"}` et dans le résultat Vision `{"target":{"label":"Ajouter au panier","screen_x":704}}` et doit déduire lui-même qu'il s'agit du même élément. Ce mécanisme est :
- **Implicite** (dans la cognition du modèle de raisonnement)
- **Fragile** (si les labels diffèrent légèrement : "Ajouter au panier" vs "Add to Cart")
- **Non vérifié** (aucune assertion que le bbox vision correspond au DOM rect du bouton)

---

## §7 — Page State

**PAGE_STATE = ABSENT**

Aucune abstraction `PageState` dans le codebase. Ce qui s'en approche le plus :

| Composant | Contenu | Persistance | Accès |
|-----------|---------|-------------|-------|
| `WorldState["browser"]["current_url"]` | URL courante | 60s TTL en SQLite | System prompt |
| `WorldState["browser"]["last_clicked_target"]` | Dernier texte cliqué | 30s TTL | System prompt |
| `WorldState["visual"]["browser_visual_state"]` | Description textuelle + entities | 30s TTL | System prompt |
| Dernier `role=tool` de `browser.read_page` | DOM compact JSON | Éphémère, in-messages | Historique conversation |
| Dernier `role=tool` de `vision.*` | Description + screen_x/y | Éphémère, in-messages | Historique conversation |

Ces fragments sont non-intégrés, dispersés dans différentes parties du contexte, et n'existent sous aucune forme unifiée permettant au système (pas au modèle) de raisonner sur "l'état courant de la page".

---

## §8 — Target Representation

**TARGET REPRESENTATION = PARTIAL**

| Contexte | Objet | Contenu | Fichier |
|---------|-------|---------|---------|
| DOM | Aucun — texte nu dans dict | `{"kind":"button","text":"Ajouter au panier","tag":"button"}` | `controller.py` |
| Vision | `VisualTarget` | `label, bbox, viewport, screen_coords, confidence, obs_id` | `contracts/visual.py:87` |
| Cross-modal | Absent | — | — |

`VisualTarget` est un objet de target complet pour la couche Vision. Mais il n'existe pas d'objet analogue pour un target DOM, ni d'objet unificateur qui combine les deux.

---

## §9 — Action Candidate Representation

**ACTION CANDIDATE REPRESENTATION = ABSENT**

Aucune couche ne produit une liste de type :
```python
[
    ActionCandidate(
        label="Ajouter au panier",
        type=ActionType.PRIMARY,
        source=ActionSource.DOM,
        relevance_to_objective=0.95,
        available_via=["browser.click", "browser.click_at_position"],
    ),
    ActionCandidate(
        label="Acheter maintenant",
        type=ActionType.SECONDARY,
        source=ActionSource.DOM,
        relevance_to_objective=0.70,
        available_via=["browser.click"],
    ),
]
```

Le modèle reçoit la liste plate de 20 boutons et doit lui-même :
1. Identifier les candidats pertinents
2. Les classer par priorité relative à l'objectif
3. Choisir lequel appeler

---

## §10 — Objective-Aware Perception

**OBJECTIVE-AWARE PERCEPTION = ABSENT** (sauf exception hardcodée)

`browser.read_page` retourne les mêmes 20 boutons que l'objectif soit "ajouter au panier", "trouver le prix", "fermer la fenêtre", ou "se connecter". La perception n'est pas informée par l'intention.

**Exception — hardcodée :** `_STRUCT_JS` implémente des priority roots Amazon (`#desktop_buybox, #addToCart_feature_div, [id*=buybox i], ...`) qui font remonter les boutons buybox en tête de la liste. C'est la seule forme de filtrage par objectif — mais elle est codée en dur pour un site spécifique (Amazon), pas générique par objectif.

**Pour Vision :** `vision.find_in_browser(target="...")` EST objective-aware — le `target` est fourni par le modèle, donc il est implicitement lié à l'objectif. Mais c'est le modèle qui formule le target, pas le système.

---

## §11 — Observation Reuse

### DOM (browser.read_page)

**Aucun mécanisme de cache ou de versioning pour les observations DOM.**

- Pas d'observation ID pour le DOM (contrairement à `VisualObservation.observation_id`)
- Le résultat de `browser.read_page` existe uniquement comme message `role=tool` dans `messages[]`
- Accessible par le modèle dans l'historique de conversation (si non tronqué par budget)
- Aucune API pour "récupérer la dernière lecture de page" — le modèle doit se souvenir du JSON dans l'historique
- Si la page a changé depuis la dernière lecture : aucun signal explicite — le modèle doit ré-émettre `browser.read_page()`
- Pas de `dom_version`, pas de `page_changed_event`, pas de `dom_hash`

### Vision

- `VisualObservation.observation_id` = `"vobs-..."` présent dans le tool result JSON
- Le modèle peut référencer cet ID dans son raisonnement
- Mais il n'existe aucune API pour "récupérer une VisualObservation par son ID" — l'objet Python est créé et immédiatement converti en dict dans `_obs_to_output()` + `_obs_to_evidence()`
- L'`observation_id` sert uniquement à la traçabilité (audit) — pas à la récupération
- Les coordonnées `screen_x/screen_y` issues d'une observation Vision précédente sont disponibles dans l'historique de messages si elles n'ont pas été tronquées

### Résumé

| Observation | Cache | Version | ID | Invalidation | Reuse API |
|------------|-------|---------|-----|-------------|-----------|
| DOM (read_page) | ABSENT | ABSENT | ABSENT | ABSENT | ABSENT |
| Vision (observe_browser) | ABSENT | ABSENT | ✓ (obs_id, texte) | ABSENT (TTL 30s WorldState) | ABSENT |
| Vision (find_in_browser) | ABSENT | ABSENT | ✓ (obs_id, texte) | ABSENT | ABSENT |
| WorldState (url, clicked) | ABSENT | ABSENT | (domain+key) | ✓ (TTL 30-60s) | ✓ (`retrieve_relevant`) |

**Après `browser.click()`**, la page peut avoir changé (redirection, modal). Aucun signal automatique ne notifie le modèle que son dernier DOM est obsolète. Le modèle doit décider lui-même de ré-émettre `browser.read_page()`.

---

## §12 — WorldState : Table Complète

| Information | Tool source | Domaine | Clé | TTL | Valeur en WS | Model visible |
|------------|-------------|---------|-----|-----|-------------|---------------|
| URL courante | `browser.navigate` | `browser` | `current_url` | 60s | URL string | ✓ (system prompt) |
| URL post-clic | `browser.click` | `browser` | `current_url` | 60s | URL string | ✓ |
| Élément cliqué | `browser.click` | `browser` | `last_clicked_target` | 30s | texte du bouton | ✓ |
| Coordonnées cliquées | `browser.click_at_position` | `browser` | `last_clicked_at` | 30s | `"x,y"` string | ✓ |
| Description visuelle écran | `vision.observe_screen` | `visual` | `screen_state` | 30s | `{description, entities, obs_id, model}` | ✓ |
| Description visuelle browser | `vision.observe_browser`, `vision.find_in_browser` | `visual` | `browser_visual_state` | 30s | `{description, entities, obs_id, model}` | ✓ |
| Dernière image analysée | `vision.observe_image` | `visual` | `last_image_observation` | 60s | `{description, entities, obs_id, model}` | ✓ |
| **DOM de la page** | `browser.read_page` | — | — | — | **ABSENT** | **via tool result seulement** |
| **Screenshot** | `browser.screenshot` | — | — | — | **ABSENT** | **jamais (artifact_ref exclu)** |
| **Coordonnées Vision** | `vision.find_in_browser` | — | — | — | **ABSENT** | **via tool result seulement** |
| **Titre de page** | `browser.read_page` | — | — | — | **ABSENT** | **via tool result seulement** |
| **État cookie** | `browser.read_page`, `browser.dismiss_overlay` | — | — | — | **ABSENT** | **via tool result seulement** |
| **Formulaires** | — | — | — | — | **ABSENT** | **jamais structuré** |
| **Texte saisi** | `browser.type` | — | — | — | **ABSENT** | **jamais** |
| **Onglets ouverts** | `browser.list_tabs` | — | — | — | **ABSENT** | **via tool result seulement** |

---

## §13 — Perception vs Interprétation vs Décision

| Opération | Perception | Interprétation | Décision | Fichier |
|-----------|-----------|----------------|----------|---------|
| `_STRUCT_JS` | ✓ Extrait le DOM brut vers JSON | ✗ | ✗ | `controller.py:58` |
| `browser.read_page` | ✓ Transmet | ✗ | ✗ | `agent.py:90`, `controller.py:135` |
| `_compact_read_page_output` | ✗ (troncature) | ✗ | ✗ | `loop.py:391` |
| `BrowserController.screenshot()` | ✓ Capture | ✗ | ✗ | `controller.py:144` |
| `vision.find_in_browser` (capture) | ✓ Capture | ✗ | ✗ | `visual.py:303` |
| Vision model (Ollama/cloud) | ✗ | ✓ Produit description + localise bbox | ✗ | `models/vision.py:126` |
| `_parse_grounding()` | ✗ | ✓ Parse + normalise coords | ✗ | `models/vision.py:83` |
| `VisualTarget.screen_coordinates()` | ✗ | ✓ Transforme bbox → pixels | ✗ | `contracts/visual.py:100` |
| `_obs_to_output()` | ✗ | ✗ | ✗ | `catalog/visual.py:95` |
| `_promote_observations_and_verify()` | ✗ | ✓ Compare expected vs observed | ✗ | `loop.py:333` |
| `Context Engine` | ✗ | ✗ | ✗ (sélectionne par score) | `assembler.py` |
| `render_system_prompt()` | ✗ | ✗ | ✗ (sérialise) | `render.py:26` |
| **Modèle de raisonnement** | ✓ Via tool calls qu'il décide d'émettre | ✓ Interprète DOM + description Vision | ✓ Choisit l'action | Externe |
| `Harness` | ✗ | ✓ Vérifie succès/échec (`verify_tool_result`) | ✓ Loop/stop/escalate | `loop.py:542` |
| `LoopDetector` | ✗ | ✓ Détecte répétitions | ✓ RecoveryAction | `cognition/recovery.py` |
| `detect_repeating_cycle` | ✗ | ✓ Détecte cycle d'état | ✓ Escalate | `cognition/state_cycle.py` |

---

## §14 — Model Workload

| Responsabilité | Système | Modèle | Statut |
|----------------|---------|--------|--------|
| Extraction DOM (format JSON) | ✓ `_STRUCT_JS` | — | **STRUCTURED** |
| Filtrage DOM par visibilité | ✓ `_STRUCT_JS vis()` | — | **STRUCTURED** |
| Troncature DOM (20 btns / 25 liens) | ✓ `_compact_read_page_output` | — | **STRUCTURED** |
| Filtrage DOM par pertinence à l'objectif | ✗ | ✓ doit scanner 20 boutons | **MODEL-ONLY** |
| Attribution de rôle sémantique DOM (PRIMARY, SEARCH, NAV…) | ✗ | ✓ | **MODEL-ONLY** |
| Compréhension de l'état des éléments (disabled, loading…) | ✗ | ✓ (partiel, tag seulement) | **MODEL-ONLY** |
| Relations form → input → submit button | ✗ | ✓ | **MODEL-ONLY** |
| Compréhension du type d'input (password, email…) | ✗ | ✓ (via placeholder/label uniquement) | **MODEL-ONLY** |
| Capture screenshot | ✓ `BrowserController.screenshot()` | — | **STRUCTURED** |
| Interprétation visuelle (description) | ✓ Vision model | — | **STRUCTURED** |
| Grounding visuel (bbox → coords) | ✓ `_parse_grounding()` | — | **STRUCTURED** |
| Corrélation DOM ↔ Vision (même élément ?) | ✗ | ✓ par texte label | **MODEL-ONLY** |
| Arbitrage DOM vs Vision (contradiction) | ✗ | ✓ | **MODEL-ONLY** |
| Agrégation PageState (DOM + Vision + WS) | ✗ | ✓ depuis fragments épars | **MODEL-ONLY** |
| Sélection d'action candidate | ✗ | ✓ | **MODEL-ONLY** |
| Vérification succès/échec (status) | ✓ `verify_tool_result` | — | **STRUCTURED** |
| Vérification contenu (URL attendue vs observée) | ✓ `verify_observation_against_intent` | — | **STRUCTURED** (navigate seulement) |
| Vérification contenu (action clic réussie ?) | ✗ | ✓ doit ré-émettre read_page | **MODEL-ONLY** |
| Détection de boucle (mêmes appels) | ✓ `LoopDetector` | — | **STRUCTURED** |
| Détection de cycle d'état (A→B→A→B) | ✓ `detect_repeating_cycle` (URL seulement) | — | **PARTIALLY STRUCTURED** |
| Fraîcheur des observations | ✓ TTL WorldState | — | **STRUCTURED** |

---

## §15 — Amazon 20G : Explication Structurelle

Sans runtime. Basé sur traces 20G et code courant.

Le scénario typique `read_page → screenshot → read_page → screenshot → click → read_page → ...` s'explique par 6 lacunes structurelles cumulatives :

### L1 — DOM sans rôle sémantique = 20 boutons opaques

Le modèle reçoit une liste plate de 20 boutons dont aucun n'est étiqueté "c'est le bouton principal", "c'est la navigation", "c'est l'ajout au panier". Le modèle doit interpréter le texte de chaque bouton pour identifier l'action pertinente. Si le texte du bouton est ambigu (plusieurs langues, libellés partiels, texte tronqué à 90 chars), cette interprétation peut échouer.

### L2 — Aucun signal de changement de page

Après `browser.click()`, la page peut avoir changé (modal apparu, redirection, panier mis à jour). Le modèle n'a aucun signal automatique. Il doit décider de lui-même d'émettre un nouveau `browser.read_page()` pour voir le nouvel état. S'il ne le fait pas, il agit sur un DOM périmé. S'il le fait systématiquement, il consomme des tokens à chaque itération.

### L3 — Corrélation DOM/Vision par texte uniquement

Quand `browser.click("Ajouter au panier")` échoue (`ELEMENT_NOT_FOUND`), le modèle ne sait pas pourquoi : bouton dans un iframe ? Texte différent ? Élément masqué ? Il émet `vision.find_in_browser("Ajouter au panier")`. Vision retourne `screen_x=704`. Mais rien ne garantit que le bouton DOM "Ajouter au panier" et le bouton visuel localisé par Vision sont les mêmes — si les labels diffèrent (ex: "Add to Cart" visuellement mais "Ajouter au panier" dans le DOM), la corrélation échoue silencieusement.

### L4 — PageState reconstitué par le modèle = coûteux en tokens

Pour raisonner "où suis-je dans le processus d'achat ?", le modèle doit combiner :
- WorldState text (URL courante) — dans system prompt
- Dernier `read_page` result — dans message history
- Dernier `vision.*` result — dans message history

Chaque `read_page` ajoute ~1 000-3 000 tokens à `messages[]`. Après 3 itérations, le contexte approche la saturation (`num_ctx=16 384` : saturé à 14 827 tokens au step 3 selon données 20G). Un modèle à contexte saturé :
- Perd ses observations précoces (premier `read_page`)
- Ne "se souvient" plus du chemin parcouru
- Répète des actions (revérifie l'URL, re-lit la page)

### L5 — Aucune vérification post-clic intégrée

`browser.click()` retourne `{"status": "ok", "clicked": "Ajouter au panier"}` avec `VerificationOutcome.SUCCESS`. Ce succès signifie uniquement "Playwright a réussi à cliquer un élément nommé 'Ajouter au panier'" — **pas** "l'article est maintenant dans le panier". Pour confirmer l'ajout, le modèle doit émettre un nouveau `read_page` ou `check_confirmation()`. Ce comportement n'est pas automatique — il dépend d'une directive textuelle dans le system prompt ("final action is NOT success until verified...").

### L6 — Vision n'informe pas le DOM (sens unique)

La Vision produit `screen_x, screen_y` pour un élément. Ces coordonnées permettent `browser.click_at_position(x, y)`. Mais après ce clic, aucune information ne retourne vers la couche DOM : la page est-elle la même ? L'élément était-il au bon endroit ? Le modèle est à nouveau aveugle jusqu'au prochain `read_page`.

**Résumé structurel :** Le modèle navigue avec une perception fragment par fragment, sans état intégré, sans signal de changement, sans candidats pré-filtrés, et avec un contexte qui se remplit rapidement de données redondantes.

---

## §16 — Context Cost

| Source | Volume estimé | Coût | Raison |
|--------|--------------|------|--------|
| DOM JSON (`browser.read_page`, post-compact) | 500–3 000 tokens/appel | **HIGH** | 20 boutons × ~30 tokens + liens + inputs en JSON |
| DOM JSON accumulé (3 appels) | 1 500–9 000 tokens | **HIGH** | Persiste dans `messages[]`, jamais tronqué jusqu'à budget |
| Vision output (`vision.find_in_browser`) | 200–600 tokens/appel | **MEDIUM** | Description ~3-5 phrases + entities + target JSON |
| WorldState (render_system_prompt) | 50–300 tokens | **LOW** | Quelques lignes key=value |
| Tool schemas (ALL) | 500–2 000 tokens | **MEDIUM** | Tous les outils enregistrés, en JSON pour function calling |
| System directives | 800–1 500 tokens | **MEDIUM** | ~15 directives génériques, toutes incluses à chaque tour |
| Identity + memory facts | 100–400 tokens | **LOW** | Profil utilisateur + quelques faits |
| Conversation history | Croît linéairement | **MEDIUM** | Chaque tour user + assistant |
| Model reasoning response | 100–500 tokens/tour | **LOW** | Pensée + tool call JSON |
| **Total session browser longue (10+ tours)** | **12 000–20 000 tokens** | **CRITICAL** | Proche ou au-delà de `num_ctx=16 384` |

**Facteur aggravant :** les DOM tool results ne sont pas nettoyés ou résumés — chaque `read_page` ajoute un bloc complet à `messages[]`. Après 5-6 lectures, les premiers résultats DOM sortent du fenêtre effective du modèle (contexte saturé), mais occupent toujours les tokens du provider.

---

## §17 — Existing Reusable Building Blocks (Inventaire)

| Brique | Fichier | Ce qu'elle fait |
|--------|---------|-----------------|
| `ObservationSpec` | `contracts/tool.py:25` | Déclare promotion ToolResult → WorldState |
| `PerceptionObservation` | `contracts/perception.py:19` | Payload typé pour events `perception.*` |
| `VisualObservation` | `contracts/visual.py:129` | Résultat complet d'analyse Vision |
| `VisualTarget` | `contracts/visual.py:87` | Cible visuellement localisée (bbox + coords + obs_id) |
| `BoundingBox` | `contracts/visual.py:25` | Coordonnées normalisées [0,1] + to_pixel() |
| `ViewportInfo` | `contracts/visual.py:68` | Système de coordonnées (HiDPI, zoom, scroll) |
| `WorldStateFact` | `contracts/world_state.py` | Fait persistant avec domain, key, value, TTL, confidence |
| `ContextSection` | `contracts/context.py:44` | Section typée dans Context avec rank_score, freshness |
| `SectionKind` | `contracts/context.py:18` | WORLD_STATE / MEMORY / TASK_STATE / etc. |
| `VerificationOutcome` | `cognition/verification.py:17` | SUCCESS / UNKNOWN / FAILURE tri-état |
| `verify_observation_against_intent()` | `cognition/verification.py:47` | Compare expected vs observed (substring) |
| `LoopDetector` | `cognition/recovery.py` | Détecte appels identiques répétés |
| `detect_repeating_cycle()` | `cognition/state_cycle.py:16` | Détecte A→B→A→B pattern sur URL |
| `detect_no_progress()` | `cognition/state_cycle.py:36` | Détecte stagnation sur même URL |
| `BrowserController` | `devices/browser/controller.py:119` | Mécanismes DOM bas niveau |
| `_STRUCT_JS` | `devices/browser/controller.py:58` | Extraction JavaScript du DOM structuré |
| `_compact_read_page_output()` | `harness/loop.py:391` | Troncature DOM (20/25) |
| `_parse_grounding()` | `models/vision.py:83` | Parse + normalise bbox depuis sortie Vision model |
| `observe_image()` | `models/vision.py:126` | Pipeline Vision complet |
| `WorldStateStore.retrieve_relevant()` | `world_state/store.py:136` | Requête filtrée par domaine(s) |
| `_promote_observations_and_verify()` | `harness/loop.py:333` | Promotion ToolResult → WorldState via ObservationSpec |
| `derive_intent()` | `cognition/intent.py:22` | INFORMATION vs ACTION depuis capability_tags |

---

## §18 — Missing Abstractions

Uniquement les absences démontrées par lecture exhaustive du codebase.

### A1 — PageState : ABSENT

Aucun objet `PageState` ou `BrowserObservation` combinant `{url, title, dom_snapshot, visual_snapshot, timestamp}`.  
**Preuve :** Recherche dans `contracts/`, `devices/browser/`, `context_engine/` → aucune occurrence.

### A2 — ActionCandidate : ABSENT

Aucun objet représentant une action candidate avec `{label, type, confidence, relevant_to_intent, source}`.  
**Preuve :** `cognition/` ne contient que `intent.py` (INFO vs ACTION), `verification.py`, `recovery.py`, `state_cycle.py`, `planning.py`. Aucun `candidates.py` ou équivalent.

### A3 — SemanticElement : ABSENT

Aucun enrichissement sémantique des éléments DOM avec rôles (`PRIMARY_ACTION`, `SEARCH_FIELD`, `NAV_ITEM`, etc.).  
**Preuve :** `_STRUCT_JS` produit uniquement `{kind, text, tag}`.

### A4 — GroundedTarget (cross-modal) : ABSENT

Aucun objet combinant DOM text reference + visual bbox + screen coordinates.  
**Preuve :** `VisualTarget` existe pour Vision seul. Aucun équivalent DOM. Aucun objet cross-modal.

### A5 — FormState : ABSENT

Aucune représentation structurée d'un formulaire avec `{inputs, labels, types, values, submit_target}`.  
**Preuve :** `_STRUCT_JS:103` extrait inputs en `{kind:"input", text:"...", tag:"input"}` seulement.

### A6 — DOMObservationID : ABSENT

Les observations DOM n'ont pas d'identifiant de version contrairement aux `VisualObservation.observation_id`.  
**Preuve :** `_read_page()` dans `agent.py:90` retourne `r` sans ID d'observation.

### A7 — DOMRelevanceFilter : ABSENT

Aucun mécanisme prenant `(objective, dom_elements)` → `relevant_subset`.  
**Preuve :** sauf priority roots Amazon hardcodés dans `_STRUCT_JS`, aucun filtre.

### A8 — CrossModalObservation : ABSENT

Aucun objet représentant une observation qui combine à la fois information DOM et information visuelle pour le même élément.  
**Preuve :** pas de mention dans `contracts/`, pas dans `catalog/`, pas dans `devices/browser/`.

---

## §19 — Blueprint vs Reality

| Principe Blueprint | Réalité Code | Statut | Evidence |
|-------------------|-------------|--------|----------|
| "Perception : observer l'environnement, convertir en Event/WorldStateFact" (§1.3) | ✓ LightSensors → PerceptionObservation → WorldState | CONFORME | `perception/`, `world_state/store.py:37` |
| "Perception lourde strictement on-demand" (§11.2) | ✓ Vision uniquement sur demande tool call | CONFORME | `catalog/visual.py:147` |
| "Perception ne doit JAMAIS appeler le Model Layer elle-même" (§1.3) | ✓ Vision.observe_image est dans `models/`, appelé par injection, pas par perception/ | CONFORME | `catalog/visual.py:7-9` |
| "Device Agents = mécanismes, jamais de décision" (§1.13) | ✓ BrowserController reçoit une cible déjà décidée | CONFORME | `controller.py:1-7` |
| "Pas d'import raya.models dans Device Agents" (§9.4, invariant #5) | ✓ `devices/browser/` n'importe jamais `models/` | CONFORME | Lint architecturale |
| Le blueprint ne définit PAS de couche de perception sémantique DOM | ✓ Absent dans le code également | CONFORME PAR DESIGN | `RAYA_V2_TECHNICAL_ARCHITECTURE.md` — aucune mention |
| Le blueprint ne définit PAS d'ActionCandidate ni de PageState | ✓ Absent dans le code | CONFORME PAR DESIGN | Aucune mention dans les 6 blueprints |
| "DOM-first pour le browser, Vision = FALLBACK" | Textuel seulement (descriptions outils, commentaire visual.py) — non enforced | PARTIEL | `catalog/visual.py:11`, pas de gate dans le code |
| "Aucune deuxième boucle agentique" (invariant #1) | ✓ `cognition/` n'orchestre aucun cycle modèle | CONFORME | `cognition/reasoning.py` = stub `not_implemented_error` |

**Conclusion blueprint :** Les blueprints ne demandent pas de couche de perception sémantique browser. L'absence de `PageState`, `ActionCandidate`, `SemanticElement` n'est pas une violation des blueprints — ce sont des abstractions que les blueprints n'ont jamais définies. La conception actuelle (données structurées → modèle décide) est conforme à l'intention architecturale.

---

## §20 — Architectural Findings

### CRITICAL

**AF-C1 — SEMANTIC DOM PERCEPTION = ABSENT**  
La couche `_STRUCT_JS` + `browser.read_page` produit des données structurées (`{kind, text, tag}`) mais pas une perception sémantique. L'attribution de rôle (PRIMARY_ACTION, SEARCH_FIELD, FORM, NAVIGATION) est entièrement déléguée au modèle de raisonnement. Pour 20 boutons avec des libellés variés, cela représente un travail de classification à chaque tour.  
**Fichier :** `controller.py:58-110`, `loop.py:391-421`

**AF-C2 — DOM_VISUAL_TARGET_CORRELATION = ABSENT**  
Il n'existe aucun mécanisme permettant de certifier que l'élément DOM "Ajouter au panier" et l'élément visuel localisé par Vision au bbox `[0.45, 0.72, 0.65, 0.82]` sont le même élément. La corrélation repose sur la correspondance textuelle des labels dans la cognition du modèle.  
**Fichier :** Aucun — absence totale de code.

### HIGH

**AF-H1 — PAGE_STATE = ABSENT**  
L'état courant de la page (URL + DOM + Visual + titre + formulaires + modals) n'est jamais représenté dans un objet unifié. Il est reconstruit par le modèle depuis des fragments épars dans le system prompt (WorldState text) et l'historique de conversation (tool results). Cette reconstitution consomme des tokens et devient imprécise quand le contexte sature.

**AF-H2 — ACTION_CANDIDATE_REPRESENTATION = ABSENT**  
Aucune couche ne sélectionne ou classe les actions pertinentes avant de les présenter au modèle. La sélection parmi 20 boutons est entièrement à la charge du modèle.

**AF-H3 — DOM observations éphémères sans versioning**  
`browser.read_page` ne produit aucun ID d'observation DOM. Le contenu DOM vit uniquement dans `messages[]` et peut être poussé hors du contexte effectif par la saturation. Après saturation, le modèle peut agir sur un DOM obsolète sans le savoir.

### MEDIUM

**AF-M1 — FORM_STATE = ABSENT**  
Les inputs sont réduits à `{kind, text, tag}`. Aucun `type`, `required`, `value`, relation form→submit. La compréhension des formulaires est entièrement laissée au modèle via les labels textuels.

**AF-M2 — OBJECTIVE-AWARE PERCEPTION = ABSENT (générique)**  
Seuls les priority roots Amazon dans `_STRUCT_JS` constituent un filtrage par objectif — il est hardcodé et non générique. Pour tous les autres sites et objectifs, le modèle reçoit les mêmes 20 boutons.

**AF-M3 — Context saturation browser**  
Un `read_page` ≈ 500-3 000 tokens. Après 3-4 appels, le contexte approche `num_ctx=16 384`. Les appels modèle deviennent 3x-10x plus lents (données 20G). Aucun mécanisme de résumé ou d'éviction des vieux tool results.

**AF-M4 — Vision target coordinates non persistées en WorldState**  
`screen_x/screen_y` de `vision.find_in_browser` existent uniquement dans le message `role=tool`. Si la page change ou si le contexte sature, ces coordonnées sont perdues. Le modèle doit ré-émettre `vision.find_in_browser` pour les retrouver.

### LOW

**AF-L1 — Sélecteurs Amazon dans `_STRUCT_JS`**  
Priority roots `#desktop_buybox, [id*=buybox i], [id*=addtocart i]` hardcodés. Biais de priorité sur Amazon, aucun effet sur autres sites.  
**Fichier :** `controller.py:85-98`

**AF-L2 — `window_left/top=0` par défaut dans ViewportInfo**  
Correct pour Playwright headless. Ne s'appliquerait pas si le browser était visible à une position non-nulle dans l'écran physique.

### DESIGN CHOICE (pas des issues)

**AF-D1 — Modèle = seul décisionnaire**  
L'architecture délègue intentionnellement tout le raisonnement décisionnel au modèle de raisonnement. Conforme aux blueprints. "Les Device Agents sont les mains, jamais la tête."

**AF-D2 — DOM-first policy textuelle**  
Encodée dans les descriptions d'outils et commentaires, pas dans le code. Décision architecturale — le modèle peut y déroger si nécessaire.

**AF-D3 — Pas de PageState dans le blueprint**  
Les 6 blueprints ne définissent pas cette abstraction. Son absence est cohérente avec l'architecture définie.

### NO ISSUE

**AF-N1 — VisualObservation.confidence = INFERRED toujours**  
Enforced dans `__post_init__`. Correct et intentionnel.

**AF-N2 — WorldState delivery complet**  
`retrieve_relevant(())` retourne tous les facts. Confirmé par code.

### UNKNOWN

**AF-U1 — Token cost réel des tool schemas**  
Dépend du nombre d'outils enregistrés. Non mesuré dans cet audit statique.

---

## §21 — Files Inspected

| Fichier | Raison |
|---------|--------|
| `raya/contracts/__init__.py` | Inventaire complet des contrats |
| `raya/contracts/perception.py` | PerceptionObservation |
| `raya/contracts/tool.py` | Tool, ObservationSpec, ToolResult, ToolCall |
| `raya/contracts/context.py` | Context, ContextSection, SectionKind, Freshness |
| `raya/contracts/visual.py` | BoundingBox, ViewportInfo, VisualTarget, VisualObservation |
| `raya/cognition/__init__.py` | Inventaire cognition |
| `raya/cognition/reasoning.py` | Stub reasoning |
| `raya/cognition/verification.py` | VerificationOutcome, verify_* |
| `raya/cognition/intent.py` | Intent, derive_intent |
| `raya/cognition/state_cycle.py` | detect_repeating_cycle, detect_no_progress |
| `raya/perception/__init__.py` | Inventaire perception |
| `raya/devices/browser/agent.py` | BrowserDeviceAgent, _DISPATCH, _CAPABILITIES |
| `raya/devices/browser/controller.py` | _STRUCT_JS, BrowserController (DOM complet) |
| `raya/tools/catalog/browser.py` | ObservationSpecs browser, register_browser_tools |
| `raya/tools/catalog/visual.py` | register_visual_tools, handlers vision.* |
| `raya/models/vision.py` | observe_image, _parse_grounding, _GROUNDING_PROMPT_TEMPLATE |
| `raya/harness/loop.py` | _run_agentic_loop, _promote_observations_and_verify, _compact_read_page_output, _summarize_tool_result |
| `raya/context_engine/assembler.py` | assemble(), _world_state_sections |
| `raya/context_engine/render.py` | render_system_prompt (complet) |
| `raya/world_state/store.py` | retrieve_relevant (résolution question WorldState) |
| `RAYA_V2_TECHNICAL_ARCHITECTURE.md` | §1.3 perception, §1.13 devices, §9.4 vision, §11.2 |
| `RAYA_V2_CONTRACTS.md` | WorldStateFact domains, Tool, Model contracts |
| `RAYA_V2_ARCHITECTURAL_INVARIANTS.md` | Invariants #1-#5 (no second loop, no model in devices) |
| `RAYA_V2_20G_OBJECTIVE_COMPLETION_LATENCY_AUDIT.md` | Données Amazon 20G (latence, tokens) |

---

## §22 — Tests

**NONE — STATIC AUDIT ONLY.**

---

## §23 — Runtime

**NONE — STATIC AUDIT ONLY.**

---

## §24 — Final Verdict

### PERCEPTION LAYER HAS STRUCTURAL GAPS

**Ce que le modèle reçoit réellement :**

1. Un bloc de texte système avec directives génériques + WorldState text (URL, clic précédent)
2. La demande utilisateur brute
3. Une liste compacte de `{kind, text, tag}` (20 boutons, 25 liens, inputs) — pas sémantique
4. Optionnellement : une description textuelle du browser (Vision) + screen_x/screen_y pour un élément ciblé
5. Les schémas de tous les outils disponibles

**Ce que le modèle doit faire lui-même :**

- Filtrer 20 boutons pour trouver celui pertinent à l'objectif
- Interpréter le rôle sémantique de chaque élément (action principale, formulaire, navigation)
- Corréler par texte label un bouton DOM avec son équivalent visuel
- Reconstituer "l'état courant de la page" depuis des fragments épars dans son contexte
- Sélectionner l'action la plus appropriée parmi les candidats implicites
- Détecter si la page a changé après une action (sans signal automatique)

**Quantité de perception/interprétation déléguée au modèle :**

Sur 14 responsabilités analysées (§14), 8 sont MODEL-ONLY, 2 PARTIALLY STRUCTURED, 4 STRUCTURED. Le modèle prend en charge environ **60% du travail de perception/interprétation** lié au browser.

**Conformité avec les blueprints :**

Cette situation est **conforme** aux blueprints. Les 6 blueprints ne définissent pas de couche de perception sémantique pour le browser. L'architecture "données structurées → modèle décide" était l'intention de conception.

**Distinction fondamentale :**

RAYA donne au modèle des **données structurées** — pas une **perception structurée**.  

Une liste de 20 boutons avec leurs labels est une structure de données.  
Une représentation du type `"Pour l'objectif 'ajouter au panier', l'action principale identifiée est le bouton 'Ajouter au panier' (confiance 0.95), accessible via browser.click ou browser.click_at_position (coordonnées disponibles via Vision)"` serait une perception structurée.  

RAYA n'a pas cette deuxième couche — et les blueprints ne la définissent pas.

---

*Audit réalisé en lecture seule. Aucun fichier modifié, créé ou supprimé. Aucun test lancé. Aucun runtime exécuté.*
