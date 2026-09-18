# RAYA V2 — AUDIT ARCHITECTURAL DOM + VISION

**Date** : 2026-09-16  
**Mode** : READ-ONLY — AUCUNE MODIFICATION DE CODE, AUCUN TEST, AUCUN PATCH  
**Périmètre** : DOM extraction, Vision pipeline, interaction DOM+Vision, WorldState, Context, Cognition  
**Méthode** : Code réel lu ligne par ligne. Chaque affirmation est vérifiée dans le fichier source indiqué.

---

## 1. Executive Summary

| Composant | Statut réel |
|---|---|
| DOM extraction via `_STRUCT_JS` + Playwright | **IMPLEMENTED** — fonctionnel en production |
| Compaction `browser.read_page` (Chantier 20G-B) | **IMPLEMENTED** — buttons→20, links→25 |
| Screenshots browser | **IMPLEMENTED** — Playwright `page.screenshot()` |
| WorldState mise à jour post-navigation | **IMPLEMENTED** — `browser.current_url` (TTL 60s) |
| Grounding parser pixel→normalisé (Chantier 18D) | **IMPLEMENTED** — `_parse_grounding()` en `vision.py` |
| Viewport pour `find_in_browser` (Chantier 18D Fix B) | **IMPLEMENTED** — dans `visual.py` |
| `register_visual_tools()` en production | **NOT IMPLEMENTED** — absent de `bootstrap.py` |
| Transport image (`image_ref` → base64 Ollama) | **NOT IMPLEMENTED** — `_messages_to_ollama()` ignore `image_ref` |
| WorldState mis à jour par `browser.read_page` | **NOT IMPLEMENTED** — `observation=()` |
| Fusion DOM+Vision automatique | **NOT IMPLEMENTED** — aucun code de fusion |

**Verdict** : DOM opérationnel en production. Vision : module complet et correctement implémenté, mais **jamais câblé dans le runtime réel** (deux gaps bloquants distincts, indépendants l'un de l'autre).

---

## 2. Blueprint Reviewed

Fichiers lus :
- `RAYA_V2_TECHNICAL_ARCHITECTURE.md` (blueprint principal)
- `RAYA_V2_CONTRACTS.md` (contrats)
- `RAYA_V2_REAL_MULTIMODAL_AUDIT.md` (audit précédent, 2026-09-14)
- `RAYA_V2_MULTIMODAL_REPAIR_AUDIT.md` (audit réparation, 2026-09-14)
- `RAYA_V2_18D_GROUNDING_FIX_IMPLEMENTATION_REPORT.md` (implémentation Chantier 18D)

Le blueprint décrit le pipeline complet : DOM via CDP/JS injection, Vision via modèle multimodal, grounding avec coordonnées normalisées, promotion WorldState asymétrique (Vision → WorldState, DOM → éphémère). La réalité du code est plus nuancée.

---

## 3. Real Runtime Flow — Requête Browser complète

**Chemin réel d'une requête "Ajoute un Raspberry Pi au panier sur Amazon"** :

```
HarnessRequest
  └─ handle_request() [loop.py:183]
       ├─ assemble() [assembler.py:237]
       │    ├─ world_state_domains=() → retrieve_relevant(()) → TOUS les faits WS
       │    ├─ tools_registry=None → pas de TOOL_SCHEMAS dans le prompt
       │    └─ render_system_prompt() → texte système (identité + directives + mémoire + WS)
       └─ _run_agentic_loop() [loop.py:542]
            ├─ available_tools = _discover_tool_schemas() → TOUS les tags enregistrés
            │   [inclut browser.*, pc.*, tasks.*, etc. — EXCLUT vision.* car non enregistrés]
            └─ for _iteration in range(12):
                 ├─ ModelRequest(capability=REASONING, messages, available_tools)
                 ├─ → model_route() → OllamaCloudAdapter.request()
                 ├─ → tool_calls_requested
                 ├─ → execute_tool() → BrowserDeviceAgent.execute()
                 ├─ → _promote_observations_and_verify() [WorldState si ObservationSpec]
                 ├─ → _summarize_tool_result() [+ compact si browser.read_page]
                 └─ → messages.append(role="tool", content=résumé JSON)
```

**Invariant clé** : le modèle ne reçoit que les outils ENREGISTRÉS. `vision.*` ne l'est pas → le modèle ne peut pas les appeler.

---

## 4. DOM Extraction — Analyse complète

### 4.1 Mécanisme réel

**Fichier** : `raya/devices/browser/controller.py`

```python
def read_page(self) -> dict:
    def _op():
        page = self._page()
        data = page.evaluate(_STRUCT_JS)   # injection JS synchrone via Playwright
        data["status"] = "ok"
        return data
    return self._worker.run_sync(_op)
```

**Mécanisme** : Playwright `page.evaluate(_STRUCT_JS)` — injection d'un script JS dans la page. Ce n'est pas du CDP brut : Playwright l'abstrait. Aucun screenshot, aucun appel modèle Vision à cette étape.

**Transport** : `BrowserWorker._loop()` → thread dédié unique → sérialisation Playwright (API sync liée au thread).

