# RAYA V2 — Chantier 20I : Production Reality / Implementation Drift Audit
## Photographie exacte du système — zéro modification, zéro test, zéro runtime

**Date** : 2026-09-16  
**Mode** : AUDIT STATIQUE UNIQUEMENT  
**Méthode** : Lecture directe du code source, hiérarchie CODE > BOOTSTRAP > REGISTRY > RAPPORTS  
**Tests exécutés** : AUCUN — STATIC AUDIT ONLY  
**Rapport précédent** : `RAYA_V2_20H_PRODUCTION_PATH_INTEGRITY_AUDIT.md`

---

## Section 1 — Executive Summary

**Question centrale** : Parmi toutes les capacités déclarées "IMPLEMENTED / PASS / TERMINÉ" dans les rapports précédents, combien sont réellement accessibles au modèle dans le runtime de production actuel ?

**Réponse directe** :

Sur les grandes familles de capacités :

| Famille | Statut production | Notes |
|---|---|---|
| Harness loop (agentic, verification, finalization) | ✅ PRODUCTION | 20G-B inclus, steer() = NOT_IMPLEMENTED |
| Context / WorldState / Memory | ✅ PRODUCTION | assemblage complet, freshness, provenance |
| Tasks (create/list/pause/resume/cancel/steer) | ✅ PRODUCTION | 6 outils enregistrés |
| Safety (SAFE/SENSITIVE/STOP/confirmation) | ✅ PRODUCTION | permission gating complet |
| Attention (classification, interruption) | ✅ PRODUCTION | pure function, wired à EventBus |
| PC / Windows (27 capacités) | ✅ PRODUCTION | conditionnel enable_windows_device=True |
| Browser / DOM (7 outils) | ✅ PRODUCTION | conditionnel enable_browser_device=True |
| Spatial (8 outils) | ✅ PRODUCTION | unconditionally registered |
| Vision (5 outils) | ❌ SCAFFOLDED | code présent, jamais enregistré |
| Voice (VAD/STT/TTS) | ⚙️ PARTIAL | code complet, `enable_voice=False` par défaut |
| Telegram | ⚙️ PARTIAL | code complet, `enable_telegram=False` par défaut |
| Steering mid-turn (harness.steer) | ❌ NOT_IMPL | retourne explicitement NOT_IMPLEMENTED |
| browser.click_at_position | ❌ NOT_IMPL | absent dispatch + catalog |
| browser.last_clicked_target (WorldState) | ❌ DEAD KEY | jamais écrite par aucun outil |
| Image transport (image_ref → Ollama) | ❌ NOT_IMPL | silently dropped |
| Incoming call → INTERRUPT (13G) | ❌ NOT_IMPL | retourne IGNORE (pas de RuleEngine) |

**Verdict global** : **PRODUCTION REALITY HAS SIGNIFICANT DRIFT**

Le runtime core (harness, context, memory, tasks, safety) est solide. Les drifts majeurs sont concentrés dans la couche Vision/Multimodal et dans plusieurs capacités "annoncées" qui sont soit absentes du code, soit présentes mais jamais câblées au runtime.

---

## Section 2 — Blueprint Capability Inventory

Source : `RAYA_V2_TECHNICAL_ARCHITECTURE.md` (16 subsystems)

| Capacité Blueprint | Attendu dans | Statut code actuel |
|---|---|---|
| Harness agentic loop | harness/loop.py | ✅ IMPLEMENTED |
| Tool discovery (capability-based) | harness/loop.py + tools/discovery.py | ✅ IMPLEMENTED |
| Budget finalization (BUDGET ≠ FAIL) | harness/loop.py | ✅ IMPLEMENTED (20G-B) |
| Steering mid-turn | harness/steering.py | ❌ NOT_IMPLEMENTED (stub Phase 0) |
| LoopDetector (identical failures) | cognition/recovery.py | ✅ IMPLEMENTED |
| Context assembly | context_engine/assembler.py | ✅ IMPLEMENTED |
| WorldState freshness | world_state/store.py | ✅ IMPLEMENTED |
| Memory lifecycle | memory/store.py | ✅ IMPLEMENTED |
| Attention classification | attention/evaluator.py | ✅ IMPLEMENTED |
| Task persistence + recovery | tasks/registry.py | ✅ IMPLEMENTED |
| Safety permissions (SAFE/SENSITIVE/DESTRUCTIVE) | safety/ | ✅ IMPLEMENTED |
| STOP controller | safety/stop.py | ✅ IMPLEMENTED |
| Windows Device Agent | devices/windows/ | ✅ IMPLEMENTED (27 capabilities) |
| Browser Device Agent | devices/browser/ | ✅ IMPLEMENTED (7 tools) |
| iOS/Phone Link Agent | devices/ios/ | ✅ CODE (conditional on Phone Link) |
| Vision tools | tools/catalog/visual.py | ❌ SCAFFOLDED (never registered) |
| Voice interface | interfaces/voice/ | ⚙️ PARTIAL (code, disabled by default) |
| Telegram interface | interfaces/telegram/ | ⚙️ PARTIAL (code, disabled by default) |
| CLI interface | interfaces/cli/ | ✅ IMPLEMENTED (Phase 0) |
| Web/UI interface | interfaces/ui/ | ⚙️ PARTIAL (code exists, startup unknown) |
| Spatial / SceneStore | tools/catalog/spatial.py | ✅ IMPLEMENTED (8 tools) |
| Perception sensors | perception/ | ✅ IMPLEMENTED (3 sensors) |
| Model routing (capability-based) | models/router.py | ✅ IMPLEMENTED |
| OllamaCloud adapter | models/providers/ollama_cloud.py | ✅ IMPLEMENTED |
| OllamaLocal adapter | models/providers/ollama_cloud.py | ✅ IMPLEMENTED (inherits Cloud) |
| NullProvider (honest refusal) | models/providers/ | ✅ IMPLEMENTED |
| Image transport (image_ref) | models/providers/ollama_cloud.py | ❌ NOT_IMPLEMENTED |
| ExecutionRecord (crash safety) | harness/loop.py | ✅ IMPLEMENTED |
| ObservabilityTracer | observability/ | ✅ IMPLEMENTED |

---

## Section 3 — Current Production Architecture

