# RAYA V2 — CAPABILITY BASELINE / PRODUCTION REALITY AUDIT
## Chantier 20J — Production Capability Matrix + Architectural Readiness

**Date :** 2026-09-16  
**Mode :** AUDIT STRICT — READ ONLY  
**ZERO CODE — ZERO TEST — ZERO RUNTIME — ZERO PATCH**  
**Source de vérité :** code production actuel > bootstrap > registry/dispatch/wiring > contrats > blueprints

---

## 1. Executive Summary

RAYA V2 possède un cœur production solide et éprouvé : Harness (loop agentique, 12 itérations, finalization), Context Engine (7 types de sections, budget tokens), Memory (lifecycle complet), Tasks (6 états, persistence SQLite), Safety (classify_risk, STOP, permissions), WorldState (perception → EventBus → facts), Attention (4 décisions, évaluateur réel), Persistence (WAL SQLite), 3 tiers de devices (PC/Windows 26 outils, Browser/DOM 7 outils, Phone/iOS 9 outils), Spatial (8 outils), Tasks (6 outils), plus 10 outils utilitaires. Total production : **~73 outils accessibles au modèle** (conditionnel sur la plateforme).

Deux sous-systèmes d'interface complets (Voice, Telegram) existent avec architecture et code finis, mais ne sont pas démarrés par `bootstrap()` — ils requièrent un entrypoint séparé.

Cinq gaps critiques bloquent toute ambition DOM+Vision :
1. `register_visual_tools` n'est pas câblé au bootstrap ni exporté du catalog ;
2. `_messages_to_ollama()` écrase silencieusement tout `ContentPart(type="image_ref")` ;
3. `browser.click_at_position` absent de `_DISPATCH` et du catalog browser ;
4. `browser.last_clicked_target` : clé WorldState jamais écrite (`browser.click` has `observation=()`) ;
5. `interaction.track`/`interaction.reply` : code présent, non exporté, non câblé.

Sept capabilities supplémentaires existent dans le code mais ne sont pas accessibles en production (obsidian, telegram.send_message proactif, caméra, écran-sensor, harness.steer mid-turn).

**Verdict production :** RAYA V2 est opérationnel pour les interactions PC, browser DOM, et téléphoniques. Il n'est PAS opérationnel pour la vision multimodale, les interactions externes proactives, ni le steering mid-turn.

---

## 2. Blueprint Baseline

### 2.1 Subsystems attendus (RAYA_V2_TECHNICAL_ARCHITECTURE.md §0)

16 subsystems définis :

| # | Subsystem | Responsabilité blueprint |
|---|---|---|
| 1 | `runtime` | Composition root, bootstrap, entrypoints |
| 2 | `world_state` | État courant de l'environnement, freshness, TTL |
| 3 | `perception` | Capteurs légers continus, captures ponctuelles sur demande |
| 4 | `attention` | Priorisation des events entrants (PROCESS_NOW/BACKGROUND/INTERRUPT/IGNORE) |
| 5 | `harness` | Boucle agentique, orchestration Harness |
| 6 | `context` | Assemblage du contexte modèle, budget tokens |
| 7 | `cognition` | Intent, planning, vérification, recovery |
| 8 | `memory` | Faits/préférences/expériences durables, lifecycle |
| 9 | `tools` | Catalogue, registry, discovery, exécution |
| 10 | `safety` | Classification risque, STOP, permissions |
| 11 | `devices` | DeviceAgent, DeviceRegistry |
| 12 | `models` | Providers, router, adapters |
| 13 | `tasks` | TaskRegistry, cycle de vie, scheduling |
| 14 | `interfaces` | CLI, Voice, Telegram |
| 15 | `observability` | EventStore, tracer, logs |
| 16 | `spatial` | Scène 3D, SceneStore, ThreeJS |

### 2.2 Capabilities blueprint vs implémentées

| Capability blueprint | Présente | Câblée | Notes |
|---|---|---|---|
| Agentic loop | ✓ | ✓ | `harness/loop.py` |
| Tool discovery + execution | ✓ | ✓ | registry.all_capability_tags() |
| Context assembly | ✓ | ✓ | `context_engine/assembler.py` |
| Memory (fact/pref/exp) | ✓ | ✓ | `memory/store.py` |
| WorldState | ✓ | ✓ | `world_state/store.py` |
| Tasks lifecycle | ✓ | ✓ | `tasks/registry.py` |
| Safety / STOP | ✓ | ✓ | `safety/` |
| Attention | ✓ | ✓ | `attention/evaluator.py` |
| Perception légers | ✓ | ✓ | 3 sensors + PerceptionRuntime |
| PC/Windows tools | ✓ | ✓ | conditional |
| Browser tools | ✓ | ✓ | conditional |
| Phone/iOS tools | ✓ | ✓ | conditional |
| Spatial / Scene | ✓ | ✓ | unconditional |
| Voice | ✓ | PARTIEL | entrypoint séparé, non bootstrap |
| Telegram | ✓ | PARTIEL | entrypoint séparé, non bootstrap |
| Vision | ✓ | ✗ | SCAFFOLDED — non câblé |
| Camera capture | ✓ | ✗ | sensor existe, non instancié |
| image_ref transport | ✓ | ✗ | silently dropped |
| Mid-turn steering | ✓ | ✗ | NOT_IMPLEMENTED stub |
| Captures lourdes à la demande | ✓ | ✗ | API définie, non branchée |
| Long-horizon planning | PARTIEL | PARTIEL | create_long_horizon_task existe, pas de planner |
| Security camera | ✗ | ✗ | absent du codebase |
| Linux device | ✗ | ✗ | absent du codebase |

**Éléments présents non décrits dans le blueprint :**
- `TaskState.BLOCKED` (Chantier 15) — absent du blueprint, présent dans le code
- `not_before` scheduling dans TaskRegistry — extension non documentée au blueprint
- `IncomingCallNotificationSensor` (Chantier 13G) — capteur spécifique ShellExperienceHost
- `_finalize_turn` / `_compact_read_page_output` (Chantier 20G-B)
- `ExternalInteraction` contract (Chantier 20) — contrat présent, tools non câblés

---

## 3. Bootstrap Graph

