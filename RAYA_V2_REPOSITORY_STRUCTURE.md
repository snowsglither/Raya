# RAYA V2 — REPOSITORY STRUCTURE

**Statut :** Structure de dossiers cible, prête pour revue avant implémentation.
**Documents parents :** `RAYA_V2_TECHNICAL_ARCHITECTURE.md` (frontières), `RAYA_V2_CONTRACTS.md` (schémas).

Chaque dossier de premier niveau correspond à EXACTEMENT un subsystem de l'architecture technique (§1 du document parent), sauf `contracts/` (transversal, pur données) et `tests/` (miroir de structure). Aucun dossier ne porte un nom ambigu type "brain" ou "core" fourre-tout — c'est une contrainte délibérée après le constat d'audit (3 systèmes nommés "brain" en V1, un `core/` qui a fini par tout contenir).

```
raya/
├── runtime/
├── contracts/
├── world_state/
├── perception/
├── attention/
├── harness/
├── cognition/
├── context_engine/
├── memory/
├── tasks/
├── tools/
├── models/
├── safety/
├── devices/
├── interfaces/
├── persistence/
├── observability/
tests/
├── contracts/
├── harness/
├── memory/
├── world_state/
├── attention/
├── tasks/
├── tools/
├── models/
├── safety/
├── devices/
├── persistence/
├── concurrency/
├── recovery/
├── integration/
scripts/
docs/
.env.example
pyproject.toml
```

---

## 1. `raya/runtime/`

```
runtime/
├── __init__.py
├── bootstrap.py        # composition root — câble toutes les implémentations concrètes
├── config.py            # chargement .env / config résolue
├── entrypoints/
│   ├── cli.py            # point d'entrée headless (Phase 0-3)
│   ├── api_server.py      # point d'entrée HTTP (utilisé par interfaces/web)
│   └── desktop.py         # point d'entrée desktop (Phase 6, différé)
```