```
bootstrap()  [runtime/bootstrap.py]
  │
  ├── EventBus
  ├── SqliteBackend
  ├── ObservabilityTracer + EventStore
  ├── SafetyService (StopController + AuditTrail)
  ├── WorldStateStore
  ├── MemoryStore
  ├── TaskRegistry
  ├── ExecutionRecordRepository
  ├── SceneStore
  │
  ├── ModelRegistry
  │   ├── OllamaCloudAdapter [if OLLAMA_API_KEY]
  │   │   ├── deepseek-v4-flash : reasoning|planning|fast_response
  │   │   ├── kimi-k2.7-code   : coding
  │   │   └── gemma4           : vision|classification|summarization
  │   ├── OllamaLocalAdapter [if RAYA_ENABLE_OLLAMA_LOCAL=true]
  │   └── NullProvider (always)
  │
  ├── ToolRegistry
  │   ├── register_demo_tools (workspace demo)
  │   ├── register_system_time_tool (system.time)
  │   ├── register_ui_view_tools (ui.*)
  │   ├── register_spatial_tools (8 tools: scene.*)
  │   ├── register_task_control_tools (6 tools: tasks.*)
  │   ├── register_preference_tools (preferences.*)
  │   ├── [if enable_windows_device=True] register_pc_tools (26 tools: pc.*)
  │   ├── [if enable_browser_device=True] register_browser_tools (7 tools: browser.*)
  │   ├── [if enable_phone_device=True] register_phone_tools (ios/phone link)
  │   └── ❌ register_visual_tools → NEVER CALLED
  │
  ├── DeviceRegistry
  │   ├── [if enable_windows_device] WindowsDeviceAgent (27 capabilities)
  │   ├── [if enable_browser_device] BrowserDeviceAgent (7 capabilities)
  │   └── [if enable_phone_device] PhoneLinkDeviceAgent (N capabilities)
  │
  ├── PerceptionRuntime [if enable_perception=True, default=True]
  │   ├── ActiveWindowSensor → perception.window_changed
  │   ├── PhoneCallActivitySensor → perception.phone_call_activity
  │   └── IncomingCallNotificationSensor → perception.incoming_call_notification
  │
  └── Harness (orchestrateur principal)
        └── AttentionEngine (subscribed to interface.request_received, task.*, safety.*, perception.*)

DEFAULTS (from config.py):
  enable_windows_device = True
  enable_browser_device = True
  enable_phone_device   = True
  enable_voice          = False ← DISABLED BY DEFAULT
  enable_perception     = True
  enable_telegram       = False ← DISABLED BY DEFAULT
  max_tool_iterations   = 12
  context_budget_tokens = 4096
```

**Interfaces** (NOT wired in bootstrap, started by separate entrypoints) :
- CLI : `interfaces/cli/repl.py` (Phase 0, entry point)
- Voice : `interfaces/voice/runtime.py` (RAYA_ENABLE_VOICE=true required)
- Telegram : `interfaces/telegram/runtime.py` (RAYA_TELEGRAM_ENABLED=true required)
- UI/Web : `interfaces/ui/` (startup path UNKNOWN — not in bootstrap)

---

## Section 4 — Production Capability Matrix

### Harness + Cognition

| Capability | Code | Registered | Discoverable | Model | Executable | Result | Production |
|---|---|---|---|---|---|---|---|
| handle_request() | ✅ | — | — | — | ✅ | ✅ | ✅ |
| _run_agentic_loop() | ✅ | — | — | — | ✅ | ✅ | ✅ |
| _discover_tool_schemas() (all tags) | ✅ | — | — | — | ✅ | ✅ | ✅ |
| _finalize_turn() (20G-B) | ✅ | — | — | — | ✅ | ✅ | ✅ |
| _explain_blocked_turn() (ESCALATE) | ✅ | — | — | — | ✅ | ✅ | ✅ |
| steer() mid-turn | ✅ | — | — | — | ✅ | ❌ NOT_IMPL | ❌ |
| LoopDetector (2 failures → ESCALATE) | ✅ | — | — | — | ✅ | ✅ | ✅ |
| safety.should_stop() | ✅ | — | — | — | ✅ | ✅ | ✅ |
| ExecutionRecord (crash safety) | ✅ | — | — | — | ✅ | ✅ | ✅ |
| detect_repeating_cycle (state loop) | ✅ | — | — | — | ✅ | ✅ | ✅ |

### Tasks

| Tool | Code | Registered | Model | Executable | WorldState | Production |
|---|---|---|---|---|---|---|
| tasks.pause | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| tasks.resume | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| tasks.cancel | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| tasks.steer (write checkpoint.steering_guidance) | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| tasks.create (with delay/run_at scheduling) | ✅ | ✅ (ops.create wired) | ✅ | ✅ | — | ✅ |
| tasks.list | ✅ | ✅ (ops.list wired) | ✅ | ✅ | — | ✅ |

**Note** : `tasks.steer` (tool, writes checkpoint) ≠ `harness.steer()` (mid-turn injection, NOT_IMPL).

### Spatial

| Tool | Code | Registered | Model | Executable | WorldState | Production |
|---|---|---|---|---|---|---|
| scene.create | ✅ | ✅ | ✅ | ✅ | spatial.active_scene | ✅ |
| scene.add_object | ✅ | ✅ | ✅ | ✅ | spatial.scene_object_count | ✅ |
| scene.update_object | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| scene.remove_object | ✅ | ✅ | ✅ | ✅ | spatial.scene_object_count | ✅ |
| scene.describe | ✅ | ✅ | ✅ | ✅ | — | ✅ |
| scene.render (→ ui.view_requested event) | ✅ | ✅ | ✅ | ✅ | spatial.renderer_state | ✅ |
| scene.close | ✅ | ✅ | ✅ | ✅ | spatial.renderer_state | ✅ |
| scene.export | ✅ | ✅ | ✅ | ✅ | — | ✅ |

---

## Section 5 — Core Runtime

### 5.1 Harness

**Statut** : IMPLEMENTED

`loop.py` — boucle agentique complète :
- `handle_request()` → `_run_agentic_loop()` → `for _iteration in range(max_tool_iterations)`
- `_discover_tool_schemas()` : tous les capability_tags → tous les outils enregistrés
- `_promote_observations_and_verify()` : promotion WorldState via `Tool.observation` (générique, jamais un `if tool_name ==`)
- `_finalize_turn()` : disponible si budget épuisé (BUDGET ≠ FAIL) — `available_tools=None`
- `steer()` : `ErrorInfo(code="NOT_IMPLEMENTED")` — Phase 0 stub, **aucune injection mid-turn possible**
- LoopDetector : 2 échecs identiques (SHA256) → ESCALATE

### 5.2 Context Engine

**Statut** : IMPLEMENTED

`assemble()` appelé sans `world_state_domains` → TOUS les domaines WorldState récupérés.