```
bootstrap(config=None, backend=None) → RuntimeHandles
│
├─ load_config() → RuntimeConfig
│   ├─ enable_voice: bool = False         [OPT-IN]
│   ├─ enable_telegram: bool = False      [OPT-IN]
│   ├─ enable_perception: bool = True     [DEFAULT ON]
│   ├─ enable_windows_device: bool = True [DEFAULT ON]
│   ├─ enable_browser_device: bool = True [DEFAULT ON]
│   ├─ enable_phone_device: bool = True   [DEFAULT ON]
│   ├─ enable_ollama_local: bool = False  [OPT-IN]
│   ├─ max_tool_iterations: int = 12
│   └─ context_budget_tokens: int = 4096
│
├─ EventBus()                              ← in-process, per-subscriber daemon threads
├─ ObservabilityTracer(bus)               ← subscribe("*", DROP_OLDEST)
├─ SqliteBackend(config.db_path)          ← WAL + migrations on init
├─ EventStore(backend, bus)
├─ StopController(bus)                    ← subscribe("interface.stop_requested")
├─ AuditTrail()
├─ SafetyService(stop, audit)
│
├─ WorldStateStore(backend, bus)          ← subscribe("perception.*") → _on_perception_event
├─ MemoryStore(backend, bus)
├─ TaskRegistry(backend, bus)
├─ ExecutionRecordRepository(backend)
│
├─ ModelRegistry()
│   ├─ [if ollama_api_key] OllamaCloudAdapter × N (per config.model_pool)
│   ├─ [if enable_ollama_local] OllamaLocalAdapter × M (per config.local_model_pool)
│   └─ NullProvider()                     ← ALWAYS registered, honest fallback
│
├─ ToolRegistry()
│   ├─ register_demo_tools(tools, workspace_dir)
│   │   → filesystem.write_file, filesystem.read_file,
│   │     demo.always_fail, demo.idempotent_counter, demo.non_idempotent_append
│   ├─ register_system_time_tool(tools, timezone)
│   │   → system.time.now
│   ├─ register_ui_view_tools(tools, bus)
│   │   → ui.show_view, ui.hide_view
│   ├─ register_spatial_tools(tools, scene_store, bus, workspace_dir)
│   │   → scene.create, scene.add_object, scene.update_object, scene.remove_object,
│   │     scene.describe, scene.render, scene.close, scene.export
│   ├─ [CONDITIONAL] _register_devices(devices, tools, config, safety)
│   │   ├─ [enable_windows_device] register_pc_tools(tools, windows_agent, should_stop)
│   │   │   → pc.window.list/focus/close, pc.application.launch/close/focus/list,
│   │   │     pc.keyboard.type/press, pc.mouse.click/move,
│   │   │     pc.screen.capture, pc.ui.inspect/click/type,
│   │   │     pc.process.list, pc.filesystem.find_folder/open_path,
│   │   │     pc.capability.discover, pc.power.battery_level, pc.shell.execute,
│   │   │     pc.software.discover/list_installed/search_packages/package_info/
│   │   │       probe_path/resolve_launch_options  [26 tools]
│   │   ├─ [enable_browser_device] register_browser_tools(tools, browser_agent, should_stop)
│   │   │   → browser.navigate, browser.read_page, browser.screenshot,
│   │   │     browser.list_tabs, browser.click, browser.type, browser.dismiss_overlay  [7 tools]
│   │   └─ [enable_phone_device] register_phone_tools(tools, phone_agent, should_stop)
│   │       → phone.contacts.lookup, phone.call.number, phone.call.contact,
│   │         phone.call.end, phone.call.answer, phone.call.reject, phone.call.state,
│   │         phone.sms.send, phone.connection.state  [9 tools]
│   ├─ register_task_control_tools(tools, TaskControlOps(...))  [POST-HARNESS]
│   │   → tasks.pause, tasks.resume, tasks.cancel, tasks.steer,
│   │     [if ops.create] tasks.create, [if ops.list] tasks.list
│   └─ register_preference_tools(tools, PreferenceOps(...))    [POST-HARNESS]
│       → preferences.set_channel
│
├─ SceneStore()
│
├─ _start_perception(config, bus) → PerceptionRuntime | None
│   └─ [if enable_perception]
│       PerceptionRuntime([ActiveWindowSensor(), PhoneCallActivitySensor(),
│                          IncomingCallNotificationSensor()], bus, interval_s=config.perception_poll_interval_s)
│       → .start()  [starts "raya-perception" daemon thread, polls every 3s]
│
├─ Harness(world_state, memory, tasks, safety, models, bus, tools, ...,
│          context_budget_tokens=4096, max_tool_iterations=12)
│   ├─ SessionStore
│   ├─ LoopDetector(max_identical_failures=2)
│   ├─ TaskScheduler
│   ├─ AttentionEngine  [evaluates events → AttentionDecision]
│   ├─ FocusTracker
│   └─ bus.subscribe("attention.decision_made", _on_attention_decision,
│                     BLOCK_PUBLISHER_WITH_TIMEOUT)
│
├─ bus.publish(Event("runtime.started"))
└─ harness.recover()  [RUNNING → PAUSED for crashed tasks]
    → return RuntimeHandles

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
NOT STARTED BY bootstrap():
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  VoiceRuntime         ← voice/factory.py::build_real_voice_runtime(handles)
  TelegramRuntime      ← telegram/factory.py (separate entrypoint assumed)
  CameraLightSensor    ← code exists, never instantiated
  ScreenLightSensor    ← code exists, never instantiated
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

---

## 4. Global Capability Matrix

**Classification :**
- **L0** = NOT IMPLEMENTED — aucune implémentation
- **L1** = SCAFFOLDED — contrat/classe présent, comportement incomplet
- **L2** = CODE IMPLEMENTED — code complet, non connecté au runtime production
- **L3** = REGISTERED — dans son registry
- **L4** = MODEL ACCESSIBLE — le modèle peut le découvrir/utiliser
- **L5** = ENVIRONMENT CONNECTED — atteint réellement l'environnement/device/provider
- **L6** = VERIFIED PRODUCTION PATH — chemin complet prouvé statiquement

| Capability | Level | Production | Evidence source | Drift |
|---|---:|---|---|---|
| **CORE** | | | | |
| Harness agent loop | 6 | ✓ | loop.py:183, bootstrap.py:213 | — |
| Context assembly (7 sections) | 6 | ✓ | assembler.py + loop.py | — |
| Memory read/write/search | 6 | ✓ | memory/store.py, bootstrap.py:185 | — |
| WorldState update/query | 6 | ✓ | world_state/store.py, bootstrap.py:184 | — |
| Task lifecycle (6 states) | 6 | ✓ | tasks/registry.py, bootstrap.py | — |
| Safety risk classification | 6 | ✓ | safety/risk.py, safety/permissions.py | — |
| STOP signal | 6 | ✓ | stop.py + bus subscribe | — |
| Attention evaluation | 6 | ✓ | attention/evaluator.py + harness | — |
| Persistence (SQLite WAL) | 6 | ✓ | sqlite_backend.py, bootstrap.py:177 | — |
| LoopDetector / ESCALATE | 6 | ✓ | cognition/recovery.py + loop.py | — |
| _finalize_turn (20G-B) | 6 | ✓ | loop.py (post 20G-B) | — |
| Context compaction (20G-B) | 6 | ✓ | loop.py:391 | — |
| Task recovery on restart | 6 | ✓ | registry.py:recover_after_restart, harness.recover() | — |
| **MODEL LAYER** | | | | |
| Model routing (capabilities) | 6 | ✓ | router.py + registry.py | — |
| OllamaCloudAdapter | 6 | ✓ (if api_key) | providers/ollama_cloud.py | — |
| OllamaLocalAdapter | 6 | ✓ (if enabled) | providers/ollama_local.py | — |
| NullProvider (fallback) | 6 | ✓ | providers/null_provider.py, always registered | — |
| text ContentPart | 6 | ✓ | ollama_cloud.py:60 — `if p.type == "text"` | — |
| image_ref ContentPart | 0 | ✗ | ollama_cloud.py:60 — silently DROPPED | Reports claimed |
| **PC / WINDOWS** | | | | |
| pc.window.list/focus/close | 6 | conditional | pc.py + windows/agent.py | — |
| pc.application.launch/close/focus/list | 6 | conditional | pc.py + windows/agent.py | — |
| pc.keyboard.type/press | 6 | conditional | pc.py + windows/agent.py | — |
| pc.mouse.click/move | 6 | conditional | pc.py + windows/agent.py | — |
| pc.screen.capture (w/ dimensions) | 6 | conditional | pc.py + screen.py | — |
| pc.ui.inspect/click/type | 6 | conditional | pc.py + uia.py | — |
| pc.process.list | 6 | conditional | pc.py + windows/agent.py | — |
| pc.filesystem.find_folder/open_path | 6 | conditional | pc.py | — |
| pc.capability.discover | 6 | conditional | pc.py + capability_discovery.py | — |
| pc.power.battery_level | 6 | conditional | pc.py | — |
| pc.shell.execute | 6 | conditional | pc.py + shell.py | — |
| pc.software.discover/list/search/info/probe/resolve | 6 | conditional | pc.py + software_discovery.py | — |
| ActiveWindowSensor → WorldState | 6 | ✓ | windows_sensors.py + store.py:37 | — |
| **BROWSER / DOM** | | | | |
| browser.navigate | 6 | conditional | browser.py + agent.py | — |
| browser.read_page (+ compaction) | 6 | conditional | browser.py + agent.py, _compact_read_page_output | — |
| browser.screenshot | 6 | conditional | browser.py + agent.py | — |
| browser.list_tabs | 6 | conditional | browser.py + agent.py | — |
| browser.click | 6 | conditional | browser.py + agent.py | — |
| browser.type | 6 | conditional | browser.py + agent.py | — |
| browser.dismiss_overlay | 6 | conditional | browser.py + agent.py | — |
| browser.click_at_position | 0 | ✗ | ABSENT de _DISPATCH + catalog | Reports (18D) |
| browser.screenshot width/height | 0 | ✗ | _screenshot() returns no dimensions | 18D report |
| browser.last_clicked_target (WS key) | 0 | ✗ | browser.click has observation=() | 20H |
| **VISION** | | | | |
| vision.observe_screen | 2 | ✗ | visual.py — not exported, not in bootstrap | Reports |
| vision.find_on_screen | 2 | ✗ | visual.py — not exported, not in bootstrap | Reports |
| vision.observe_browser | 2 | ✗ | visual.py — not exported, not in bootstrap | Reports |
| vision.find_in_browser | 2 | ✗ | visual.py — not exported, not in bootstrap | Reports |
| vision.observe_image | 2 | ✗ | visual.py — not exported, not in bootstrap | Reports |
| image transport to Ollama | 0 | ✗ | _messages_to_ollama() drops image_ref | Reports |
| VisualObservation / BoundingBox | 2 | ✗ | contracts/visual.py — defined, never produced | — |
| **VOICE** | | | | |
| VoiceRuntime (VAD→STT→Channel→Harness) | 5 | separate entrypoint | voice/runtime.py + factory.py | — |
| VoiceChannel (barge-in, TTS) | 5 | separate entrypoint | voice/channel.py | — |
| STT (Whisper) | 2 | ✗ via bootstrap | voice/stt/whisper_adapter.py | — |
| TTS (Kokoro) | 2 | ✗ via bootstrap | voice/tts/kokoro_adapter.py | — |
| VAD (Silero) | 2 | ✗ via bootstrap | voice/vad/silero_adapter.py | — |
| **TELEGRAM** | | | | |
| TelegramRuntime (inbound/outbound) | 5 | separate entrypoint | telegram/runtime.py | — |
| TelegramChannel → Harness | 5 | separate entrypoint | telegram/channel.py | — |
| Telegram STOP command | 5 | separate entrypoint | runtime.py::_handle_command | — |
| telegram.send_message (tool) | 2 | ✗ | notify.py exported, NOT in bootstrap | — |
| Telegram proactive from model | 2 | ✗ | tool not registered | — |
| **PHONE** | | | | |
| phone.call.number/contact | 6 | conditional | phone.py + ios/agent.py | — |
| phone.call.end/answer/reject/state | 6 | conditional | phone.py + ios/agent.py | — |
| phone.contacts.lookup | 6 | conditional | phone.py + ios/agent.py | — |
| phone.sms.send | 6 | conditional | phone.py + ios/agent.py | — |
| phone.connection.state | 6 | conditional | phone.py + ios/agent.py | — |
| PhoneCallActivitySensor → WorldState | 6 | ✓ | phone_sensors.py + store.py:37 | — |
| IncomingCallNotificationSensor → WS | 6 | ✓ | incoming_call_sensor.py + store.py:37 | — |
| IncomingCall → INTERRUPT (attention) | 0 | ✗ | evaluator.py: returns IGNORE | Reports |
| **PERCEPTION** | | | | |
| ActiveWindowSensor | 6 | ✓ | bootstrap.py:159 | — |
| PhoneCallActivitySensor | 6 | ✓ | bootstrap.py:159 | — |
| IncomingCallNotificationSensor | 6 | ✓ | bootstrap.py:159 | — |
| CameraLightSensor | 2 | ✗ | camera_sensor.py — never in bootstrap | Vision report |
| ScreenLightSensor | 2 | ✗ | screen_sensor.py — never in bootstrap | Vision report |
| **SPATIAL / UI** | | | | |
| scene.create/add/update/remove/describe | 6 | ✓ | spatial.py + store.py | — |
| scene.render (EventBus → UI) | 6 | ✓ | spatial.py + ui_views event | — |
| scene.close/export | 6 | ✓ | spatial.py + store.py | — |
| ui.show_view / ui.hide_view | 6 | ✓ | ui_views.py, bootstrap.py:200 | — |
| Cockpit WebSocket (UI layer) | 5 | ✓ (Chantier 17) | separate UI process | — |
| Three.js renderer adapter | 2 | ✗ | spatial/renderer/threejs_adapter.py | — |
| **INTERACTION (Chantier 20)** | | | | |
| interaction.track | 2 | ✗ | interaction.py — NOT exported, NOT in bootstrap | Chantier 20 "DONE" |
| interaction.reply | 2 | ✗ | interaction.py — NOT exported, NOT in bootstrap | Chantier 20 "DONE" |
| **OBSIDIAN** | | | | |
| obsidian.list_notes / obsidian.read_note | 2 | ✗ | obsidian.py — NOT exported, NOT in bootstrap | — |
| **UTILITIES** | | | | |
| filesystem.write_file / read_file | 6 | ✓ | demo.py, bootstrap.py:193 | — |
| demo.always_fail / idempotent_counter / non_idempotent_append | 6 | ✓ | demo.py | — |
| system.time.now | 6 | ✓ | system_time.py, bootstrap.py:196 | — |
| ui.show_view / ui.hide_view | 6 | ✓ | ui_views.py | — |
| preferences.set_channel | 6 | ✓ | preferences.py | — |
| **HARNESS INTERNALS** | | | | |
| harness.steer() mid-turn | 1 | ✗ | steering.py: NOT_IMPLEMENTED stub | Reports |
| tasks.steer (checkpoint guidance) | 6 | ✓ | tasks.py + registry.py | ≠ harness.steer |
| Mid-turn interruption | 1 | ✗ | depends on harness.steer | — |
| Long-horizon task creation | 6 | ✓ | create_long_horizon_task, bootstrap.py:241 | — |
| Task scheduling (not_before) | 6 | ✓ | TaskRegistry.create + TaskScheduler | — |

---

## 5. Core Runtime

### 5.1 Harness

**File :** `raya/harness/loop.py`

**`Harness.__init__`** (production parameters confirmed from bootstrap.py:213):
```
world_state=world_state, memory=memory, tasks=tasks, safety=safety,
model_registry=models, bus=bus, tools_registry=tools,
execution_records=execution_records, scene_store=scene_store,
devices=devices, context_budget_tokens=4096, max_concurrent_tasks=2,
max_tool_iterations=12
```

**`handle_request(request: HarnessRequest) → HarnessState`** (line 183):
- Attention evaluation → PROCESS_NOW or queued
- `assemble(session_id, channel_scope, world_state, memory, budget_tokens, world_state_domains=())` — NOTE: `world_state_domains=()` → ALL WorldState facts included (no filtering)
- `_run_agentic_loop()` → up to `max_tool_iterations=12` tool calls
- `_finalize_turn()` at budget exhaustion (20G-B) — NOT `_explain_blocked_turn`
- `_explain_blocked_turn()` reserved for LoopDetector ESCALATE only

**`_discover_tool_schemas()`** (line 323):
- `registry.all_capability_tags()` → all registered tags
- `discover(registry, tags)` → all registered tools
- Returns ALL tools (no per-session filtering)

**`_promote_observations_and_verify()`** — iterates `Tool.observation` tuple. If `observation=()` → nothing promoted to WorldState.

**`_summarize_tool_result()`** → calls `_compact_read_page_output` for `browser.read_page` (buttons→20, links→25, url/title/inputs preserved).

**`state_signal`** (line 737): `(tool_result.evidence or {}).get("url")` — only `url` key in evidence nourishes cycle detection history.

**Channel → ChannelScope mapping** (in Harness):
```python
_CHANNEL_TO_SCOPE = {
  "voice": VOICE, "web": CHAT, "cli": CHAT,
  "desktop": CHAT, "mobile": IOS, "api": CHAT
}
```
No `"telegram"` → Telegram presumably injected with explicit ChannelScope from TelegramChannel.

**harness.steer()** (`raya/harness/steering.py`, 21 lines):
```python
return ErrorInfo(code="NOT_IMPLEMENTED",
  message="Steering mid-turn nécessite une boucle asynchrone (Phase 2) — Phase 0 stub.")
