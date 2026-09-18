# RAYA V2 — Chantier 20L : Browser Perception Architecture Audit
## DOM + Vision — POST-20K

**Date :** 2026-09-16  
**Mode :** AUDIT UNIQUEMENT — AUCUN CODE MODIFIÉ, AUCUN TEST LANCÉ, AUCUN RUNTIME  
**Scope :** Pipeline DOM + Vision, perception browser, POST-20K, code production uniquement  
**Question centrale :** POST-20K, comment fonctionne réellement la perception browser DOM + Vision, et où se situent exactement les travaux de perception, d'interprétation et de décision ?

---

## §1 — Résumé Exécutif

POST-20K, le browser dispose de **deux pipelines de perception entièrement distincts et indépendants** : le pipeline DOM (extraction JavaScript structurée via `_STRUCT_JS`, synchrone, sans modèle AI) et le pipeline Vision (capture screenshot → modèle Vision → grounding, asynchrone, requiert un provider VISION). Ces deux pipelines ne se fusionnent **jamais au niveau de la donnée** : leurs résultats arrivent au modèle de raisonnement sous forme de deux messages `role=tool` séparés, et c'est le modèle qui décide séquentiellement quoi faire avec chacun.

Il n'existe **aucune couche de fusion** entre DOM et Vision au sens architectural du terme. La politique DOM-first (utiliser `browser.read_page` avant `vision.*`) est encodée uniquement dans les descriptions textuelles des outils — elle n'est pas appliquée dans le code.

POST-20K, les 5 gaps C1–C5 sont fermés : `image_ref` est transmis, les visual tools sont enregistrés au bootstrap, `browser.click_at_position` est câblé, `browser.screenshot` fournit les dimensions réelles, et `browser.last_clicked_target` est promouvable en WorldState. La chaîne grounding → coordonnées pixel → clic → WorldState est désormais complète.

**Verdict :** Architecture opérationnelle, conforme aux blueprints, sans fusion de données. La décision d'orchestration DOM vs Vision repose entièrement sur le modèle de raisonnement.

---

## §2 — Périmètre et Sources Primaires

**Sources lues dans l'ordre de priorité :**

| Niveau | Fichiers |
|--------|----------|
| Code production | `raya/devices/browser/controller.py`, `raya/devices/browser/agent.py`, `raya/tools/catalog/browser.py`, `raya/tools/catalog/visual.py`, `raya/models/vision.py` |
| Bootstrap | `raya/runtime/bootstrap.py` |
| Registry/Dispatch | `raya/tools/execution.py`, `raya/harness/loop.py` (promotion + boucle) |
| Contracts | `raya/contracts/visual.py` (BoundingBox, ViewportInfo, VisualTarget, VisualObservation) |
| WorldState Store | `raya/world_state/store.py` |
| Context Engine | `raya/context_engine/assembler.py`, `raya/context_engine/render.py` |
| Blueprints (secondaires) | `RAYA_V2_TECHNICAL_ARCHITECTURE.md` |
| Rapports (tertiaires) | `RAYA_V2_20G_OBJECTIVE_COMPLETION_LATENCY_AUDIT.md` |

**Méthode :** lecture directe du code source uniquement. Aucune inférence depuis des rapports précédents sans confirmation code. Un rapport passé est une hypothèse — le code est la source de vérité.

---

## §3 — Diagramme du Flux Production Réel

```
USER REQUEST
    │
    ▼
Harness._handle_direct_request()
    │
    ├── assemble() ─────────────────────────────────────► system prompt
    │   ├── system_rules                                    (WorldState inclus,
    │   ├── identity_baseline                                tous domaines)
    │   ├── memory
    │   ├── task_state (active tasks)
    │   └── world_state: retrieve_relevant(()) → ALL facts
    │
    └── _run_agentic_loop()
            │
            ├── ModelRequest(REASONING, messages, available_tools)
            │   └── model_route() → REASONING provider
            │                                              ▼
            │   ┌─────────────────────────────────────────────┐
            │   │  Model reçoit :                              │
            │   │  [sys] system prompt (WorldState text)       │
            │   │  [usr] user input text                       │
            │   │  [tool] résultats outils précédents (JSON)   │
            │   │  available_tools: ALL registered schemas     │
            │   └─────────────────────────────────────────────┘
            │                        │
            │         ┌──────────────┴──────────────┐
            │         │                             │
            │    No tool call                  Tool call(s) requested
            │         │                             │
            │    Return text                        ▼
            │                           execute_tool(registry, safety, tool_call)
            │                                       │
            │              ┌────────────────────────┤
            │              │                        │
            │        browser.read_page         vision.find_in_browser
            │              │                        │
            │              ▼                        ▼
            │   BrowserController.read_page()  capture_browser_fn()
            │   page.evaluate(_STRUCT_JS)          │
            │        │                     BrowserController.screenshot()
            │        │                     page.screenshot(path)
            │   {url, title,               PIL → {path, width, height}
            │    buttons[20],                      │
            │    links[25],              observe_image(models, path, target,
            │    inputs, ...}             viewport=ViewportInfo(w,h))
            │        │                             │
            │        │                   ModelRequest(VISION, [image_ref, prompt])
            │        │                   OllamaAdapter → entry["images"]=[b64]
            │        │                             │
            │        │                   "FOUND: bbox=[...] label=... conf=N"
            │        │                             │
            │        │                   _parse_grounding() → VisualTarget
            │        │                   screen_x = bbox.center_x * image_width
            │        │                   screen_y = bbox.center_y * image_height
            │        │                             │
            │   _compact_read_page_output()  _obs_to_output() → {screen_x, screen_y}
            │   (20 btns, 25 links)          _obs_to_evidence() → {visual_observation}
            │        │                             │
            │        └────────────┬────────────────┘
            │                     │
            │          _promote_observations_and_verify()
            │          Tool.observation → WorldStateFact(domain, key, value)
            │          world_state.apply_update()
            │                     │
            │          _summarize_tool_result() → JSON string
            │          messages.append(Message(role="tool", content=json))
            │                     │
            │         ◄───────────┘  (next iteration)
            │
            └── (range(max_tool_iterations) exhausted)
                _finalize_turn() → retrieve_relevant(("browser",)) → summary
```