Contenu du system prompt (via `render_system_prompt(context)`) :
1. SYSTEM_RULES (identité + directives)
2. MEMORY (personnel + conversation, derniers 5 tours)
3. WORLD_STATE (tous les faits actifs — freshness TTL appliqué)
4. TASK_STATE + ACTIVE_TASKS (non-terminaux seulement)
5. Pas de TOOL_SCHEMAS dans le system prompt (envoyés via `available_tools`)

### 5.3 Memory

**Statut** : IMPLEMENTED

`MemoryType` : `fact`, `preference`, `rule`, `experience`  
`MemoryLayer` : `working`, `conversation`, `personal`, `project`, `task`, `experience`  
`MemoryLifecycle` : `candidate`, `active`, `confirmed`, `aging`, `obsolete`

Persistence : SQLite via `SqliteBackend`.

Tour utilisateur → `MemoryEntry(FACT, CONVERSATION)` écrit dans `handle_request()`.  
Tour assistant → `MemoryEntry(FACT, CONVERSATION, provenance=":assistant")` écrit via `_remember_assistant_turn()`.

**Experience distillation** : contrat MemoryType.EXPERIENCE existe. Mécanisme de distillation automatique : NON VÉRIFIÉ dans cette session (pas de code lu).

### 5.4 WorldState

**Statut** : IMPLEMENTED

`WorldStateFact` : domain, key, value, status (active/stale/superseded), freshness_ttl_s, confidence  
Lazily transitions to STALE on read if TTL expired.

Faits produits en production (vérifié par lecture des ObservationSpec dans les catalogs) :
- `browser.current_url` (navigate, TTL 60s) ✅
- `pc.active_window` (application.launch/focus, window.focus, TTL 60s) ✅
- `pc.last_interaction_target` (keyboard.type, ui.type, TTL 300s) ✅
- `pc.battery_level` (power.battery_level, TTL 120s) ✅
- `filesystem.*` (filesystem.open_path, key_from_argument="name") ✅
- `software.launcher_detected` (software.discover, TTL 3600s) ✅
- `spatial.active_scene` (scene.create) ✅
- `spatial.scene_object_count` (scene.add_object, remove_object) ✅
- `spatial.renderer_state` (scene.render/close, TTL 300s) ✅
- `visual.screen_state` (vision.observe_screen — ❌ DEAD: vision non enregistrée) ✅ spec exists, ❌ never written
- `visual.browser_visual_state` (vision.observe_browser — ❌ DEAD) ✅ spec, ❌ never written
- `browser.last_clicked_target` (browser.click — ❌ DEAD: observation=()) ❌ NEVER written

**Perception → WorldState** : ActiveWindowSensor publie `perception.window_changed` sur EventBus. Comment ce fait parvient à WorldState : NON VÉRIFIÉ DIRECTEMENT dans cette session (chemin EventBus → WorldStateStore non tracé via lecture de code).

### 5.5 Attention

**Statut** : IMPLEMENTED

`AttentionEvaluator` — fonction pure, aucun appel modèle ni tool.

Subscriptions : `interface.request_received`, `task.*`, `safety.*`, `perception.*`

Décisions confirmées :
- `interface.request_received` → PROCESS_NOW (toujours) ✅
- `task.completed` (in focus) → PROCESS_NOW ✅
- `task.recovered` → PROCESS_NOW ✅
- `task.blocked` → PROCESS_NOW ✅
- `task.failed` (CRITICAL) → INTERRUPT ✅
- `task.failed` (HIGH/focus) → PROCESS_NOW ✅
- `task.failed` (normal) → BACKGROUND ✅
- `perception.phone_call_activity` → INTERRUPT (dédupliqué) ✅
- `perception.incoming_call_notification` (call_state="incoming") → **IGNORE** (NOT_IMPLEMENTED contextuel) ⚠️
- Tous les autres `perception.*` → IGNORE ✅
- `safety.*` → BACKGROUND ✅

**Note 13G** : `_decide_incoming_call_notification()` retourne IGNORE pour un appel entrant "parce qu'il n'y a aucune justification contextuelle explicite câblée". Le rapport 13G affirme "INTERRUPT implémenté" — le code actuel dit **IGNORE**. Voir Section 16 (drift).

---

## Section 6 — PC / Windows

### 6.1 Windows Device Agent (windows/agent.py)

**_DISPATCH** — 27 entrées confirmées :
```
application.launch, application.close, application.focus, application.list
window.list, window.focus, window.close
keyboard.type, keyboard.press
mouse.click, mouse.move
screen.capture
ui.inspect, ui.click, ui.type
process.list
filesystem.find_folder, filesystem.open_path
capability.discover
shell.execute
power.battery_level
software.discover, software.list_installed, software.search_packages,
software.package_info, software.probe_path, software.resolve_launch_options
```

**screen.capture** retourne `{"path": ..., "width": r["width"], "height": r["height"]}` via `screen.capture()` → pyautogui. **Dimensions INCLUSES** (contrairement à BrowserController.screenshot).

### 6.2 PC Tools (pc.py)

**26 tools registered** (register_pc_tools) :
```
pc.window.list, pc.window.focus, pc.window.close
pc.application.launch, pc.application.close, pc.application.focus, pc.application.list
pc.keyboard.type, pc.keyboard.press
pc.mouse.click, pc.mouse.move
pc.screen.capture
pc.ui.inspect, pc.ui.click, pc.ui.type
pc.process.list
pc.filesystem.find_folder, pc.filesystem.open_path
pc.capability.discover
pc.power.battery_level
pc.shell.execute
pc.software.discover, pc.software.list_installed, pc.software.search_packages,
pc.software.package_info, pc.software.probe_path, pc.software.resolve_launch_options
```

### 6.3 WorldState observations depuis PC tools

| WorldState key | Producteur | TTL | Condition |
|---|---|---|---|
| `pc.active_window` | application.launch, application.focus, window.focus | 60s | SUCCESS uniquement |
| `pc.last_interaction_target` | keyboard.type, ui.type | 300s | SUCCESS uniquement |
| `pc.battery_level` | power.battery_level | 120s | SUCCESS uniquement |
| `filesystem.{name}` | filesystem.open_path | ∞ | key_from_argument="name" |
| `software.launcher_detected` | software.discover | 3600s | SUCCESS uniquement |

### 6.4 Statut global PC

**IMPLEMENTED** — niveaux A→H tous confirmés pour les 26 tools. Conditionnel `enable_windows_device=True` (défaut True).

---

## Section 7 — Browser / DOM

### 7.1 Table de statut complète