```
**CONFIRMED NOT_IMPLEMENTED.** Mid-turn injection impossible.

**`tasks.steer`** (distinct) : écrit `steering_guidance` dans `task.checkpoint` dict. Ce n'est PAS du mid-turn steering. Le Harness lit ce checkpoint au prochain tour, pas pendant l'itération en cours.

### 5.2 Context Engine

**File :** `raya/context_engine/assembler.py`

`assemble()` construit 7 types de sections dans l'ordre :
1. `_system_rules_section(runtime_identity)` — directives système, identité RAYA
2. `_identity_baseline_sections(memory, channel_scope)` — faits personnels (layer=PERSONAL, provenance=profile_migration:identity)
3. `_task_state_section(task)` — tâche courante si applicable
4. `_active_tasks_sections(active_tasks, exclude)` — autres tâches RUNNING/PAUSED
5. `_world_state_sections(world_state, domains)` — faits WorldState (tous si domains=())
6. `_memory_sections(memory, channel_scope, query_text)` — recherche par pertinence (score = confidence × freshness × lifecycle weight × keyword match)
7. `_conversation_history_section(memory, channel_scope, limit=5)` — 5 derniers échanges (fetch 50, filtre CONVERSATION layer, `[:5][::-1]`)

**`_tool_schema_section`** : lazy-import de `from raya.tools import discover` — respecte invariant (pas d'import catalog dans context_engine).

**`render_system_prompt(context: Context) → str`** (`render.py`) : sérialise toutes les sections sauf `TOOL_SCHEMAS` (qui vont dans le payload API, non dans le system prompt).

### 5.3 Memory

**File :** `raya/memory/store.py`

`MemoryStore` — collection `"memory_entries"`, SQLite backend.

**Types :** `FACT | PREFERENCE | RULE | EXPERIENCE`  
**Layers :** `WORKING | CONVERSATION | PERSONAL | PROJECT | TASK | EXPERIENCE`  
**Lifecycle :** `CANDIDATE → ACTIVE → CONFIRMED → AGING → OBSOLETE`  
**Isolation :** filtre `channel_scope in (SHARED, channel_scope)` — les faits VOICE ne filtrent pas sur les requêtes CHAT, sauf si SHARED.

**`search(query, channel_scope, ...)` :** score = `_LIFECYCLE_WEIGHT[lifecycle] × _CONFIDENCE_WEIGHT[confidence] × keyword_overlap`. Retourne jamais OBSOLETE.

**`experience.py` :** présent dans le dossier — module d'expérience/distillation, non audité en détail dans ce scope.

### 5.4 Tasks

**File :** `raya/tasks/registry.py`

**États :** `PENDING | RUNNING | PAUSED | COMPLETED | FAILED | CANCELLED | BLOCKED`  
(NOTE : `BLOCKED` est une extension Chantier 15 non présente dans le blueprint original.)

**`not_before`** : timestamp ISO optionnel — `TaskScheduler` gère le déclenchement différé.

**`recover_after_restart()`** : transitions RUNNING → PAUSED, émet `task.recovered` pour chaque tâche récupérée. Appelé via `harness.recover()` au bootstrap.

**`report_progress()`** : cache mémoire uniquement (`_progress_cache`), pas d'écriture SQLite. La progression est éphémère.

### 5.5 Safety

**File :** `raya/safety/risk.py` + `safety/permissions.py`

**`classify_risk(capability_tags, arguments=None) → PermissionLevel`** :
- Regarde les tags dans `_RISK_BY_TAG` (35 entrées)
- Si les arguments mentionnent des actions dangereuses (`_DANGEROUS_ACTION_STEMS` : 33 stems incluant "buy", "delete", "send", "confirm", "transfer"...) → upgrade vers SENSITIVE
- `_DEFAULT_UNKNOWN_RISK = SENSITIVE`

**Niveaux :** `SAFE | SENSITIVE | DESTRUCTIVE`

**`SafetyService`** : délègue à `StopController.should_stop()` + `AuditTrail`. Appelé par `tools/execution.py::execute()` avant chaque outil.

**`StopController`** : subscribe à `"interface.stop_requested"` — STOP passe par EventBus, jamais par import direct (invariant #21 respecté).

### 5.6 Attention

**File :** `raya/attention/evaluator.py`

**`SUBSCRIBED_PATTERNS`** : `("interface.request_received", "task.*", "safety.*", "perception.*")`

**Décisions confirmées :**
- `interface.request_received` → **PROCESS_NOW** (toujours)
- `perception.phone_call_activity` → **INTERRUPT** (dédupliqué)
- `perception.incoming_call_notification` (call_state="incoming") → **IGNORE** — commentaire explicite: "contextual RuleEngine NOT_IMPLEMENTED, preference mechanism not wired"
- `task.failed` CRITICAL → **INTERRUPT**
- `safety.*` → **BACKGROUND**
- Autres `perception.*` → **IGNORE**

**CONTRADICTION RAPPORT vs CODE :** Le rapport 20G et Chantier 13G affirmaient que les appels entrants déclenchaient INTERRUPT. Le code (evaluator.py:169-186) retourne IGNORE avec un long commentaire NOT_IMPLEMENTED. Voir §18.

### 5.7 WorldState

**File :** `raya/world_state/store.py`

**Chemin production COMPLET confirmé :**
```
LightSensor.sample() → Event(payload=to_dict(PerceptionObservation))
→ PerceptionRuntime.poll_once() → bus.publish(event)
→ WorldStateStore._on_perception_event() [subscribed to "perception.*"]
→ apply_update(WorldStateFact(domain, key, value, source, confidence, freshness_ttl_s))
→ SqliteBackend.save("world_state_facts", "domain:key", payload)
```

**Freshness** : lazy staleness appliqué lors de `retrieve_fact(apply_lazy_staleness=True)`. Invalidation explicite via `invalidate_fact()` ou `expire_fact()`.

**Producers confirmés :**
- `ActiveWindowSensor` → `pc:active_window` (TTL 20s)
- `PhoneCallActivitySensor` → `phone:call_activity`
- `IncomingCallNotificationSensor` → `phone:incoming_call_notification`
- Tools PC via `ObservationSpec` : `pc:active_window`, `pc:last_interaction_target` (TTL 300s), `pc:battery_level`, `filesystem:{name}`, `software:launcher_detected`
- Tools Browser via `ObservationSpec` : `browser:current_url` (navigate seul)
- `interaction.track`/`interaction.reply` via `world_state.apply_update()` direct — **NON CÂBLÉ en production**

**Consumers :** Context Engine (`_world_state_sections`), Harness (finalize_turn lit `browser.last_clicked_target` — jamais écrit), tests.

---

## 6. Model Layer

**Files :** `raya/models/router.py`, `providers/ollama_cloud.py`, `providers/ollama_local.py`, `providers/null_provider.py`

### 6.1 ModelRequest → ModelResponse

**`route(registry, req: ModelRequest, prefer_local=False) → ModelResponse`** (`router.py`) :
1. `_ordered_candidates(registry, req.required_capability)` — filtre par capability + `is_available()`, cloud-first (sauf si `prefer_local=True`)
2. Essaie chaque provider dans l'ordre, retourne `ModelResponse` du premier succès
3. Si tous échouent → retourne la dernière erreur (NullProvider toujours disponible en dernier)

### 6.2 Default model pool

```
_DEFAULT_MODEL_POOL = "deepseek-v4-flash:cloud:reasoning|planning|fast_response,
                        kimi-k2.7-code:cloud:coding,
                        gemma4:cloud:vision|classification|summarization"