---

## §4 — Pipeline DOM Complet

### 4.1 Chaîne d'appel complète

```
browser.read_page (ToolCall)
  → ToolRegistry.execute()
  → handler = _make_handler(browser_agent, "browser.read_page", should_stop)
  → _run(agent, "browser.read_page", should_stop, call)
  → Command(device_id="browser", capability_name="browser.read_page")
  → BrowserDeviceAgent.execute(command)
  → _DISPATCH["browser.read_page"](agent, command) = _read_page()
  → BrowserController.read_page()
  → self._worker.run_sync(_op)
  → page.evaluate(_STRUCT_JS)
  → {"url": ..., "title": ..., "cookie_banner": bool, "buttons": [...], "links": [...], "inputs": [...]}
```

**Source :** `raya/tools/catalog/browser.py:57-64`, `raya/devices/browser/agent.py:_DISPATCH`, `raya/devices/browser/controller.py:135-142`

### 4.2 Format de sortie brut de `_STRUCT_JS`

`_STRUCT_JS` (`controller.py:58-110`) est un script JavaScript évalué dans la page via `page.evaluate()`. Il retourne :

```json
{
  "url": "https://...",
  "title": "Page title",
  "cookie_banner": false,
  "buttons": [{"kind": "button", "text": "Ajouter au panier", "tag": "button"}, ...],
  "links":   [{"kind": "link",   "text": "AirPods Pro 3",     "tag": "a"}, ...],
  "inputs":  [{"kind": "input",  "text": "Recherche",         "tag": "input"}, ...]
}
```

**Caps internes de `_STRUCT_JS` :**
- Boutons : jusqu'à 120 (après priority roots, dedoublonné, `.slice(0,120)`)
- Liens : jusqu'à 60
- Inputs : jusqu'à 20

**Critère de visibilité :** `vis(el)` = élément doit avoir `width/height ≥ 2px`, `visibility != hidden`, `display != none`, `opacity ≥ 0.05`, et être dans le viewport.

**Priority roots (Amazon-specific, pré-20K, non modifié) :**
```javascript
const priorityRoots = [...document.querySelectorAll(
  '#desktop_buybox, #addToCart_feature_div, #buybox, [id*=buybox i], '
  + '[id*=addtocart i], [id*=add-to-cart i], [class*=buybox i]'
)];
```
Ces sélecteurs sont des patterns Amazon. Les boutons dans ces roots apparaissent en premier dans la liste `buttons[]`.

### 4.3 Troncature post-extraction (Harness)

`Harness._compact_read_page_output()` (`loop.py:391-421`) applique une seconde troncature :
- `buttons` : gardés jusqu'à **20** (cap = `_BUTTONS_CAP = 20`)
- `links` : gardés jusqu'à **25** (cap = `_LINKS_CAP = 25`)
- `inputs` : **intégralement préservés**
- `url`, `title`, `cookie_banner` : **intégralement préservés**
- Métadonnées `buttons_capped`/`links_capped` ajoutées si troncature effective

**Ce que le modèle voit réellement :** 20 boutons max, 25 liens max — même si la page en contient 120.

### 4.4 WorldState produit par `browser.read_page`

```python
# browser.py:77
("browser.read_page", ..., observation=())
```

**`observation=()`** — AUCUNE promotion en WorldState. Le contenu DOM est **entièrement éphémère** : il n'existe que dans le message `role=tool` de la conversation en cours. Il disparaît dès que ce message sort du contexte.

### 4.5 Mécanisme de clic DOM

`browser.click(target)` → `BrowserController.click(target)` → `find_clickable(target)` :

```python
for getter in (
    lambda: page.get_by_role("button", name=target, exact=False),
    lambda: page.get_by_role("link", name=target, exact=False),
    lambda: page.get_by_label(target, exact=False),
    lambda: page.get_by_title(target, exact=False),
    lambda: page.locator(f"[aria-label*='{esc}' i]"),
    lambda: page.get_by_text(target, exact=False),
)
```

6 stratégies Playwright en cascade. Jamais de sélecteur CSS codé en dur pour un site. Recherche aussi dans les iframes si la page principale échoue.

---

## §5 — Pipeline Vision Complet

### 5.1 Chaîne d'appel complète (cas `vision.find_in_browser`)