**Pourquoi ce dossier existe :** un seul endroit où le graphe de dépendances concret est assemblé (quel `ModelProvider`, quel `PersistenceBackend`, quels `Device`). Aucun autre dossier n'a le droit d'instancier directement une implémentation concrète d'un autre subsystem — tout passe par injection depuis `bootstrap.py`. C'est le remplaçant direct de `main.py`/`raya_cli.py`/`core/bootstrap.py` de V1, mais qui NE contient plus de logique applicative (V1 mélangeait vérifications de démarrage ET construction de l'Orchestrator monolithique dans le même chemin).

---

## 2. `raya/contracts/`

```
contracts/
├── event.py              # Event
├── world_state.py         # WorldStateFact
├── memory.py               # MemoryEntry
├── attention.py             # AttentionDecision
├── task.py                   # Task, TaskEvent
├── harness.py                 # HarnessRequest, HarnessState
├── tool.py                     # Tool, ToolCall, ToolResult
├── model.py                     # ModelCapability, ModelDescriptor, ModelRequest, ModelResponse
├── context.py                    # Context
├── permission.py                   # Permission
├── device.py                        # Device, Capability, Command, Result, Health
├── interface.py                      # InterfaceRequest, InterfaceResponse
└── errors.py                          # ErrorInfo
```

**Pourquoi ce dossier existe :** les contrats de `RAYA_V2_CONTRACTS.md` traduits en dataclasses/pydantic models, SANS AUCUNE logique métier — uniquement des structures de données, validation de schéma, et sérialisation. N'importe quel subsystem peut importer `contracts/`, mais `contracts/` n'importe jamais rien d'autre dans `raya/`. C'est la seule dépendance "horizontale" autorisée dans tout le repo (tout le monde en dépend, elle ne dépend de rien).

---

## 3. `raya/world_state/`

```
world_state/
├── store.py            # WorldStateStore — get_fact/query/apply_update
├── domains/
│   ├── pc.py             # schémas de faits spécifiques au domaine "pc"
│   ├── browser.py
│   ├── network.py
│   └── system.py
```

**Pourquoi ce dossier existe :** un seul store d'état courant, séparé physiquement de `memory/` pour rendre la confusion World State/Memory (identifiée en V1 entre `modules/raya_state/` et `modules/memory/`) architecturalement impossible à reproduire par erreur — ce sont deux packages Python distincts avec des imports distincts, pas deux fichiers dans le même dossier.

---

## 4. `raya/perception/`

```
perception/
├── sensors/
│   ├── window.py         # fenêtre active (léger, continu) — EXTRACT modules/context/monitor.py
│   ├── resources.py        # CPU/RAM/batterie (léger, continu) — EXTRACT modules/hud/monitor.py
│   ├── network.py
│   ├── usb.py
│   └── presence.py          # capteur léger non-LLM (diff de frame) — REBUILD modules/vision/presence_watcher.py
├── capture/
│   ├── camera.py             # capture on-demand — EXTRACT modules/camera/stream.py
│   ├── screen.py               # capture on-demand
│   └── audio_segment.py         # VAD continu léger, transcription seulement sur segment détecté
```

**Pourquoi ce dossier existe :** séparation physique stricte entre `sensors/` (continu, léger, jamais de LLM) et `capture/` (à la demande, potentiellement lourd/coûteux) — rend visible dans la structure elle-même la règle "vision is a capability, not a permanent sensor" du blueprint. Un reviewer qui voit un nouveau fichier ajouté dans `sensors/` avec un appel modèle dedans doit immédiatement le questionner.

---

## 5. `raya/attention/`

```
attention/
├── evaluator.py          # evaluate(event) -> AttentionDecision
├── policy.py               # règles urgency/importance/novelty/cost — EXTRACT modules/awareness/monitor.py (politique)
├── interruption.py           # hold-and-flush par mode — EXTRACT modules/context/dnd.py
```

**Pourquoi ce dossier existe :** isole la politique de priorité dans un package qui n'a PAS le droit d'importer `tools/`, `devices/`, ou `models/` (contrainte vérifiée par lint d'imports, voir §17) — garantit structurellement qu'Attention ne peut pas devenir un second orchestrateur, même par accident au fil des ajouts de fonctionnalités.

---

## 6. `raya/harness/`

```
harness/
├── loop.py              # LA boucle nominale (Architecture §3.1) — remplace core/orchestrator.py
├── session.py             # gestion de HarnessState, checkpoint/resume
├── steering.py              # injection d'instructions mid-turn
├── cancellation.py            # vérification should_stop() aux points de contrôle
```

**Pourquoi ce dossier existe :** volontairement PETIT — chaque fichier a une seule responsabilité de la boucle (exécution, état de session, steering, annulation), contrairement à `core/orchestrator.py` (2583 lignes, une classe qui fait tout). Aucun fichier ici ne doit dépasser ~400 lignes sans déclencher une revue de découpage — c'est une règle de discipline explicite pour ne pas reconstruire le monolithe sous un nouveau nom.

---

## 7. `raya/cognition/`

```
cognition/
├── intent.py             # EXTRACT core/session_mode.py (classification rapide) + brain/cognition/intent.py
├── ambiguity.py             # EXTRACT brain/cognition/ambiguity.py — bloquant/non-bloquant
├── verification.py            # EXTRACT brain/cognition/verification.py — SUCCESS/UNKNOWN/FAILURE
├── recovery.py                  # EXTRACT brain/cognition/recovery.py — proposition d'alternative
├── prioritization.py              # EXTRACT brain/cognition/prioritization.py
```

**Pourquoi ce dossier existe :** rassemble le raisonnement (règles rapides + appels `models/`) sans jamais toucher `devices/`/`tools/` directement — Cognition PROPOSE, ne fait jamais. Nom sans ambiguïté (contrairement à "brain" en V1) : ce package n'est PAS le Model Layer (ça c'est `models/`), il l'UTILISE.

---

## 8. `raya/context_engine/`

```
context_engine/
├── assembler.py          # assemble(task_id, budget) -> Context — EXTRACT modules/context/recent_work.py (pattern)
├── ranking.py
├── budget.py
├── cache.py                 # cache court TTL, invalidation sur memory/world_state.updated
```

**Pourquoi ce nom précis (`context_engine`, pas `context`) :** V1 a un `modules/context/` qui mélange en réalité perception+attention+context engine (confusion de nommage identifiée dans l'audit). En V2, le mot "context" seul est explicitement évité comme nom de dossier pour forcer la clarté — `context_engine/` ne fait QUE de la sélection dynamique pour le modèle, rien d'autre.

---

## 9. `raya/memory/`

```
memory/
├── store.py               # API unifiée write/search/correct
├── conversation.py           # ADAPT modules/conversations/store.py — SQLite, isolation canal stricte
├── personal.py                 # ADAPT modules/memory/store.py — facts durables
├── experience.py                 # ADAPT modules/identity/store.py — apprentissages comportementaux
├── project.py
├── working.py
├── lifecycle.py                    # candidate→active→confirmed→aging→obsolete
├── channel_bridge.py                 # ADAPT modules/context_bridge/bridge.py — accès cross-canal explicite
```

**Pourquoi ce dossier existe :** une seule API d'écriture/lecture (`store.py`) par-dessus plusieurs backends de couches — empêche qu'un module quelconque écrive directement dans une table SQLite sans passer par les règles de lifecycle/confidence/provenance.

---

## 10. `raya/tasks/`

```
tasks/
├── registry.py           # ADAPT modules/async_engine/registry.py — étendu avec persistance/priorité/dépendances
├── actor.py                # logique de transition d'état, checkpoint
├── dependencies.py
```

**Pourquoi ce dossier existe (un seul, pas plusieurs) :** V1 a des velléités de gestion de tâches à plusieurs endroits (`modules/async_engine/`, `modules/tasks/` mort, `modules/pc_control/task.py` qui est en fait un journal d'observabilité, `modules/reminders/scheduler.py` qui est une boucle daemon séparée). En V2, `tasks/registry.py` est LE seul registre de `Task` — un reminder devient un `Task` avec un type spécifique, pas un système parallèle.

---

## 11. `raya/tools/`

```
tools/
├── registry.py            # Tool Registry
├── discovery.py              # discover(capability_tags)
├── validation.py                # validation de schéma input/output
├── execution.py                    # call(ToolCall) -> ToolResult, retry/timeout/cancellation
├── catalog/
│   ├── browser.py                    # capability_tags: ["browser"] — délègue à devices/browser/
│   ├── pc.py                           # capability_tags: ["pc"] — délègue à devices/windows/
│   ├── documents.py                       # EXTRACT modules/documents/ — pas de device, exécution locale
│   ├── calendar.py                          # ADAPT calendar/drive/gcontacts/gtasks unifiés
│   ├── files.py                                # EXTRACT modules/files/upload_pipeline.py
│   ├── hologram.py                                # KEEP modules/hologram/ (quasi tel quel)
│   ├── notes.py
│   ├── mail.py
│   └── utils.py                                     # calculatrice, convertisseur, météo — KEEP modules/utils/
```

**Pourquoi ce découpage :** `catalog/` regroupe les DÉCLARATIONS d'outils par domaine métier (schémas + délégation), jamais leur implémentation bas niveau (qui vit dans `devices/`). C'est le remplaçant de `core/tools.py` (4521 lignes, un seul fichier), volontairement éclaté pour qu'un domaine (ex: browser) puisse être modifié sans toucher aux 82 autres outils.

---

## 12. `raya/models/`

```
models/
├── registry.py            # ModelDescriptor registry
├── router.py                 # scoring capability/coût/latence/contexte
├── providers/
│   ├── base.py                # interface ProviderAdapter commune
│   ├── ollama_cloud.py           # EXTRACT connaissances opérationnelles de core/llm.py (think=False, etc.)
│   ├── ollama_local.py
```

**Pourquoi ce dossier existe (un seul, pas trois) :** remplace `core/llm.py` + `brain/models/` + `modules/brain/router.py` (les trois "cerveaux" partiels de V1) par UN SEUL package. `providers/` isole tout code spécifique à un format tiers (y compris d'éventuels vestiges de compatibilité) — rien en dehors de `providers/` ne connaît le format natif d'Ollama.

---

## 13. `raya/safety/`

```
safety/
├── stop.py                # KEEP quasi tel quel — modules/computeruse/safety.py
├── risk.py                   # classification de risque généralisée (ex-modules/pc_control/engine.py::_RISK)
├── permissions.py               # check_permission(), politique de confirmation
├── audit.py                       # audit trail
```

**Pourquoi ce dossier existe :** un seul point de vérité pour TOUTE décision de permission/risque dans le repo — aucun autre package n'a le droit d'implémenter sa propre logique de permission (règle vérifiable : `grep -r "risk_level\|permission_level" raya/` ne doit trouver de LOGIQUE de décision (pas juste de lecture) que dans `safety/`).

---

## 14. `raya/devices/`

```
devices/
├── base.py                # interface générique Device/Capability/Command/Result/Health
├── windows/
│   ├── agent.py              # implémente l'interface, PAS de boucle de décision
│   ├── mechanisms/
│   │   ├── uia.py              # EXTRACT modules/pc_control/elements.py
│   │   ├── keyboard.py           # EXTRACT modules/pc_control/keyboard.py
│   │   ├── mouse.py                # EXTRACT modules/pc_control/mouse.py
│   │   ├── clipboard.py              # EXTRACT modules/pc_control/clipboard.py
│   │   ├── windows_mgmt.py             # EXTRACT modules/pc_control/windows.py
│   │   ├── applications.py               # EXTRACT modules/apps/ + modules/appmanager/
│   │   └── shell.py
│   └── strategy.py                          # échelle DOM→UIA→vision — EXTRACT modules/pc_control/router.py
├── browser/
│   ├── agent.py
│   ├── session.py             # EXTRACT modules/browser/session.py — cycle de vie CDP
│   ├── worker.py                # EXTRACT modules/browser/worker.py — sérialisation thread
│   └── controller.py              # EXTRACT modules/browser/controller.py — navigate/click/type/read_page
├── camera/
│   ├── agent.py
│   └── vision_ops.py             # ADAPT modules/vision/{object_detection,ocr,face_recognition}.py
├── linux/                          # squelette, non prioritaire V2 initial
└── ios/                              # squelette, ADAPT modules/ios/api.py
```

**Pourquoi ce découpage :** chaque Device Agent est un sous-package isolé — `windows/` ne peut pas importer `browser/` et vice versa (pas de raison légitime de le faire). `mechanisms/`/`strategy.py` séparent explicitement "comment on fait" (mécanisme bas niveau) de "quoi on expose" (`agent.py`, qui implémente l'interface `base.py`). Aucun fichier de ce dossier n'a le droit d'importer `models/` — c'est la règle qui élimine structurellement le pattern `auto_agent.py` (V1 avait son appel Ollama HTTP direct dans `modules/pc_control/`).

---

## 15. `raya/interfaces/`

```
interfaces/
├── cli/
│   └── repl.py              # ADAPT raya_cli.py — Phase 0, le premier client réel du Harness
├── web/
│   ├── server.py               # ADAPT modules/web/server.py — déjà bien découplé en V1
│   └── routes.py
├── api/
│   └── http.py                   # API généraliste (utilisée par mobile/ios, future)
├── desktop/                         # REBUILD, différé (Phase 6)
│   └── app.py
├── mobile/
│   └── ios_bridge.py                  # ADAPT modules/ios/api.py
└── voice/                               # REBUILD complet (Phase 5)
    ├── vad.py
    ├── transcription.py
    ├── synthesis.py
    └── channel.py
```

**Pourquoi ce dossier existe :** chaque canal est un sous-package qui importe UNIQUEMENT `harness/` (et `contracts/`) — jamais `models/`, `tools/`, `devices/` directement. C'est la règle qui élimine `modules/ghost/ghost_mode.py` (bypass) et le couplage direct `ui.state` de V1.

---

## 16. `raya/persistence/`

```
persistence/
├── backend.py             # interface abstraite save/load/query/migrate
├── sqlite_backend.py         # ADAPT modules/conversations/store.py (schéma+migrations)
├── file_backend.py             # pour les stores JSON simples (notes, préférences)
├── remote_backend.py             # ADAPT modules/conversations/backend.py — routing local/serveur
├── migrations/
│   └── ...                          # migrations versionnées par collection
```

**Pourquoi ce dossier existe :** un seul point d'abstraction stockage, backend interchangeable — `memory/`, `tasks/`, `world_state/`, `safety/` (audit) l'utilisent tous mais n'importent JAMAIS `sqlite3` ou un chemin de fichier directement.

---

## 17. `raya/observability/`

```
observability/
├── logger.py
├── tracer.py               # reconstruction de timeline par correlation_id
├── audit_trail.py
├── health.py
```

**Pourquoi ce dossier existe :** consommateur pur d'events, jamais importé PAR un subsystem métier pour de la logique (seulement pour émettre des logs) — évite que l'observability devienne un chemin de contrôle caché.

---

## 18. `tests/`

Miroir de la structure `raya/`, plus :

```
tests/
├── integration/
│   ├── test_full_turn_cli.py         # scénario complet CLI → Harness → Tool → World State
│   ├── test_background_task_concurrency.py   # conversation + task de fond en parallèle (invariant clé)
│   ├── test_recovery_after_crash.py
│   └── test_ollama_cloud_live.py       # vrai test contre Ollama Cloud (marqué explicitement "live", pas mocké)
├── concurrency/
├── recovery/
```

Voir `RAYA_V2_ARCHITECTURAL_INVARIANTS.md` pour la stratégie de test complète et la cible ~150-200 tests.

---

## 19. Racine du repo

```
scripts/                  # ADAPT tools/ensure_deps.py, tools/test_voices.py (une fois voice reconstruite)
docs/                       # ce dossier RayaV2/ y migre une fois l'implémentation démarrée
.env.example
pyproject.toml               # remplace requirements.txt/requirements-core.txt — dépendances explicitement groupées
                                 # par extra: [core] (headless), [windows], [voice], [vision], [3d]
```

`requirements-core.txt` (V1, **KEEP** comme principe) devient le groupe `[core]` de `pyproject.toml` — c'est déjà, en miniature, la séparation headless/plateforme-spécifique que la structure V2 généralise.

---

## 20. Règle de dépendance imposée (vérifiable par lint)

Ordre de dépendance autorisé, strictement descendant (repris du diagramme Architecture §0) :

```
interfaces → harness → { cognition, tasks, context_engine } → { memory, world_state } → tools → safety → devices
                                                                                                    ↑
                                                                        models (appelé par harness/cognition/tools, feuille)
context_engine → tools (EXCEPTION EXPLICITE — voir règle ci-dessous)
perception → world_state (uniquement)
attention → { world_state, tasks } (lecture seule)
runtime → tout (composition root)
contracts ← tout (tout le monde en dépend, elle ne dépend de rien)
observability ← tout (écoute tout, n'est jamais importée pour de la logique)
persistence ← { memory, tasks, world_state (snapshot), safety (audit) }
```

**Exception explicite `context_engine → tools` :** `context_engine` a besoin de connaître les schémas des outils pertinents pour construire `ModelRequest.available_tools` (voir `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §1.7). Cette dépendance est **autorisée par le lint CI**, mais strictement bornée :
- lecture seule — uniquement `tools.discover(capability_tags) -> list[Tool]` (schémas) ;
- jamais `tools.call(...)` — `context_engine` ne déclenche aucune exécution de `Tool` ;
- toute autre méthode de `tools/` reste interdite depuis `context_engine/` (le lint doit distinguer l'import du module `tools.discovery` de celui de `tools.execution`, pas seulement autoriser `raya/tools/` en bloc).

**Application concrète :** un outil de lint d'imports (ex: `import-linter` avec des `contracts` de dépendance déclarés dans `pyproject.toml`) doit faire échouer la CI si, par exemple, `raya/devices/` importe `raya/models/`, ou si `raya/interfaces/` importe `raya/tools/` directement. C'est la seule garantie durable contre la reconstruction progressive d'un monolithe au fil des features ajoutées après le lancement de V2 — l'audit a montré que V1 a dérivé exactement de cette façon (le couplage `ui.state` direct depuis `core/agent_state.py` n'était probablement pas une décision day-1, mais un raccourci ajouté plus tard).

---

*Fin de la structure de repository. Voir `RAYA_V2_MIGRATION_PLAN.md` pour l'ordre de construction réel de ces dossiers.*