```

**gemma4 a le tag `vision`** mais : (a) vision tools non enregistrés → jamais sélectionné pour vision, (b) `_messages_to_ollama()` ne peut pas transporter d'images de toute façon.

### 6.3 Message serialization — GAP CRITIQUE

**`_messages_to_ollama()` (`ollama_cloud.py:57-69`) :**
```python
text = "".join(p.value for p in m.content if p.type == "text")
```
Tout `ContentPart(type="image_ref")` est **silencieusement ignoré**. Aucun champ `images: []` jamais construit dans le payload Ollama. `OllamaLocalAdapter` hérite entièrement de `OllamaCloudAdapter` — même gap.

**Impact :** même si `register_visual_tools` était câblé, les observations visuelles ne parviendraient jamais au modèle.

### 6.4 Model capabilities

| Capability | Adapter | Available |
|---|---|---|
| reasoning | OllamaCloud / OllamaLocal | ✓ (si configuré) |
| planning | OllamaCloud / OllamaLocal | ✓ |
| fast_response | OllamaCloud | ✓ |
| coding | OllamaCloud | ✓ |
| vision | OllamaCloud (gemma4) | ✗ — image transport absent |
| classification | OllamaCloud | ✓ (texte uniquement) |
| summarization | OllamaCloud | ✓ (texte uniquement) |
| fallback | NullProvider | ✓ (toujours) |

### 6.5 OllamaLocalAdapter

Hérite entièrement de `OllamaCloudAdapter`. La seule différence est l'URL de host et l'absence d'API key. Mêmes limites (image_ref dropped, num_ctx implicite Ollama).

---

## 7. PC / Windows

**Files :** `raya/devices/windows/agent.py`, `raya/tools/catalog/pc.py`

**`_DISPATCH`** : 27 entries confirmées (voir §4 matrix).

**`register_pc_tools()`** : 26 tools (pc.software.resolve_launch_options rajouté, pc.power.battery_level — total 26 vs 27 dispatch, différence : `capability.discover` dans agent, `pc.capability.discover` dans catalog — correspondance via `capability_name` mapping).

**WorldState observations (from ObservationSpec dans pc.py) :**

| Tool | WS domain | WS key | TTL | When |
|---|---|---|---|---|
| pc.application.launch | pc | active_window | — | on success |
| pc.application.focus | pc | active_window | — | on success |
| pc.keyboard.type | pc | last_interaction_target | 300s | on success |
| pc.ui.type | pc | last_interaction_target | 300s | on success |
| pc.power.battery_level | pc | battery_level | — | on success |
| pc.filesystem.find_folder | filesystem | {name_from_argument} | — | on success |
| pc.software.discover | software | launcher_detected | — | on success |

**pc.screen.capture** : retourne `{path, width, height}` — **dimensions incluses** (contrairement à `browser.screenshot`).

**Safety** : `should_stop` injecté dans chaque handler via closure. `pc.shell.execute` tag = `"pc.shell"` → SENSITIVE. `pc.software.uninstall` serait DESTRUCTIVE mais outil non présent.

**STOP** : `should_stop()` appelé avant chaque `Command` envoyée à l'agent (invariant #6 respecté).

---

## 8. Browser / DOM

**Files :** `raya/devices/browser/agent.py`, `raya/tools/catalog/browser.py`

### 8.1 Tools production (7/7 confirmés)

| Tool | Observation | Evidence | Notes |
|---|---|---|---|
| browser.navigate | `browser:current_url` | `{url}` | `_CURRENT_URL_OBSERVATION` |
| browser.read_page | — | `{url, title, buttons, links, inputs, cookie_banner}` | compacté: btns→20, links→25 |
| browser.screenshot | — | `{status, path}` | NO width/height |
| browser.list_tabs | — | `{tabs}` | — |
| browser.click | **observation=()** | **evidence={}** | aucune WS promotion |
| browser.type | — | — | — |
| browser.dismiss_overlay | — | `{dismissed:[]}` | — |

### 8.2 Gaps DOM confirmés

**`browser.click` (`browser.py:64`)** :
```python
observation=()  # empty tuple — RIEN n'est promu dans WorldState
```
Handler `_click()` retourne `evidence={}` (dict vide). La clé `browser:last_clicked_target` n'est **jamais écrite** — `_finalize_turn()` qui la lit trouve toujours None.

**`browser.click_at_position`** : ABSENT de `_DISPATCH` (7 entrées seulement) et ABSENT du catalog `_defs`. Jamais implémenté malgré les rapports 18D.

**`browser.screenshot`** : `_screenshot()` dans `agent.py` retourne `{status: "ok", path: path}` — sans width/height. Contrairement à `pc.screen.capture`.

**`_STRUCT_JS`** (JavaScript injecté par `browser.read_page`) : priorise les boutons buybox/addtocart au début de `buttons[]`. Cap à 20 buttons dans `_compact_read_page_output`.

### 8.3 DOM extraction (production)

La perception DOM est entièrement réalisée par `browser.read_page` via `_STRUCT_JS` injecté dans la page. Ce n'est pas un capteur continu — c'est un appel outil explicite.

**Structure retournée :**
- `url` : URL courante
- `title` : titre de la page
- `buttons[]` : tous boutons interactifs (cap 20 après compaction)
- `links[]` : tous liens (cap 25 après compaction)
- `inputs[]` : tous champs de formulaire (non cappé)
- `cookie_banner` : boolean
- `iframes` : non listés séparément (contenu non extrait)

**Freshness :** aucune — chaque appel `browser.read_page` relit la page entière.

### 8.4 Vision browser (absent)

`vision.observe_browser` existe dans `visual.py` mais n'est pas câblé. `vision.find_in_browser` aussi. `browser.screenshot` génère bien un fichier PNG mais celui-ci n'est jamais envoyé au modèle (image_ref absent de `_messages_to_ollama`).

---

## 9. Vision

**File :** `raya/tools/catalog/visual.py`

### 9.1 État de chaque outil

| Tool | Code | Exported | In bootstrap | Model accessible | Image transport |
|---|---|---|---|---|---|
| vision.observe_screen | ✓ | ✗ | ✗ | ✗ | ✗ |
| vision.find_on_screen | ✓ | ✗ | ✗ | ✗ | ✗ |
| vision.observe_browser | ✓ | ✗ | ✗ | ✗ | ✗ |
| vision.find_in_browser | ✓ | ✗ | ✗ | ✗ | ✗ |
| vision.observe_image | ✓ | ✗ | ✗ | ✗ | ✗ |

**Niveau : L2 pour tous les 5 outils.**

### 9.2 Chaîne de rupture

```
VISION
 ↓ [BREAK 1] register_visual_tools() not called in bootstrap
ToolRegistry
 ↓ [BREAK 2] image_ref ContentPart dropped in _messages_to_ollama()
ModelRequest / Ollama API
 ↓ [BREAK 3] gemma4 never selected (no vision tools registered)
Vision Model
```

3 ruptures indépendantes. Fixer l'une ne suffit pas — les 3 doivent être résolues ensemble.

### 9.3 Dépendances de register_visual_tools

```python
register_visual_tools(
    registry,
    observe_fn: ObserveFn,          # callable nécessaire — n'existe pas encore en production
    capture_screen_fn: CaptureFn,   # callable nécessaire — mapping vers WindowsAgent.screen.capture?
    capture_browser_fn: CaptureFn,  # callable nécessaire — mapping vers BrowserAgent.screenshot?
    *, prefer_local=False
)
```

Aucun de ces 3 callables n'est créé par bootstrap.

### 9.4 ObservationSpecs (définies mais jamais produites)

```python
_SCREEN_OBS_SPEC  = ObservationSpec(domain="visual", key="screen_state", ...)
_BROWSER_OBS_SPEC = ObservationSpec(domain="visual", key="browser_visual_state", ...)
_IMAGE_OBS_SPEC   = ObservationSpec(domain="visual", key="last_image_observation", ...)
```

Ces clés WorldState ne sont **jamais écrites** car les outils ne sont pas enregistrés.

### 9.5 Grounding (Chantier 20E)

**`_FOUND_PATTERN`** dans le parser de résultats vision (loop.py ou visual.py) : fix Chantier 20E pour espaces dans les bounding boxes et guillemets imbriqués dans les labels. Le parser existe dans le code. Il est inutilisé en production puisque les outils vision ne sont pas enregistrés.

---

## 10. Voice

**Files :** `raya/interfaces/voice/runtime.py`, `voice/channel.py`, `voice/factory.py`

### 10.1 Architecture complète

```
AudioInput (SounddeviceAudioInput)
 → VoiceRuntime._loop() [thread "raya-voice-runtime"]
   → process_one_chunk()
     → VAD.process() (SileroVADAdapter)
     → on SPEECH_END: _finalize_segment()
       → STT.transcribe_final() (WhisperSTTAdapter)
       → VoiceChannel.handle_final_transcript(text)
         → harness.handle_request(HarnessRequest)
         → [harness response] → VoiceChannel.speak()
           → TTS (KokoroTTSAdapter)