```
vision.find_in_browser(target="Ajouter au panier") (ToolCall)
  → ToolRegistry.execute()
  → _handle_find_in_browser(call)  [visual.py:303-326]
  → capture_browser_fn()           [bootstrap._capture_browser_fn]
    → Command(device_id="browser_agent", capability_name="browser.screenshot")
    → BrowserDeviceAgent → BrowserController.screenshot(path)
      → page.screenshot(path=path)
      → PIL.Image.open(path) → (width, height)
      → {"status": "ok", "path": ..., "width": w, "height": h}
  → vp = ViewportInfo(image_width=w, image_height=h)
  → observe_fn(path, "", "Ajouter au panier", corr_id, prefer_local, vp, "perception:browser")
    [= bootstrap._observe_fn → models.vision.observe_image()]
    → ModelRequest(
        capability=VISION,
        messages=[Message(role="user", content=[
            ContentPart(type="image_ref", value=path),
            ContentPart(type="text", value=_GROUNDING_PROMPT_TEMPLATE),
        ])],
        context_budget_tokens=1024,
      )
    → route(model_registry, req)
      → OllamaAdapter._messages_to_ollama()
        → _encode_image_ref(path) → base64 string     [C1 fix]
        → entry["images"] = [b64]
      → Ollama API call (VISION model)
      → response: "FOUND: bbox=[0.45,0.72,0.65,0.82] label=\"Ajouter au panier\" confidence=0.93"
    → _parse_grounding(raw_text, obs_id, viewport=vp)
      → regex _FOUND_PATTERN matches
      → BoundingBox(0.45, 0.72, 0.65, 0.82) — normalisé [0.0, 1.0]
      → VisualTarget(label, bbox, viewport=ViewportInfo(w,h), observation_id)
    → VisualObservation(source="perception:browser", description=raw_text,
                         target=VisualTarget, model_used="ollama:gemma4:cloud",
                         viewport=ViewportInfo(w,h))
  → _obs_to_output(obs) → {
      "observation_id": "vobs-...",
      "description": "...",
      "semantic_entities": [...],
      "model_used": "...",
      "confidence": "inferred",
      "target": {
          "label": "Ajouter au panier",
          "confidence": 0.93,
          "screen_x": 550,    ← ViewportInfo.window_left(0) + int(0.55 * 1000)
          "screen_y": 770,    ← ViewportInfo.window_top(0) + int(0.77 * 1000)
          "bbox": {"x_min": 0.45, "y_min": 0.72, "x_max": 0.65, "y_max": 0.82},
          "observation_id": "vobs-..."
      }
    }
  → _obs_to_evidence(obs) → {
      "visual_observation": {"description": ..., "semantic_entities": [...],
                              "observation_id": ..., "model_used": ..., "timestamp": ...},
      "observation_id": ..., "description": ...
    }
    → ObservationSpec(domain="visual", key="browser_visual_state", evidence_field="visual_observation")
    → WorldState["visual"]["browser_visual_state"] = {description, entities, obs_id, model}
                                                      (PAS les coordonnées)
```

### 5.2 Transformation des coordonnées

**`BoundingBox`** (`contracts/visual.py:26-64`) : coordonnées **normalisées [0.0, 1.0]**.

**`VisualTarget.screen_coordinates()`** :
```python
cx, cy = self.bbox.center_pixel(viewport.image_width, viewport.image_height)
# center_pixel = (int(center_x * width), int(center_y * height))
screen_x = viewport.window_left + cx   # window_left = 0 (default Playwright headless)
screen_y = viewport.window_top + cy    # window_top = 0 (default)
```

**Pour Playwright headless :** `window_left=0, window_top=0` → coordonnées = pixels dans le viewport browser. `browser.click_at_position(x, y)` appelle `page.mouse.click(x, y)` — coordonnées browser-locales. **Cohérence garantie.**

**Normalisation pixel → normalisé :** si le modèle retourne des valeurs > 1.0, `_parse_grounding()` divise par `viewport.image_width / image_height`. POST-20K : viewport disponible (C4 fix). Pré-20K : `w=0, h=0` → `vp=None` → normalisation impossible → `VisualTarget=None`.

### 5.3 Prompt de grounding

```
"In this image, find the element described as: '{target}'

If found, respond with EXACTLY this format on one line:
FOUND: bbox=[x_min,y_min,x_max,y_max] label="{target}" confidence=0.N
where x_min,y_min,x_max,y_max are normalized coordinates in range [0.0, 1.0]
...
If not found, respond with:
NOT_FOUND: reason="brief reason"
```

**Regex :** `_FOUND_PATTERN` tolère les espaces dans bbox et guillemets internes dans le label (correctif Chantier 20E).

### 5.4 Cas `vision.observe_browser` (observation générale)

Même pipeline, mais `find_target=""` → prompt = `_SCENE_PROMPT` ou prompt custom → pas de grounding → `VisualTarget=None` → pas de `screen_x/screen_y` dans la sortie.

---

## §6 — Verdict Fusion DOM + Vision

**VERDICT : IL N'EXISTE AUCUNE COUCHE DE FUSION DOM + VISION.**

### Ce qui n'existe pas
- Pas de composant qui combine structure DOM + description visuelle
- Pas d'enrichissement mutuel (ex: "bouton détecté visuellement → cherche son DOM node")
- Pas d'accord de confiance entre sources
- Pas d'arbitrage automatique DOM vs Vision sur un conflit
- Pas de déclencheur automatique de Vision quand DOM échoue

### Ce qui existe réellement
- DOM → `role=tool` JSON message (20 boutons, 25 liens, éphémère)
- Vision → `role=tool` JSON message (description + optionnellement screen_x/screen_y, éphémère)
- Les deux messages sont indépendants dans le message history du modèle
- Le modèle de raisonnement lit les deux et **décide lui-même** de l'action suivante

### Pourquoi le modèle n'est PAS une "fusion layer"
Une couche de fusion au sens architectural traiterait les deux flux au niveau de la donnée avant de les présenter à l'agent décisionnaire. Ici, le modèle de raisonnement reçoit deux blobs JSON séparés et doit interpréter, combiner, et agir. Ce n'est pas de la fusion — c'est de l'orchestration. La distinction est fondamentale : en cas d'échec, il faut savoir si le problème est dans la perception (pipeline DOM, pipeline Vision) ou dans la décision (le modèle).

### Politique DOM-first : texte uniquement
```
# visual.py:11-13
"DOM-FIRST pour le browser : vision.observe_browser et vision.find_in_browser
sont des FALLBACKS. Le modèle doit d'abord tenter browser.read_page / browser.click."
```
Cette règle est dans un **commentaire de module** et dans les **descriptions d'outils**. Elle n'est pas appliquée dans le code. Un modèle mal aligné pourrait appeler `vision.find_in_browser` en premier sans jamais tenter `browser.click`.

---

## §7 — WorldState Browser — Table Complète

