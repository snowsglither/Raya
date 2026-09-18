# RAYA V2 — Chantier 20H : Production Path Integrity Audit
## DOM + Vision — Photographie exacte du système en production

**Date** : 2026-09-16  
**Mode** : AUDIT UNIQUEMENT — zéro modification, zéro test, zéro patch, zéro runtime  
**Méthode** : Lecture directe du code source. Chaque affirmation est liée à un fichier:ligne précis.  
**Fichiers lus** : `raya/runtime/bootstrap.py`, `raya/harness/loop.py`, `raya/tools/registry.py`,  
`raya/tools/__init__.py`, `raya/tools/execution.py`, `raya/tools/discovery.py`,  
`raya/tools/catalog/browser.py`, `raya/tools/catalog/visual.py`, `raya/tools/catalog/__init__.py`,  
`raya/devices/browser/agent.py`, `raya/devices/browser/controller.py`, `raya/devices/browser/session.py`,  
`raya/models/providers/ollama_cloud.py`, `raya/models/vision.py`, `raya/contracts/tool.py`,  
`raya/contracts/__init__.py`, `raya/world_state/store.py`, `raya/context_engine/assembler.py`,  
`raya/context_engine/render.py`  
**Rapport précédent** : `RAYA_V2_DOM_VISION_ARCHITECTURAL_AUDIT.md` (audit de surface — ce rapport est l'audit de profondeur)

---

## Section 1 — Résumé exécutif

**Question centrale** : Quand Ruben parle à RAYA V2 en production, quelles capacités DOM + Vision sont réellement accessibles au modèle, par quel chemin exact, avec quelles données, et lesquelles existent seulement dans le code ou dans les tests ?

**Réponse directe** :

| Capacité | Accessible au modèle en production ? |
|---|---|
| DOM (browser.read_page, browser.click, etc.) | **OUI — 7 outils, chemin complet** |
| Vision (vision.observe_screen, etc.) | **NON — zéro outil, jamais enregistré** |
| browser.click_at_position | **NON — absent du dispatch et de la registry** |
| browser.last_clicked_target (WorldState) | **NON — jamais écrit** |
| Image transport vers le modèle (image_ref) | **NON — silently dropped par _messages_to_ollama** |

**Résumé en une phrase** : Le modèle a accès à 7 outils browser DOM pleinement fonctionnels, zéro outil vision, et trois capacités référencées dans des rapports et des tests (`click_at_position`, `last_clicked_target`, image transport) qui n'existent pas en production.

---

## Section 2 — Périmètre et méthode de l'audit 20H

**Ce que cet audit vérifie** :
- La chaîne complète `bootstrap → ToolRegistry → Harness → ModelRequest → Ollama`
- Pour chaque capacité DOM et Vision : à quel niveau exact la chaîne est intacte ou rompue
- La véracité des affirmations dans les rapports précédents (`RAYA_V2_REAL_MULTIMODAL_AUDIT.md`, `RAYA_V2_BROWSER_AGENTIC_EXECUTION_REPAIR_REPORT.md`, etc.)

**Ce que cet audit ne fait PAS** :
- Aucun runtime (pas de pytest, pas d'Ollama, pas de browser)
- Aucune modification de code
- Les rapports précédents sont des artefacts à comparer au code — pas une source de vérité

**Hiérarchie des niveaux vérifiés** (du plus faible au plus fort) :

| Niveau | Signification | Abréviation |
|---|---|---|
| A. CODE EXISTS | La fonction/classe est définie dans un fichier .py | CE |
| B. IMPORTABLE | Importable sans erreur (dépendances satisfaites) | IM |
| C. REGISTERED | L'outil est dans le ToolRegistry à runtime | RG |
| D. DISCOVERABLE | L'outil apparaît dans `_discover_tool_schemas()` | DI |
| E. EXPOSED TO MODEL | L'outil est dans `available_tools` dans le ModelRequest | EX |
| F. EXECUTABLE | Le handler est appelé, le code s'exécute | XE |
| G. RESULT CORRECT | Le résultat retourné est correct | RC |
| H. END-TO-END CONNECTED | Le résultat impacte WorldState ou le modèle | E2E |

---

## Section 3 — Lexique du statut

- **IMPLEMENTED** : niveaux A→H tous présents et vérifiés dans le code
- **PARTIALLY IMPLEMENTED** : certains niveaux présents, au moins un rompu
- **SCAFFOLDED** : A+B présents, C absent ou pire (code écrit mais jamais câblé)
- **NOT IMPLEMENTED** : absent du code de production
- **DEAD CODE** : présent dans le code, mais chemin d'exécution inatteignable en production
- **DOCUMENTATION DRIFT** : affirmé dans un rapport, contredit par le code actuel

---

## Section 4 — Carte de la chaîne de production (bootstrap → modèle)

```
bootstrap.py
  ├── _register_model_pool()
  │   └── OllamaCloudAdapter(model_id, capabilities, api_key) → ModelRegistry
  ├── _register_devices()
  │   ├── [if enable_windows_device] WindowsDeviceAgent → register_pc_tools(tools, agent)
  │   ├── [if enable_browser_device] BrowserDeviceAgent → register_browser_tools(tools, agent)  ← 7 outils
  │   └── [if enable_phone_device]   PhoneLinkDeviceAgent → register_phone_tools(tools, agent)
  ├── register_demo_tools(tools, workspace_dir)
  ├── register_system_time_tool(tools)
  ├── register_ui_view_tools(tools, bus)
  ├── register_spatial_tools(tools, scene_store, bus, workspace_dir)
  ├── register_task_control_tools(tools, TaskControlOps(...))
  ├── register_preference_tools(tools, PreferenceOps(...))
  └── ❌ register_visual_tools() → JAMAIS APPELÉ

ToolRegistry (en mémoire, mutable)
  → contient: demo tools + system.time + ui.view + spatial + pc + browser(7) + phone + task + preference
  → ne contient PAS: vision.* (0 outil vision)

Harness.__init__(tools_registry=tools)
  └── self._tools_registry = tools

handle_request(request)
  └── _run_agentic_loop(request, state, context)
        ├── available_tools = _discover_tool_schemas()
        │     └── tags = registry.all_capability_tags()  ← TOUS les tags enregistrés
        │     └── discover(registry, tags)               ← TOUS les outils enregistrés
        │     └── [to_dict(t) for t in tools]            ← sérialisé en JSON-Schema
        └── ModelRequest(messages=..., available_tools=available_tools)
              └── OllamaCloudAdapter.request(req)
                    ├── _messages_to_ollama(req.messages)   ← text ONLY, image_ref DROPPED
                    └── _tools_to_ollama(req.available_tools)
```

**Constat** : La chaîne est complète pour les outils DOM. Elle est rompue au niveau C (REGISTERED) pour les outils Vision.

---

## Section 5 — Registre des outils en production (exhaustif)

### 5.1 Outils browser (DOM)

Source : `raya/tools/catalog/browser.py:59-67`

| Outil | Tag | Permission | Observation WorldState | Idempotent |
|---|---|---|---|---|
| browser.navigate | browser.read | SAFE | `browser.current_url` (url, TTL 60s) | ✅ |
| browser.read_page | browser.read | SAFE | `()` — AUCUNE | ✅ |
| browser.screenshot | browser.read | SAFE | `()` — AUCUNE | ✅ |
| browser.list_tabs | browser.read | SAFE | `()` — AUCUNE | ✅ |
| browser.click | browser.interact | SENSITIVE | `()` — AUCUNE | ❌ |
| browser.type | browser.interact | SENSITIVE | `()` — AUCUNE | ❌ |
| browser.dismiss_overlay | browser.interact | SENSITIVE | `()` — AUCUNE | ❌ |

**Statut** : IMPLEMENTED (niveaux A→H confirmés pour les 7 outils)

### 5.2 Outils vision

Source : `raya/tools/catalog/visual.py:123-406` + `raya/tools/catalog/__init__.py:1-25`

| Outil | Défini | Exporté catalog/__init__ | Appelé depuis bootstrap | Dans ToolRegistry |
|---|---|---|---|---|
| vision.observe_screen | ✅ | ❌ | ❌ | ❌ |
| vision.find_on_screen | ✅ | ❌ | ❌ | ❌ |
| vision.observe_browser | ✅ | ❌ | ❌ | ❌ |
| vision.find_in_browser | ✅ | ❌ | ❌ | ❌ |
| vision.observe_image | ✅ | ❌ | ❌ | ❌ |

**Statut** : SCAFFOLDED — A+B présents, C→H absents.

### 5.3 Outil absent (browser.click_at_position)

Source : `raya/devices/browser/agent.py:132-140` + `raya/tools/catalog/browser.py:59-67`

| Référence | Valeur |
|---|---|
| `_DISPATCH` entries (agent.py) | 7 : navigate/read_page/screenshot/list_tabs/click/type/dismiss_overlay |
| `_defs` entries (browser.py) | 7 : mêmes noms |
| `browser.click_at_position` dans _DISPATCH | ABSENT |
| `browser.click_at_position` dans _defs | ABSENT |

**Statut** : NOT IMPLEMENTED (côté production). DOCUMENTATION DRIFT (présent dans tests + rapports).

---

## Section 6 — Chemin DOM complet : browser.read_page

### 6.1 Chemin d'exécution complet (niveaux A→H)

```
A. CODE EXISTS
   raya/tools/catalog/browser.py:61
   ("browser.read_page", "browser.read_page", "Lit la structure...", ..., PermissionLevel.SAFE, "browser.read", True, ())

B. IMPORTABLE
   bootstrap.py:35 : from raya.tools.catalog import register_browser_tools
   catalog/__init__.py exporte register_browser_tools ✅

C. REGISTERED
   bootstrap.py:125 : register_browser_tools(tools, browser_agent, should_stop=safety.should_stop)
   → registry.register(tool, handler) pour les 7 outils browser ✅
   Conditionnel : [if config.enable_browser_device] — si False, aucun outil browser n'est enregistré.

D. DISCOVERABLE
   loop.py:323-331 : _discover_tool_schemas()
   → tags = registry.all_capability_tags()  # inclut "browser.read"
   → discover(registry, tags) → retourne tous les outils ayant "browser.read" ✅

E. EXPOSED TO MODEL
   loop.py:556 : available_tools = self._discover_tool_schemas()
   loop.py:591 : ModelRequest(available_tools=available_tools or None)
   → browser.read_page est dans available_tools ✅

F. EXECUTABLE
   Model génère {"name": "browser.read_page", "arguments": {}}
   loop.py:651 : tool_result = execute_tool(registry, safety, tool_call)
   execution.py:52 : tool = registry.get("browser.read_page")  → trouvé ✅
   execution.py:82 : handler = registry.handler_for("browser.read_page")  → trouvé ✅
   execution.py:95 : result = handler(call)
   browser.py:76 : _run(agent, "browser.read_page", should_stop, call)
   browser.py:43 : Command(device_id="browser", capability_name="browser.read_page")
   browser.py:44 : agent.execute(command)
   agent.py:_read_page() : self._browser.read_page()
   controller.py:BrowserController.read_page() : page.evaluate(_STRUCT_JS) ✅

G. RESULT CORRECT
   controller.py : retourne dict{url, title, cookie_banner, buttons[], links[], inputs[]}
   agent.py:_read_page : ToolResult(SUCCESS, output=dom_dict, evidence={"url":..., "cookie_banner":...})
   loop.py:373-388 : _summarize_tool_result → _compact_read_page_output(output)
     → buttons capped à 20, links capped à 25, url/title/inputs préservés ✅

H. END-TO-END CONNECTED
   loop.py:333-370 : _promote_observations_and_verify
   browser.read_page : observation=() → RIEN promu dans WorldState
   Mais : résultat JSON compacté ajouté comme message role="tool" → modèle le voit ✅
   Note : WorldState n'est PAS mis à jour (current_url inchangé après read_page)
```

**Statut : IMPLEMENTED** (A→H tous présents), avec la nuance que WorldState ne reçoit rien de read_page.

### 6.2 Données que le modèle reçoit après browser.read_page

```json
{
  "tool": "browser.read_page",
  "status": "success",
  "verification": "success",
  "output": {
    "url": "https://...",
    "title": "...",
    "cookie_banner": false,
    "inputs": [...],       // préservé intégralement
    "buttons": [...],      // max 20 (capé par _compact_read_page_output)
    "links": [...],        // max 25 (capé par _compact_read_page_output)
    "buttons_capped": N,   // si > 20 originaux
    "links_capped": M      // si > 25 originaux
  },
  "evidence": {"url": "...", "cookie_banner": false},
  "error": null
}
```

Source : `loop.py:373-388` (`_summarize_tool_result`) + `loop.py:390-421` (`_compact_read_page_output`)

---

## Section 7 — Chemin DOM : browser.click

### 7.1 Chemin d'exécution

```
A-E. Identiques à browser.read_page — même chaîne bootstrap → registry → discovery → available_tools

F. EXECUTABLE
   agent.py:_click(call, args) :
     target = args.get("target", "")
     controller.click(target) → find_clickable(target) via Playwright locators
     ToolResult(status=SUCCESS|FAILURE, output={...}, evidence={})
     evidence = {} — VIDE

G. RESULT CORRECT
   ToolResult retourné avec evidence={} (vide) ✅ (correct pour un clic réussi)

H. END-TO-END CONNECTED
   loop.py:333-370 : _promote_observations_and_verify
   browser.click : observation=() → RIEN promu dans WorldState ← CRITIQUE
   Modèle voit: status=success/failure, evidence={} — AUCUNE donnée sur la cible cliquée
```

Source : `raya/tools/catalog/browser.py:64` — `observation=()` pour browser.click  
Source : `raya/devices/browser/agent.py` — `_click()` retourne `evidence={}` (vide)

**Statut : PARTIALLY IMPLEMENTED**
- Niveaux A→G : fonctionnels
- Niveau H (WorldState) : rompu — rien n'est promu
- Conséquence : `browser.last_clicked_target` n'existe JAMAIS dans WorldState

### 7.2 Absence de _LAST_CLICKED_OBSERVATION

**Ce que le code devrait avoir** (selon rapports et tests) :
```python
_LAST_CLICKED_OBSERVATION = (
    ObservationSpec(domain="browser", key="last_clicked_target", evidence_field="clicked_target", freshness_ttl_s=300),
)
# browser.click aurait observation=_LAST_CLICKED_OBSERVATION
```

**Ce que le code a réellement** (`browser.py:64`) :
```python
("browser.click", "browser.click", "Clique un élément par description.",
 {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
 PermissionLevel.SENSITIVE, "browser.interact", False, ())
#                                                              ^^ observation=() VIDE
```

**Ce que _click() retourne** (`agent.py`) :
```python
return ToolResult(tool_call_id=call.id, status=_STATUS, output={...}, evidence={})
#                                                                        ^^ VIDE — aucun "clicked_target"
```

**Ce que _finalize_turn lit** (`loop.py:496-504`) :
```python
browser_facts = self._world_state.retrieve_relevant(("browser",))
ws_relevant = {f.key: f.value for f in browser_facts
               if f.key in ("current_url", "last_clicked_target")}
```
→ `last_clicked_target` : JAMAIS dans browser_facts → `ws_relevant` contient uniquement `current_url` (si navigate a été appelé).

---

## Section 8 — Tous les outils browser : table de statut E2E

| Outil | Chemin A→F | Evidence produite | WorldState mis à jour | Modèle reçoit |
|---|---|---|---|---|
| browser.navigate | ✅ complet | `{"url": actual_url}` | `browser.current_url` ✅ | status + url atteinte |
| browser.read_page | ✅ complet | `{"url": ..., "cookie_banner": bool}` | ❌ rien | DOM compacté (20 btns, 25 liens) |
| browser.screenshot | ✅ complet | `{"path": "..."}` | ❌ rien | path fichier |
| browser.list_tabs | ✅ complet | `{"tabs": [...]}` | ❌ rien | liste onglets |
| browser.click | ✅ complet | `{}` (VIDE) | ❌ rien | status success/failure |
| browser.type | ✅ complet | `{}` (VIDE) | ❌ rien | status success/failure |
| browser.dismiss_overlay | ✅ complet | `{"dismissed": [...]}` | ❌ rien | liste overlays fermés |

Source : `raya/tools/catalog/browser.py:59-67` (observation specs), `raya/devices/browser/agent.py` (evidence retournée)

**Seul outil qui met à jour WorldState : `browser.navigate`.**

---

## Section 9 — Promotion WorldState (browser domain) — chemin complet

### 9.1 Mécanisme

`loop.py:333-370` — `_promote_observations_and_verify()` :
```python
for spec in tool_def.observation:          # itère sur Tool.observation tuple
    value = (tool_result.evidence or {}).get(spec.evidence_field)
    if value is None:
        value = (tool_result.output or {}).get(spec.evidence_field)
    if value is None:
        continue
    self._world_state.apply_update(WorldStateFact(
        domain=spec.domain, key=key, value=value,
        source=f"tool:{requested.tool_name}",
        confidence=spec.confidence,
        freshness_ttl_s=spec.freshness_ttl_s,
    ))
```

**Si `tool_def.observation = ()` : la boucle ne s'exécute pas. WorldState n'est jamais mis à jour.**

### 9.2 État WorldState browser domain en production

| Fait WorldState | Producteur | Observé en production |
|---|---|---|
| `browser.current_url` | browser.navigate via `_CURRENT_URL_OBSERVATION` | ✅ OUI (quand navigate appelé) |
| `browser.last_clicked_target` | aucun producteur | ❌ JAMAIS |
| Tout autre fait browser | aucun producteur | ❌ JAMAIS |

### 9.3 Fraîcheur et staleness

`world_state/store.py` : TTL-based. `browser.current_url` expire après 60s (freshness_ttl_s=60).  
Après expiration : fact passe à `STALE` au prochain `retrieve_relevant()`. `_finalize_turn` peut donc lire une URL stale si navigate a été appelé > 60s avant la fin du budget.

### 9.4 Ce que le modèle voit dans le system prompt (WorldState)

`render.py:render_system_prompt()` inclut les faits WorldState actifs :
```
Observed environment state [active]: browser.current_url = 'https://...'
```
Si navigate n'a pas été appelé dans les 60 dernières secondes : cette ligne est ABSENTE du system prompt.  
`browser.last_clicked_target` : JAMAIS dans le system prompt (jamais produit).

---

## Section 10 — Compaction browser.read_page (20G-B)

### 10.1 Implémentation vérifiée

`loop.py:373-421` — `_summarize_tool_result` + `_compact_read_page_output` :

```python
if tool_name == "browser.read_page":
    output = Harness._compact_read_page_output(output)
```

`_compact_read_page_output(output)` :
- Préserve : `url`, `title`, `cookie_banner`, `inputs` (intégralement)
- Cap : `buttons[:20]`, `links[:25]`
- Ajoute : `buttons_capped: N` si N buttons supprimés, `links_capped: M` si M links supprimés
- Repli sûr : si input non-dict/non-str ou JSON invalide → retourne output original inchangé

**Statut : IMPLEMENTED** — appelé dans le chemin de production pour chaque appel browser.read_page.

### 10.2 Impact sur le contexte modèle

`_STRUCT_JS` dans `controller.py` produit jusqu'à 120 buttons + 60 links.  
Après compaction : max 20 buttons + 25 links.  
Réduction mesurée (20G-B E2E) : -44.7% en moyenne sur 6 appels réels.  
Priorité des buttons préservée : `_STRUCT_JS` met déjà les boutons buybox/addtocart en tête de `buttons[]`.

---

## Section 11 — Vision : register_visual_tools — chaîne complète

### 11.1 Niveau A — CODE EXISTS

`raya/tools/catalog/visual.py:123-406` — `register_visual_tools(registry, observe_fn, capture_screen_fn, capture_browser_fn, *, prefer_local=False)` définit 5 tools complets avec handlers.  
**VÉRIFIÉ : ✅ CODE EXISTS**

### 11.2 Niveau B — IMPORTABLE

`visual.py` importe uniquement `raya.contracts`, `raya.models.vision` (indirectement via observe_fn injectée), `pathlib`, `typing`. Pas de dépendance circulaire.  
**VÉRIFIÉ : ✅ IMPORTABLE** (mais jamais importé depuis bootstrap ou catalog/__init__)

### 11.3 Niveau C — REGISTERED

`raya/tools/catalog/__init__.py:1-25` — exports :
```python
from .browser import register_browser_tools
from .demo import register_demo_tools
from .pc import register_pc_tools
from .phone import register_phone_tools
from .preferences import register_preference_tools
from .spatial import register_spatial_tools
from .system_time import register_system_time_tool
from .tasks import register_task_control_tools
from .ui_view import register_ui_view_tools
```
`register_visual_tools` : ABSENT de catalog/__init__.py.

`raya/runtime/bootstrap.py:32-44` — imports depuis catalog :
```python
from raya.tools.catalog import (
    PreferenceOps, TaskControlOps,
    register_browser_tools, register_demo_tools, register_pc_tools,
    register_phone_tools, register_preference_tools, register_spatial_tools,
    register_system_time_tool, register_task_control_tools, register_ui_view_tools,
)
```
`register_visual_tools` : ABSENT de bootstrap.py.

Recherche dans bootstrap.py pour `visual` ou `register_visual` : 0 occurrence.

**VÉRIFIÉ : ❌ NOT REGISTERED — register_visual_tools n'est JAMAIS appelé.**

### 11.4 Niveaux D→H — conséquence

Puisque C = absent, D→H sont tous absents par construction :
- D (DISCOVERABLE) : les 5 outils vision ne sont pas dans `registry.all()` → jamais dans `_discover_tool_schemas()`
- E (EXPOSED TO MODEL) : `available_tools` ne contient aucun outil `vision.*`
- F→H : handlers jamais appelés

**Statut : SCAFFOLDED** — code défini (A+B), jamais câblé (C→H absents).

### 11.5 Signature complète de register_visual_tools

```python
def register_visual_tools(
    registry,
    observe_fn: ObserveFn,           # callable fourni par bootstrap — JAMAIS créé
    capture_screen_fn: CaptureFn,    # callable wrappant pyautogui.screenshot — JAMAIS créé
    capture_browser_fn: CaptureFn,   # callable wrappant BrowserController.screenshot — JAMAIS créé
    *,
    prefer_local: bool = False,
) -> None:
```

Trois callables injectables sont nécessaires. Bootstrap ne les crée pas — aucun import de `pyautogui` ni de `raya.models.vision.observe_image` dans `bootstrap.py`.

---

## Section 12 — Vision : transport image (image_ref → modèle)

### 12.1 Le chemin attendu

`raya/models/vision.py:observe_image()` crée :
```python
ModelRequest(
    capability=ModelCapability.VISION,
    messages=[Message(role="user", content=[
        ContentPart(type="image_ref", value=str(image_path))
    ])]
)
```

### 12.2 Ce que _messages_to_ollama fait réellement

`raya/models/providers/ollama_cloud.py:57-69` :
```python
def _messages_to_ollama(messages: list[Message]) -> list[dict]:
    payload = []
    for m in messages:
        text = "".join(p.value for p in m.content if p.type == "text")
        entry: dict = {"role": m.role, "content": text}
        if m.tool_calls:
            entry["tool_calls"] = [...]
        if m.tool_call_id:
            entry["tool_call_id"] = m.tool_call_id
        payload.append(entry)
    return payload
```

Filtre : `if p.type == "text"` — seules les ContentParts de type "text" sont incluses.  
`ContentPart(type="image_ref", value="/path/to/img.png")` : SILENTLY DROPPED.  
Aucun `images: [base64_data]` field n'est jamais construit dans le payload Ollama.

**Conséquence** :
- Si `observe_image()` était appelé (hypothétiquement, puisque vision tools ne sont pas enregistrés)
- Et que le modèle avait capability VISION
- L'image ne serait **jamais envoyée à Ollama**
- Le modèle recevrait un message `{"role": "user", "content": ""}` — contenu vide

### 12.3 OllamaLocalAdapter

`raya/models/providers/ollama_cloud.py` — `OllamaLocalAdapter` hérite intégralement de `OllamaCloudAdapter` :
```python
class OllamaLocalAdapter(OllamaCloudAdapter):
    ...
```
Aucun override de `_messages_to_ollama`. Même gap.

**Statut : NOT IMPLEMENTED** — le transport image est une lacune architecturale, pas un oubli d'appel.

### 12.4 Vérification du rapport précédent

`RAYA_V2_REAL_MULTIMODAL_AUDIT.md` §2.2 affirme :
> "fix encodant image_ref → images: [base64_pure] est présent et fonctionnel"

**INCORRECT** selon le code actuel. `_messages_to_ollama()` ne contient aucun encodage base64 ni construction de `images:[]`. Cette affirmation était vraisemblablement issue d'un test qui injectait `observe_fn` directement (bypassing `_messages_to_ollama`), et non d'un test du chemin de production.

---

## Section 13 — Vision : browser.click_at_position

### 13.1 Présence dans le code de production

`raya/devices/browser/agent.py:_DISPATCH` (confirmé, 141 lignes total) :
```python
_DISPATCH = {
    "browser.navigate": _navigate,
    "browser.read_page": _read_page,
    "browser.screenshot": _screenshot,
    "browser.list_tabs": _list_tabs,
    "browser.click": _click,
    "browser.type": _type,
    "browser.dismiss_overlay": _dismiss_overlay,
}
```
7 entrées. `browser.click_at_position` : **ABSENT**.

`raya/tools/catalog/browser.py:59-67` (`_defs`) :
7 entrées. `browser.click_at_position` : **ABSENT**.

### 13.2 Présence dans les tests et scripts

`tests/devices/browser/test_chantier18b_browser_agentic.py:126` :
```python
assert "browser.click_at_position" in _DISPATCH
```
Ce test **échouerait** contre le code de production actuel.

`scripts/audit_20f_post_action_verification.py:628` :
```python
("browser.click_at_position", ..., observation=())  # from raya/tools/catalog/browser.py:91
```
La ligne 91 référencée n'existe pas — `browser.py` ne fait que 79 lignes. Cela indique que cette version de `browser.py` est antérieure à une suppression.

`scripts/validate_20c_vision_browser_e2e.py` : trace `browser.click_at_position` calls.

### 13.3 Interprétation

`browser.click_at_position` a existé dans une version antérieure de `browser.py` (> 79 lignes) et de `agent.py`. Il a été supprimé du code de production mais pas des tests ni des scripts. C'est un cas de **DOCUMENTATION DRIFT + DEAD TESTS** — la capacité a été retirée.

**Statut : NOT IMPLEMENTED** (en production actuelle).

---

## Section 14 — Vision : browser.last_clicked_target

### 14.1 Producteurs en production

Pour que `browser.last_clicked_target` existe dans WorldState, il faudrait :
1. `browser.click` retourne `evidence={"clicked_target": ...}`
2. Tool definition de `browser.click` a `observation` avec `spec.domain="browser"`, `spec.key="last_clicked_target"`, `spec.evidence_field="clicked_target"`
3. `_promote_observations_and_verify()` lit ce spec et écrit dans WorldState

**Réalité** :
1. `agent.py:_click()` retourne `evidence={}` (vide) — confirmé
2. `browser.py:64` : `observation=()` pour browser.click — confirmé
3. `_promote_observations_and_verify()` itère sur `observation=()` → boucle vide

**Aucun producteur existe en production.**

### 14.2 Consommateurs en production

`loop.py:496-504` (`_finalize_turn`) :
```python
browser_facts = self._world_state.retrieve_relevant(("browser",))
ws_relevant = {f.key: f.value for f in browser_facts
               if f.key in ("current_url", "last_clicked_target")}
```
`last_clicked_target` est lu mais jamais présent → `ws_relevant` ne contient que `current_url` (si navigate récent).

**Conséquence** : `_finalize_turn` inclut rarement des données WorldState utiles — uniquement l'URL de navigation, jamais la cible cliquée.

### 14.3 Tests qui échoueraient

Selon le rapport précédent (`RAYA_V2_DOM_VISION_ARCHITECTURAL_AUDIT.md`), les tests suivants échoueraient :
- `test_browser_click_declares_last_clicked_target_observation_spec()` — attend `_LAST_CLICKED_OBSERVATION` sur browser.click
- `test_browser_click_promotes_last_clicked_target_to_world_state` — attend une promotion WorldState après click

**Statut : NOT IMPLEMENTED** — `browser.last_clicked_target` est une clé WorldState morte.

---

## Section 15 — Fix A : bootstrap _capture_browser (Chantier 18D)

**Fix A attendu** : bootstrap.py devrait créer une `capture_browser_fn` wrappant `BrowserController.screenshot()` avec dimensions PIL (width, height), et la passer à `register_visual_tools()`.

### 15.1 Vérification dans bootstrap.py (282 lignes, lu intégralement)

Recherche de : `_capture_browser`, `pyautogui`, `PIL`, `capture_screen_fn`, `capture_browser_fn`, `register_visual_tools`

| Terme | Occurrences dans bootstrap.py |
|---|---|
| `register_visual_tools` | 0 |
| `_capture_browser` | 0 |
| `pyautogui` | 0 |
| `PIL` | 0 |
| `capture_screen_fn` | 0 |
| `capture_browser_fn` | 0 |

**Fix A : ABSENT** — le code n'a jamais été écrit dans bootstrap.py.

### 15.2 Ce que BrowserController.screenshot() retourne réellement

`raya/devices/browser/controller.py` — `screenshot()` :
```python
return {"status": "ok", "path": path}
```
Pas de `width`, pas de `height`. Même si `register_visual_tools` était appelé avec cette fonction comme `capture_browser_fn`, le viewport serait toujours `None` (car `if w and h` serait False avec w=0, h=0).

---

## Section 16 — Fix B : _handle_find_in_browser viewport (Chantier 18D)

### 16.1 Code présent dans visual.py

`raya/tools/catalog/visual.py` — `_handle_find_in_browser()` :
```python
cap = capture_browser_fn()
path = cap.get("path", "")
w, h = cap.get("width", 0), cap.get("height", 0)
viewport = ViewportInfo(image_width=w, image_height=h) if w and h else None
```

**Le code Fix B EST présent** dans visual.py.

### 16.2 Statut en production

- `register_visual_tools()` n'est jamais appelé → `_handle_find_in_browser` n'est jamais enregistré comme handler
- Ce code est **DEAD CODE** en production — implémenté, jamais exécutable

### 16.3 Gap résiduel même si Fix B était actif

Si `register_visual_tools` était appelé avec `BrowserController.screenshot` comme `capture_browser_fn` :
- `capture_browser_fn()` retournerait `{"status": "ok", "path": "..."}` — sans width/height
- `w, h = cap.get("width", 0), cap.get("height", 0)` → w=0, h=0
- `if w and h` → False → `viewport = None`
- Le grounding serait tenté sans information de viewport → normalisation impossible

Fix A (ajouter width/height à BrowserController.screenshot) est un prérequis pour que Fix B soit utile.

**Statut : DEAD CODE** (code présent mais inatteignable + gap résiduel sur capture_browser_fn)

---

## Section 17 — Fix C : _parse_grounding pixel normalization (Chantier 18D)

### 17.1 Code présent dans models/vision.py

`raya/models/vision.py` — `_parse_grounding()` :
```python
if max(vals) > 1.0:
    # normalize by viewport dimensions
    if viewport is not None:
        vals = [v / viewport.image_width if i % 2 == 0 else v / viewport.image_height
                for i, v in enumerate(vals)]
```

**Le code Fix C EST présent** dans vision.py.

### 17.2 Statut en production

- `observe_image()` crée un `ModelRequest(capability=VISION, messages=[ContentPart(type="image_ref",...)])`
- `_messages_to_ollama()` drop silencieusement `image_ref` → le modèle reçoit un message vide
- Même si le modèle répondait, `_parse_grounding()` ne serait jamais atteint (car la réponse contiendrait du texte brut, pas un `FOUND: bbox=...` pattern)
- De plus, vision tools ne sont pas enregistrés → la chaîne ne démarre jamais

**Statut : DEAD CODE** (implémenté mais deux ruptures de chaîne l'empêchent d'être atteint).

---

## Section 18 — Chemin modèle complet (ModelRequest → Ollama)

### 18.1 Construction du ModelRequest (boucle agentique)

`loop.py:586-593` :
```python
model_request = ModelRequest(
    capability=ModelCapability.REASONING,
    messages=messages,
    correlation_id=request.correlation_id,
    available_tools=available_tools or None,
    context_budget_tokens=context_budget_tokens,
)
```

- `capability=REASONING` (pas VISION) : le router cherche un provider capable de REASONING
- `messages` : liste de Message avec ContentPart(type="text") uniquement (jamais image_ref dans la boucle agentique DOM)
- `available_tools` : liste JSON-Schema des outils enregistrés (DOM + autres, pas vision)

### 18.2 Model routing

`model_route(registry, request)` → sélectionne un provider par capability :
- REASONING → OllamaCloudAdapter (si OLLAMA_API_KEY configurée) ou OllamaLocalAdapter
- VISION → aucun modèle configuré actuellement (OllamaCloudAdapter registre REASONING, pas VISION automatiquement — dépend de la configuration `model_pool` dans RuntimeConfig)

### 18.3 Sérialisation du payload Ollama

`ollama_cloud.py:147-161` :
```python
payload = {
    "model": self._model_id,
    "messages": _messages_to_ollama(req.messages),
    "think": False,
    "stream": False,
    "options": {
        "num_predict": max(256, req.context_budget_tokens // 4),
        "num_ctx": min(self._context_limit, req.context_budget_tokens * 4 or self._context_limit),
    },
}
tools_payload = _tools_to_ollama(req.available_tools)
if tools_payload:
    payload["tools"] = tools_payload
```

- `_messages_to_ollama()` : extrait text uniquement, sérialise tool_calls/tool_call_id structurés
- `_tools_to_ollama()` : convertit Tool JSON-Schema → format function-calling Ollama
- `options.num_ctx` : `min(context_limit=128000, budget*4)` — avec budget=4096 par défaut → num_ctx=16,384

### 18.4 Ce que le payload Ollama ne contient JAMAIS

- `images: [...]` (base64) — jamais construit
- Toute ContentPart de type != "text"
- Schémas d'outils vision (non enregistrés)

---

## Section 19 — Tests : chemin production vs chemin injecté

### 19.1 Tests qui valident le chemin de production

Les tests harness qui utilisent `Harness` avec un vrai `ToolRegistry` + handlers enregistrés via `register_browser_tools` valident le chemin production.

### 19.2 Tests qui valident un chemin injecté/bypassé (résultats non extrapolables à la production)

**Test : `test_chantier18b_browser_agentic.py:126`**
```python
assert "browser.click_at_position" in _DISPATCH
```
Import direct de `_DISPATCH` depuis `agent.py`. Échouerait contre le code actuel (7 entrées, pas de click_at_position).

**Tests E2E Vision (scripts/validate_20c_vision_browser_e2e.py)**  
Injectent `observe_fn` directement dans les handlers vision sans passer par `_messages_to_ollama`. Ils testent la logique de `_obs_to_output`, `_parse_grounding`, etc. mais pas le transport image réel.

**Contexte de `RAYA_V2_REAL_MULTIMODAL_AUDIT.md`** :
> 4/5 E2E PASS, 20/20 automated PASS

Ces tests ont très probablement utilisé une injection de `observe_fn` mockée ou une version antérieure du code incluant le transport image. Le résultat "PASS" ne prouve pas que le transport image fonctionne via `_messages_to_ollama()`.

### 19.3 Tests pré-existants qui échoueraient en production actuelle

| Test | Fichier | Assertion | Pourquoi échoue |
|---|---|---|---|
| `test_browser_click_at_position_in_dispatch` | test_chantier18b_browser_agentic.py:126 | `"browser.click_at_position" in _DISPATCH` | absent du _DISPATCH actuel |
| `test_browser_click_declares_last_clicked_target_observation_spec` | test_referential_resolution.py | `_LAST_CLICKED_OBSERVATION` sur browser.click | observation=() sur browser.click |
| `test_browser_click_promotes_last_clicked_target_to_world_state` | test_referential_resolution.py | WorldState mis à jour après click | observation=() → rien promu |

---

## Section 20 — Drift documentation/code

### 20.1 `RAYA_V2_REAL_MULTIMODAL_AUDIT.md` §2.2

**Affirmation** : "fix encodant image_ref → images: [base64_pure] est présent et fonctionnel"  
**Code actuel** : `_messages_to_ollama()` — `if p.type == "text"` seul filtre. Aucun base64. Aucun `images:[]`.  
**Verdict** : DOCUMENTATION DRIFT — l'affirmation est fausse par rapport au code actuel.

### 20.2 `RAYA_V2_BROWSER_AGENTIC_EXECUTION_REPAIR_REPORT.md`

**Affirmation** : "`browser.last_clicked_target` works via `_promote_observations_and_verify()`"  
**Code actuel** : `browser.py:64` — `observation=()`. `agent.py:_click()` — `evidence={}`.  
**Verdict** : DOCUMENTATION DRIFT — `last_clicked_target` n'est jamais produit.

### 20.3 `RAYA_V2_IMPROVEMENT_SAFE_AUTONOMY_LATENCY_REPORT.md:100`

**Affirmation** :
```python
ObservationSpec(domain="browser", key="last_clicked_target", evidence_field="clicked_target", freshness_ttl_s=300),
```
sur browser.click.  
**Code actuel** : `browser.py:64` — `observation=()`.  
**Verdict** : DOCUMENTATION DRIFT — spec décrite dans le rapport, absente du code.

### 20.4 Tout rapport mentionnant `browser.click_at_position` comme disponible

**Code actuel** : absent de `_DISPATCH` et de `_defs`.  
**Verdict** : DOCUMENTATION DRIFT — capability retirée du code, présente dans les rapports.

### 20.5 `RAYA_V2_ARCHITECTURAL_INVARIANTS.md` (non lu directement)

Sur la base des rapports précédents, probablement mentionné. À vérifier si ce document est mis à jour.

---

## Section 21 — Table de vérité complète des capacités (niveaux A→H)

| Capacité | CE | IM | RG | DI | EX | XE | RC | E2E | Statut |
|---|---|---|---|---|---|---|---|---|---|
| browser.navigate | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | **IMPLEMENTED** |
| browser.read_page | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅* | **IMPLEMENTED** |
| browser.screenshot | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅* | **IMPLEMENTED** |
| browser.list_tabs | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅* | **IMPLEMENTED** |
| browser.click | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | **PARTIALLY** |
| browser.type | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | **PARTIALLY** |
| browser.dismiss_overlay | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ⚠️ | **PARTIALLY** |
| browser.click_at_position | ❌ | — | — | — | — | — | — | — | **NOT IMPL.** |
| browser.last_clicked_target (WS) | ❌ | — | — | — | — | — | — | — | **NOT IMPL.** |
| vision.observe_screen | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **SCAFFOLDED** |
| vision.find_on_screen | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **SCAFFOLDED** |
| vision.observe_browser | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **SCAFFOLDED** |
| vision.find_in_browser | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **SCAFFOLDED** |
| vision.observe_image | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **SCAFFOLDED** |
| Image transport (image_ref→Ollama) | ❌ | — | — | — | — | — | — | — | **NOT IMPL.** |
| _compact_read_page_output (20G-B) | ✅ | ✅ | — | — | ✅ | ✅ | ✅ | ✅ | **IMPLEMENTED** |
| _finalize_turn (20G-B) | ✅ | ✅ | — | — | ✅ | ✅ | ✅ | ✅ | **IMPLEMENTED** |
| Fix A (bootstrap capture_browser) | ❌ | — | — | — | — | — | — | — | **NOT IMPL.** |
| Fix B (find_in_browser viewport) | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **DEAD CODE** |
| Fix C (_parse_grounding normalization) | ✅ | ✅ | — | — | — | ❌ | ❌ | ❌ | **DEAD CODE** |

*Notes :
- ✅* (E2E pour read_page/screenshot/list_tabs) : modèle reçoit les données, mais WorldState n'est pas mis à jour
- ⚠️ (E2E pour click/type/dismiss_overlay) : exécutés correctement mais WorldState jamais mis à jour (observation=())
- CE=A, IM=B, RG=C, DI=D, EX=E, XE=F, RC=G, E2E=H

---

## Section 22 — Ce que le modèle voit réellement en production (session browser type)

### 22.1 System prompt (rendu par render_system_prompt)

```
[SYSTEM RULES]
Identité RAYA + ~12 directives (never invent, always honest, etc.)

[MEMORY]
Profil utilisateur + faits personnels + derniers 5 tours de conversation

[WORLD_STATE]
Observed environment state [active]: browser.current_url = 'https://...'  ← seulement si navigate appelé <60s
(aucun browser.last_clicked_target — jamais produit)
(aucun visual.screen_state — vision tools non enregistrés)

[TASK_STATE / ACTIVE_TASKS]
(tâches actives en fond si present)
```

### 22.2 available_tools (envoyés dans le ModelRequest)

```json
[
  {"name": "browser.navigate", "description": "...", "capability_tags": ["browser.read"], ...},
  {"name": "browser.read_page", ...},
  {"name": "browser.screenshot", ...},
  {"name": "browser.list_tabs", ...},
  {"name": "browser.click", ...},
  {"name": "browser.type", ...},
  {"name": "browser.dismiss_overlay", ...},
  {"name": "system.time", ...},
  {"name": "tasks.create", ...},
  ... (autres outils non-vision enregistrés)
  // JAMAIS : vision.observe_screen, vision.find_on_screen, vision.observe_browser, vision.find_in_browser, vision.observe_image
  // JAMAIS : browser.click_at_position
]
```

### 22.3 Messages de contexte (pendant la boucle agentique)

- Messages système (system prompt) : 1 message initial
- Message utilisateur : request.input.text
- Pour chaque itération : message assistant (tool_calls) + message outil (résultat JSON)
- Chaque résultat browser.read_page : DOM compacté (≤20 boutons, ≤25 liens)

### 22.4 Ce que le modèle ne peut PAS voir

- Capture écran (screenshot contenu analysé) — vision non disponible
- Description textuelle de ce qui est visible à l'écran — vision non disponible
- Coordonnées pixel d'un élément — click_at_position absent, find_in_browser absent
- Confirmation que le bon élément a été cliqué — last_clicked_target jamais dans WorldState
- Images brutes — image_ref droppé par _messages_to_ollama

---

## Section 23 — Lacunes par priorité

### Priorité 1 (correctness) — Impact immédiat sur les résultats

**GAP 1 : register_visual_tools jamais appelé depuis bootstrap**  
- Impact : 5 outils vision inexistants pour le modèle
- Fichier : bootstrap.py (import + appel manquant), catalog/__init__.py (export manquant)
- Fix minimal : (1) exporter register_visual_tools depuis catalog/__init__.py, (2) créer observe_fn + capture_screen_fn + capture_browser_fn dans bootstrap, (3) appeler register_visual_tools(tools, ...)

**GAP 2 : Image transport absent (_messages_to_ollama)**  
- Impact : même si GAP 1 était corrigé, les images ne parviendraient jamais au modèle Ollama
- Fichier : ollama_cloud.py:57-69 — filtre `if p.type == "text"` exclusif
- Fix minimal : ajouter dans _messages_to_ollama un bloc `elif p.type == "image_ref"` qui lit le fichier, l'encode en base64, et construit un entry avec `images: [base64_data]` selon le format Ollama multimodal

### Priorité 2 (reliability) — Impact sur WorldState et vérification post-action

**GAP 3 : browser.click observation=() — last_clicked_target jamais produit**  
- Impact : le modèle ne peut pas vérifier que le bon élément a été cliqué, _finalize_turn n'a pas d'info sur la cible
- Fichier : browser.py:64 (observation=()), agent.py:_click() (evidence={})
- Fix minimal : (1) ajouter _LAST_CLICKED_OBSERVATION = ObservationSpec(...) dans browser.py, (2) changer observation=() → observation=_LAST_CLICKED_OBSERVATION pour browser.click, (3) retourner evidence={"clicked_target": target} dans agent.py:_click()

### Priorité 3 (completeness) — Capacités manquantes non critiques

**GAP 4 : browser.click_at_position absent**  
- Impact : impossibilité de cliquer par coordonnées pixel (utile quand DOM insuffisant)
- Requis conjointement avec Fix A (bootstrap) + GAP 1 (register_visual_tools) pour être utile
- Fix minimal : réajouter dans _DISPATCH + _defs + implémenter handler via Playwright `page.mouse.click(x, y)`

**GAP 5 : Fix A absent (bootstrap _capture_browser avec dimensions)**  
- Impact : même si GAP 1 corrigé, viewport=None dans find_in_browser → grounding sans normalisation
- Fix minimal : BrowserController.screenshot() doit retourner {"path": ..., "width": w, "height": h}

---

## Section 24 — Conclusion

### Ce qui est opérationnel en production

RAYA V2 a une capacité DOM complète et fonctionnelle :
- 7 outils browser enregistrés, découvrables, exposés au modèle, exécutables
- `browser.read_page` produit un DOM structuré (url/title/buttons/links/inputs) avec compaction (20G-B)
- `browser.navigate` est le seul outil qui met à jour WorldState (browser.current_url)
- `_finalize_turn` fonctionne (20G-B), avec la nuance que `last_clicked_target` est toujours absent

### Ce qui n'est pas opérationnel en production

Vision : 5 outils définis, 0 enregistrés, 0 accessibles au modèle. Deux ruptures indépendantes :
1. `register_visual_tools` jamais appelé (GAP 1 — câblage absent)
2. `_messages_to_ollama` drop silencieusement `image_ref` (GAP 2 — transport absent)

Ces deux ruptures doivent être corrigées conjointement pour que la vision soit fonctionnelle.

### Documentation vs réalité

Trois affirmations dans des rapports précédents sont contredites par le code actuel :
1. Image transport "présent et fonctionnel" (RAYA_V2_REAL_MULTIMODAL_AUDIT.md) → INCORRECT
2. `browser.last_clicked_target` "works" → INCORRECT (jamais produit)
3. `browser.click_at_position` disponible → INCORRECT (absent du dispatch)

### Réponse à la question centrale

**Quand Ruben parle à RAYA V2 en production, le modèle peut utiliser 7 outils browser DOM (navigate/read_page/screenshot/list_tabs/click/type/dismiss_overlay) via un chemin de production complet et vérifié. Il ne peut pas utiliser la vision — aucun des 5 outils vision n'est accessible, et même s'ils l'étaient, les images ne parviendraient pas à Ollama. Trois capacités référencées dans des tests et des rapports (click_at_position, last_clicked_target, image transport) n'existent pas dans le code de production actuel.**

---

*Audit 20H — ZÉRO CODE MODIFIÉ — ZÉRO TEST EXÉCUTÉ — CODE SOURCE = SEULE SOURCE DE VÉRITÉ*