```

### 10.2 Wiring status

| Composant | Code | Factory | Bootstrap |
|---|---|---|---|
| AudioInput (SounddeviceAudioInput) | ✓ | ✓ | ✗ |
| VAD (SileroVADAdapter) | ✓ | ✓ | ✗ |
| STT (WhisperSTTAdapter) | ✓ | ✓ | ✗ |
| TTS (KokoroTTSAdapter) | ✓ | ✓ | ✗ |
| VoiceChannel | ✓ | ✓ | ✗ |
| VoiceRuntime | ✓ | ✓ | ✗ |

`build_real_voice_runtime(handles)` (`voice/factory.py`) : toutes les dépendances réelles sont instanciées ici. Nécessite `handles.harness` et `handles.bus`.

**STOP wiring** : `VoiceChannel.request_stop()` publie `Event("interface.stop_requested")` — n'importe jamais `safety` directement. Invariant #21 respecté.

**Barge-in** : `VoiceChannel.barge_in()` : annule TTS si l'utilisateur parle pendant que RAYA parle. Implémenté. Non actif car Voice non démarrée via bootstrap.

**Niveau :** L5 — entrypoint séparé existe, code complet, non activé par défaut.

---

## 11. Telegram

**Files :** `raya/interfaces/telegram/runtime.py`, `telegram/channel.py`

### 11.1 TelegramRuntime — inbound

`_poll_loop()` : long-polling Telegram API (25s timeout), backoff 1s→30s.
- Messages texte → `_handle_message()` → `TelegramAuthorizer.is_allowed()` → command or `channel.handle_message()` → `harness.handle_request()`
- Commands : `/start`, `/help`, `/status`, `/stop`
- Callback queries : `confirm:yes` / `confirm:no` → confirmation flow

### 11.2 TelegramRuntime — outbound

`_send(chat_id, text, reply_markup=None)` : chunking via `chunk_message()` + Telegram Bot API send.
- Utilisé pour réponses aux messages entrants
- Utilisé pour `/stop` acknowledgement
- Utilisé pour confirmations inline keyboard

### 11.3 Proactive from model (GHOST)

`notify.py` définit `telegram.send_message` → `register_notify_tools(registry, NotifyOps(send_telegram=...))`. Ce tool **est exporté** de `catalog/__init__` mais **n'est pas enregistré** dans bootstrap. RAYA ne peut pas proactivement envoyer un message Telegram via outil.

### 11.4 Wiring status

| Composant | Code | Bootstrap |
|---|---|---|
| TelegramClient (HTTP polling) | ✓ | ✗ |
| TelegramAuthorizer | ✓ | ✗ |
| TelegramChannel → Harness | ✓ | ✗ |
| TelegramRuntime | ✓ | ✗ |
| telegram.send_message tool | ✓ (not registered) | ✗ |

**Niveau :** L5 — entrypoint séparé, code complet, non activé par défaut.

---

## 12. Phone / iOS

**Files :** `raya/devices/ios/agent.py`, `raya/tools/catalog/phone.py`

### 12.1 Capabilities confirmées (9/9)

| Tool (catalog) | Capability (agent) | Mechanism | Permission |
|---|---|---|---|
| phone.contacts.lookup | contacts.lookup | phone_link_uia_contact_search | SAFE |
| phone.call.number | call.dial_number | phone_link_uia_dial | SENSITIVE |
| phone.call.contact | call.dial_contact | phone_link_uia_dial | SENSITIVE |
| phone.call.end | call.end | phone_link_uia_end_call | SAFE |
| phone.call.answer | call.answer | phone_link_uia_answer_call | SENSITIVE |
| phone.call.reject | call.reject | phone_link_uia_reject_call | SAFE |
| phone.call.state | call.state | phone_link_uia_read | SAFE |
| phone.sms.send | sms.send | phone_link_uia_compose | SENSITIVE |
| phone.connection.state | connection.state | phone_link_uia_read | SAFE |

Toutes délèguent à `mechanisms/phone_link.py` via UI Automation sur `PhoneExperienceHost.exe`.

**Mécanisme :** Phone Link (Microsoft) — Bluetooth pour appels + permissions contacts/SMS iPhone. Android non supporté (architecture différente, nécessiterait DeviceAgent séparé).

**Bootstrap :** `enable_phone_device=True` → best-effort try/except → `PhoneLinkDeviceAgent()` → `register_phone_tools()`. Niveau L6 si Phone Link disponible et connecté.

**`_from_mechanism_result()`** : distingue `status="blocked"` (permission iPhone non accordée, `PERMISSION_NOT_GRANTED_ON_PHONE`) des erreurs techniques transitoires.

---

## 13. Perception

**Files :** `raya/perception/`, `raya/runtime/bootstrap.py:141-166`

### 13.1 Sensor Status Matrix

| Sensor | Instantiated | Active | EventBus | WorldState | Attention |
|---|---|---|---|---|---|
| ActiveWindowSensor | ✓ bootstrap:159 | ✓ (poll 3s) | ✓ `perception.window_changed` | ✓ `pc:active_window` | IGNORE (default) |
| PhoneCallActivitySensor | ✓ bootstrap:159 | ✓ (poll 3s, Win32 hook priority) | ✓ `perception.phone_call_activity` | ✓ `phone:call_activity` | INTERRUPT |
| IncomingCallNotificationSensor | ✓ bootstrap:159 | ✓ (Win32 hook driven) | ✓ `perception.incoming_call_notification` | ✓ `phone:incoming_call_notification` | **IGNORE** |
| CameraLightSensor | ✗ never instantiated | ✗ | ✗ | ✗ | — |
| ScreenLightSensor | ✗ never instantiated | ✗ | ✗ | ✗ | — |

### 13.2 Perception → WorldState wiring (CONFIRMED)

1. `LightSensor.sample()` → `Event(type="perception.X", payload=to_dict(PerceptionObservation))`
2. `PerceptionRuntime.poll_once()` → `bus.publish(event)`
3. `WorldStateStore.__init__` : `bus.subscribe("perception.*", _on_perception_event)` (ligne 37)
4. `_on_perception_event()` : extrait domain/key/value/source → `apply_update(WorldStateFact)` → SQLite

**Chemin END-TO-END CONNECTÉ** pour les 3 capteurs actifs.

### 13.3 Perception vs Blueprint

Le blueprint prévoyait : `perception.process_started/stopped`, `perception.usb_connected/disconnected`, `perception.battery_changed`, `perception.network_changed`, `perception.presence_changed`, `perception.capture_ready`.

**Implémentés :** `perception.window_changed`, `perception.phone_call_activity`, `perception.incoming_call_notification`, `perception.visual.camera_observation` (CameraLightSensor — non instancié), `perception.visual.screen_changed` (ScreenLightSensor — non instancié).

**Non implémentés :** USB, battery (via sensor — pc.power.battery_level le fait via tool), réseau, présence, process started/stopped.

---

## 14. Spatial / UI

**Files :** `raya/spatial/store.py`, `raya/tools/catalog/spatial.py`, `raya/interfaces/ui/`

### 14.1 Scene tools (8, tous L6)

| Tool | Action | Storage | Notes |
|---|---|---|---|
| scene.create | crée une scène | SceneStore (in-memory) | retourne scene_id |
| scene.add_object | ajoute objet 3D | SceneStore | géométrie, matériau, parent |
| scene.update_object | met à jour objet | SceneStore | position/rotation/scale |
| scene.remove_object | supprime objet | SceneStore | suppression récursive descendants |
| scene.describe | décrit la scène | SceneStore.describe_scene() | dict complet |
| scene.render | rendu visuel | EventBus `ui.view_requested` | nécessite Cockpit actif |
| scene.close | ferme la scène | SceneStore.unmount() | — |
| scene.export | exporte | SceneStore | format non audité ici |

**SceneStore** : in-memory uniquement (pas de PersistenceBackend). Pas de model calls. Feuille de l'arbre.

**Three.js renderer** (`spatial/renderer/threejs_adapter.py`) : code présent, non connecté au runtime production. L2.

### 14.2 UI tools

| Tool | Action | Notes |
|---|---|---|
| ui.show_view | publie `ui.view_requested` (action="show") | 7 vues valides |
| ui.hide_view | publie `ui.view_requested` (action="hide") | "all" accepté |

Vues valides : `conversation, tasks, world, browser, computer, attention, spatial`.

### 14.3 Cockpit (Chantier 17)

Cockpit = interface pywebview/canvas 2D JARVIS + WebSocket. Process séparé. Écoute les events EventBus via WebSocket. Non audité en détail dans ce scope (pas de fichier Cockpit lu en 20J).

---

## 15. Devices

**File :** `raya/devices/registry.py`

### 15.1 DeviceRegistry methods

- `register(device_id, agent, device_type=None)` → agents exécutables
- `register_info(device_id, device_type, ...)` → dispositifs informationnels (ex: téléphone non connecté)
- `touch(device_id, metadata=None)` → mise à jour last_seen + metadata
- `mark_offline(device_id)` → status OFFLINE
- `get(device_id) → DeviceAgent | None` → exécutable seulement
- `describe(device_id) → Device | None` → agents + informationnels
- `list_devices() → list[Device]` → tous
- `health_all() → dict[str, Health]`

### 15.2 Device Matrix

| Device | DeviceType | Declared | Registered | Instantiated | Conditional | Production reachable |
|---|---|---|---|---|---|---|
| WindowsDeviceAgent | WINDOWS | ✓ | ✓ (bootstrap) | ✓ (try/except) | enable_windows_device | ✓ (Windows+pywin32) |
| BrowserDeviceAgent | BROWSER | ✓ | ✓ (bootstrap) | ✓ (try/except) | enable_browser_device | ✓ (Playwright) |
| PhoneLinkDeviceAgent | IOS | ✓ | ✓ (bootstrap) | ✓ (try/except) | enable_phone_device | ✓ (PhoneLink+Win) |
| VoiceRuntime | — | ✓ | ✗ | ✗ (entrypoint séparé) | enable_voice | ✗ via bootstrap |
| TelegramRuntime | — | ✓ | ✗ | ✗ (entrypoint séparé) | enable_telegram | ✗ via bootstrap |
| CameraLightSensor | — | ✓ | ✗ | ✗ | non implémenté dans bootstrap | ✗ |
| Linux | — | ✗ | ✗ | ✗ | — | ✗ |
| Android | — | ✗ | ✗ | ✗ | — | ✗ |

**Note sur les Device IDs :**
- `DEVICE_ID = "windows_agent"` (windows/__init__.py)
- `DEVICE_ID = "browser_agent"` (browser/__init__.py)
- `DEVICE_ID = "phone_agent"` (ios/agent.py)

---

## 16. Persistence

**Files :** `raya/persistence/sqlite_backend.py`, `backend.py`

### 16.1 SQLite Backend

`SqliteBackend` : table unique `kv_store(collection, item_id, payload_json, updated_at)` avec UPSERT (INSERT OR REPLACE). Mode WAL. Foreign keys activés. `migrate(conn)` appelé au `__init__`.

**Collections utilisées :**
- `"world_state_facts"` — WorldStateStore
- `"memory_entries"` — MemoryStore
- `"tasks"` — TaskRegistry
- `"execution_records"` — ExecutionRecordRepository (harness)
- `"events"` — EventStore (observability)

### 16.2 Distinctions critiques

| Store | Rôle | Durée | Notes |
|---|---|---|---|
| MemoryStore | Faits/préférences/expériences durables sur l'utilisateur | Long terme | lifecycle: candidate→obsolete |
| WorldStateStore | État COURANT de l'environnement | Court terme (TTL) | freshness, stale/superseded |
| TaskRegistry | Cycle de vie des tâches | Durée de la tâche | recovery on restart |
| ExecutionRecordRepository | Trace des tours agentic | Long terme (audit) | non purgé auto |
| EventStore | Log d'events récents | Court terme (in-memory + SQLite) | observability |

### 16.3 InMemoryBackend

Présent dans `backend.py` — pour tests uniquement. Thread-safe. Injecté via `bootstrap(backend=InMemoryBackend())`.

### 16.4 Absence notable

Pas de backend Obsidian, OneDrive, ou fichier partagé câblé dans bootstrap. `obsidian.py` tool catalog référence un `vault_dir` optionnel — non câblé. `RAYA_V2_MEMORY_SHARED_DATA_IMPLEMENTATION_REPORT.md` existe dans les rapports mais le wiring obsidian n'est pas dans bootstrap.

---

## 17. Test Evidence Audit

**NOTE : AUCUN TEST EXÉCUTÉ. Lecture statique uniquement.**

### 17.1 Classification des tests

| Test file | Type | Ce que le test prouve | Preuve production ? |
|---|---|---|---|
| `tests/harness/test_loop.py` | integration | Loop logic (mocked providers, real registry) | PARTIEL — providers mockés |
| `tests/harness/test_identity_context_wiring.py` | integration | Context assembly avec harness réel | PARTIEL — backend in-memory |
| `tests/architecture/test_chantier17_portability.py` | architecture | Import structure, no cross-layer imports | OUI — lint architectural |
| `tests/architecture/test_targeted_execution_repair_architecture_proof.py` | architecture | Structure des classes, pas d'invocation | NON — static proof |
| `tests/context_engine/test_identity_context.py` | unit | Assembler sections (world_state, memory mockés) | NON — mocks |
| `tests/devices/browser/test_browser_agent.py` | unit | BrowserAgent handlers | PARTIEL — agent réel, Playwright mockable |
| `tests/integration/test_phase4_scenarios.py` | integration | Multi-step tool scenarios | PARTIEL — E2E framework |
| `tests/integration/test_phase7_scenarios.py` | integration | Memory/WorldState integration | PARTIEL |
| `tests/integration/test_phase4_scenarios.py` (Vision) | integration | Vision tools | ✗ — visual.py non câblé |
| `tests/perception/test_windows_sensors.py` | unit | Sensor logic (read_fn injected) | PARTIEL — sensor seul |
| `tests/integration/test_post_repair_validation.py` | integration | Validation post-repair | PARTIEL |
| `scripts/validate_20gb_real_e2e.py` | real E2E | _finalize_turn, compaction — REAL BROWSER | OUI pour 20G-B |

### 17.2 Evidence Level definitions

- **E1 — unit** : teste une fonction isolée avec mocks
- **E2 — integration** : teste plusieurs composants vrais avec backend in-memory
- **E3 — production-path** : teste le chemin bootstrap → harness → tool → result avec vrais devices
- **E4 — injected capability** : teste une capability non câblée via injection directe dans les tests (ne prouve pas la production)
- **E5 — real E2E** : exécution avec vrais providers, vrai browser, vrai modèle

### 17.3 Tests suspects / DEAD

**Tests qui échoueraient contre le code production actuel :**

| Test | Raison probable |
|---|---|
| Tests vision (si présents) | `register_visual_tools` non exportée, non câblée |
| Tests `interaction.track`/`interaction.reply` (si présents) | Non câblés en production |
| Tests `harness.steer()` si testés comme fonctionnel | Retourne NOT_IMPLEMENTED |
| Tests assumant `browser.screenshot` avec dimensions | `_screenshot()` ne retourne pas width/height |
| Tests sur `incoming_call → INTERRUPT` | Retourne IGNORE en production |

---

## 18. Documentation Drift

| Rapport précédent | Claim | Code actuel | Level actuel | Drift |
|---|---|---|---|---|
| RAYA_V2_VISION_FOUNDATION_IMPLEMENTATION_REPORT.md | "Vision tools implémentés, PASS" | `visual.py` non exporté, non dans bootstrap | L2 | **CRITIQUE** |
| RAYA_V2_REAL_MULTIMODAL_AUDIT.md | "4/5 E2E PASS" | `image_ref` dropped par `_messages_to_ollama`, vision non câblée | L0 (transport) | **CRITIQUE** |
| RAYA_V2_18D_GROUNDING_FIX_IMPLEMENTATION_REPORT.md | "click_at_position implémenté" | Absent de `_DISPATCH` + `_defs` catalog | L0 | **CRITIQUE** |
| RAYA_V2_18D_GROUNDING_FIX_IMPLEMENTATION_REPORT.md | "viewport/dimensions transmis" | `_screenshot()` retourne `{status, path}` seulement | L0 | **CRITIQUE** |
| RAYA_V2_CHANTIER_13G_IMPLEMENTATION_REPORT.md | "Appel entrant → INTERRUPT" | `evaluator.py:169-186` retourne IGNORE | L6 (mais IGNORE) | **CRITIQUE** |
| RAYA_V2_20G_B_OBJECTIVE_FINALIZATION_CONTEXT_COMPACTION_IMPLEMENTATION_REPORT.md | "Fix A implémenté" | `_explain_blocked_turn` évolue vers `_finalize_turn` — CONFIRMÉ | L6 | Pas de drift |
| RAYA_V2_CHANTIER20_EXTERNAL_INTERACTION_CONTINUITY_REPORT.md | "interaction.track/reply DONE, 25/25 tests" | `catalog/__init__` n'exporte pas `register_interaction_tools`, absent bootstrap | L2 | **MAJEUR** |
| RAYA_V2_PHASE11_IMPLEMENTATION_REPORT.md (Chantier 18B) | "Browser agentic PASS" | browser.click_at_position ABSENT | L0 | **CRITIQUE** |
| RAYA_V2_PRE_PHASE7_MEMORY_IDENTITY_REPORT.md | "Memory identity PASS" | Memory fonctionne — pas de drift | L6 | Pas de drift |
| Chantier 20B WORLD STATE SIGNAL CONFLICT | "URL conflict réglé" | state_signal = evidence.get("url") → navigations répétées cycles encore | L6 (partiel) | MINEUR |
| Divers rapports | "harness.steer() wiring" | NOT_IMPLEMENTED stub | L1 | MAJEUR |
| RAYA_V2_MEMORY_SHARED_DATA_IMPLEMENTATION_REPORT.md | "Obsidian/shared memory wired" | obsidian.py non exporté, non dans bootstrap | L2 | MAJEUR |

---

## 19. Capability Ghosts

Une "ghost capability" est une capability qui apparaît dans les rapports, tests, ou imports mais qui est **inaccessible depuis le chemin de production**.

| Ghost capability | Pourquoi ghost | Où elle existe | Production reachable |
|---|---|---|---|
| `vision.*` (5 outils) | `register_visual_tools` non exportée de catalog, non appelée en bootstrap | `visual.py` | ✗ |
| `browser.click_at_position` | Absent de `_DISPATCH` (agent) et `_defs` (catalog) | Rapports uniquement | ✗ |
| `browser.last_clicked_target` (WS key) | `browser.click` has `observation=()`, `evidence={}` — clé jamais écrite | `_finalize_turn` tente de la lire | ✗ |
| `interaction.track` / `interaction.reply` | `register_interaction_tools` non exportée de catalog, non appelée en bootstrap | `interaction.py` | ✗ |
| `telegram.send_message` | `register_notify_tools` exportée mais non appelée en bootstrap | `notify.py`, `catalog/__init__` | ✗ |
| `obsidian.list_notes` / `obsidian.read_note` | `register_obsidian_tools` non exportée, non appelée | `obsidian.py` | ✗ |
| `harness.steer()` mid-turn | `steering.py` retourne `ErrorInfo(NOT_IMPLEMENTED)` inconditionnellement | `steering.py` | ✗ |
| `image_ref` ContentPart | Filtré par `if p.type == "text"` dans `_messages_to_ollama()` | Contrat `ContentPart(type="image_ref")` | ✗ |
| `CameraLightSensor` | Jamais instancié dans bootstrap (`_start_perception()` liste 3 capteurs seulement) | `camera_sensor.py` | ✗ |
| `ScreenLightSensor` | Jamais instancié dans bootstrap | `screen_sensor.py` | ✗ |
| `gemma4` pour vision | Model pool l'inclut (capabilities=vision) mais aucun outil vision n'est enregistré → jamais sélectionné | `config.py _DEFAULT_MODEL_POOL` | ✗ (jamais sélectionné) |
| `ThreeJSAdapter` | Présent dans `spatial/renderer/threejs_adapter.py`, non connecté au runtime | `spatial/renderer/` | ✗ |
| `_LAST_CLICKED_OBSERVATION` | Jamais défini dans `browser.py` (contrairement à `_CURRENT_URL_OBSERVATION`) | Référencé implicitement via _finalize_turn | ✗ |
| `cognition/reasoning.py::not_implemented_error` | Exporté comme stub explicite | `cognition/__init__.py` | — (marker) |
| `perception.process_started/stopped` | Events définis dans le blueprint, aucun capteur correspondant dans codebase | Blueprint | ✗ |
| `perception.usb_connected/disconnected` | Idem | Blueprint | ✗ |
| `perception.network_changed` | Idem | Blueprint | ✗ |
| `TaskState.BLOCKED` sans transition vers FAILED/COMPLETED automatique | `block()` existe, mais `recover_after_restart` ne gère que RUNNING→PAUSED, pas BLOCKED | `registry.py` | PARTIEL |

---

## 20. Dependency Graphs

### 20.1 VISION — chaîne complète avec ruptures

```
VISION CAPABILITY
 ↓