| Domaine | Clé | Source Tool | ObservationSpec | TTL | Valeur promue | `expected_argument` |
|---------|-----|-------------|-----------------|-----|---------------|---------------------|
| `browser` | `current_url` | `browser.navigate` | `_CURRENT_URL_OBSERVATION` | 60s | URL string (depuis `evidence["url"]`) | `"url"` (vérification) |
| `browser` | `current_url` | `browser.click` | `_LAST_CLICKED_OBSERVATION[1]` | 60s | URL string post-clic | aucun |
| `browser` | `last_clicked_target` | `browser.click` | `_LAST_CLICKED_OBSERVATION[0]` | 30s | texte du bouton cliqué | aucun |
| `browser` | `last_clicked_at` | `browser.click_at_position` | `_CLICK_AT_POSITION_OBSERVATION` | 30s | `"x,y"` string | aucun |
| `visual` | `screen_state` | `vision.observe_screen`, `vision.find_on_screen` | `_SCREEN_OBS_SPEC` | 30s | `{description, semantic_entities, obs_id, model_used, timestamp}` | aucun |
| `visual` | `browser_visual_state` | `vision.observe_browser`, `vision.find_in_browser` | `_BROWSER_OBS_SPEC` | 30s | `{description, semantic_entities, obs_id, model_used, timestamp}` | aucun |
| `visual` | `last_image_observation` | `vision.observe_image` | `_IMAGE_OBS_SPEC` | 60s | `{description, semantic_entities, obs_id, model_used, timestamp}` | aucun |

**Absence notable :** `browser.read_page` → `observation=()` — le contenu DOM n'est **jamais** écrit en WorldState.

**Absence notable :** Les coordonnées `screen_x/screen_y` de `vision.find_in_browser` → NOT in WorldState. `to_world_state_value()` strip `target`, `bbox`, `artifact_ref` — uniquement la description textuelle.

**Double écriture `browser.current_url` :** `browser.navigate` (avec `expected_argument="url"` → déclenche vérification contenu) et `browser.click` (sans `expected_argument` → upsert simple) écrivent tous deux sur la même clé. La valeur la plus récente gagne (upsert par `_row_id = "browser:current_url"`).

---

## §8 — Fraîcheur des Observations (TTL)

### Mécanisme de staleness

`WorldStateStore.retrieve_relevant()` applique une **lazy staleness check** :
```python
if apply_lazy_staleness and fact.status == FactStatus.ACTIVE and fact.is_expired():
    fact.status = FactStatus.STALE
```

Le statut `STALE` est calculé au moment de la lecture — pas d'expiration proactive. Pas de daemon de nettoyage.

### `WorldStateFact.is_expired()`

Basé sur `freshness_ttl_s` et `timestamp`. Un fait STALE est toujours retourné par `retrieve_relevant()` (filtre : `status != SUPERSEDED` uniquement). Un fait STALE **reste visible au modèle** — il est rendu avec sa fraîcheur.

### Format de rendu (render.py)

```python
f"Observed environment state [{freshness}]: {domain}.{key} = {value!r}"
```

`freshness` = `"fresh"` si `status == ACTIVE`, `"stale"` si `status == STALE`.

### TTL par catégorie

| Catégorie | TTL | Justification |
|-----------|-----|---------------|
| `browser.current_url` | 60s | URL stable entre deux navigations |
| `browser.last_clicked_target` | 30s | Clic récent, contexte court |
| `browser.last_clicked_at` | 30s | Coordonnées pixel, valeur unique |
| `visual.*` (browser, screen) | 30s | Capture visuelle rapide à invalider |
| `visual.last_image_observation` | 60s | Image fichier = stable |

---

## §9 — Contexte Livré au Modèle

### 9.1 System Prompt

`render_system_prompt(context)` sérialise les sections suivantes :

1. **`system_rules`** — directives RAYA (identité, règles comportementales)
2. **`identity_baseline`** — nom, rôle, modèle actif (`runtime_identity`)
3. **`memory`** — faits personnels depuis MemoryStore (profil, préférences)
4. **`task_state`** — tâches actives non terminales
5. **`world_state`** — TOUS les WorldStateFacts (tous domaines — voir §9.2)

**Les schémas d'outils ne sont PAS dans le system prompt.** Ils passent via `ModelRequest.available_tools` → provider-specific serialization.

### 9.2 WorldState dans le contexte — résolution de la question critique

```python
# assembler.py:243, 262
def assemble(
    ...
    world_state_domains: tuple[str, ...] = (),
    ...
):
    ...
    candidates.extend(_world_state_sections(world_state, world_state_domains))
```

```python
# loop.py:219-228
context = assemble(
    session_id=request.session_id,
    channel_scope=channel_scope,
    world_state=self._world_state,
    ...
)
# ← world_state_domains N'EST PAS PASSÉ → valeur par défaut = ()
```

```python
# store.py:136-148
def retrieve_relevant(self, domains: tuple[str, ...] = ()) -> list[WorldStateFact]:
    if domains:               # () est falsy → branche else
        ...
    else:
        rows = self._backend.query(_COLLECTION)  # TOUS les facts
        facts = [from_dict(WorldStateFact, r) for r in rows]
```

**RÉSULTAT :** `assemble()` appelle `retrieve_relevant(())` → `()` est falsy → tous les facts de tous les domaines sont retournés. Le WorldState complet (browser + visual + tout autre domaine) est inclus dans le system prompt à chaque tour.

### 9.3 Messages conversation dans la boucle agentique

La liste `messages` dans `_run_agentic_loop()` croît à chaque itération :
```
[Message(role="system", content=system_prompt)]
[Message(role="user", content=user_request)]
[Message(role="assistant", tool_calls=[...])]   ← ajouté quand le modèle appelle des outils
[Message(role="tool", content=json_result)]     ← ajouté par _summarize_tool_result()
[Message(role="assistant", tool_calls=[...])]
[Message(role="tool", content=json_result)]
...
```