| Capability | Exists | Registered | Dispatch | Model-visible | Executable | Observation | Production |
|---|---|---|---|---|---|---|---|
| browser.navigate | ✅ | ✅ | ✅ | ✅ | ✅ | browser.current_url | ✅ |
| browser.read_page | ✅ | ✅ | ✅ | ✅ | ✅ | () — aucune | ✅* |
| browser.screenshot | ✅ | ✅ | ✅ | ✅ | ✅ | () — aucune | ✅* |
| browser.list_tabs | ✅ | ✅ | ✅ | ✅ | ✅ | () — aucune | ✅* |
| browser.click | ✅ | ✅ | ✅ | ✅ | ✅ | () — aucune | ✅** |
| browser.type | ✅ | ✅ | ✅ | ✅ | ✅ | () — aucune | ✅** |
| browser.dismiss_overlay | ✅ | ✅ | ✅ | ✅ | ✅ | () — aucune | ✅** |
| browser.click_at_position | ❌ | ❌ | ❌ | ❌ | ❌ | — | ❌ |
| browser.last_clicked_target (WS) | ❌ | — | — | — | — | — | ❌ |
| DOM compaction (read_page) | ✅ | — | — | ✅ | ✅ | — | ✅ |

*Résultat visible au modèle via message role="tool", mais WorldState non mis à jour.  
**Exécution réussie, evidence={} vide, WorldState jamais mis à jour.

### 7.2 BrowserController.screenshot() — dimensions absentes

`agent.py:_screenshot()` → `controller.screenshot()` → retourne `{"status": "ok", "path": path}`.  
**PAS de width, PAS de height.**  
`pc.screen.capture` via `screen.capture()` (pyautogui) → retourne `width` et `height`. ← ASYMÉTRIE.

### 7.3 LoopDetector dans le contexte browser

`detect_repeating_cycle(state_history)` : seul le `url` de l'evidence est un state_signal (`loop.py:737`). Donc seul `browser.navigate` peut nourrir la détection de cycle d'état. `browser.read_page`, `browser.click`, etc. n'alimentent pas `state_history`.

---

## Section 8 — Vision

### 8.1 Table de statut A→H

| Capability | CE | IM | RG | DI | EX | XE | RC | E2E | Statut |
|---|---|---|---|---|---|---|---|---|---|
| vision.observe_screen | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **SCAFFOLDED** |
| vision.find_on_screen | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **SCAFFOLDED** |
| vision.observe_browser | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **SCAFFOLDED** |
| vision.find_in_browser | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **SCAFFOLDED** |
| vision.observe_image | ✅ | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | **SCAFFOLDED** |
| image_ref → Ollama base64 | ❌ | — | — | — | — | — | — | — | **NOT_IMPL** |
| _parse_grounding normalization | ✅ | ✅ | — | — | — | ❌ | ❌ | ❌ | **DEAD CODE** |
| _handle_find_in_browser viewport | ✅ | ✅ | — | — | — | ❌ | ❌ | ❌ | **DEAD CODE** |
| bootstrap capture_browser_fn | ❌ | — | — | — | — | — | — | — | **NOT_IMPL** |

### 8.2 Cause racine du blocage complet

Deux ruptures indépendantes :  
1. `register_visual_tools()` jamais appelée (bootstrap.py:32-44 — absent des imports)  
2. `_messages_to_ollama()` filtre `if p.type == "text"` → drop silencieux de `image_ref`

Même si (1) était corrigé, (2) empêcherait les images d'atteindre Ollama.

### 8.3 Modèle Vision configuré mais inaccessible

`_DEFAULT_MODEL_POOL` inclut `gemma4:cloud:vision|classification|summarization`.  
Le modèle est configuré pour VISION. Mais :  
- Aucun outil vision ne génère un `ModelRequest(capability=VISION)` depuis la boucle agentique (vision tools non enregistrés)  
- Si `observe_image()` était appelé hors boucle, `image_ref` serait droppé par `_messages_to_ollama`

**gemma4 avec capability VISION : configuré, jamais sollicité.**

---

## Section 9 — Voice

### 9.1 Structure du code (confirmée par Glob)

```
raya/interfaces/voice/
├── audio/sounddevice_input.py  (microphone réel via sounddevice)
├── vad/silero_adapter.py       (Silero VAD)
├── stt/whisper_adapter.py      (OpenAI Whisper STT)
├── tts/kokoro_adapter.py       (Kokoro TTS)
├── channel.py                  (Voice channel → Harness)
├── runtime.py                  (orchestrateur complet)
├── presence.py                 (barge-in)
├── session.py                  (VoiceSession)
└── language.py                 (détection langue)
```

### 9.2 Statut production

**Default** : `enable_voice = False` (config.py:126)  
**Activation** : `RAYA_ENABLE_VOICE=true` dans .env  
**Startup** : NON dans bootstrap.py — interfaces démarrées séparément par entrypoint  

Niveaux :
- A (CODE EXISTS) : ✅ stack complète présente
- B (IMPORTABLE) : ✅ dépendances distinctes (sounddevice, silero, whisper, kokoro)
- C (REGISTERED) : ⚠️ startup par entrypoint externe, non par bootstrap()
- D→H : UNKNOWN — non vérifiable sans lire runtime.py et entrypoint

### 9.3 Ce que les rapports affirment vs réalité

Phase 5/6 rapports affirment "Voice IMPLEMENTED". Le code est présent. La voice n'est **pas active par défaut** et son chemin de démarrage n'est pas dans bootstrap.py.  
Statut : **PARTIALLY WIRED** — code complet, activation opt-in non vérifiée dans cette session.

### 9.4 "Réellement testé hardware"

Les rapports Phase 5/6 mentionnent des tests voix réels. Ces tests prouvent :  
- "le composant X fonctionne dans ce contexte de test"  
- Ils ne prouvent PAS que le chemin production complet (entrypoint → bootstrap → Voice runtime → Harness → response → TTS → audio) fonctionne de bout en bout.

---

## Section 10 — Telegram

### 10.1 Structure du code (confirmée)

```
raya/interfaces/telegram/
├── auth.py     (bot_token management)
├── channel.py  (chat_id per user, HarnessRequest)
├── chunker.py  (4096 char limit)
├── client.py   (polling/webhook)
├── commands.py (/start, /help, /cancel, /steer)
├── factory.py
├── runtime.py  (bot lifecycle)
└── session.py  (per-user sessions)
```

### 10.2 Statut production

**Default** : `enable_telegram = False` (config.py:141)  
**Activation** : `RAYA_TELEGRAM_ENABLED=true` + `RAYA_TELEGRAM_BOT_TOKEN=...`  
**Sécurité** : `telegram_allowed_user_ids` — FAIL CLOSED si vide (personne autorisé par défaut)  
**Startup** : NON dans bootstrap.py — démarré séparément  