register_visual_tools(registry, observe_fn, capture_screen_fn, capture_browser_fn)
 ↓ [BREAK 1: non appelé dans bootstrap — callables non créés]
ToolRegistry.register("vision.*")
 ↓ [BREAK 2: non exported from catalog/__init__]
_discover_tool_schemas() → available_tools
 ↓
Harness → model request (system_prompt + tool_schemas)
 ↓
Model selects vision tool → ToolCall
 ↓
execution.py::execute() → handler(call)
 ↓
observe_fn(path, ...) → VisualObservation
 ↓
ContentPart(type="image_ref", value=path) in ModelRequest.content
 ↓ [BREAK 3: _messages_to_ollama() drops image_ref]
Ollama API {role, content: text_only} — NO images[]
 ↓ [BREAK 4: gemma4 jamais sélectionné car vision tools absents]
Vision Model → response
```

**4 ruptures.** Fixer uniquement BREAK 1 laisse BREAK 3 bloquant.

### 20.2 PC INTERACTION — chemin complet (production)

```
User request → HarnessRequest
 ↓
harness.handle_request()
 ↓
assemble() → Context [WorldState(pc:active_window), Memory, ...]
 ↓
_discover_tool_schemas() → [pc.*, browser.*, phone.*, scene.*, ...]
 ↓