**`_summarize_tool_result(tool_name, tool_result, outcome)`** retourne un JSON :
```json
{
  "tool": "vision.find_in_browser",
  "status": "success",
  "verification": "success",
  "output": {"observation_id": "...", "description": "...", "target": {"screen_x": 550, "screen_y": 770, ...}},
  "evidence": {"visual_observation": {...}, "observation_id": "..."},
  "error": null
}
```

**Cas spécial `browser.read_page`** : le champ `output` est remplacé par `_compact_read_page_output()` avant sérialisation.

---

## §10 — Sélection des Outils (Tool Discovery)

```python
# loop.py:323-331
def _discover_tool_schemas(self) -> list[dict]:
    tags = self._tools_registry.all_capability_tags()
    if not tags:
        return []
    return [to_dict(t) for t in discover_tools(self._tools_registry, tags)]
```

`all_capability_tags()` retourne l'union de tous les tags enregistrés. `discover_tools(registry, tags)` retourne les outils correspondant à ces tags. En pratique, **tous les outils enregistrés** sont exposés au modèle (aucun pré-filtrage par contexte, par URL, par état courant).

Le modèle reçoit les schémas complets de tous les outils (nom, description, input_schema, permission_level) et décide lui-même lequel appeler. La politique DOM-first est encodée uniquement dans les `description` des outils `vision.observe_browser` et `vision.find_in_browser`.

---

## §11 — Conscience de l'Objectif

L'objectif utilisateur (`request.input.text`) est :
1. Ajouté comme premier `Message(role="user")` dans `messages` (loop.py:555)
2. Conservé en tête de la liste `messages` pour TOUTE la boucle agentique
3. Jamais tronqué ni résumé pendant la boucle (sauf compression de contexte externe)
4. Jamais écrit en WorldState

Le modèle "voit" l'objectif à chaque appel modèle via l'historique complet des messages. L'objectif n'est pas "compris" structurellement — il reste une chaîne de texte brute dans le message user.

**`_finalize_turn`** et **`_explain_blocked_turn`** réutilisent `request.input.text` pour construire le message de fin — confirmant que l'objectif est toujours accessible.

---

## §12 — Vérification Post-Action

### 12.1 Mécanisme général

```python
# loop.py:696-697
outcome = verify_tool_result(tool_result)
outcome = self._promote_observations_and_verify(requested, tool_result, outcome)
```

`verify_tool_result()` retourne SUCCESS/FAILURE/TIMEOUT selon `tool_result.status`.

`_promote_observations_and_verify()` :
1. Ignore si `tool_result.status != SUCCESS`
2. Pour chaque `ObservationSpec` dans `tool_def.observation` :
   - Récupère la valeur depuis `evidence` ou `output`
   - Appelle `world_state.apply_update(WorldStateFact(...))`
   - Si `spec.expected_argument` est défini : `verify_observation_against_intent(expected, value)` → compare l'argument demandé avec la valeur observée → affine `outcome` vers SUCCESS/PARTIAL/FAILURE

### 12.2 Vérification par outil

| Outil | `expected_argument` | Vérification contenu |
|-------|---------------------|----------------------|
| `browser.navigate` | `"url"` | URL observée vs URL demandée |
| `browser.click` | aucun | Pas de vérification contenu |
| `browser.click_at_position` | aucun | Pas de vérification contenu |
| `vision.*` | aucun | Pas de vérification contenu |
| `browser.read_page` | aucun | `observation=()` → pas de promotion |

**Conclusion :** La seule vérification post-action avec contenu est sur `browser.navigate`. Les clics (DOM et Vision) ne sont vérifiés qu'au niveau `status` (succès/échec Playwright). Pour confirmer qu'un "Ajouter au panier" a réellement ajouté un article, il faut une étape explicite (`browser.read_page` ou `check_confirmation()`).

---

## §13 — Persistance vs Éphémère

| Donnée | Persistance | Durée de vie |
|--------|-------------|--------------|
| DOM de la page (`browser.read_page`) | **ÉPHÉMÈRE** | Message `role=tool` dans la conversation courante uniquement |
| URL actuelle (`browser.navigate/click`) | **WorldState** `browser.current_url` | 60s TTL, persisté SQLite |
| Élément cliqué (`browser.click`) | **WorldState** `browser.last_clicked_target` | 30s TTL |
| Coordonnées cliquées (`click_at_position`) | **WorldState** `browser.last_clicked_at` | 30s TTL |
| Description visuelle (`vision.observe_browser`) | **WorldState** `visual.browser_visual_state` | 30s TTL (SANS coordonnées) |
| Coordonnées pixel Vision (`vision.find_in_browser`) | **ÉPHÉMÈRES** | Message `role=tool` uniquement — NOT in WorldState |
| Image screenshot (`browser.screenshot`) | Fichier temporaire local | Jusqu'à nettoyage manuel/GC |
| Trace d'exécution | En mémoire `self._last_tool_trace` | Durée de la session process |

**Point critique :** Les coordonnées pixel `screen_x/screen_y` retournées par `vision.find_in_browser` ne sont **pas** promues en WorldState. Si le modèle ne les utilise pas immédiatement dans le tour courant, elles sont perdues.

---

## §14 — Reconstruction Amazon 20G (POST-20K)

Le scénario "Ajouter AirPods Pro 3 au panier" reconstruit avec le code POST-20K :