Statut : **PARTIALLY WIRED** — code complet, activation opt-in, startup external.

---

## Section 11 — Phone / iOS

### 11.1 Statut production

`enable_phone_device = True` par défaut.  
Enregistré dans bootstrap via `_register_devices()` si `config.enable_phone_device`.  
Dégrade honnêtement si Phone Link n'est pas disponible (try/except, jamais un crash).

**ios/agent.py** : non lu directement. Les capabilities sont référencées dans la documentation comme : `call.dial_number`, `call.dial_contact`, `call.end`, `call.answer`, `call.reject`, `call.state`, `contacts.lookup`, `sms.send`, `connection.state`.

**Tools catalog** : `raya/tools/catalog/phone.py` non lu directement.

Statut : **PARTIALLY VERIFIED** — présence dans bootstrap confirmée, dispatch exact UNKNOWN.

---

## Section 12 — Spatial / UI

### 12.1 Spatial (scene.*)

**8 tools** enregistrés inconditionnellement (register_spatial_tools toujours appelé) :  
`scene.create`, `scene.add_object`, `scene.update_object`, `scene.remove_object`, `scene.describe`, `scene.render`, `scene.close`, `scene.export`

`scene.render` publie `ui.view_requested` sur EventBus → interface UI consomme l'event.

**Statut** : IMPLEMENTED (niveaux A→H confirmés)

### 12.2 UI / Cockpit

`raya/interfaces/ui/` : `channel.py`, `events.py`, `viewmodels.py` — présent.  
Démarrage : non dans bootstrap.py.  
Intégration Three.js (Cockpit frontend) : non vérifiée dans cette session.

**Statut** : PARTIALLY WIRED — code server-side présent, wiring frontend UNKNOWN.

---

## Section 13 — Perception

### 13.1 Sensors actifs par défaut

`enable_perception = True` par défaut. Démarré dans bootstrap via `_start_perception()`.

**Sensors** :
- `ActiveWindowSensor` → publie `perception.window_changed` quand fenêtre change
- `PhoneCallActivitySensor` → publie `perception.phone_call_activity`
- `IncomingCallNotificationSensor` → publie `perception.incoming_call_notification`

**Poll interval** : 3s par défaut.

### 13.2 Promotion vers WorldState

`ActiveWindowSensor.sample()` publie un `Event` avec `PerceptionObservation(domain="pc", key="active_window", value={...})`.