route(ModelRequest) → ModelResponse → ToolCall("pc.mouse.click", {x, y})
 ↓
execution.py::execute()
 → safety.should_stop() [STOP check]
 → registry.get("pc.mouse.click") [Tool found]
 → safety.check_permission() [SENSITIVE → user approval or auto if configured]
 → registry.handler_for("pc.mouse.click")
 → handler(call) → WindowsDeviceAgent.execute(Command)
 → mechanisms/mouse.py
 → win32api / pyautogui
 → ToolResult(status=SUCCESS, evidence={...})
 ↓
_promote_observations_and_verify() [ObservationSpec → WorldState]
 ↓
model request #N+1 (with updated WorldState in context)
```

**Chemin L6 — prouvé statiquement.**

### 20.3 PHONE CALL — chemin complet (production, conditionnel)

```
User: "Appelle Jean-Pierre"
 ↓
harness.handle_request()
 ↓
assemble() [context includes WorldState phone:connection_state if known]
 ↓
ToolCall("phone.call.contact", {"name": "Jean-Pierre"})
 ↓
execution.py → safety.check_permission() [SENSITIVE]
 ↓
phone.py handler → Command(capability_name="call.dial_contact", device_id="phone_agent")
 ↓
PhoneLinkDeviceAgent.execute()
 → _DISPATCH["call.dial_contact"] → _call_dial_contact()
 → phone_link.dial_contact_suggestion("Jean-Pierre")
 → UIA automation on PhoneExperienceHost.exe
 → Result(status=SUCCESS|FAILURE|BLOCKED)
 ↓
ToolResult → harness continues
```

**Chemin L6 conditionnel (Windows + Phone Link + iPhone Bluetooth connecté).**

### 20.4 PERCEPTION → WORLDSTATE (production)

```
PerceptionRuntime._loop() [daemon thread, 3s interval]
 ↓
ActiveWindowSensor.sample()
 → win32gui.GetForegroundWindow() → {title, process}
 → Event(type="perception.window_changed", payload=to_dict(PerceptionObservation))
 ↓
bus.publish(event) → per-subscriber queue
 ↓
WorldStateStore._on_perception_event(event) [subscribed to "perception.*"]
 → WorldStateFact(domain="pc", key="active_window", value={title, process}, TTL=20s)
 → SqliteBackend.save("world_state_facts", "pc:active_window", payload)
 ↓
harness.handle_request() → assemble() → _world_state_sections()
 → retrieve_relevant() → WorldStateFact(pc:active_window)
 → ContextSection in system prompt
 ↓
Model sees: "Fenêtre active: Chrome.exe — 'GitHub - ...'"
```

**Chemin L6 — prouvé statiquement.**

### 20.5 INTERACTION.TRACK — chaîne avec rupture

```
INTERACTION.TRACK CAPABILITY
 ↓
register_interaction_tools(registry, world_state)
 ↓ [BREAK 1: non exportée de catalog/__init__]
 ↓ [BREAK 2: non appelée dans bootstrap]
ToolRegistry.register("interaction.track")
 ↓
Model → ToolCall("interaction.track", {...})
 ↓
world_state.apply_update(WorldStateFact(domain="interaction", ...))
 ↓