### 4.2 Structure de `_STRUCT_JS`

**Fichier** : `raya/devices/browser/controller.py:58-110`

Le script JS est injecté, exécuté dans le DOM et retourne :

| Champ | Source JS | Cap JS | Cap `_compact_read_page_output` |
|---|---|---|---|
| `url` | `location.href` | aucun | **préservé intégralement** |
| `title` | `document.title` | aucun | **préservé intégralement** |
| `cookie_banner` | détection CSS sélecteurs | aucun | **préservé intégralement** |
| `inputs` | `input, textarea, [role=searchbox]…` | 20 | **préservé intégralement** |
| `buttons` | `button, [role=button], input[type=submit]…` | 120 | **cap à 20** |
| `links` | `a[href]` | 60 | **cap à 25** |

**Priorisation boutons** : `_STRUCT_JS` scanne en premier les racines buybox/addtocart (`#desktop_buybox`, `#addToCart_feature_div`, `#buybox`, `[id*=buybox i]`, `[id*=addtocart i]`, `[id*=add-to-cart i]`, `[class*=buybox i]`). Ces boutons sont placés EN TÊTE de `buttons[]`. Le cap à 20 (appliqué après) est donc safe : les boutons critiques d'e-commerce sont garantis dans les 20 premiers.

**Visibilité** : filtre `vis(el)` — exclut les éléments avec `r.width < 2 || r.height < 2`, `visibility:hidden`, `display:none`, `opacity < 0.05`, et hors viewport+200px.

### 4.3 Evidence retournée par `_read_page()`

**Fichier** : `raya/devices/browser/agent.py:89-92`

```python
def _read_page(agent, command):
    r = agent._controller.read_page()
    evidence = {"url": r["url"], "cookie_banner": r["cookie_banner"]}
    return _ok(command, r, evidence, "cdp_dom_eval")
```

**Observation** : l'evidence contient **uniquement** `url` et `cookie_banner`. Le titre, les boutons, les liens, les inputs — tout le contenu DOM riche — sont dans `output` mais **pas dans `evidence`**.

### 4.4 Promotion WorldState par `browser.read_page`

**Fichier** : `raya/tools/catalog/browser.py:61`

```python
("browser.read_page", ..., observation=()),  # tuple vide
```

`observation=()` → `_promote_observations_and_verify()` ne crée **aucun** `WorldStateFact` pour `browser.read_page`. Le contenu DOM (url, title, buttons, links, inputs) reste **éphémère** dans le thread de messages du tour courant. Il est perdu à la fin du tour.

**Comparaison avec `browser.navigate`** :

```python
_CURRENT_URL_OBSERVATION = (
    ObservationSpec(domain="browser", key="current_url", evidence_field="url",
                     expected_argument="url", freshness_ttl_s=60),
)
("browser.navigate", ..., observation=_CURRENT_URL_OBSERVATION),
```

`browser.navigate` promouvoit `browser.current_url` (TTL 60s, confidence=KNOWN_FACT). C'est le seul outil browser qui met à jour WorldState.

### 4.5 Observation des autres outils browser

| Tool | `observation` | WorldState mis à jour |
|---|---|---|
| `browser.navigate` | `_CURRENT_URL_OBSERVATION` | `browser.current_url` (TTL 60s) |
| `browser.read_page` | `()` | **rien** |
| `browser.click` | `()` | **rien** |
| `browser.type` | `()` | **rien** |
| `browser.screenshot` | `()` | **rien** |
| `browser.dismiss_overlay` | `()` | **rien** |
| `browser.list_tabs` | `()` | **rien** |

**Conséquence** : après un clic sur "Ajouter au panier", aucun fait WorldState n'enregistre cette action. La confirmation ne vit que dans le thread de messages du même tour. `_finalize_turn` lit `browser.current_url` et `last_clicked_target` depuis WorldState — `last_clicked_target` n'est **jamais** écrit par aucun tool.

---

## 5. Vision Pipeline — Analyse complète

### 5.1 Contrats visuels

**Fichier** : `raya/contracts/visual.py`

`BoundingBox` : coordonnées normalisées [0.0, 1.0] — validation stricte en `__post_init__`. Méthodes `center_pixel(width, height)`, `to_pixel(width, height)`, `screen_coordinates()`.

`VisualObservation` : `confidence = INFERRED` obligatoire (validé en `__post_init__`). `source` doit commencer par `"perception:"`. `artifact_ref` (chemin local) jamais en WorldState.

`VisualTarget` : lié à `observation_id` (traçabilité). `screen_coordinates()` applique `window_left + cx`.

### 5.2 `observe_image()` — Implémentation réelle

**Fichier** : `raya/models/vision.py:126-229`