Comment ce fait parvient à WorldState : **NON VÉRIFIÉ DIRECTEMENT** dans cette session (chemin EventBus → WorldStateStore non tracé via lecture de PerceptionRuntime ou d'un subscriber dédié).  
Statut : **UNKNOWN** — le capteur publie, le consommateur WorldState non confirmé.

### 13.3 Comparaison avec pc.active_window (Device Tool)

`ActiveWindowSensor` (perception, poll léger, read-only, pas de Safety) vs  
`_ACTIVE_WINDOW_OBSERVATION` dans `pc.py` (Tool, Safety, ObservationSpec) :  
Deux mécanismes distincts, délibérément indépendants (documenté dans `windows_sensors.py`).

---

## Section 14 — Test Evidence Audit

NE PAS EXÉCUTER.

### 14.1 Tests production-path (chemin bootstrap → registry → harness)

| Test file | Ce qu'il prouve | Ce qu'il NE prouve PAS |
|---|---|---|
| `tests/harness/test_chantier20gb_finalization.py` (16 tests) | `_finalize_turn`, `_compact_read_page_output`, `_build_finalization_trace` fonctionnent | Pas que vision tools sont accessibles |
| `tests/harness/test_targeted_execution_repair.py` (6 tests) | LoopDetector, _explain_blocked_turn | Pas la chaîne end-to-end Ollama |
| `tests/harness/test_loop.py` | Harness loop complète avec mocks | Pas le modèle Ollama réel |

### 14.2 Tests injectés (bypasse production path)

| Test file | Ce qu'il prouve | Ce qu'il NE prouve PAS |
|---|---|---|
| `tests/devices/browser/test_chantier18b_browser_agentic.py:126` — `assert "browser.click_at_position" in _DISPATCH` | Rien (assertion ÉCHOUERAIT contre code actuel) | **DEAD TEST** |
| `tests/context_engine/test_referential_resolution.py` — `test_browser_click_declares_last_clicked_target_observation_spec` | Rien (assertion ÉCHOUERAIT) | **DEAD TEST** |
| Tests vision E2E (scripts/validate_20c_vision_browser_e2e.py) | observe_fn injectable fonctionne dans ce contexte | Que vision tools sont dans ToolRegistry production |
| `RAYA_V2_REAL_MULTIMODAL_AUDIT.md` "20/20 PASS" | Logique de parsing VisualObservation | Que image_ref arrive à Ollama, que vision est enregistrée |

### 14.3 Tests unitaires (valident une primitive)

| Test file | Ce qu'il prouve |
|---|---|
| `test_compact_read_page_output*` | `_compact_read_page_output()` logique correcte |
| `test_build_finalization_trace*` | Borning de la trace |
| `test_loop_detector*` | SHA256 hashing + ESCALATE logic |

### 14.4 Scripts d'audit (valident un comportement, pas la disponibilité production)

| Script | Niveau de preuve |
|---|---|
| `scripts/validate_20gb_real_e2e.py` | Chemin production confirmé pour `_finalize_turn` + compaction |
| `scripts/audit_20f_post_action_verification.py` | Référence browser.py:91 qui n'existe plus (79 lignes) — **SCRIPT DRIFT** |
| `scripts/validate_20c_vision_browser_e2e.py` | Inject observe_fn — ne prouve pas production |

---

## Section 15 — Dead Features

| Feature | Exists | Producer | Consumer | Production reachable | Statut |
|---|---|---|---|---|---|
| `register_visual_tools()` | ✅ visual.py | Personne (bootstrap ne l'appelle pas) | Personne | ❌ | DEAD CODE |
| `vision.observe_screen` handler | ✅ visual.py | Non enregistré | Non enregistré | ❌ | DEAD CODE |
| `vision.find_in_browser` + viewport Fix B | ✅ visual.py | Non enregistré | Non enregistré | ❌ | DEAD CODE |
| `_parse_grounding()` normalization Fix C | ✅ models/vision.py | models/vision.py (observe_image) | Jamais appelé (vision non enregistrée) | ❌ | DEAD CODE |
| `_LAST_CLICKED_OBSERVATION` (browser.click) | ❌ non défini | Jamais défini | `_finalize_turn` (lit WS mais jamais écrit) | ❌ | NOT_IMPL |
| `browser.last_clicked_target` (WorldState key) | ❌ jamais écrit | Aucun outil | `_finalize_turn` → `ws_relevant` | ❌ | DEAD KEY |
| `browser.click_at_position` handler | ❌ absent | — | Tests/scripts | ❌ | NOT_IMPL |
| `steer()` harness mid-turn | ✅ steering.py | Retourne NOT_IMPL | Qui que ce soit | ❌ | NOT_IMPL |
| `OllamaCloudAdapter` image_ref encoding | ❌ absent | — | `observe_image()` (jamais appelé) | ❌ | NOT_IMPL |
| `harness.steer()` mid-turn | ✅ steering.py | — | — | ❌ | NOT_IMPL (stub) |
| `visual.screen_state` (WorldState key) | spec ✅ visual.py | vision.observe_screen (non enregistrée) | context_engine | ❌ | DEAD SPEC |
| `visual.browser_visual_state` (WorldState key) | spec ✅ visual.py | vision.observe_browser (non enregistrée) | context_engine | ❌ | DEAD SPEC |
| `_capture_browser_fn` (bootstrap fixture) | ❌ absent | — | register_visual_tools | ❌ | NOT_IMPL |
| `_capture_screen_fn` (bootstrap fixture) | ❌ absent | — | register_visual_tools | ❌ | NOT_IMPL |

---

## Section 16 — Documentation Drift (table exhaustive)

| Rapport | Affirmation | Code actuel | Réalité | Sévérité |
|---|---|---|---|---|
| RAYA_V2_REAL_MULTIMODAL_AUDIT.md §2.2 | "fix encodant image_ref → images:[base64_pure] est présent et fonctionnel" | `_messages_to_ollama`: `if p.type == "text"` seul filtre, zéro base64 | ABSENT | ⚠️ CRITIQUE |
| RAYA_V2_BROWSER_AGENTIC_EXECUTION_REPAIR_REPORT.md | "browser.last_clicked_target works via _promote_observations_and_verify()" | browser.click a `observation=()`, evidence={} | JAMAIS ÉCRIT | ⚠️ CRITIQUE |
| RAYA_V2_IMPROVEMENT_SAFE_AUTONOMY_LATENCY_REPORT.md:100 | `ObservationSpec(domain="browser", key="last_clicked_target", evidence_field="clicked_target", freshness_ttl_s=300)` sur browser.click | `browser.py:64` : `observation=()` | ABSENT | ⚠️ CRITIQUE |
| Multiples rapports Phase 18/18B/18D | "browser.click_at_position IMPLEMENTED, disponible en production" | absent de `_DISPATCH` (agent.py) et `_defs` (browser.py) | ABSENT | ⚠️ CRITIQUE |
| RAYA_V2_CHANTIER_13G_IMPLEMENTATION_REPORT.md | "Incoming call → INTERRUPT implémenté" | `_decide_incoming_call_notification()` retourne IGNORE (raisonnement détaillé: "aucune justification contextuelle") | IGNORE pas INTERRUPT | ⚠️ MAJEUR |
| RAYA_V2_REAL_MULTIMODAL_AUDIT.md | "20/20 automated PASS" pour vision | Tests injectaient observe_fn directement, bypass _messages_to_ollama et ToolRegistry | Tests injectés, pas production | ⚠️ MAJEUR |
| RAYA_V2_VISION_FOUNDATION_IMPLEMENTATION_REPORT.md | "Vision foundation IMPLEMENTED" | register_visual_tools() jamais appelée dans bootstrap | SCAFFOLDED | ⚠️ MAJEUR |
| Rapports Phase 18D | "Fix A bootstrap _capture_browser implémenté" | bootstrap.py 0 occurrences PIL, pyautogui, _capture_browser | ABSENT | ⚠️ MAJEUR |
| RAYA_V2_CHANTIER18B_VALIDATION_AUDIT.md | "browser.click_at_position in _DISPATCH PASS" | absent du _DISPATCH actuel | DEAD TEST | ⚠️ MAJEUR |
| Rapports divers | "Steering IMPLEMENTED" | `steer()` retourne `ErrorInfo(code="NOT_IMPLEMENTED")` | NOT_IMPL | MODÉRÉ |
| Scripts audit_20f | référence browser.py:91 | browser.py = 79 lignes (ligne 91 n'existe pas) | SCRIPT DRIFT | MODÉRÉ |
| RAYA_V2_COOKIE_CONSENT_IMPLEMENTATION_REPORT.md | Plusieurs comportements cookie consent | Liés à browser._dismiss_overlay (implémenté) | Plausible | FAIBLE |

---

## Section 17 — Test Drift

Tests qui référencent des APIs absentes du code actuel :

| Test | API référencée | Statut code actuel | Type drift |
|---|---|---|---|
| `test_chantier18b_browser_agentic.py:126` : `assert "browser.click_at_position" in _DISPATCH` | `browser.click_at_position` | ABSENT de _DISPATCH | **TEST DRIFT** |
| `test_referential_resolution.py` : `test_browser_click_declares_last_clicked_target_observation_spec` | `_LAST_CLICKED_OBSERVATION` sur browser.click | `observation=()` | **TEST DRIFT** |
| `test_referential_resolution.py` : `test_browser_click_promotes_last_clicked_target_to_world_state` | WorldState mis à jour après click | Jamais promu | **TEST DRIFT** |

Ces tests échoueraient contre le code actuel. Leur présence suggère que ces capacités ont existé à un moment, puis ont été retirées sans mettre à jour les tests.

---

## Section 18 — Silent Regression Candidates

### 18.1 DOCUMENTATION DRIFT (annoncé → absent)
- `browser.click_at_position` — présent dans rapports Phase 18B/18D, absent du code
- `browser.last_clicked_target` — présent dans rapports, jamais écrit
- `image_ref` transport — annoncé "fonctionnel", silently dropped
- Fix A bootstrap `_capture_browser` — annoncé, absent de bootstrap.py

### 18.2 TEST DRIFT (tests référencent APIs supprimées)
- `test_chantier18b_browser_agentic.py:126` — `browser.click_at_position` in `_DISPATCH`
- `test_referential_resolution.py` — `_LAST_CLICKED_OBSERVATION` sur browser.click
- `scripts/audit_20f_post_action_verification.py:628` — référence browser.py:91 (fichier = 79 lignes)

### 18.3 SCRIPT DRIFT
- `scripts/audit_20f_post_action_verification.py` cite `browser.py:91` → ligne inexistante
- `scripts/validate_20c_vision_browser_e2e.py` teste `browser.click_at_position` dans la trace

### 18.4 CODE REGRESSION (suppression sans nettoyage)
- `browser.click_at_position` : supprimé du _DISPATCH et des _defs, mais tests/scripts non mis à jour
- `_LAST_CLICKED_OBSERVATION` sur browser.click : supprimé du code tool catalog (observation=()), mais tests attendent encore cette spec
- `steer()` mid-turn : retourne NOT_IMPL depuis Phase 0, plusieurs rapports ont annoncé "steering implémenté" sans préciser que c'était `tasks.steer` (checkpoint), pas `harness.steer()` (mid-turn injection)

### 18.5 UNKNOWN (non vérifiable dans cette session)
- Perception → WorldState promotion path (EventBus subscriber non tracé)
- Phone/iOS dispatch exact (ios/agent.py non lu)
- Voice runtime.py startup path exact
- Telegram runtime.py startup path exact
- Memory experience distillation mechanism
- UI/Cockpit WebSocket wiring exact

---

## Section 19 — Rapport Claims vs Code Reality

### Claims vérifiés ✅ (rapports corrects)

| Rapport | Claim | Vérification |
|---|---|---|
| 20G-B implementation report | `_finalize_turn` + `_compact_read_page_output` IMPLEMENTED | ✅ confirmé par lecture loop.py |
| 20G-B E2E validation | `_explain_blocked_turn` = 0 lors de budget exhaustion | ✅ logique loop.py confirmée |
| Phase 4 report | WindowsDeviceAgent 27 capabilities | ✅ confirmé agent.py _DISPATCH |
| Phase 3 report | Browser 7 tools wired | ✅ confirmé browser.py + agent.py |
| Phase 10 report | tasks.create + tasks.list registered | ✅ confirmé tasks.py + bootstrap.py |
| Chantier 13G report | "NOT_IMPLEMENTED contextual rule engine" | ✅ confirmé evaluator.py (IGNORE + long comment) |
| 20H audit | Tous les findings | ✅ confirmés par relecture |

### Claims incorrects ou partiels ❌ (rapports en drift)

| Rapport | Claim | Réalité |
|---|---|---|
| RAYA_V2_REAL_MULTIMODAL_AUDIT.md | image_ref transport "fonctionnel" | NOT_IMPL |
| RAYA_V2_BROWSER_AGENTIC_EXECUTION_REPAIR_REPORT.md | last_clicked_target "works" | JAMAIS ÉCRIT |
| Multiple 18/18D reports | browser.click_at_position "IMPLEMENTED" | ABSENT |
| Phase 5/6 reports | "Voice IMPLEMENTED" | PARTIAL (code, disabled by default) |
| Phase 9/Telegram reports | "Telegram IMPLEMENTED" | PARTIAL (code, disabled by default) |
| 13G report headline | "INTERRUPT incoming calls" | IGNORE (deliberate NOT_IMPL) |
| RAYA_V2_VISION_FOUNDATION_IMPLEMENTATION_REPORT.md | "Vision foundation DONE" | SCAFFOLDED |

---

## Section 20 — Global Status

### Comptage par statut

| Statut | Capacités | Exemples |
|---|---|---|
| **REAL PRODUCTION** | 80+ | harness, context, memory, tasks(6), PC(26), browser(7), spatial(8), safety, attention, LoopDetector, perception(sensors), model routing |
| **PARTIALLY WIRED** | 3 | voice (code complet, disabled), telegram (code complet, disabled), UI/Cockpit (code, startup UNKNOWN) |
| **SCAFFOLDED** | 5 | vision.observe_screen, vision.find_on_screen, vision.observe_browser, vision.find_in_browser, vision.observe_image |
| **DEAD CODE** | 7 | register_visual_tools, vision handlers, Fix B viewport, Fix C grounding, dead WS specs (visual.*) |
| **DOCUMENTATION DRIFT** | 6 | image_ref transport, click_at_position, last_clicked_target, Fix A bootstrap, "13G INTERRUPT", "image_ref fonctionnel" |
| **NOT_IMPLEMENTED** | 4 | image_ref→Ollama, browser.click_at_position, steer() mid-turn, _capture_browser_fn |
| **DEAD KEY** | 3 | browser.last_clicked_target, visual.screen_state, visual.browser_visual_state (specs existent, jamais écrits) |
| **UNKNOWN** | 5 | perception→WS path, phone dispatch exact, voice startup exact, telegram startup exact, memory experience distillation |

---

## Section 21 — Les 10 plus gros écarts

Par ordre d'impact opérationnel sur l'utilisateur final :

1. **Vision complètement inaccessible** : 5 outils définis, register_visual_tools jamais appelée, image_ref transport absent. Aucun chemin correctif partiellement fonctionnel.

2. **image_ref silently dropped par _messages_to_ollama** : même si vision était enregistrée, les images ne parviendraient jamais à Ollama. Rupture architecturale indépendante de (1).

3. **browser.last_clicked_target jamais écrit** : `_finalize_turn()` lit cette clé WorldState → toujours absente. La finalisation de tour n'a jamais accès à la cible cliquée.

4. **steer() mid-turn NOT_IMPLEMENTED** : il n'est pas possible d'injecter une instruction dans un tour Harness en cours. Les rapports mentionnent "steering" mais c'est `tasks.steer` (checkpoint) qui est implémenté, pas l'injection mid-turn.

5. **browser.click_at_position disparu** : référencé dans 3+ rapports comme disponible, absent du dispatch et du catalog. Tests échouent en silence (jamais exécutés contre le code actuel ?).

6. **_LAST_CLICKED_OBSERVATION spec absente de browser.click** : browser.click retourne evidence={} et a observation=(). Post-action verification ne peut jamais confirmer qu'un clic a changé l'état.

7. **Incoming call → IGNORE** (13G) : le rapport titre annonce INTERRUPT. Le code actuel dit IGNORE inconditionnellement avec un commentaire explicatif de 200 mots. Divergence entre titre du rapport et comportement réel.

8. **Vision E2E "PASS" prouve un chemin injecté, pas production** : les tests `scripts/validate_20c_vision_browser_e2e.py` injectent `observe_fn` directement. Ils ne prouvent pas que la stack vision fonctionne depuis bootstrap.

9. **WorldState ne reçoit rien de browser.read_page/click/type/screenshot/dismiss_overlay** : 6 des 7 outils browser ont observation=(). Le modèle ne peut jamais retrouver dans WorldState ce qui a été lu, cliqué, ou tapé via browser.

10. **Perception → WorldState path non tracé** : ActiveWindowSensor publie `perception.window_changed`. Qui écrit dans WorldState ? Aucun code lu dans cette session ne montre le subscriber → la question reste ouverte.

---

## Section 22 — Architecture Blueprint vs Current Reality

| Invariant Blueprint | Implémentation actuelle | Statut |
|---|---|---|
| Un seul orchestrateur Harness (invariant #1) | loop.py seul, steering.py = stub | ✅ CORRECT |
| Pas de model call hors Model Layer (#2) | vérifié — models/ seul point | ✅ CORRECT |
| Pas d'exécution outil hors Tool System (#3) | vérifié — tools.execution seul point | ✅ CORRECT |
| Device Agent n'importe pas models (#5) | vérifié — devices/ importe contracts | ✅ CORRECT |
| STOP toujours prioritaire (#6) | vérifié — should_stop() avant chaque tool/model | ✅ CORRECT |
| World State ≠ Memory (#8) | deux stores séparés, contracts distincts | ✅ CORRECT |
| Single point of write per data type (#13) | un ToolRegistry, un WorldStateStore, un MemoryStore | ✅ CORRECT |
| Vision tools registered in bootstrap | ❌ jamais appelée | **VIOLATION** |
| image_ref correctly serialized | ❌ silently dropped | **VIOLATION** |
| browser.click_at_position in production | ❌ absent dispatch | **VIOLATION** |
| browser.last_clicked_target writable | ❌ observation=() | **VIOLATION** |
| Steering mid-turn injectable | ❌ NOT_IMPLEMENTED | KNOWN_LIMITATION |

---

## Section 23 — Files Inspected

Fichiers lus directement (code source) :
- `raya/runtime/bootstrap.py` (282 lignes, intégral)
- `raya/runtime/config.py` (193 lignes, intégral)
- `raya/harness/loop.py` (760+ lignes, partiels : 1-100, 183-262, 300-500, 542-760)
- `raya/harness/steering.py` (21 lignes, intégral)
- `raya/cognition/recovery.py` (63 lignes, intégral)
- `raya/tools/registry.py` (40 lignes, intégral)
- `raya/tools/__init__.py` (13 lignes, intégral)
- `raya/tools/execution.py` (106 lignes, intégral)
- `raya/tools/discovery.py` (21 lignes, intégral)
- `raya/tools/catalog/__init__.py` (25 lignes, intégral)
- `raya/tools/catalog/browser.py` (79 lignes, intégral)
- `raya/tools/catalog/pc.py` (237 lignes, intégral)
- `raya/tools/catalog/visual.py` (406 lignes, partiels : 1-200)
- `raya/tools/catalog/spatial.py` (partial : 1-135)
- `raya/tools/catalog/tasks.py` (220 lignes, intégral)
- `raya/devices/browser/agent.py` (141 lignes, intégral)
- `raya/devices/browser/controller.py` (312 lignes, intégral)
- `raya/devices/windows/agent.py` (435 lignes, intégral)
- `raya/models/providers/ollama_cloud.py` (249 lignes, intégral)
- `raya/models/vision.py` (partial)
- `raya/contracts/__init__.py` (180 lignes, intégral)
- `raya/contracts/tool.py` (126 lignes, intégral)
- `raya/world_state/store.py` (154 lignes, intégral)
- `raya/context_engine/assembler.py` (~270 lignes, intégral)
- `raya/context_engine/render.py` (286 lignes, intégral)
- `raya/memory/__init__.py` (partial)
- `raya/attention/evaluator.py` (392 lignes, intégral)
- `raya/perception/windows_sensors.py` (91 lignes, intégral)
- `raya/perception/__init__.py` (partial)

Fichiers vérifiés (Glob uniquement) :
- `raya/interfaces/**/*.py` (36 fichiers identifiés, contenus non lus)

Rapports lus :
- `RAYA_V2_20H_PRODUCTION_PATH_INTEGRITY_AUDIT.md`
- `RAYA_V2_20G_B_REAL_E2E_VALIDATION_REPORT.md`
- `RAYA_V2_20G_B_OBJECTIVE_FINALIZATION_CONTEXT_COMPACTION_IMPLEMENTATION_REPORT.md`

---

## Section 24 — Tests Executed

**AUCUN — STATIC AUDIT ONLY.**

Aucun test, aucun script, aucun runtime, aucun Ollama, aucun browser n'a été exécuté.  
Toutes les affirmations de ce rapport sont basées sur la lecture directe du code source.

---

## Section 25 — Final Verdict

### PRODUCTION REALITY HAS SIGNIFICANT DRIFT

**Ce qui est solide** :
- Le runtime core (harness, context, memory, tasks, safety, attention) est complet et cohérent
- Les outils PC (26 tools), Browser (7 tools), Spatial (8 tools), Tasks (6 tools) ont un chemin production complet A→H
- Les invariants architecturaux (no second harness, no device brain, STOP priority) sont respectés dans le code
- 20G-B (finalization + compaction) est correctement implémenté

**Ce qui présente un drift réel** :
- Vision : 5 outils scaffoldés, jamais enregistrés, image transport absent — **système multimodal non fonctionnel**
- 3 capacités référencées dans des rapports comme "IMPLEMENTED" sont absentes du code production actuel : `browser.click_at_position`, `browser.last_clicked_target`, `image_ref transport`
- Steering mid-turn (`harness.steer()`) retourne NOT_IMPLEMENTED depuis Phase 0 — les rapports mentionnant "steering" référencent `tasks.steer` (checkpoint), pas l'injection mid-turn
- Incoming call → IGNORE (pas INTERRUPT) malgré des rapports indiquant INTERRUPT

**Ce qui nécessite investigation complémentaire** :
- Chemin perception → WorldState (EventBus subscriber non tracé)
- Startup path exact de Voice et Telegram (non dans bootstrap.py)
- iOS/Phone dispatch exact
- Memory experience distillation

**Confiance dans les anciens "IMPLEMENTED / PASS" :**  
Les claims concernant le **runtime core** (harness, context, tasks, safety, PC tools, browser tools) peuvent être considérés comme fiables — le code est là et cohérent.  
Les claims concernant **Vision, steering mid-turn, et plusieurs capacités browser** ne peuvent PAS être considérés comme fiables — le code actuel les contredit directement.

---

*Chantier 20I — ZÉRO CODE MODIFIÉ — ZÉRO TEST EXÉCUTÉ — STATIC READ-ONLY AUDIT*