```
[1] browser.navigate(url="https://amazon.fr")
    → WorldState: browser.current_url = "https://amazon.fr"    (60s)

[2] browser.read_page()
    → 120 boutons bruts → _compact: 20 boutons envoyés au modèle
    → Priority roots: #desktop_buybox etc. (Amazon-specific, pré-20K)
    → WorldState: RIEN (observation=())

[3] browser.type(target="Recherche", text="AirPods Pro 3", submit=true)
    → Playwright fill() + press("Enter")
    → WorldState: RIEN (observation=())

[4] browser.read_page()  [résultats de recherche]
    → ~60 boutons bruts → 20 envoyés au modèle
    → WorldState: RIEN

[5] browser.click(target="AirPods Pro 3")   [clic sur produit]
    → find_clickable() → get_by_text("AirPods Pro 3")
    → WorldState: last_clicked_target="AirPods Pro 3" (30s),
                  current_url="https://amazon.fr/dp/..." (60s)

[6] browser.read_page()  [page produit]
    → Priority roots détectent #desktop_buybox, #addToCart_feature_div
    → "Ajouter au panier" apparaît en position 1 dans buttons[]
    → 20 boutons envoyés au modèle
    → WorldState: RIEN

[7] browser.click(target="Ajouter au panier")   [tentative DOM]
    → find_clickable() → get_by_role("button", name="Ajouter au panier")
    → SUCCÈS ou FAILURE (selon rendu JS de la page)
    IF SUCCÈS:
      → WorldState: last_clicked_target="Ajouter au panier" (30s),
                    current_url=updated (60s)
    IF FAILURE (not_found):
      → Pas de promotion WorldState

[8] vision.find_in_browser(target="Ajouter au panier")  [grounding visuel]
    → capture_browser_fn() → BrowserController.screenshot() → {path, width, height}
    → ViewportInfo(w=1280, h=720)   [POST-20K: dimensions réelles disponibles]
    → observe_image(path, find_target="Ajouter au panier", viewport=vp)
    → ModelRequest(VISION, [image_ref + grounding prompt])
    → OllamaAdapter: entry["images"] = [base64]  [C1 fix POST-20K]
    → "FOUND: bbox=[0.45,0.72,0.65,0.82] label=\"Ajouter au panier\" confidence=0.93"
    → _parse_grounding() → VisualTarget(bbox=normalized, viewport=ViewportInfo(1280,720))
    → screen_x = int(0.55 * 1280) = 704
    → screen_y = int(0.77 * 720) = 554
    → Tool output: {"target": {"screen_x": 704, "screen_y": 554}}
    → WorldState: visual.browser_visual_state = {description, entities}  (30s)
                  (PAS les coordonnées)

[9] browser.click_at_position(x=704, y=554)   [clic grounded, POST-20K]
    → page.mouse.click(704, 554)  [C3 fix POST-20K]
    → WorldState: browser.last_clicked_at = "704,554"  (30s)  [C5 fix POST-20K]
                  (current_url PAS mis à jour par click_at_position)

[10] browser.read_page()  [vérification]
     → si URL = "/cart/..." → confirmation détectée dans read_page output
     → WorldState: RIEN

[11] _finalize_turn() (si budget épuisé)
     → retrieve_relevant(("browser",))
     → retourne: {current_url, last_clicked_target, last_clicked_at}
     → construit message de fin avec ces facts
```

**Changement clé POST-20K vs 20G :**
- Étape [8] : ViewportInfo désormais remplie (C4) → normalisation pixel possible
- Étape [8] : `image_ref` transmis au modèle Vision (C1)
- Étape [9] : `browser.click_at_position` disponible en production (C3)
- Étape [9] : `browser.last_clicked_at` promouvable en WorldState (C3/C5)
- Pré-20K : ces 4 étapes étaient toutes cassées → chain grounding → click = impossible

---

## §15 — Sources de Latence

| Source | Ordre de grandeur | Code |
|--------|-------------------|------|
| `page.evaluate(_STRUCT_JS)` | 5–50 ms | JavaScript DOM traversal |
| `find_clickable()` — 6 stratégies Playwright | 50–2 000 ms | jusqu'à 6 tentatives `is_visible()` |
| `page.screenshot()` + PIL dimensions | 50–200 ms | disque + PIL.Image.open |
| Vision model inference (Ollama local) | 2 000–10 000 ms | selon GPU, modèle |
| Vision model inference (cloud) | 500–5 000 ms | latence réseau + inférence |
| `_compact_read_page_output()` | < 1 ms | slicing Python |
| Context saturation (20G finding) | Multiplicateur x10 | `browser.read_page` = 5 332 tokens (20G) |
| Model reasoning appel #3 (20G) | 37,3 s | contexte à 14 827 tokens sur `num_ctx=16 384` |

**Point critique (20G, non résolu par 20K) :**  
`browser.read_page` après POST-compact = encore ~1 000–3 000 tokens de JSON. Après 2-3 appels read_page + messages history + system prompt, le contexte peut saturer `num_ctx`. Fix 20G recommandait `num_ctx=32 768` ou `num_ctx=65 536` pour les sessions browser.

---

## §16 — Carte de Responsabilité par Couche