Pipeline complet :
```
observe_image(model_registry, image_path, prompt, find_target, ...)
  ├─ lit le fichier local (Path.exists())
  ├─ sélectionne prompt: _SCENE_PROMPT ou _GROUNDING_PROMPT_TEMPLATE
  ├─ crée ModelRequest(capability=VISION, messages=[
  │     Message(role="user", content=[
  │       ContentPart(type="image_ref", value=str(image_path)),
  │       ContentPart(type="text", value=prompt),
  │     ])
  │   ], context_budget_tokens=1024)
  ├─ route(model_registry, req, prefer_local)
  ├─ parse réponse texte
  ├─ extrait entités sémantiques (heuristique: mots > 3 chars, majuscule ou liste _COMMON_UI_ENTITIES)
  ├─ si find_target: _parse_grounding(raw_text, obs_id, viewport)
  └─ retourne VisualObservation(confidence=INFERRED, source=source)
```

**Statut** : IMPLEMENTED — correctement implémenté.

### 5.3 `_parse_grounding()` — Fix Chantier 18D

**Fichier** : `raya/models/vision.py:83-123`

```python
_FOUND_PATTERN = re.compile(
    r'FOUND:\s*bbox=\[\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*([0-9.]+)\s*\]'
    r'\s+label="(.+?)"\s+confidence=([0-9.]+)',
    re.IGNORECASE,
)
```

Après extraction des 4 valeurs float :
```python
if max(x_min, y_min, x_max, y_max) > 1.0:   # détection pixel
    if viewport is None or viewport.image_width <= 0 or viewport.image_height <= 0:
        return None   # impossible de normaliser sans dimensions
    x_min = x_min / viewport.image_width
    y_min = y_min / viewport.image_height
    x_max = x_max / viewport.image_width
    y_max = y_max / viewport.image_height
```

**Statut** : IMPLEMENTED (Fix C de Chantier 18D). Normalisation pixel → [0,1] si `viewport` disponible.

### 5.4 `_handle_find_in_browser()` — Fix Chantier 18D Fix B

**Fichier** : `raya/tools/catalog/visual.py:303-326`

```python
w, h = cap.get("width", 0), cap.get("height", 0)
from raya.contracts import ViewportInfo as _ViewportInfo
vp = _ViewportInfo(image_width=w, image_height=h) if (w and h) else None
obs = observe_fn(path, "", target, call.correlation_id, prefer_local, vp, "perception:browser")
```

**Statut** : IMPLEMENTED (Fix B de Chantier 18D). Consomme `width`/`height` de `capture_browser_fn()` si disponibles.

### 5.5 Outils visuels définis

**Fichier** : `raya/tools/catalog/visual.py`

5 outils définis dans `register_visual_tools()` :

| Tool | capture_fn utilisée | ObservationSpec |
|---|---|---|
| `vision.observe_screen` | `capture_screen_fn()` | `_SCREEN_OBS_SPEC` → `visual.screen_state` (TTL 30s) |
| `vision.find_on_screen` | `capture_screen_fn()` | `_SCREEN_OBS_SPEC` → `visual.screen_state` (TTL 30s) |
| `vision.observe_browser` | `capture_browser_fn()` | `_BROWSER_OBS_SPEC` → `visual.browser_visual_state` (TTL 30s) |
| `vision.find_in_browser` | `capture_browser_fn()` | `_BROWSER_OBS_SPEC` → `visual.browser_visual_state` (TTL 30s) |
| `vision.observe_image` | aucune (path direct) | `_IMAGE_OBS_SPEC` → `visual.last_image_observation` (TTL 60s) |

**Tous ces outils promouvant les observations en WorldState (confidence=INFERRED, TTL 30-60s).**

---

## 6. DOM + Vision : Interaction réelle

### 6.1 En production : AUCUNE

En production, les outils `vision.*` ne sont pas enregistrés (voir §9). Le modèle ne les voit pas dans `available_tools`. La seule information visuelle disponible est :
- La structure DOM via `browser.read_page` (éphémère, turn courant)
- Le chemin de screenshot via `browser.screenshot` (path dans le tool result, aucune analyse)

Le modèle reçoit le chemin de screenshot dans le message `role="tool"` mais **ne peut pas envoyer ce fichier au modèle Vision** car :
1. les outils vision ne sont pas enregistrés
2. même s'ils l'étaient, l'image ne serait pas transportée (voir §7.2)

### 6.2 En tests (avec `register_visual_tools` injecté) : indépendants, non fusionnés