Context Engine → conversation includes interaction state
```

**2 ruptures.** Code présent et correct. Non wired.

---

## 21. Critical Gaps

### CRITICAL

| Gap | Impact | Source |
|---|---|---|
| C1: image_ref DROPPED par `_messages_to_ollama()` | Vision impossible même si tools câblés | `ollama_cloud.py:60` |
| C2: `register_visual_tools` non câblé en bootstrap | Vision non accessible au modèle | `bootstrap.py` imports |
| C3: `browser.click_at_position` absent de `_DISPATCH` | Grounding pixel → clic impossible | `agent.py` |
| C4: `browser.screenshot` sans dimensions | Coordonnées normalisées non convertibles | `agent.py:_screenshot()` |
| C5: `browser.last_clicked_target` jamais écrit | `_finalize_turn` lit une clé toujours absente | `browser.py:64 observation=()` |

### HIGH

| Gap | Impact | Source |
|---|---|---|
| H1: `interaction.track`/`interaction.reply` non câblés | WorldState interaction non disponible | `catalog/__init__` + `bootstrap.py` |
| H2: `telegram.send_message` non enregistré | RAYA ne peut pas envoyer proactivement via outil | `bootstrap.py` |
| H3: `incoming_call → IGNORE` (rapport disait INTERRUPT) | RAYA ne répond pas aux appels entrants | `evaluator.py:169-186` |
| H4: `harness.steer()` NOT_IMPLEMENTED | Mid-turn steering impossible | `steering.py` |
| H5: `obsidian.*` non câblé | RAYA ne peut pas lire la vault Obsidian | `bootstrap.py` |

### MEDIUM

| Gap | Impact | Source |
|---|---|---|
| M1: CameraLightSensor jamais instancié | Perception visuelle caméra absente | `bootstrap.py:_start_perception` |
| M2: ScreenLightSensor jamais instancié | Perception changement écran absente | `bootstrap.py:_start_perception` |
| M3: `gemma4` vision inutilisable | Modèle vision configuré mais inaccessible | model pool + image transport |
| M4: `browser.click observation=()` | Actions browser non tracées dans WorldState | `browser.py:64` |
| M5: ThreeJSAdapter non connecté | Rendu 3D natif non disponible | `spatial/renderer/` |

### LOW

| Gap | Impact | Source |
|---|---|---|
| L1: `perception.usb/network/process` capteurs absents | WorldState incomplet | `perception/` |
| L2: `TaskState.BLOCKED` sans recovery auto | Tâches bloquées nécessitent intervention manuelle | `registry.py` |
| L3: `_conversation_history_section` limit=5 hardcodé | Contexte conversationnel court | `assembler.py` |
| L4: `world_state_domains=()` dans handle_request | ALL facts in context (pas de filtrage par relevance) | `loop.py` |

---

## 22. Production Capabilities Actually Available

**Ce que RAYA peut réellement faire depuis son chemin production actuel (`bootstrap()`) :**

### Interaction & Processing
- Traiter des requêtes texte via le modèle LLM (Ollama Cloud/Local)
- Maintenir l'historique de conversation (5 tours, layer CONVERSATION)
- Gérer des préférences utilisateur durables (layer PERSONAL)
- Créer, gérer, annuler, mettre en pause et reprendre des tâches longues
- Répondre en prenant en compte l'état du monde (WorldState en contexte)

### PC / Windows
- Lancer, fermer, focuser des applications
- Lister les fenêtres et processus
- Taper du texte et envoyer des raccourcis clavier
- Cliquer/déplacer la souris
- Capturer l'écran (avec dimensions)
- Inspecter et interagir avec des éléments UI (UIA)
- Exécuter des commandes shell
- Chercher et ouvrir des dossiers
- Détecter les logiciels installés, découvrir les launchers
- Surveiller la batterie

### Browser / DOM
- Naviguer vers des URLs
- Lire le DOM structuré d'une page (boutons, liens, inputs, URL — compact)
- Capturer des screenshots de la page
- Lister les onglets ouverts
- Cliquer un élément par description textuelle
- Taper dans des champs
- Fermer les overlays/cookie banners

### Phone / iOS (via Phone Link)
- Passer des appels par numéro ou par contact
- Terminer, répondre, rejeter des appels
- Consulter l'état d'un appel en cours
- Envoyer des SMS
- Chercher des contacts
- Vérifier l'état de la connexion Phone Link

### Spatial
- Créer et gérer des scènes 3D en mémoire
- Ajouter, modifier, supprimer des objets 3D
- Décrire une scène
- Déclencher un rendu visuel (si Cockpit actif)

### Utilitaires
- Lire/écrire des fichiers dans le workspace sandboxé
- Lire l'heure système
- Afficher/cacher des vues du Cockpit
- Enregistrer des préférences de canal de notification

### Perception passive (fond)
- Suivre la fenêtre active (poll 3s, WorldState)
- Détecter l'activité d'appel téléphonique (Phone Link)
- Détecter les notifications d'appels entrants (via ShellExperienceHost)

---

## 23. Non-Production / Partial Capabilities

| Capability | Status | Notes |
|---|---|---|
| Voice (VAD→STT→TTS→Harness) | L5 — entrypoint séparé | `voice/factory.py::build_real_voice_runtime(handles)` |
| Telegram (inbound/outbound polling) | L5 — entrypoint séparé | `telegram/runtime.py` |
| Vision (5 tools) | L2 — code complet, non câblé | 3 ruptures dans la chaîne |
| image_ref transport | L0 — jamais implémenté | `_messages_to_ollama()` |
| browser.click_at_position | L0 — absent | jamais ajouté à `_DISPATCH` |
| browser.screenshot dimensions | L0 — absent | `_screenshot()` sans width/height |
| interaction.track / interaction.reply | L2 — code présent, non câblé | `catalog/__init__` manquant |
| telegram.send_message (outil) | L2 — code présent, non câblé | `bootstrap.py` ne l'appelle pas |
| obsidian.list_notes / read_note | L2 — code présent, non câblé | non exporté, non dans bootstrap |
| harness.steer() mid-turn | L1 — stub | NOT_IMPLEMENTED retourné inconditionnellement |
| CameraLightSensor | L2 — code complet | jamais instancié dans `_start_perception` |
| ScreenLightSensor | L2 — code complet | jamais instancié dans `_start_perception` |
| incoming_call → INTERRUPT | L0 — code retourne IGNORE | evaluator.py |
| Three.js native renderer | L2 — code présent | non connecté au runtime |
| Long-horizon planner (multi-step replanning) | L1 — scaffolded | `create_long_horizon_task` existe, pas de vrai planner |
| Security camera | L0 — absent | pas de code |
| Linux device | L0 — absent | pas de code |

---

## 24. Architecture Readiness

### Pour une refonte Browser DOM + Vision

**Verdict : READY WITH FOUNDATIONAL GAPS**

**Ce qui est solide et réutilisable :**
- Pattern DeviceAgent (éprouvé pour Windows, Browser, Phone)
- Pattern ToolRegistry + ObservationSpec (wiring WorldState automatique)
- Pattern bootstrap wiring (try/except best-effort, injection étroite)
- Context Engine (assembler prend les facts WorldState en compte)
- Safety classification (tag "vision": SAFE déjà présent dans risk.py)
- EventBus (perception → WorldState déjà prouvé)
- `_finalize_turn` et `_compact_read_page_output` (20G-B, production)

**Gaps fondamentaux à résoudre avant de commencer :**

| Gap | Fichier concerné | Complexité |
|---|---|---|
| 1. `register_visual_tools` non câblé | bootstrap.py + catalog/__init__.py | FAIBLE — wiring seulement |
| 2. `image_ref` dropped par `_messages_to_ollama()` | ollama_cloud.py:57-69 | MOYEN — format Ollama images[] |
| 3. `browser.click_at_position` absent de `_DISPATCH` | devices/browser/agent.py + catalog/browser.py | MOYEN — nouveau handler + catalog entry |
| 4. `browser.screenshot` sans dimensions | devices/browser/agent.py:_screenshot() | FAIBLE — +2 clés dans le retour |
| 5. `browser.click observation=()` | catalog/browser.py:64 + handler | FAIBLE — ajouter ObservationSpec + evidence |
| 6. Factory callables pour `register_visual_tools` | bootstrap.py | MOYEN — décider mapping agent→callable |

Ces 6 gaps sont **adressables et localisés**. L'architecture n'a pas besoin d'être reconstruite — les abstractions sont correctes. Seul le câblage est manquant.

---

## 25. Files Inspected

**Production source (lus directement) :**
- `raya/runtime/bootstrap.py` (282 lignes, intégral)
- `raya/runtime/config.py` (193 lignes, intégral)
- `raya/harness/loop.py` (760+ lignes, lignes 1-400 + contexte sessions précédentes)
- `raya/harness/steering.py` (21 lignes, intégral)
- `raya/context_engine/assembler.py` (intégral via agent)
- `raya/context_engine/render.py` (intégral via agent)
- `raya/memory/__init__.py`, `memory/store.py` (intégraux via agent)
- `raya/tasks/__init__.py`, `tasks/registry.py` (intégraux via agent)
- `raya/cognition/__init__.py`, `cognition/recovery.py`, `cognition/verification.py`, `cognition/intent.py` (via agent)
- `raya/safety/__init__.py`, `safety/risk.py` (intégraux via agent)
- `raya/observability/__init__.py`, `observability/logger.py` (via agent)
- `raya/persistence/sqlite_backend.py`, `persistence/backend.py` (via agent)
- `raya/models/__init__.py`, `models/registry.py`, `models/router.py` (via agent)
- `raya/models/providers/ollama_cloud.py` (249 lignes, intégral — sessions précédentes)
- `raya/interfaces/telegram/runtime.py` (intégral via agent)
- `raya/interfaces/voice/runtime.py` (intégral, session courante)
- `raya/interfaces/voice/channel.py`, `voice/factory.py` (via agent)
- `raya/tools/catalog/__init__.py` (intégral — sessions précédentes)
- `raya/tools/catalog/browser.py` (79 lignes, intégral — sessions précédentes)
- `raya/tools/catalog/pc.py` (237 lignes, intégral — sessions précédentes)
- `raya/tools/catalog/phone.py` (intégral via agent)
- `raya/tools/catalog/spatial.py` (partial — sessions précédentes)
- `raya/tools/catalog/visual.py` (406 lignes, lignes 1-260 via agent)
- `raya/tools/catalog/ui_views.py` (intégral, session courante)
- `raya/tools/catalog/demo.py` (intégral, session courante)
- `raya/tools/catalog/preferences.py` (intégral, session courante)
- `raya/tools/catalog/tasks.py` (220 lignes — sessions précédentes)
- `raya/tools/catalog/interaction.py` (structure via agent)
- `raya/tools/catalog/obsidian.py` (structure via agent)
- `raya/tools/catalog/notify.py` (structure via agent)
- `raya/tools/execution.py` (106 lignes — sessions précédentes)
- `raya/tools/registry.py` (40 lignes — sessions précédentes)
- `raya/tools/discovery.py` (21 lignes — sessions précédentes)
- `raya/devices/__init__.py`, `devices/base.py`, `devices/registry.py` (via agent)
- `raya/devices/browser/agent.py` (141 lignes — sessions précédentes)
- `raya/devices/windows/agent.py` (435 lignes — sessions précédentes)
- `raya/devices/ios/agent.py` (153 lignes, session courante)
- `raya/world_state/store.py` (154 lignes, session courante)
- `raya/attention/evaluator.py` (392 lignes — sessions précédentes)
- `raya/perception/__init__.py`, `perception/runtime.py`, `perception/windows_sensors.py` (session courante)
- `raya/perception/phone_sensors.py`, `perception/incoming_call_sensor.py` (via agent)
- `raya/perception/camera_sensor.py`, `perception/screen_sensor.py` (via agent)
- `raya/spatial/__init__.py`, `spatial/store.py` (via agent)
- `raya/event_bus/__init__.py`, `event_bus/bus.py` (via agent)
- `raya/contracts/__init__.py` (via agent — 62 exports listés)
- `raya/contracts/perception.py` (session courante)

**Blueprints :**
- `RAYA_V2_TECHNICAL_ARCHITECTURE.md` (lignes 1-400)
- `RAYA_V2_CONTRACTS.md` (lignes 1-200 via agent)
- `RAYA_V2_ARCHITECTURAL_INVARIANTS.md` (intégral via agent)

**Non lus dans ce scope (hors périmètre ou non critiques pour la baseline) :**
- `raya/cognition/planning.py`, `cognition/state_cycle.py`
- `raya/interfaces/ui/` (Cockpit WebSocket)
- `raya/devices/browser/controller.py`, `browser/session.py`, `browser/worker.py`
- `raya/devices/windows/mechanisms/` (tous sauf structure connue)
- `raya/devices/ios/mechanisms/phone_link.py`
- `raya/models/catalog.toml`, `models/scheduler.py`, `models/vision.py`
- `raya/tasks/priority.py`
- `raya/harness/cancellation.py`, `harness/scheduler.py`, `harness/session.py`, `harness/execution_records.py`
- `raya/memory/experience.py`
- `RAYA_V2_MIGRATION_PLAN.md`, `RAYA_V2_REPOSITORY_STRUCTURE.md`, `RAYA_V2_ARCHITECTURE_FINAL_REVIEW.md`

---

## 26. Tests Executed

**NONE — STATIC AUDIT ONLY.**

Aucun test n'a été lancé dans cet audit. Toutes les conclusions sont établies par lecture statique du code source de production.

---

## 27. Final Verdict

### PRODUCTION BASELINE VERIFIED

**Avec les gaps documentés ci-dessus.**

RAYA V2 dispose d'une baseline production solide, cohérente et fonctionnelle pour son cœur (Harness, Context, Memory, Tasks, Safety, WorldState, Attention, Persistence) et ses 3 tiers de devices (PC/Windows, Browser/DOM, Phone/iOS).

La baseline est **vérifiée statiquement** sur le chemin :
```
bootstrap() → ToolRegistry → Harness → Model → Tool → Device → Result → WorldState → Context
```

Les gaps identifiés sont documentés, localisés et adressables. Ils ne remettent pas en cause la solidité de l'architecture — ils indiquent les points précis de non-câblage ou de non-implémentation.

**Cinq capabilities reportées comme "DONE" dans d'anciens rapports sont absentes du chemin production actuel :**
1. Vision tools (non câblés)
2. image_ref transport (non implémenté dans l'adapter Ollama)
3. browser.click_at_position (jamais ajouté au dispatch)
4. interaction.track/reply (code présent, non câblé)
5. incoming_call → INTERRUPT (code retourne IGNORE)

**Ces drifts sont des faits, pas des jugements.** Le code est lisible, les ruptures sont précises, et leur correction est ciblée.

---

*Chantier 20J — Audit read-only complet. ZERO test exécuté. ZERO code modifié. ZERO patch proposé.*