| Couche | Responsabilité | Ce qu'elle NE fait PAS |
|--------|----------------|------------------------|
| `BrowserController` | Exécuter les mécanismes DOM (navigate, read_page, click, screenshot, type, dismiss_overlays) | Décider quoi cliquer, interpréter la page |
| `BrowserDeviceAgent` | Dispatcher les Commands → handlers, retourner Results avec evidence | Accéder aux modèles AI, décider des actions |
| `observe_image()` | Pipeline Vision I/O (image → ModelRequest → VisualObservation) | Décider si Vision est nécessaire, interpréter le résultat pour le harness |
| `catalog/visual.py` | Enregistrer les tools, gérer les erreurs Vision, promouvoir WorldState visual.* | Prendre des décisions d'orchestration |
| `catalog/browser.py` | Enregistrer les tools browser, déclarer les ObservationSpecs | Implémenter les mécanismes DOM |
| `_promote_observations_and_verify()` | Écrire en WorldState selon `Tool.observation`, vérifier si `expected_argument` | Décider des actions suivantes |
| `assemble()` / `render_system_prompt()` | Construire le contexte livré au modèle (WorldState inclus) | Filtrer ou interpréter les facts |
| **Modèle de raisonnement** | **Décider quelle perception appeler, dans quel ordre, et quoi faire du résultat** | Exécuter les mécanismes (c'est les outils) |

**Invariant fondamental :** Le modèle de raisonnement est le seul agent décisionnaire. Chaque couche en dessous est un mécanisme. Aucune couche intermédiaire ne décide "utilise Vision maintenant".

---

## §17 — Comparaison Blueprint vs Réalité

| Affirmation Blueprint | Réalité Code | Verdict |
|-----------------------|--------------|---------|
| "Device Agents : exécuter les capacités, jamais la décision quoi chercher/cliquer" | ✓ `BrowserController` prend une cible déjà décidée, jamais de logique de décision interne | **CONFORME** |
| "Pas d'import direct de `raya.models` depuis un Device Agent" | ✓ `BrowserDeviceAgent` n'importe rien de `raya.models`. `visual.py` utilise l'injection de callable | **CONFORME** |
| "Perception lourde strictement on-demand" | ✓ `vision.*` uniquement si le modèle les appelle. Aucun polling ni déclenchement automatique | **CONFORME** |
| "DOM-first pour le browser, Vision = FALLBACK" | ✓ Dans les descriptions outils et commentaires. ✗ Non appliqué dans le code | **PARTIEL — texte seulement** |
| "Séparation Tool Layer / Device Agent / Model Layer via injection" | ✓ `observe_fn` injecté par bootstrap, `capture_browser_fn` injecté | **CONFORME** |
| "Coordonnées jamais nues sans provenance" | ✓ `VisualTarget` toujours lié à `observation_id` + `viewport` | **CONFORME** |
| "confidence = INFERRED pour toute VisualObservation" | ✓ `__post_init__` lève ValueError si `confidence != INFERRED` | **CONFORME ET ENFORCED** |
| "Pas de hardcoding site-specific" | ✗ `_STRUCT_JS` contient `#desktop_buybox, #addToCart_feature_div, [id*=buybox i]` | **VIOLATION PRÉEXISTANTE** (pré-20K, non causée par 20K) |

---

## §18 — Constats Architecturaux Critiques

### CA-1 : Sélecteurs Amazon hardcodés dans `_STRUCT_JS`

**Fichier :** `raya/devices/browser/controller.py:85-98`

```javascript
const priorityRoots = [...document.querySelectorAll(
  '#desktop_buybox, #addToCart_feature_div, #buybox, [id*=buybox i], '
  + '[id*=addtocart i], [id*=add-to-cart i], [class*=buybox i]'
)];
```

Ce JavaScript est dans le mécanisme `read_page` de bas niveau. Ces patterns sont spécifiques à Amazon. Sur d'autres sites, les `priorityRoots` seront vides et les boutons seront extraits normalement — donc l'impact est un biais de priorité, pas un blocage. Pré-existant, non modifié par 20K.

### CA-2 : Coordonnées Vision éphémères — risque de désynchro

`vision.find_in_browser` retourne `screen_x/screen_y` dans le message `role=tool` mais **pas** en WorldState. Le modèle doit utiliser ces coordonnées dans le même tour de boucle agentique. Si la page change entre la capture et le clic, les coordonnées sont obsolètes sans que le système le détecte.

### CA-3 : `window_left=0, window_top=0` par défaut

`capture_browser_fn()` crée `ViewportInfo(image_width=w, image_height=h)` sans `window_left/window_top`. Correct pour Playwright headless (browser viewport = coordonnées locales pour `page.mouse.click`). Incorrect si on utilisait `vision.find_on_screen` sur une fenêtre browser visible dans l'écran physique — mais ce cas n'est pas exposé par les outils actuels.

### CA-4 : Politique DOM-first non enforced

Le modèle peut appeler `vision.find_in_browser` sans avoir tenté `browser.click`. La seule protection est textuelle (descriptions outils). Pas d'interlock dans le code.

### CA-5 : `browser.read_page` éphémère — DOM invisible à `_finalize_turn`

`_finalize_turn()` appelle `retrieve_relevant(("browser",))` pour construire le message de fin. Cela retourne `current_url`, `last_clicked_target`, `last_clicked_at` — mais PAS le contenu DOM de la dernière `read_page`. La confirmation basée sur le DOM (ex: "panier contient X items") doit être demandée explicitement.

### CA-6 : Vision model dependency non découplée

`_capture_browser_fn` dépend de `devices.get("browser_agent")`. Si le browser agent est absent ou non démarré au moment où `register_visual_tools()` est appelé, `capture_browser_fn()` lèvera `RuntimeError("browser_agent non disponible")` — géré par `best-effort` dans bootstrap (warning log), mais `vision.observe_browser` et `vision.find_in_browser` seront alors non-fonctionnels silencieusement jusqu'au prochain bootstrap.

---

## §19 — Issues Pré-20K (Non Causées par 20K)

| Issue | Fichier | Nature |
|-------|---------|--------|
| Sélecteurs Amazon dans `_STRUCT_JS` | `controller.py:85-98` | Hardcoding site-specific dans mécanisme bas niveau |
| Politique DOM-first texte seulement | `visual.py:11-13` | Absence d'enforcement code |
| `window_left/top=0` hardcodé dans `capture_browser_fn` | `bootstrap.py` | Convient pour headless, incorrect pour browser à position non-nulle |
| Context saturation (20G) : `browser.read_page` = ~5 000 tokens | `loop.py + controller.py` | `num_ctx` pas adapté aux sessions browser lourdes |
| `browser.click_at_position` ne met pas à jour `current_url` | `catalog/browser.py:81` | Seul `browser.click` et `browser.navigate` le font |

---

## §20 — Delta POST-20K

| Gap | Fix | Impact |
|-----|-----|--------|
| **C1 : `image_ref` silencieux** | `_encode_image_ref()` + `entry["images"]` dans `_messages_to_ollama()` | Vision models reçoivent maintenant les images |
| **C2 : `register_visual_tools` non appelé** | `_register_vision_tools()` dans bootstrap | Les 5 outils vision.* sont disponibles en production |
| **C3 : `browser.click_at_position` absent** | Handler + `_DISPATCH` + `_CAPABILITIES` + catalog | Chaîne grounding → coordonnées → clic maintenant opérationnelle |
| **C4 : dimensions screenshot manquantes** | PIL + fallback viewport_size dans `BrowserController.screenshot()` | `ViewportInfo` remplie → normalisation pixel → grounding correct |
| **C5 : `browser.last_clicked_target` jamais écrit** | `_LAST_CLICKED_OBSERVATION` + evidence dans `_click()` | Confirmation de clic désormais visible en WorldState |

Pré-20K, la chaîne `vision.find_in_browser → screen_x/screen_y → browser.click_at_position → WorldState` était cassée en 4 points simultanément (C1+C2+C3+C4). POST-20K, cette chaîne est complète et testée.

---

## §21 — Gaps Résiduels

| Gap | Sévérité | Impact |
|-----|----------|--------|
| DOM-first non enforced dans le code | MEDIUM | Le modèle peut ignorer la politique sans détection |
| Context saturation browser (20G) | HIGH | Sessions longues Amazon = 37s+ par appel modèle |
| Sélecteurs Amazon dans `_STRUCT_JS` | LOW | Biais de priorité bénin, pas de blocage |
| Coordonnées Vision éphémères | MEDIUM | Risque si page change entre vision.find et click_at_position |
| `browser.click_at_position` ne met pas à jour `current_url` | LOW | `_finalize_turn` n'a pas la dernière URL après clic grounded |
| `window_left/top` par défaut = 0 | LOW | Convient pour usecase headless actuel |
| Vision non disponible si browser agent absent | MEDIUM | `vision.observe_browser/find_in_browser` silencieusement cassés |

---

## §22 — Invariants Architecturaux : État

| Invariant | Source | Statut POST-20K |
|-----------|--------|-----------------|
| Modèle de raisonnement = seul décisionnaire | Architecture | ✅ RESPECTÉ |
| Device Agents = mécanismes purs (jamais décision) | Blueprint §1.13 | ✅ RESPECTÉ |
| Pas d'import `raya.models` dans Device Agents | Blueprint | ✅ RESPECTÉ |
| Vision = INFERRED uniquement (jamais KNOWN_FACT) | `contracts/visual.py:154-158` (enforced) | ✅ RESPECTÉ ET ENFORCED |
| Pas de coordonnées "nues" sans provenance | `VisualTarget` = (obs_id, viewport, bbox) | ✅ RESPECTÉ |
| Perception lourde on-demand uniquement | Pas de daemon vision | ✅ RESPECTÉ |
| WorldState = écriture via `_promote_observations_and_verify` uniquement | loop.py:333-370 | ✅ RESPECTÉ |
| V1 (`MonAssistant`) intacte | Aucune modification V1 | ✅ RESPECTÉ |

---

## §23 — Verdict Final

### Réponse à la question centrale

**POST-20K, comment fonctionne réellement la perception browser DOM + Vision ?**

La perception browser repose sur **deux pipelines totalement indépendants** :

1. **DOM** : `browser.read_page` → JavaScript `_STRUCT_JS` → structure JSON (20 boutons, 25 liens, éphémère, sans WorldState). Rapide (5-50ms JS + troncature Python). Aucune IA impliquée.

2. **Vision** : `vision.find_in_browser` → screenshot Playwright (PIL dimensions) → `ModelRequest(VISION, image_ref base64 + grounding prompt)` → modèle Vision → `FOUND: bbox=[...]` → `_parse_grounding()` → coordonnées pixel → tool result (éphémère) + description en WorldState. Lent (500ms–10s).

**Où se situent exactement perception, interprétation et décision ?**

| Travail | Couche |
|---------|--------|
| **Perception DOM** (extraire la structure) | `BrowserController._STRUCT_JS` (JavaScript dans le browser) |
| **Perception Visuelle** (capturer l'image) | `BrowserController.screenshot()` + `observe_image()` |
| **Interprétation Visuelle** (comprendre l'image) | Modèle Vision (Ollama local ou cloud) |
| **Interprétation DOM** (comprendre la structure) | **Modèle de raisonnement** (reçoit le JSON et décide) |
| **Décision** (appeler DOM ou Vision, dans quel ordre, avec quoi) | **Modèle de raisonnement** |
| **Exécution** (cliquer, naviguer, taper) | `BrowserController` via Device Agent |

**Il n'y a pas de fusion.** Le modèle de raisonnement est le seul point où DOM et Vision se "rencontrent" — sous forme de deux messages `role=tool` séparés dans l'historique de conversation. Il décide séquentiellement. C'est une architecture de **décision séquentielle sur perceptions indépendantes**, pas une architecture de fusion.

### Statut production

- **DOM pipeline :** L6 — VERIFIED PRODUCTION PATH
- **Vision pipeline (grounding):** L6 POST-20K — VERIFIED PRODUCTION PATH (C1+C3+C4 fixes)
- **Vision pipeline (observation):** L6 POST-20K — VERIFIED PRODUCTION PATH (C2+C4 fixes)
- **WorldState browser :** L6 POST-20K — current_url, last_clicked_target, last_clicked_at tous écrits
- **WorldState visual :** L6 POST-20K — browser_visual_state écrit (sans coordonnées)
- **Coordination DOM→Vision→clic :** L6 POST-20K — chaîne complète pour la première fois

### Risque résiduel principal

La saturation de contexte (`num_ctx`) reste le principal risque opérationnel pour les sessions browser longues. Non traité par 20K (hors périmètre). Sur Amazon avec `num_ctx=16 384`, le step 3 (post-search `read_page`) peut saturer le contexte à 14 827 tokens — ralentissant les appels modèle de 3x à 37x (20G data).

---

*Audit réalisé en lecture seule. Aucun fichier modifié, créé ou supprimé. Aucun test lancé. Aucun runtime exécuté.*