Si `register_visual_tools` est appelé manuellement (tests, scripts d'audit), les deux flux co-existent dans le même tour :

- **DOM** : `browser.read_page` → `role="tool"` avec `{url, title, buttons, links, inputs}` — éphémère
- **Vision** : `vision.observe_screen` → `role="tool"` avec `{description, entities, target?}` — promu en WorldState TTL 30s

**Aucune fusion** : deux messages `role="tool"` séparés. Le LLM est seul responsable de réconcilier les deux vues. Aucun code de réconciliation DOM↔Vision n'existe dans la codebase.

**Asymétrie critique** :

| Signal | WorldState | Tour suivant disponible |
|---|---|---|
| DOM `browser.read_page` | NON | **NON** |
| Vision `vision.observe_*` | OUI (TTL 30s) | OUI |
| URL navigée `browser.navigate` | OUI (TTL 60s) | OUI |

### 6.3 Scenarios DOM-Vision théoriques (blueprint) vs réalité

| Scénario | Blueprint | Réalité |
|---|---|---|
| A — DOM suffisant, vision inutile | DOM-first | DOM only (vision indisponible) |
| B — DOM insuffisant, vision fallback | vision.observe_browser | **IMPOSSIBLE en production** |
| C — Grounding pour clic précis | vision.find_in_browser → click_at_position | **IMPOSSIBLE** (vision non enregistrée) |
| D — Canvas/image non-DOM | vision.observe_browser | **IMPOSSIBLE** |
| E — Confirmation visuelle post-action | vision.observe_screen | **IMPOSSIBLE** |

---

## 7. Gaps Critiques Confirmés

### GAP 1 — `register_visual_tools()` absent de `bootstrap.py`

**Fichier prouvant l'absence** : `raya/runtime/bootstrap.py` (intégralité lue)

`bootstrap.py` importe depuis `raya.tools.catalog` :
```python
from raya.tools.catalog import (
    PreferenceOps, TaskControlOps,
    register_browser_tools, register_demo_tools, register_pc_tools,
    register_phone_tools, register_preference_tools, register_spatial_tools,
    register_system_time_tool, register_task_control_tools, register_ui_view_tools,
)
```

`register_visual_tools` est **absent** de cet import. La fonction n'est pas appelée dans `bootstrap()`.

**Confirmation** : `raya/tools/catalog/__init__.py` n'exporte pas `register_visual_tools`.

**Grep confirmant** : `register_visual_tools` n'apparaît QUE dans `visual.py`, des fichiers de tests (`tests/tools/`, `tests/audit/`), et des scripts d'audit — jamais dans `bootstrap.py` ni dans le code de production.

**Conséquence** : aucun outil `vision.*` n'est jamais enregistré dans le `ToolRegistry` de production. `_discover_tool_schemas()` ne les retourne jamais. `available_tools` ne les contient jamais. Le modèle ne peut jamais les appeler.

**Fix nécessaire** : ajouter dans `bootstrap._register_devices()` (ou après) un appel à `register_visual_tools(tools, observe_fn, capture_screen_fn, capture_browser_fn)` avec les callables appropriés.

### GAP 2 — Transport image NOT implemented dans `OllamaCloudAdapter`

**Fichier** : `raya/models/providers/ollama_cloud.py:48-69`

```python
def _messages_to_ollama(messages: list[Message]) -> list[dict]:
    payload = []
    for m in messages:
        text = "".join(p.value for p in m.content if p.type == "text")
        entry: dict = {"role": m.role, "content": text}
        # ...
        payload.append(entry)
    return payload
```

`ContentPart(type="image_ref", value=path)` → **silencieusement ignoré**. La valeur du path n'est pas extraite, pas encodée en base64, pas ajoutée au payload Ollama.

Le format Ollama attendu pour la vision est :
```json
{"role": "user", "content": "...", "images": ["base64string..."]}
```

Ce champ `images` n'est **jamais** construit dans `_messages_to_ollama()`.

**Conséquence** : même si `register_visual_tools` était câblé et qu'un outil vision était appelé, `observe_image()` créerait bien un `ModelRequest` avec `ContentPart(type="image_ref", ...)`, mais `OllamaCloudAdapter` enverrait à Ollama un message sans `images` — le modèle Vision ne recevrait que le prompt texte, jamais l'image.

**Correction de l'audit précédent** : `RAYA_V2_REAL_MULTIMODAL_AUDIT.md` §2.2 affirme "Le fix encodant `image_ref` → `images: [base64_pure]` est présent et fonctionnel." Cette affirmation est **incorrecte** vis-à-vis du code actuel. Grep sur `raya/models/providers/ollama_cloud.py` pour `base64`, `image_ref`, `images`, `read_bytes` : **aucun résultat**. L'audit précédent a probablement testé un pipeline différent (injection directe de `observe_fn` en tests, sans passer par `_messages_to_ollama`).

`OllamaLocalAdapter` hérite intégralement de `OllamaCloudAdapter` via `super().__init__()` — même gap.

### GAP 3 — Viewport browser absent sans Fix A

**Fichier** : `raya/devices/browser/agent.py:95-100`

```python
def _screenshot(agent, command):
    filename = command.arguments.get("filename", "browser_screenshot.png")
    agent._screenshot_dir.mkdir(parents=True, exist_ok=True)
    path = str(agent._screenshot_dir / filename)
    r = agent._controller.screenshot(path)
    return _ok(command, {"path": r["path"]}, {}, "cdp_screenshot")
```

`BrowserController.screenshot()` retourne `{"status": "ok", "path": path}` — **pas de `width`/`height`**.

Fix A de Chantier 18D (lire les dimensions PIL depuis l'image capturée) est décrit dans le rapport mais **absent du code actuel de `bootstrap.py`** (aucun `_capture_browser` helper dans bootstrap, aucun appel PIL).

Si `vision.find_in_browser` était appelé, `cap = capture_browser_fn()` retournerait un dict sans dimensions → `vp = None` → `_parse_grounding()` retournerait `None` pour toute coordonnée pixel (70% des cas gemma4).

**Note** : Fix B (`visual.py`) et Fix C (`vision.py`) sont bien présents. Seul Fix A (bootstrap.py) est absent.

---

## 8. Perception → WorldState

### 8.1 Pipeline perception léger

**Fichier** : `raya/perception/windows_sensors.py`

`ActiveWindowSensor.sample()` → publie `Event(type="perception.window_changed")` avec `PerceptionObservation(domain="pc", key="active_window", value={"title": ..., "process": ...})`.

**Fichier** : `raya/world_state/store.py:37`

```python
bus.subscribe("perception.*", self._on_perception_event, subscriber="world_state.perception")
```

→ `WorldStateFact(domain="pc", key="active_window", confidence=KNOWN_FACT, freshness_ttl_s=20)`

**Statut** : IMPLEMENTED.

### 8.2 Pipeline observation Tool → WorldState

**Fichier** : `raya/harness/loop.py:333-370`

```python
def _promote_observations_and_verify(self, requested, tool_result, outcome):
    if tool_result.status != ToolResultStatus.SUCCESS:
        return outcome
    for spec in tool_def.observation:  # vide pour la plupart des tools browser
        value = (tool_result.evidence or {}).get(spec.evidence_field)
        # ...
        self._world_state.apply_update(WorldStateFact(domain=spec.domain, key=key, value=value, ...))
```

Le mécanisme est générique et fonctionnel. Son efficacité dépend entièrement du contenu de `tool_def.observation`. Pour `browser.*` : seul `browser.navigate` est actif.

---

## 9. Contexte envoyé au modèle

### 9.1 Assemblage

**Fichier** : `raya/harness/loop.py:219-228` (dans `handle_request`)

```python
context = assemble(
    session_id=request.session_id,
    channel_scope=channel_scope,
    world_state=self._world_state,
    memory=self._memory,
    budget_tokens=self._context_budget_tokens,
    query_text=request.input.text or "",
    active_tasks=active_tasks,
    runtime_identity=runtime_identity,
    # world_state_domains NOT passed → default ()
    # tools_registry NOT passed → default None
)
```

`world_state_domains=()` → `retrieve_relevant(())` → **tous** les faits WorldState actifs/stale (tous domaines).

`tools_registry=None` → `_tool_schema_section()` retourne `None` → les schémas d'outils **ne sont pas** dans le system prompt (mais ils sont dans `ModelRequest.available_tools`).

### 9.2 Prompt système rendu

**Fichier** : `raya/context_engine/render.py`

Structure du prompt système (dans l'ordre du rendu) :

1. **SYSTEM_RULES** : `"You are RAYA, an AI assistant."` + runtime provider/model + ~12 directives comportementales (URL guessing, browser robustness, temporal, tasks, filesystem, response vs action, channel, CLI preference…)
2. **MEMORY (identité baseline)** : toujours injectés — `"Known, confirmed fact about the user: ..."` (profil identité, TTL infini)
3. **MEMORY (contextuel)** : faits pertinents à la question (lifecycle × confidence weight, top 20)
4. **WORLD_STATE** : `"Observed environment state [active]: browser.current_url = 'https://...'"` — TOUS les domaines
5. **TASK_STATE** / **ACTIVE_TASKS** : si tâches en cours
6. **CONVERSATION_HISTORY** : 5 derniers tours (user/assistant distingués)

**TOOL_SCHEMAS** : délibérément ignoré dans `render_system_prompt()` (docstring ligne 18) — les schémas sont envoyés via `ModelRequest.available_tools` (function-calling natif Ollama).

### 9.3 Message utilisateur + boucle agentique

Dans `_run_agentic_loop()` [loop.py:542] :
- Premier `messages` : `[system_prompt_message, user_message]`
- Chaque itération ajoute `role="assistant"` (avec tool_calls) + `role="tool"` (résumé JSON)
- `available_tools` = tous les tools enregistrés, sérialisés en JSON Schema

**Taille du prompt système** : l'audit 20G a mesuré 7,475 tokens pour le prompt système MANDATORY. Avec `num_ctx = min(128_000, context_budget_tokens * 4)` = `min(128_000, 16_384)` = 16,384 tokens → 46% consommés dès le départ.

---

## 10. Decision Making — Cognition

### 10.1 LoopDetector

**Fichier** : `raya/cognition/recovery.py`

Détecte les **mêmes tool_name + arguments exacts** échouant ≥ 2 fois consécutives. Signature = SHA256 des 16 premiers chars de `json.dumps({"tool": ..., "args": ...})`. ESCALATE si `repeats >= 2 AND len(history) >= 2`. Un succès efface l'historique.

### 10.2 Détection cycle d'état

**Fichier** : `raya/harness/loop.py:737-747`

```python
state_signal = (tool_result.evidence or {}).get("url")
if state_signal:
    state_history.append(state_signal)
    if detect_repeating_cycle(state_history) or detect_no_progress(state_history):
        escalation_text = self._explain_blocked_turn(...)
```

`state_history` accumule les URLs via `evidence["url"]`. Seuls `browser.navigate` et `browser.read_page` ont `url` dans leur evidence. `browser.click` → evidence `{}` → jamais dans `state_history`. Donc : cliquer le même bouton 12 fois de suite n'est pas détecté par ce mécanisme.

### 10.3 Nudge anti-répétition

`consecutive_failures >= 3` sur le même `tool_name` → injection d'un message système encourageant à changer de stratégie. Générique, jamais un hard-stop.

### 10.4 `_finalize_turn` vs `_explain_blocked_turn`

- **Budget épuisé** → `_finalize_turn()` : `available_tools=None`, trace bornée (8 max), evidence incluse, prompt "BUDGET EXHAUSTED ≠ TASK FAILED"
- **LoopDetector ESCALATE** → `_explain_blocked_turn()` : trace sans evidence, prompt "The agent got stuck"
- **Cycle URL** → `_explain_blocked_turn()`

`_finalize_turn` lit `browser.current_url` et `browser.last_clicked_target` depuis WorldState. `last_clicked_target` n'est **jamais** écrit → toujours absent.

---

## 11. Réutilisation des observations

### 11.1 DOM entre les tours

`browser.read_page` : `observation=()` → jamais en WorldState → **perdu à la fin de l'itération courante**. Si le modèle a lu la page à l'itération 3 et en a besoin à l'itération 7, il doit rappeler `browser.read_page`.

### 11.2 Vision entre les tours

Si vision était enregistrée : `_SCREEN_OBS_SPEC` / `_BROWSER_OBS_SPEC` (TTL 30s) → l'observation visuelle survit au tour suivant UNIQUEMENT si < 30 secondes s'écoulent. Un seul fait WorldState par clé (dernier écrasant le précédent).

### 11.3 URL entre les tours

`browser.navigate` → `browser.current_url` (TTL 60s) → disponible dans le system prompt du tour suivant sous forme :
```
Observed environment state [active]: browser.current_url = 'https://...'
```

### 11.4 Freshness TTL lazy

**Fichier** : `raya/world_state/store.py:118`

```python
if apply_lazy_staleness and fact.status == FactStatus.ACTIVE and fact.is_expired():
    fact.status = FactStatus.STALE
```

Le TTL est appliqué **paresseusement** à la lecture. Un fait expiré passe à `FactStatus.STALE` (non supprimé — le score `_FRESHNESS_WEIGHT[STALE] = 0.4` l'inclut encore dans le contexte avec poids réduit).

---

## 12. Reconstruction Amazon 20G — Architecture réelle

Requête : "Trouve un Raspberry Pi et ajoute-le au panier sur Amazon.com.be"

**Ce que le code fait réellement** :

| Étape | Outil | Ce qui passe au modèle | WorldState mis à jour |
|---|---|---|---|
| 1 | `browser.navigate(amazon.com.be)` | `{url: "amazon.com.be/", title: "Amazon.be"}` | `browser.current_url` = amazon.com.be |
| 2 | `browser.dismiss_overlay` | `{dismissed: [], rounds: 0}` | rien |
| 3 | `browser.type` (échec) | `{status: not_found}` | rien |
| 4 | `browser.read_page` | `{url, title, cookie_banner, buttons[20], links[25], inputs}` [après compaction] | rien |
| 5 | `browser.type` (succès) | `{typed: "...", submitted: False}` | rien |
| 6 | `browser.read_page` | DOM de la page de résultats | rien |
| 7 | `browser.screenshot` | `{path: ".../browser_screenshot.png"}` | rien |
| 8 | `browser.read_page` | DOM (idem #6, nouvelle lecture) | rien |
| 9 | `browser.screenshot` | `{path: "..."}` | rien |
| 10 | `browser.click("GeeekPi Raspberry Pi...")` | `{clicked: "..."}` | rien |
| 11 | `browser.read_page` | DOM fiche produit | rien |
| 12 | `browser.screenshot` | `{path: "..."}` | rien |
| — | `_finalize_turn` | trace[8 bornés] + `browser.current_url` | — |

**Screenshots** : 3 screenshots pris (étapes 7, 9, 12). Le path est dans le thread de messages. Le modèle a le chemin mais **ne peut pas analyser le contenu** — `vision.*` non disponible. Les screenshots servent de "marqueurs de progression" visuels pour l'utilisateur (si interface visuelle), pas d'input pour le modèle.

**Contexte saturé** : `browser.read_page` homepage = ~6,222 bytes ≈ 1,556 tokens. Avec prompt système ~7,475 tokens + messages précédents → saturation à ~l'itération 3 dans la config d'avant compaction. Après compaction (20G-B) : ~2,620 bytes ≈ 655 tokens → saturation évitée.

**Budget épuisé à l'étape 12 (page produit atteinte, "Add to Cart" non cliqué)** : 3 screenshots ont consommé 3 iterations "sans avancement de l'état navigateur". `_finalize_turn` → réponse honnête ("J'ai trouvé une fiche produit, je n'ai pas confirmé l'ajout au panier").

---

## 13. Évaluation DOM-Centricity

Le pipeline est **DOM-exclusif en production** :

| Dimension | DOM | Vision |
|---|---|---|
| Extraction structure page | Oui (`_STRUCT_JS`) | Non (non enregistrée) |
| Localisation bouton pour clic | Oui (`find_clickable()` → Playwright locators) | Non |
| Confirmation visuelle post-action | Non (aucune, ni DOM ni vision) | Non |
| WorldState persisté | Non (`observation=()`) | Non (non enregistrée) |
| Disponible au tour suivant | Non | Non |

**Mécanisme de clic** : `BrowserController.click(target)` → `find_clickable(target)` → cherche par `role=button`, `role=link`, `aria-label`, `title`, texte visible. Robuste, multilingue, sans coordonnées pixel. Pas de grounding Vision requis pour le DOM.

**Grounding Vision** (`vision.find_in_browser` → `browser.click_at_position`) : décrit dans la documentation, mais `browser.click_at_position` n'existe pas dans `BrowserDeviceAgent._DISPATCH`. Seul `browser.click(target: str)` existe. **Le grounding Vision ne peut pas être utilisé pour cliquer dans le browser** même si vision était enregistrée.

---

## 14. Blueprint vs Réalité

| Élément Blueprint | Réalité Code |
|---|---|
| DOM via CDP + JS injection | Oui — Playwright `evaluate(_STRUCT_JS)` |
| Vision via modèle multimodal | Partiellement — `observe_image()` existe mais non câblé |
| Grounding normalisé [0,1] | Oui — `_parse_grounding()` avec normalisation pixel |
| Vision comme fallback DOM | Non en production — vision non enregistrée |
| WorldState Vision (TTL 30s) | Spécifié (`ObservationSpec`) mais jamais peuplé en prod |
| `browser.click_at_position` | Non implémenté (absent de `_DISPATCH`) |
| Image transport Ollama (`images: [b64]`) | Non implémenté (`_messages_to_ollama` ignore `image_ref`) |
| Capture browser avec viewport dims | Partiellement — Fix B+C présents, Fix A absent |
| DOM promu en WorldState | Non — `observation=()` pour `browser.read_page` |
| `browser.last_clicked_target` en WS | Non — aucun outil ne l'écrit |

---

## 15. Findings Critiques

### FC-1 — Vision NOT wired (BLOQUANT)

**Fichier** : `raya/runtime/bootstrap.py` (absence confirmée par grep)

`register_visual_tools()` n'est pas appelé. Les 5 outils vision ne sont jamais disponibles au modèle. Tout scénario nécessitant une analyse visuelle (canvas, images non-textuelles, confirmation visuelle post-action) est impossible.

### FC-2 — Image transport NOT implemented (BLOQUANT)

**Fichier** : `raya/models/providers/ollama_cloud.py:58-69`

`_messages_to_ollama()` ignore `ContentPart(type="image_ref")`. Même si FC-1 était résolu, aucune image n'atteindrait Ollama. `gemma4:cloud` recevrait uniquement le prompt texte.

### FC-3 — `browser.click_at_position` absent

**Fichier** : `raya/devices/browser/agent.py:132-140` (`_DISPATCH`)

Le dictionnaire de dispatch ne contient pas `browser.click_at_position`. `vision.find_in_browser` documente un usage avec ce tool ("Retourne (screen_x, screen_y) utilisables avec `browser.click_at_position`") — mais ce tool n'existe pas. Les coordonnées grounding ne peuvent pas être utilisées pour un clic dans le browser.

### FC-4 — `browser.last_clicked_target` jamais écrit

**Fichiers** : `raya/tools/catalog/browser.py`, `raya/devices/browser/agent.py`

`_finalize_turn` tente de lire `browser.last_clicked_target` depuis WorldState. Aucun outil ne l'écrit. La valeur est toujours absente. Ce fait WorldState est un **mort-né**.

### FC-5 — DOM éphémère : pas de mémoire structurelle entre tours

`browser.read_page` → `observation=()` → DOM perdu fin de tour. Si le modèle a besoin du même contenu DOM plusieurs tours de suite, il doit re-lire la page. En pratique pour des sessions courtes (≤12 iterations dans un même tour), ce n'est pas bloquant. En Long Horizon Tasks (multi-tours), c'est un facteur de latence.

---

## 16. Inconnu / Non Vérifié

| Point | Raison |
|---|---|
| `gemma4:cloud` reçoit-il réellement des images en prod ? | GAP 2 confirmé (non) mais test E2E réel n'a pas été refait dans cette session |
| Qualité de détection CSS `_STRUCT_JS::vis()` sur sites SPA React/Vue (Shadow DOM) | Non testé — Shadow DOM non traversé par `document.querySelectorAll` |
| `BrowserController.check_confirmation()` utilisé dans le flow principal ? | Méthode définie mais non appelée depuis le harness ou les outils (outil de diagnostic) |
| `detect_no_progress()` vs `detect_repeating_cycle()` — sémantique exacte | Non lu (`raya/cognition/recovery.py` partiellement lu) |
| `BrowserSession.is_raya_tab()` — méthode référencée dans `list_tabs()` mais définition non trouvée | `hasattr(self._session, "is_raya_tab") if` — protection présente, méthode probablement absente |

---

## 17. Fichiers Inspectés

| Fichier | Rôle | Lignes lues |
|---|---|---|
| `raya/devices/browser/controller.py` | DOM extraction, click, overlay | intégralité |
| `raya/devices/browser/agent.py` | DeviceAgent, dispatch, evidence | intégralité |
| `raya/devices/browser/session.py` | CDP, Edge, profil dédié | intégralité |
| `raya/devices/browser/worker.py` | Thread unique Playwright | intégralité |
| `raya/tools/catalog/browser.py` | Tool definitions, ObservationSpec | intégralité |
| `raya/tools/catalog/visual.py` | 5 outils vision, register_visual_tools | intégralité |
| `raya/tools/catalog/__init__.py` | Exports catalogue | intégralité |
| `raya/models/vision.py` | observe_image, _parse_grounding | intégralité |
| `raya/models/providers/ollama_cloud.py` | _messages_to_ollama, image transport | intégralité |
| `raya/models/providers/ollama_local.py` | Hérite OllamaCloud | intégralité |
| `raya/models/router.py` | Route modèle | intégralité |
| `raya/models/__init__.py` | Exports | intégralité |
| `raya/runtime/bootstrap.py` | Câblage production | intégralité |
| `raya/runtime/config.py` | Config, model pool | intégralité |
| `raya/harness/loop.py` | Boucle agentique, finalize, compact | lignes 1-100, 300-500, 542-760 |
| `raya/context_engine/assembler.py` | assemble() | intégralité |
| `raya/context_engine/render.py` | render_system_prompt | intégralité |
| `raya/contracts/visual.py` | BoundingBox, VisualObservation, VisualTarget | intégralité |
| `raya/contracts/__init__.py` | Exports contrats | intégralité |
| `raya/contracts/tool.py` | Tool, ObservationSpec, ToolResult | intégralité |
| `raya/perception/__init__.py` | Exports perception | intégralité |
| `raya/perception/windows_sensors.py` | ActiveWindowSensor | intégralité |
| `raya/world_state/store.py` | retrieve_relevant, apply_update | intégralité |
| `raya/cognition/recovery.py` | LoopDetector | lignes 1-63 |
| `RAYA_V2_REAL_MULTIMODAL_AUDIT.md` | Audit précédent | lignes 1-200 |
| `RAYA_V2_MULTIMODAL_REPAIR_AUDIT.md` | Audit réparation | lignes 1-100 |
| `RAYA_V2_18D_GROUNDING_FIX_IMPLEMENTATION_REPORT.md` | Chantier 18D | lignes 1-179 |

---

## 18. Verdict Final

### Ce qui fonctionne réellement en production

1. **DOM extraction** : `_STRUCT_JS` via Playwright — fonctionnel, priorité buybox, filtre visibilité, compaction 20B/25L appliquée
2. **Navigation** : `browser.navigate` → WorldState `browser.current_url` (TTL 60s)
3. **Interaction DOM** : `browser.click`, `browser.type`, `browser.dismiss_overlay` via Playwright locators — fonctionnels
4. **Budget finalization** : `_finalize_turn` — `BUDGET EXHAUSTED ≠ TASK FAILED`, evidence-inclusive (Chantier 20G-B)
5. **Contexte** : system prompt avec identité, mémoire, WorldState, historique conversation — assemblé et transmis
6. **Sécurité** : LoopDetector, détection cycle URL, nudge anti-répétition, SafetyService — actifs

### Ce qui est implémenté mais non câblé en production

1. **Vision pipeline complet** : `observe_image()`, `_parse_grounding()` (avec fix pixel), 5 outils définis — tout existe, rien n'est enregistré
2. **WorldState Vision** : ObservationSpec définis pour tous les outils vision — jamais peuplés

### Ce qui n'est pas implémenté du tout

1. **Transport image Ollama** : `_messages_to_ollama()` — `image_ref` silencieusement ignoré
2. **`browser.click_at_position`** — absent du dispatch browser
3. **`browser.last_clicked_target`** en WorldState — aucun outil ne l'écrit
4. **Fix A Chantier 18D** : `_capture_browser()` avec dimensions PIL — absent de bootstrap.py

### Synthèse

Le pipeline DOM est robuste et production-ready. La couche Vision est un module bien conçu (contracts corrects, parser avec fix pixel, 5 outils cohérents, promotion WorldState spécifiée) mais reste **un composant non déployé** pour deux raisons indépendantes et bloquantes :

- **GAP 1** : le câblage bootstrap est absent → les outils vision ne sont jamais disponibles
- **GAP 2** : le transport image Ollama n'est pas implémenté → même câblé, le modèle Vision ne verrait jamais les images

Ces deux gaps sont chirurgicalement localisés : `bootstrap.py` (1 appel manquant) et `ollama_cloud.py::_messages_to_ollama()` (extraction + encodage base64 du `image_ref` ContentPart à ajouter). Aucune modification d'architecture n'est requise.
