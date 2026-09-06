# RAYA V2 — TECHNICAL ARCHITECTURE

**Statut :** Architecture technique définitive, prête pour revue avant implémentation. Aucun code existant modifié.
**Sources :** `RAYA_V2_ARCHITECTURAL_BLUEPRINT.md` (vision), `RAYA_V2_AUDIT_REPORT.md` + `RAYA_V2_MIGRATION_MAP.md` (matière première V1).
**Documents compagnons :** `RAYA_V2_CONTRACTS.md`, `RAYA_V2_REPOSITORY_STRUCTURE.md`, `RAYA_V2_MIGRATION_PLAN.md`, `RAYA_V2_ARCHITECTURAL_INVARIANTS.md`.

**Principe fondamental, rappelé une fois pour toutes :** `MODEL != RAYA`. Le modèle est une capacité cognitive parmi d'autres, invoquée par le Harness. Il ne possède ni état, ni mémoire, ni décision finale. Tout composant qui appelle un modèle pour décider seul de sa prochaine action (le pattern `auto_agent.py`/`ghost_mode.py` identifié en V1) est une violation architecturale, peu importe où il se trouve dans l'arbre de fichiers.

---

## 0. Vue d'ensemble des 16 subsystems

```
                              INTERFACES
                                  |
                              PERCEPTION → events → WORLD STATE
                                  |                      |
                              ATTENTION ←—————————————————
                                  |
                               HARNESS  ←→ PERSISTENCE
                    +—————————————+—————————————+
                    |             |             |
               COGNITION       TASKS        CONTEXT
                    |             |             |
                    +—————————————+—————————————+
                                  |
                         MEMORY ←—+—→ WORLD STATE
                                  |
                              TOOLS ←→ SAFETY
                                  |
                              DEVICES

MODELS   : transversal, invoqué uniquement par Harness/Cognition
SAFETY   : transversal, consulté par Harness/Tools/Devices
OBSERVABILITY : transversal, écoute tous les events, n'influence jamais le flux
RUNTIME  : englobant — démarre et câble tout le reste
```

Règle de câblage générale : **un subsystem ne dépend que des subsystems strictement en dessous de lui dans le diagramme, jamais latéralement de force, jamais vers le haut.** Toute communication latérale ou ascendante passe par un Event, pas par un appel direct de fonction. C'est la règle qui a été violée partout en V1 (voice→async_engine direct, camera→ui.state direct, ghost→core.llm direct).

---

## 1. FRONTIÈRES PAR SUBSYSTEM

Format pour chacun : responsabilité / peut faire / ne doit JAMAIS faire / dépendances autorisées / dépendances interdites / données possédées / events émis / events consommés / API publique / sync ou async / persistence.

### 1.1 `runtime`

- **Responsabilité :** composition root du process. Charge la config, câble les implémentations concrètes (quel Provider modèle, quel Device Agent, quel Storage backend) derrière les interfaces abstraites, exécute les vérifications de démarrage, gère l'arrêt propre.
- **Peut faire :** lire l'environnement (.env, OS), instancier les subsystems dans le bon ordre, exposer un point d'entrée par interface (CLI/API/serveur).
- **Ne doit JAMAIS faire :** contenir de la logique métier, décider du routage d'un outil ou d'un modèle, connaître la structure interne d'une conversation.
- **Dépendances autorisées :** tous les subsystems (il les instancie).
- **Dépendances interdites :** aucune — mais rien d'autre ne doit dépendre de `runtime` (feuille de l'arbre de dépendances, à l'envers).
- **Données possédées :** configuration résolue (pas de données utilisateur).
- **Events émis :** `runtime.started`, `runtime.stopping`.
- **Events consommés :** aucun.
- **API publique :** `bootstrap() -> RuntimeHandles`, `shutdown()`.
- **Sync/async :** synchrone au démarrage, délègue l'async à ce qu'il câble.
- **Persistence :** aucune propre ; résout le chemin de la persistence des autres subsystems (équivalent V2 de `config/paths.py`, **KEEP**).

### 1.2 `world_state`

- **Responsabilité :** représenter l'état COURANT connu de l'environnement (PC, apps, fenêtres, processus, réseau, devices, tâches en cours, batterie…). Pas de connaissance durable, pas d'historique long.
- **Peut faire :** accepter des mises à jour de `perception` et des actions vérifiées, répondre à des requêtes "quel est l'état de X", appliquer une politique de péremption (freshness).
- **Ne doit JAMAIS faire :** stocker des préférences/faits durables sur l'utilisateur (→ memory), devenir un log d'events non borné, être injecté en entier dans un contexte modèle (le Context Engine sélectionne).
- **Dépendances autorisées :** `persistence` (snapshot périodique optionnel, pas obligatoire), `observability`.
- **Dépendances interdites :** `models`, `tools`, `devices`, `interfaces` (aucun de ces subsystems n'est appelé DEPUIS world_state).
- **Données possédées :** table de `WorldStateFact` indexée par domaine + clé, avec freshness/confidence/status.
- **Events émis :** `world_state.updated` (fact ajouté/changé/expiré).
- **Events consommés :** tout event `perception.*`, `task.completed`/`task.failed` (pour mettre à jour l'état "tâche X en cours" → "terminée").
- **API publique :** `get_fact(domain, key) -> WorldStateFact | None`, `query(domain, filters) -> list[WorldStateFact]`, `apply_update(fact)`.
- **Sync/async :** lecture synchrone rapide (in-memory), écriture peut être asynchrone vers persistence.
- **Persistence :** snapshot optionnel pour reprise après crash (pas de journal complet — le blueprint interdit explicitement le "uncontrolled permanent event log").

### 1.3 `perception`

- **Responsabilité :** observer l'environnement, convertir en `Event`/`WorldStateFact`. Capteurs légers en continu, capteurs lourds strictement à la demande.
- **Peut faire :** poller à intervalle raisonnable des signaux légers (fenêtre active, process list, batterie, USB, réseau — cf. `modules/context/monitor.py`, `modules/hud/monitor.py` **EXTRACT**), déclencher une capture ponctuelle (frame webcam, screenshot, analyse vision) UNIQUEMENT sur demande explicite d'un appelant identifié.
- **Ne doit JAMAIS faire :** appeler le Model Layer lui-même pour "comprendre" ce qu'il observe (il émet le fait brut, c'est à Attention/Cognition de décider si ça mérite une analyse coûteuse), transcrire le micro en continu, analyser la webcam en boucle (violation confirmée en V1 par `presence_watcher.py` — interdite en V2), analyser l'écran en continu.
- **Dépendances autorisées :** `world_state` (pour publier), `observability`.
- **Dépendances interdites :** `models`, `attention` (perception ne décide pas si c'est important, elle observe), `harness`.
- **Données possédées :** aucune donnée durable — état interne des capteurs (dernier échantillon) seulement.
- **Events émis :** `perception.window_changed`, `perception.process_started/stopped`, `perception.usb_connected/disconnected`, `perception.battery_changed`, `perception.network_changed`, `perception.presence_changed` (signal léger, pas d'analyse LLM), `perception.capture_ready` (résultat d'une capture à la demande).
- **Events consommés :** `perception.capture_requested` (commande explicite venant d'Attention/Harness pour déclencher une capture lourde).
- **API publique :** `start_light_sensors()`, `request_capture(kind, params) -> CaptureHandle` (async, résultat via event).
- **Sync/async :** capteurs légers en threads/tâches de fond continues ; captures lourdes asynchrones, résultat livré par event.
- **Persistence :** aucune.

### 1.4 `attention`

- **Responsabilité :** décider, pour chaque `Event`/requête entrante, si elle mérite traitement immédiat, traitement en fond, interruption d'un focus courant, ou aucun traitement.
- **Peut faire :** consulter `world_state` (mode courant : idle/game/call/coding — cf. `modules/context/dnd.py` **EXTRACT**), consulter la priorité des `Task` en cours, appliquer une politique d'urgence/importance/nouveauté/coût (cf. `modules/awareness/monitor.py` **EXTRACT** pour la politique hold-and-flush), émettre une `AttentionDecision`.
- **Ne doit JAMAIS faire :** exécuter quoi que ce soit elle-même, appeler un outil ou un modèle, devenir un second orchestrateur qui redécide COMMENT faire (elle décide seulement QUOI mérite l'attention — le Harness décide comment l'exécuter), maintenir un historique de conversation.
- **Dépendances autorisées :** `world_state` (lecture), `tasks` (lecture de priorité, pas d'écriture), `observability`.
- **Dépendances interdites :** `models`, `tools`, `devices`, `harness` (Attention ne dépend pas du Harness — c'est le Harness qui la consulte, jamais l'inverse).
- **Données possédées :** politique de priorité active (mode courant, seuils), aucune donnée utilisateur durable.
- **Events émis :** `attention.decision_made(event_ref, decision)`.
- **Events consommés :** tous les `Event` entrants avant qu'ils n'atteignent le Harness ; `task.priority_changed`.
- **API publique :** `evaluate(event) -> AttentionDecision` (synchrone, doit être rapide — pas d'appel modèle dans le chemin critique).
- **Sync/async :** synchrone, latence budgétée en millisecondes (règle déterministe + heuristiques légères, pas de LLM dans la boucle chaude — cf. `modules/planning/detector.py` **ADAPT** comme modèle de "règle rapide avant modèle").
- **Persistence :** aucune (politique reconstruite au démarrage depuis la config).

### 1.5 `harness` — le runtime central

- **Responsabilité :** exécuter la boucle agentique complète pour une requête/tâche donnée : assembler le contexte, invoquer le modèle, découvrir/exécuter les outils, vérifier, mettre à jour le World State, décider de continuer/replanifier/terminer. Gère cancellation, pause, resume, checkpoint, recovery, steering.
- **Peut faire :** orchestrer des appels vers `cognition`, `context`, `tasks`, `tools`, `models`, `memory`, `world_state`, `safety` — dans cet ordre de composition, jamais en les court-circuitant.
- **Ne doit JAMAIS faire :** contenir de la logique spécifique à un device (pas de coordonnées UIA, pas de sélecteurs CDP), contenir de la logique métier d'un outil précis, être le SEUL endroit où l'état vit (l'état de session doit être persistable et reconstructible), maintenir plusieurs implémentations concurrentes (un seul Harness, jamais un "mini-Harness" local à un module — c'est exactement l'anti-pattern `auto_agent.py`/`continuous_voice.py` à éliminer).
- **Dépendances autorisées :** `attention`, `cognition`, `context`, `memory`, `world_state`, `tasks`, `tools`, `models`, `safety`, `persistence`, `observability`.
- **Dépendances interdites :** `devices` directement (toujours via `tools`), `interfaces` directement (le Harness ne connaît pas Flask/pywebview/etc. — il expose une API que les interfaces consomment).
- **Données possédées :** `HarnessState` par session (voir contrats) — état d'exécution en cours, pas les données métier elles-mêmes (déléguées à memory/tasks/world_state).
- **Events émis :** `harness.turn_started`, `harness.turn_completed`, `harness.turn_failed`, `harness.steering_applied`.
- **Events consommés :** `attention.decision_made`, `task.*`, `tool.call_completed/failed`, `model.response_received`, `safety.stop_requested` (priorité absolue — voir §3).
- **API publique :** `handle_request(HarnessRequest) -> HarnessState` (démarre/continue une session), `cancel(session_id)`, `pause(session_id)`, `resume(session_id)`, `steer(session_id, instruction)`.
- **Sync/async :** intrinsèquement asynchrone (une session peut vivre plusieurs secondes à plusieurs heures via des Task Actors) ; l'API publique retourne immédiatement un handle, la progression est observable via events/polling d'état.
- **Persistence :** `HarnessState` doit être persistable à chaque checkpoint (pas seulement en mémoire process — c'est le défaut principal de `core/orchestrator.py` en V1).

### 1.6 `cognition`

- **Responsabilité :** raisonnement, gestion d'incertitude/hypothèses, génération et comparaison d'options, décision de vérification, auto-correction. Combine règles déterministes rapides et appels au Model Layer pour le raisonnement ouvert.
- **Peut faire :** classifier intent/entités (règles rapides d'abord, cf. `core/session_mode.py` **EXTRACT** ; modèle si ambigu), distinguer ambiguïté bloquante/non-bloquante (cf. `brain/cognition/ambiguity.py` **EXTRACT**), produire un verdict tri-état SUCCESS/UNKNOWN/FAILURE après une action (cf. `brain/cognition/verification.py` **EXTRACT**), proposer une alternative après échec (cf. `brain/cognition/recovery.py` **EXTRACT**).
- **Ne doit JAMAIS faire :** exécuter un outil ou une action directement, persister quoi que ce soit lui-même (délègue à memory/tasks), décider seul de contacter l'utilisateur (ça passe par le Harness → Interfaces).
- **Dépendances autorisées :** `models` (raisonnement ouvert), `context` (pour obtenir ce dont il a besoin pour raisonner), `memory` (lecture, pas écriture directe).
- **Dépendances interdites :** `tools`, `devices`, `interfaces`, `harness` (Cognition est appelé PAR le Harness, jamais l'inverse).
- **Données possédées :** aucune donnée durable — les hypothèses/options générées pour un tour sont éphémères, retournées au Harness qui décide de les persister (ex : dans `Task.context`) ou non.
- **Events émis :** `cognition.ambiguity_detected`, `cognition.verification_result`.
- **Events consommés :** aucun directement (invoqué en synchrone/awaited par le Harness).
- **API publique :** `classify_intent(input) -> Intent`, `resolve_ambiguity(context) -> Question | Continue`, `verify(action, expected, observed) -> SUCCESS|UNKNOWN|FAILURE`, `propose_recovery(failure) -> list[Option]`.
- **Sync/async :** appelable en async (peut invoquer le Model Layer, donc latence non triviale).
- **Persistence :** aucune propre.

### 1.7 `context` (Context Engine)

- **Responsabilité :** assembler dynamiquement ce que le modèle doit voir pour UN appel donné — ranking, filtrage, budget de tokens, compaction, provenance. Remplace l'idée d'un system prompt statique géant.
- **Peut faire :** interroger `memory`, `world_state`, `tasks` (état de la tâche courante), l'historique de conversation, les schémas d'outils pertinents ; appliquer un budget de tokens et une politique de compaction ; mettre en cache un assemblage récent si rien n'a changé (cf. `modules/context/recent_work.py` **EXTRACT**, cache TTL 30s).
- **Ne doit JAMAIS faire :** injecter TOUTE la mémoire ou TOUS les outils par défaut, décider de la logique métier, appeler le modèle lui-même (il prépare l'input, ne l'envoie pas), transmettre un `WorldStateFact` sans son statut de fraîcheur — toute section `kind=world_state` de `Context.sections[]` porte obligatoirement `freshness.status`/`freshness.as_of` (voir `RAYA_V2_CONTRACTS.md` §14) ; le Context Engine ne peut pas présenter un fait `stale` comme s'il était `active`.
- **Dépendances autorisées :** `memory` (lecture), `world_state` (lecture), `tasks` (lecture), `tools` (lecture des schémas, pas exécution).
- **Dépendances interdites :** `models`, `devices`, `interfaces`, `harness` (appelé PAR le Harness).
- **Données possédées :** aucune donnée durable — cache d'assemblage à courte durée de vie seulement.
- **Events émis :** aucun.
- **Events consommés :** `memory.entry_written/updated` (invalidation de cache), `world_state.updated` (idem).
- **API publique :** `assemble(task_id, budget) -> Context` (voir contrat).
- **Sync/async :** synchrone rapide dans le cas commun (cache hit), potentiellement async si récupération mémoire distante.
- **Persistence :** aucune.

### 1.8 `memory`

- **Responsabilité :** connaissances persistantes en couches (Working, Conversation, Personal, Project, Task, Experience). Cycle de vie candidate→active→confirmed→aging→obsolete. Isolation stricte de canal sauf partage explicitement permis.
- **Peut faire :** écrire/lire/corriger des `MemoryEntry` avec confidence/provenance ; appliquer les priorités de correction utilisateur (cf. `modules/identity/store.py` **ADAPT**, écriture explicite uniquement, bornée, dédoublonnée) ; exposer un accès cross-canal en lecture seule EXPLICITE (cf. `modules/context_bridge/bridge.py` **ADAPT**, jamais automatique).
- **Ne doit JAMAIS faire :** stocker l'état courant de l'environnement (→ world_state), stocker du bruit conversationnel/credentials temporaires/hypothèses non confirmées comme des faits, partager un canal vers un autre sans permission explicite.
- **Dépendances autorisées :** `persistence`, `observability`.
- **Dépendances interdites :** `models`, `tools`, `devices`, `context` (Context lit Memory, pas l'inverse), `harness`.
- **Données possédées :** toutes les `MemoryEntry` (facts/preferences/rules/experiences), l'historique de conversation par canal (Conversation Memory — migration directe de `modules/conversations/store.py` **ADAPT**).
- **Events émis :** `memory.entry_written`, `memory.entry_updated`, `memory.entry_obsoleted`.
- **Events consommés :** `task.completed` (pour extraire une Experience Memory si pertinent — décision explicite, jamais automatique en silence).
- **API publique :** `write(entry)`, `search(query, scope) -> list[MemoryEntry]`, `correct(entry_id, correction)`, `read_conversation(channel, conversation_id) -> ...` (isolation stricte appliquée ici).
- **Sync/async :** lecture/écriture principalement synchrone locale (SQLite), potentiellement async si backend distant (cf. `modules/conversations/backend.py` **ADAPT**, routing local/serveur avec repli).
- **Persistence :** OUI, c'est son cœur — SQLite (Conversation Memory) + store structuré (Personal/Experience Memory), via `persistence`.

### 1.9 `tasks` (Task Actors)

- **Responsabilité :** entités persistantes représentant du travail en cours ou planifié, avec lifecycle complet, indépendantes de la conversation qui les a déclenchées.
- **Peut faire :** créer/suivre/annuler/mettre en pause/reprendre un `Task` ; gérer dépendances entre tâches ; checkpoint périodique ; notifier la fin via event (consommé par Harness pour informer l'utilisateur, jamais en poussant directement vers une UI).
- **Ne doit JAMAIS faire :** exécuter la logique métier lui-même (délègue à `tools`/`harness`), décider de la priorité tout seul (Attention la lui fournit, Tasks la stocke et l'expose), bloquer la conversation (le §12 blueprint et la demande utilisateur sont explicites : conversation et tâche de fond sont indépendantes dans les deux sens).
- **Dépendances autorisées :** `persistence`, `observability`.
- **Dépendances interdites :** `models`, `devices`, `interfaces`, `harness` direct call vers Tasks pour EXÉCUTER (Tasks est un registre d'état, pas un exécuteur — l'exécution reste dans le Harness qui consulte/actualise le Task).
- **Données possédées :** table de `Task` avec état/checkpoint/dépendances/priorité/owner.
- **Events émis :** `task.created`, `task.started`, `task.progress`, `task.checkpoint`, `task.paused`, `task.resumed`, `task.cancelled`, `task.completed`, `task.failed`.
- **Events consommés :** `safety.stop_requested` (annulation coopérative — base existante dans `modules/async_engine/registry.py` **ADAPT**, à étendre avec persistance réelle).
- **API publique :** `create(objective, priority, owner) -> Task`, `get(task_id) -> Task`, `list(filters) -> list[Task]`, `checkpoint(task_id, state)`, `cancel(task_id)`, `pause(task_id)`, `resume(task_id)`.
- **Sync/async :** l'API de gestion est synchrone rapide (CRUD sur l'état) ; l'exécution réelle du travail est asynchrone, pilotée par le Harness.
- **Persistence :** OUI, obligatoire — c'est le manque principal de `modules/async_engine/registry.py` en V1 (tout en mémoire process, perdu au redémarrage).

### 1.10 `tools`

- **Responsabilité :** registre de capacités, découverte dynamique, validation de schéma, vérification de permission, exécution, retry/timeout/cancellation, audit. Les Tools sont des capacités, pas de l'intelligence.
- **Peut faire :** exposer un catalogue interrogeable par capability_tag (pas tout injecté au modèle — le Harness/Context Engine sélectionne le sous-ensemble pertinent), router l'exécution vers le `device` approprié, appliquer les contraintes de `safety` avant exécution, retourner un `ToolResult` structuré avec preuve quand possible.
- **Ne doit JAMAIS faire :** contenir de logique métier substantielle inline (c'est le défaut identifié dans plusieurs `_dispatch_*` de `core/tools.py` — la logique va dans les modules/devices, le Tool est un adaptateur mince), bypasser `safety`, exposer un outil sans schéma de validation.
- **Dépendances autorisées :** `devices`, `safety`, `models` (uniquement pour des tools dont la fonction EST d'appeler un modèle, ex: `document.summarize` — cas explicite et rare, jamais implicite), `observability`.
- **Dépendances interdites :** `harness` (appelé PAR le Harness), `memory`/`world_state` en écriture directe (un tool peut avoir besoin de LIRE le world_state pour ses paramètres, mais les mises à jour passent par les canaux normaux — perception/vérification).
- **Données possédées :** catalogue de `Tool` (schémas), pas de données utilisateur.
- **Events émis :** `tool.call_requested`, `tool.call_completed`, `tool.call_failed`.
- **Events consommés :** aucun (appelé en synchrone/awaited par le Harness).
- **API publique :** `discover(capability_tags) -> list[Tool]`, `call(ToolCall) -> ToolResult`.
- **Sync/async :** async par défaut (un tool peut prendre du temps — action navigateur, appel réseau), avec timeout obligatoire.
- **Persistence :** aucune propre ; l'audit trail des appels passe par `observability`/`persistence`.

### 1.11 `models` (Model Layer)

- **Responsabilité :** abstraction complète du/des fournisseurs de modèles. Le Core ne connaît qu'un contrat natif RAYA, jamais un format de provider spécifique.
- **Peut faire :** enregistrer des `ModelDescriptor` (capability, contexte max, coût, latence, local/cloud), router une `ModelRequest` vers le meilleur `ModelDescriptor` disponible selon la capability demandée, adapter le contrat natif vers le format du provider concret (Ollama Cloud, Ollama Local, futur) et inversement.
- **Ne doit JAMAIS faire :** exposer un format Anthropic-shaped (`type="tool_use"`, `stop_reason`) comme contrat PUBLIC (c'est l'erreur exacte de `core/llm.py` en V1) — ce format peut exister en interne dans un adapter Ollama si nécessaire pour compat historique, mais ne doit JAMAIS remonter au-delà de `models`. Ne doit jamais décider d'action métier (le modèle propose, le Harness/Cognition décide).
- **Dépendances autorisées :** `observability` (logging des appels), réseau (HTTP vers Ollama).
- **Dépendances interdites :** tout le reste — `models` est une feuille, rien au-dessus ne doit fuiter dedans.
- **Données possédées :** registre des `ModelDescriptor` disponibles, aucune donnée utilisateur.
- **Events émis :** `model.request_sent`, `model.response_received`, `model.stream_chunk`, `model.request_failed`.
- **Events consommés :** aucun (appelé en synchrone/awaited/streamed).
- **API publique :** `request(ModelRequest) -> ModelResponse` (ou stream d'événements pour le mode streaming).
- **Sync/async :** async, support natif du streaming.
- **Persistence :** aucune propre.

### 1.12 `safety`

- **Responsabilité :** classification de risque, politique de confirmation, politique de permission, STOP unifié, audit, rollback quand possible. C'est LE subsystem transversal auquel personne ne peut se soustraire.
- **Peut faire :** classifier une action proposée (safe/sensitive/destructive), exiger confirmation selon la politique, maintenir le signal STOP global multi-source (F12/Échap/voix/programmatique — reprise quasi telle quelle de `modules/computeruse/safety.py` **KEEP**), journaliser toute action sensible.
- **Ne doit JAMAIS faire :** être contourné par un canal (voix, navigateur, PC) qui déciderait seul d'annuler/autoriser. **Règle STOP unique (verrouillée après revue de cohérence, voir `RAYA_V2_ARCHITECTURE_FINAL_REVIEW.md` §2) :** aucune `interface` n'appelle jamais `safety.request_stop()` par import direct — c'est interdit par `interfaces` §1.14. Le chemin est TOUJOURS `Interface → Event (ex: interface.stop_requested) → EventBus → Safety (s'abonne) → met à jour son flag STOP interne synchrone`. Un canal peut donc DÉCLENCHER le STOP (en publiant un Event, jamais en import direct), mais aucun canal ne doit avoir sa PROPRE logique de permission parallèle, et aucun canal ne décide QUOI annuler (ça reste `attention`+`harness`, voir §3.3).
- **Dépendances autorisées :** `persistence` (audit trail), `observability`, l'EventBus (voir §13 — abonnement uniquement, pas une dépendance métier).
- **Dépendances interdites :** `models`, `devices`, `interfaces` (Safety est consulté par eux ou reçoit leurs Events, ne les appelle jamais).
- **Données possédées :** politiques de permission, table de risque par capability, audit trail, état STOP global (un seul, partagé, jamais dupliqué par canal).
- **Events émis :** `safety.stop_requested`, `safety.permission_denied`, `safety.confirmation_requested`, `safety.action_audited`.
- **Events consommés :** `interface.stop_requested` (et équivalents `task.stop_requested`/`device.stop_requested` si un canal a besoin de déclencher STOP sans dépendance directe) — consommé UNIQUEMENT pour mettre à jour le flag STOP interne (une fois, à la réception). Cette mise à jour est quasi instantanée car l'EventBus est in-process (voir §13), pas un aller-retour réseau. **La LECTURE de ce flag par le reste du système (`should_stop()`) reste et doit rester un appel synchrone direct, jamais une consultation d'event** — c'est ce qui garantit la priorité absolue, pas le canal de déclenchement.
- **API publique :** `classify_risk(action) -> RiskLevel`, `check_permission(action, context) -> Permission`, `request_stop(source)`, `should_stop() -> bool`, `audit(action, outcome)`.
- **Sync/async :** `should_stop()` DOIT être synchrone et quasi instantané (consulté à chaque étape critique de la boucle Harness — c'est un des invariants les plus stricts, voir `RAYA_V2_ARCHITECTURAL_INVARIANTS.md`).
- **Persistence :** audit trail persistant obligatoire ; état STOP en mémoire (transitoire par design, un redémarrage clean réinitialise le STOP).

### 1.13 `devices` (Device Agents)

- **Responsabilité :** exécuter les capacités demandées par `tools` en choisissant le mécanisme concret (UIA vs shell vs DOM vs vision). Ce sont les mains — jamais la tête.
- **Peut faire :** exposer une liste de `Capability` par device, exécuter une `Command`, retourner un `Result` avec preuve de vérification, choisir en interne le meilleur mécanisme selon une échelle de stratégies (cf. `modules/pc_control/router.py` **EXTRACT**, échelle DOM→UIA→vision), remonter sa `Health`/disponibilité.
- **Ne doit JAMAIS faire :** posséder une boucle de décision autonome qui appelle un modèle pour choisir SA prochaine action sans repasser par le Harness — c'est très exactement ce qui doit disparaître de `modules/pc_control/auto_agent.py` et `modules/meet/agent.py`. Un Device Agent exécute UNE commande à la fois, reçue de `tools`, jamais une séquence auto-décidée en interne au-delà du mécanisme d'exécution lui-même (ex : "attendre que la fenêtre apparaisse" est un détail mécanique acceptable, "décider quoi cliquer ensuite selon ce que je vois" ne l'est pas).
- **Dépendances autorisées :** OS/bibliothèques bas niveau (uiautomation, Playwright/CDP, pyautogui), `observability`.
- **Dépendances interdites :** `models` (un Device Agent n'appelle jamais un modèle lui-même — si une capacité a besoin de vision/raisonnement, elle remonte une demande structurée à `tools`/`harness`, qui orchestre l'appel modèle séparément), `memory`, `world_state` en écriture directe (les mises à jour passent par verification → harness → world_state), `safety` en lecture seule uniquement (consulte, ne modifie jamais la politique).
- **Données possédées :** état interne d'exécution transitoire (ex : session CDP ouverte), pas de données utilisateur.
- **Events émis :** `device.command_completed`, `device.command_failed`, `device.health_changed`.
- **Events consommés :** `safety.stop_requested` (doit interrompre immédiatement toute commande en cours).
- **API publique :** `list_capabilities() -> list[Capability]`, `execute(Command) -> Result`, `health() -> Health`.
- **Sync/async :** async, avec annulation coopérative obligatoire (vérification de `safety.should_stop()` à intervalles courts pendant l'exécution).
- **Persistence :** aucune propre.

### 1.14 `interfaces`

- **Responsabilité :** clients minces du Core (CLI, Web, Desktop, Mobile, Voice, API). Traduisent une requête utilisateur en `InterfaceRequest` vers le Harness, et une `HarnessState`/résultat en `InterfaceResponse` adaptée au canal.
- **Peut faire :** capter l'input utilisateur, afficher/vocaliser la sortie, gérer sa propre présentation (HUD, HTML, audio), maintenir un identifiant de session/canal.
- **Ne doit JAMAIS faire :** contenir de logique métier, appeler `models`/`tools`/`devices`/`safety` directement par import (violations confirmées en V1 : `modules/ghost/ghost_mode.py` appelle `core.llm` directement, `core/agent_state.py` pousse directement vers `ui.state`), décider elle-même QUOI annuler.
- **Dépendances autorisées :** `harness` (uniquement, pour toute requête/réponse), l'EventBus (pour PUBLIER un `Event`, ex: `interface.stop_requested` — voir §13), `observability`. **Publier un Event sur l'EventBus n'est pas une dépendance vers le subsystem qui le consomme** : une interface qui publie `interface.stop_requested` n'importe pas `safety`, elle importe seulement `contracts.Event` + l'EventBus (tous deux transversaux). C'est la distinction qui résout la tension entre "Interfaces ne parlent qu'au Harness" et "la voix peut déclencher STOP" — voir la règle verrouillée en §1.12.
- **Dépendances interdites :** tout appel de FONCTION direct vers `models`, `tools`, `devices`, `memory`, `world_state`, `tasks`, `safety` (aucun `import safety` ni `import models` etc. dans `interfaces/`) — tout passe soit par le Harness (requêtes/réponses), soit par un Event publié (déclenchements asynchrones type STOP).
- **Données possédées :** état de présentation local (ex : quel onglet HUD est ouvert), jamais de données métier durables.
- **Events émis :** `interface.request_received`.
- **Events consommés :** `harness.turn_completed` (pour afficher/vocaliser le résultat), `task.completed`/`task.failed` (pour notifier une tâche de fond terminée — le canal choisit COMMENT notifier, pas SI il faut le faire, ça c'est Attention).
- **API publique :** dépend du canal, mais toutes convergent vers `harness.handle_request(InterfaceRequest) -> stream/handle`.
- **Sync/async :** dépend du canal (Web = requête/réponse HTTP + SSE, Voice = streaming bidirectionnel, CLI = boucle synchrone locale).
- **Persistence :** aucune propre (délègue tout à `memory`/`tasks` via le Harness).

### 1.15 `persistence`

- **Responsabilité :** abstraction de stockage durable utilisée par `memory`, `tasks`, `world_state` (snapshots), `safety` (audit). Un seul concept de stockage exposé, backend interchangeable.
- **Peut faire :** CRUD versionné, migrations de schéma idempotentes (cf. `modules/conversations/store.py` **ADAPT**, bon précédent de migrations v2/v3/v4 sans perte), routing optionnel local/serveur central avec repli honnête (cf. `modules/conversations/backend.py` **ADAPT**), déploiement sûr en dry-run (cf. `core/deploy/updater.py` **EXTRACT** depuis git history).
- **Ne doit JAMAIS faire :** contenir de logique métier (ne sait pas ce qu'est une `Task` ou une `MemoryEntry`, seulement comment les stocker/récupérer par contrat sérialisé), perdre silencieusement des données lors d'une migration de schéma.
- **Dépendances autorisées :** stockage concret (SQLite, fichiers, futur serveur distant), `observability`.
- **Dépendances interdites :** tout subsystem métier (aucune connaissance de `memory`/`tasks`/`world_state` en tant que CONCEPTS, seulement en tant que schémas sérialisés génériques).
- **Données possédées :** les données elles-mêmes, au sens stockage physique (SQLite files, JSON stores).
- **Events émis :** `persistence.migration_applied`, `persistence.backend_unavailable` (bascule locale).
- **Events consommés :** aucun.
- **API publique :** `save(collection, id, payload)`, `load(collection, id) -> payload`, `query(collection, filters)`, `migrate(collection, target_version)`.
- **Sync/async :** majoritairement synchrone local (SQLite), async si backend distant.
- **Persistence :** c'est lui-même la couche persistence — pas de récursion.

### 1.16 `observability`

- **Responsabilité :** logging, audit trail, métriques, traçabilité de bout en bout (correlation_id à travers Event/Task/ToolCall/ModelRequest). N'influence jamais le flux de contrôle.
- **Peut faire :** écouter tous les events publiés par tous les subsystems, agréger, exposer un état de santé (`runtime` peut l'interroger au démarrage), fournir des rapports d'audit pour `safety`.
- **Ne doit JAMAIS faire :** bloquer ou modifier le comportement d'un subsystem (best-effort, jamais dans le chemin critique — si l'observability échoue, le reste du système continue), stocker de données métier interprétées (seulement des faits d'exécution : quand, quoi, combien de temps, succès/échec).
- **Dépendances autorisées :** `persistence` (stockage des logs/traces).
- **Dépendances interdites :** aucune (subsystem purement passif, écoute tout, n'appelle rien d'autre que persistence).
- **Données possédées :** logs structurés, traces, métriques.
- **Events émis :** aucun (consommateur pur).
- **Events consommés :** TOUS les events du système.
- **API publique :** `record(event)`, `trace(correlation_id) -> Timeline`, `health() -> SystemHealth`.
- **Sync/async :** async, best-effort, jamais bloquant (fire-and-forget avec buffer, perte tolérée en cas de surcharge plutôt que ralentir le système).
- **Persistence :** OUI, logs/traces/audit trail.

---

## 2. LE MOT-CLÉ QUI DISCRIMINE CHAQUE FRONTIÈRE

Pour trancher rapidement "où va ce code" pendant l'implémentation :

| Question | Si oui → |
|---|---|
| "Ça observe l'environnement sans jugement ?" | perception |
| "Ça répond à 'quel est l'état actuel de X' ?" | world_state |
| "Ça répond à 'qu'est-ce qu'on sait durablement sur Y' ?" | memory |
| "Ça décide si quelque chose mérite du traitement maintenant ?" | attention |
| "Ça décide COMMENT accomplir un objectif ?" | harness (+ cognition pour le raisonnement) |
| "Ça sélectionne quoi montrer au modèle ?" | context |
| "Ça représente du travail qui continue après ce tour de conversation ?" | tasks |
| "Ça expose une capacité que le modèle peut demander ?" | tools |
| "Ça choisit/appelle un modèle concret ?" | models |
| "Ça décide si une action est autorisée ?" | safety |
| "Ça manipule physiquement l'environnement (clic, fichier, requête HTTP) ?" | devices |
| "Ça parle à l'utilisateur (dans un sens ou l'autre) ?" | interfaces |
| "Ça stocke des octets durablement ?" | persistence |
| "Ça regarde ce qui s'est passé sans y participer ?" | observability |

---

## 3. AGENTIC HARNESS — la boucle en détail

### 3.1 Boucle nominale

```
EVENT (perception, interface, task scheduler)
   |
   v
ATTENTION.evaluate(event) → AttentionDecision
   |
   +-- IGNORE ────────────────────────────────────→ fin, rien ne se passe
   +-- BACKGROUND ──→ Task créé/mis à jour, pas de session interactive immédiate
   +-- INTERRUPT ───→ session courante mise en pause (checkpoint), nouvelle session prioritaire
   +-- PROCESS_NOW ─┐
                    v
        HARNESS.handle_request(HarnessRequest)
                    |
                    v
        1. Charger/créer HarnessState (session) — depuis persistence si reprise
                    |
                    v
        2. CONTEXT.assemble(task_id, budget) → Context
                    |
                    v
        3. Si ambiguïté potentielle : COGNITION.classify_intent / resolve_ambiguity
                    |            (bloquante → poser la plus petite question, via interfaces,
                    |             puis PAUSE le tour ; non-bloquante → continuer avec hypothèse)
                    v
        4. MODELS.request(ModelRequest assemblé depuis Context) → ModelResponse
                    |            (SAFETY.should_stop() vérifié avant ET après l'appel modèle)
                    v
        5. Si ModelResponse contient une décision d'action :
                    |
                    +-- Action = réponse directe → passer à l'étape 8
                    +-- Action = appel(s) d'outil → étape 6
                    +-- Action = création/délégation à une Task de fond → TASKS.create(...),
                    |            réponse immédiate à l'utilisateur ("je m'en occupe"),
                    |            le Harness continue en tâche de fond de façon indépendante
                    v
        6. Pour chaque outil demandé :
                    SAFETY.check_permission(action) → si refusé/confirmation requise,
                        remonter à l'utilisateur via interfaces, PAUSE
                    TOOLS.call(ToolCall) → délègue à DEVICES si nécessaire → ToolResult
                    (SAFETY.should_stop() vérifié avant chaque appel d'outil)
                    v
        7. COGNITION.verify(action, expected, observed) → SUCCESS | UNKNOWN | FAILURE
                    |
                    +-- SUCCESS → WORLD_STATE.apply_update(fact vérifié), continuer
                    +-- UNKNOWN → soit re-vérifier (retry borné), soit escalader à FAILURE
                    +-- FAILURE → COGNITION.propose_recovery() → nouvelle tentative bornée
                    |             ou remontée d'échec honnête à l'utilisateur (jamais de faux succès)
                    v
        8. CONTINUE (retour à l'étape 2 avec contexte rafraîchi) / REPLAN / COMPLETE
                    |
                    v
        9. HarnessState checkpointé (PERSISTENCE), event harness.turn_completed émis
                    |
                    v
        10. INTERFACES reçoit l'event, présente le résultat au canal d'origine
```

### 3.2 Qui possède chaque responsabilité transversale

| Responsabilité | Propriétaire | Détail |
|---|---|---|
| **Cancellation** | `tasks` (état) + `safety` (signal) + `harness` (vérification aux points de contrôle) | `safety.should_stop()` consulté avant CHAQUE appel modèle/outil/device ; `tasks.cancel(id)` marque l'état, le Harness observe et arrête proprement à la prochaine vérification |
| **Pause/Resume** | `harness` (checkpoint HarnessState) + `tasks` (état PAUSED) | Pause = checkpoint immédiat + arrêt propre ; Resume = rechargement du dernier checkpoint valide depuis `persistence` |
| **Checkpoint** | `harness` écrit, `persistence` stocke | À chaque étape significative de la boucle (pas à chaque token — coût), au minimum avant/après chaque appel outil |
| **Recovery** | `harness` orchestre, `cognition.propose_recovery()` décide de l'alternative | Après crash/redémarrage : `harness` recharge le dernier `HarnessState` checkpointé et reprend à l'étape appropriée, jamais depuis zéro silencieusement. **Pour toute action à effet EXTERNE (fichier, requête réseau, action device), la reprise passe obligatoirement par le contrat `ExecutionRecord` (voir §14) avant tout retry — un crash entre l'exécution et le checkpoint ne doit jamais provoquer une double exécution silencieuse.** |
| **Retries bornés** | `harness` compte, `tools`/`models` exécutent | Limite explicite par type d'échec (ex : max 3 retries outil, max 2 replans cognition) — jamais de boucle infinie |
| **Tool failure** | `tools` retourne un `ToolResult` structuré avec erreur ; `harness`+`cognition` décident de la suite | Jamais de faux succès — un tool qui échoue le dit explicitement |
| **Model failure** | `models` retourne une erreur typée (timeout/rate-limit/refus) ; `harness` décide fallback (autre provider via `models.request` avec contrainte relâchée) ou remontée honnête |
| **User steering** (mid-turn) | `interfaces` reçoit l'instruction, `harness.steer(session_id, instruction)` l'injecte dans le tour en cours | La session en cours intègre l'instruction au prochain point de contrôle, sans redémarrer depuis zéro |
| **Task priority** | `attention` fournit un score, `tasks` le stocke, `harness` le consulte pour l'ordonnancement de plusieurs sessions concurrentes |
| **Concurrency** | `harness` peut gérer plusieurs `HarnessState` simultanément (une par session/tâche) — AUCUN lock global façon `core.orchestrator._process_lock` de V1 (identifié comme limitation connue et acceptée en V1, à lever explicitement en V2 : conversation et tâche de fond doivent tourner en parallèle) |
| **Persistence** | Chaque subsystem persiste SES données via `persistence` ; le Harness persiste l'état d'exécution (`HarnessState`), jamais les données métier directement |
| **Context refresh** | `context.assemble()` est rappelé à chaque itération de la boucle (étape 2), jamais un contexte figé au début du tour — c'est ce qui permet au steering et aux mises à jour world_state d'être pris en compte en cours de route |

### 3.3 Ce qui NE doit jamais exister à côté du Harness

Aucun composant ne doit implémenter sa propre variante de cette boucle. Concrètement, en V2, il ne doit plus exister d'équivalent à :
- `modules/pc_control/auto_agent.py` (boucle vision→décision→action indépendante) → sa capacité "PC autonome" devient une **Task** exécutée PAR le Harness, qui invoque `models` (capability vision+reasoning) et `tools`→`devices` (Windows Agent) normalement.
- `modules/ghost/ghost_mode.py` (bypass total) → devient une **Interface** légère qui appelle `harness.handle_request()` avec un profil "réponse rapide, contexte minimal" explicite dans le `HarnessRequest`, jamais un accès direct à `models`.
- `modules/voice/continuous_voice.py` décidant seule d'annuler des tâches → publie UNIQUEMENT un `Event` (`interface.stop_requested`, jamais un appel direct `safety.request_stop()` — voir la règle verrouillée en §1.12/§1.14) et ne décide plus elle-même QUELLES tâches annuler — c'est `attention`+`harness` qui traduisent le signal STOP en action sur les `Task` concernées.
- `modules/meet/agent.py` → même traitement que voice : devient un canal `interfaces` qui pousse des events vers le Harness, pas un agent autonome.

---

## 4. MODEL LAYER

### 4.1 Architecture

```
Harness / Cognition
        |
        v
  ModelRequest (contrat natif RAYA — voir RAYA_V2_CONTRACTS.md)
        |
        v
  MODEL REGISTRY   — connaît tous les ModelDescriptor disponibles
        |
        v
  MODEL ROUTER      — sélectionne le meilleur descriptor pour la requête
        |
        v
  PROVIDER ADAPTER  — traduit ModelRequest → format du provider, et retour
        |
        +---------------------+---------------------+
        v                     v                     v
  Ollama Cloud Adapter   Ollama Local Adapter   [futur provider]
        |
        v
  ModelResponse (contrat natif RAYA)
```

### 4.2 `ModelCapability`

Énumération ouverte (extensible sans casser le contrat) : `reasoning`, `planning`, `coding`, `vision`, `fast_response`, `classification`, `summarization`, `embedding` (si besoin futur). Une requête déclare la/les capability(ies) requises, jamais un nom de modèle en dur — c'est la règle qui manque aujourd'hui dans tout le code V1 qui référence `OLLAMA_PC_MODEL`/`OLLAMA_CODE_MODEL` en dur.

### 4.3 `ModelDescriptor` (enregistré dans le Registry)

Champs : `id`, `provider` (ollama_cloud|ollama_local|...), `capabilities: list[ModelCapability]`, `context_limit`, `supports_streaming`, `supports_tool_calls`, `supports_vision`, `estimated_latency_ms`, `cost_per_1k_tokens` (0 pour local), `vram_requirement_mb` (pour le routing local), `availability_check() -> bool`.

### 4.4 Model Router — critères de scoring

Ordre de priorité par défaut (configurable) :
1. **Capability match** — élimine tout descriptor qui ne supporte pas la capability demandée (filtre dur, pas un score).
2. **Availability** — un modèle indisponible (Ollama Cloud down, VRAM insuffisante en local) est éliminé.
3. **Explicit constraint** du `ModelRequest` (ex : `require_local: true` pour offline, `max_latency_ms`).
4. **Score pondéré** : qualité déclarée pour la capability > latence > coût, pondérations ajustables.

V1 n'a qu'un routing binaire "code vs général" par regex (`modules/brain/router.py`) — c'est un cas particulier valide du routing par capability (`capability=coding` → descriptor spécialisé), pas un mécanisme séparé à garder tel quel.

### 4.5 Ce qui est explicitement INTERDIT comme contrat

Le contrat `ModelRequest`/`ModelResponse` natif RAYA (détaillé dans `RAYA_V2_CONTRACTS.md`) ne doit **jamais** exposer :
- des blocs `type="tool_use"` façon Anthropic SDK
- un champ `stop_reason` avec les valeurs Anthropic (`end_turn`, `max_tokens`, `tool_use`) — RAYA définit son propre vocabulaire de fin de génération
- une structure `SimpleNamespace` imitant un SDK tiers

Ces formats peuvent exister STRICTEMENT à l'intérieur de l'implémentation d'un Provider Adapter donné s'il s'avère que le provider concret les utilise en interne — mais rien au-dessus de la couche adapter ne doit jamais les voir.

### 4.6 Connaissances opérationnelles V1 à préserver (pas l'architecture)

- `think=False` requis explicitement pour `deepseek-v4-flash:cloud` (bug empirique documenté en V1) → à encoder comme paramètre du `OllamaCloudAdapter`, pas comme cas spécial dans le Harness.
- Mapping `done_reason="length"` → statut natif RAYA équivalent à "tronqué par limite de tokens".
- Parsing du streaming Ollama (tool_calls incrémentaux) → logique interne de l'adapter.
- Fallback multi-provider (RoutedMessages) → généralisé en règle de routing standard du Model Router (pas un mécanisme séparé).

---

## 5. MEMORY vs WORLD STATE vs CONTEXT — la frontière absolue

C'est la distinction la plus fréquemment mal comprise pendant l'implémentation ; elle est donc traitée séparément avec des exemples exhaustifs.

| | World State | Memory | Context Engine |
|---|---|---|---|
| Répond à | "Qu'est-ce qui est vrai MAINTENANT dans l'environnement ?" | "Qu'est-ce qu'on SAIT durablement ?" | "Qu'est-ce que le modèle a BESOIN DE VOIR pour CETTE tâche ?" |
| Durée de vie | Courte, avec freshness/expiration | Longue, avec lifecycle candidate→obsolete | Nulle — recalculé à chaque appel modèle |
| Écrit par | `perception` + actions vérifiées | Écriture explicite (utilisateur, correction, ou décision `harness`/`cognition` déliberée) | Jamais écrit — c'est une fonction de lecture/sélection |
| Exemple | "Chrome est actuellement ouvert" | "Ruben préfère Chrome" | "Pour cette tâche (ouvrir un lien), Chrome est pertinent → inclus dans le contexte" |
| Exemple | "La tâche #42 est en cours d'exécution" | "Ruben corrige souvent RAYA quand elle est trop verbeuse" | "Cette tâche ressemble à la #38 d'hier → inclure son résumé" |
| Exemple | "Batterie à 15%" | "Le portable de Ruben tient mal la charge (observation répétée)" | Non inclus sauf si la tâche courante concerne l'alimentation |
| Composant V1 le plus proche | `modules/raya_state/state.py` (embryon, à reconstruire avec schéma formel) | `modules/memory/store.py` + `modules/conversations/store.py` + `modules/identity/store.py` | `modules/context/recent_work.py` (meilleur exemple existant du principe) |

**Règle de câblage :** `context` LIT `memory` et `world_state`, jamais l'inverse. `memory` et `world_state` ne se lisent pas mutuellement (aucune dépendance directe entre eux — un fait de World State qui devient durable ne "migre" pas automatiquement vers Memory, c'est toujours une décision explicite, généralement déclenchée par `cognition`/`harness` après un tour, jamais silencieuse).

---

## 6. TASK ACTORS — détail complet

Voir `RAYA_V2_CONTRACTS.md` pour le schéma exact du contrat `Task`. Ici, les règles de fonctionnement :

- **Lifecycle :** `PENDING → RUNNING → (PAUSED ⇄ RUNNING) → (COMPLETED | FAILED | CANCELLED)`. Un état terminal (COMPLETED/FAILED/CANCELLED) ne redevient jamais actif — invariant repris explicitement de V1 (`RAYA_FINAL_AUDIT.md` : "tâche annulée reste CANCELLED, pas de réactivation").
- **Cancellation :** coopérative — `tasks.cancel(id)` positionne l'intention, le worker qui exécute la tâche (dans le Harness) doit vérifier `safety.should_stop()` OU un signal de cancellation spécifique à la tâche à intervalles courts et s'arrêter proprement, avec checkpoint de ce qui a été accompli.
- **Pause/Resume réel** (pas juste "recommencer depuis le début", limitation actuelle de `resume_info` dans `modules/async_engine/registry.py`) : Pause = checkpoint complet de l'état d'exécution ; Resume = rechargement de CE checkpoint précis, reprise à l'étape suivante.
- **Checkpoint :** structure sérialisable de "où j'en suis" — objectif, étapes accomplies, résultat intermédiaire, prochaine étape prévue.
- **Dependencies :** une `Task` peut déclarer `depends_on: list[task_id]` — le Harness ne démarre l'exécution qu'une fois les dépendances en état COMPLETED (ou explicitement ignore si le mode le permet).
- **Priority :** fournie par `attention`, stockée sur le `Task`, consultée par le Harness pour l'ordonnancement quand plusieurs sessions sont concurrentes.
- **Events :** voir catalogue en §1.9 — chaque transition d'état émet un event, consommé par `interfaces` (pour notifier) et `observability` (pour l'audit).
- **Persistence :** obligatoire dès la création, pas seulement au checkpoint — un `Task` doit survivre à un redémarrage du process dans un état cohérent (au minimum PENDING/PAUSED, avec reprise explicite plutôt qu'automatique silencieuse si le crash a eu lieu en plein RUNNING).
- **Ownership :** chaque `Task` a un `owner` (quel canal/session l'a créée) — utilisé pour le routing des notifications, PAS pour restreindre la visibilité de façon absolue (une tâche de fond créée depuis la voix doit pouvoir être consultée depuis le chat, cf. le principe déjà présent dans `modules/raya_state/state.py` de visibilité cross-canal des actions).
- **Result/Error :** structure typée, jamais une simple chaîne de caractères libre — permet au Harness de décider programmatiquement de la suite (retry, escalade, etc.).

**Invariant central demandé explicitement :** une conversation ne bloque jamais une Task de fond, et une Task de fond ne bloque jamais une conversation. Concrètement : le Harness doit pouvoir avoir une `HarnessState` "conversation" ACTIVE et une `HarnessState` "tâche de fond" RUNNING simultanément, chacune avec son propre appel modèle indépendant si nécessaire — ce qui exige l'abandon du lock global `_process_lock` présent en V1.

---

## 7. ATTENTION — détail complet

### 7.1 Les 4 décisions possibles

| Décision | Signifie | Effet |
|---|---|---|
| `PROCESS_NOW` | Mérite un tour de Harness immédiat, potentiellement interactif | Le Harness démarre/continue une session |
| `BACKGROUND` | Mérite d'être traité, mais pas immédiatement ni de façon interactive | Une `Task` est créée/mise à jour, pas de session interactive |
| `INTERRUPT` | Mérite de couper le focus courant | La session en cours est checkpointée/pausée, une nouvelle session prioritaire démarre |
| `IGNORE` | Ne mérite aucun traitement | Rien ne se passe — "silence is a valid outcome" (blueprint §7) |

### 7.2 Facteurs d'entrée (repris du blueprint §7, ancrés sur des précédents V1)

`urgency` (ex : alerte batterie critique), `importance`, `novelty` (déjà vu récemment ? cf. cache TTL de `recent_work.py`), `confidence` (perception fiable ?), `cost` (coût d'un traitement complet vs signal faible), `user_relevance`, `current_focus` (mode idle/game/call/coding — cf. `modules/context/dnd.py`), `task_priority` (des `Task` en cours), `interruption_policy` (whitelist d'actions sûres à exécuter directement sans interruption, cf. `modules/awareness/monitor.py`).

### 7.3 Ce qu'Attention n'est PAS

Attention **ne redécide pas comment faire**. Une fois qu'elle a rendu `PROCESS_NOW`, elle transmet l'event au Harness et n'intervient plus dans l'exécution de ce tour — pas de deuxième niveau de dispatch, pas de logique métier. C'est la distinction demandée explicitement par l'utilisateur : *"L'Attention ne doit pas devenir un deuxième orchestrateur. Elle décide CE QUI mérite de l'attention. Le Harness décide COMMENT l'exécuter."*

---

## 8. TOOLS — détail complet

### 8.1 Pipeline d'un appel d'outil

```
Harness demande une capability (pas un nom de tool en dur)
        |
        v
TOOL DISCOVERY   — TOOLS.discover(capability_tags) → sous-ensemble pertinent
        |            (le modèle ne voit QUE ce sous-ensemble, pas les 83 outils)
        v
TOOL VALIDATION  — schéma d'entrée validé avant tout appel
        |
        v
PERMISSION CHECK — SAFETY.check_permission(action) → autorisé / confirmation requise / refusé
        |
        v
EXECUTION        — délégué au DEVICE approprié (ou exécution locale pure pour les tools
        |            sans device, ex : calculatrice)
        v
VERIFICATION     — COGNITION.verify() confirme le résultat attendu vs observé
        |
        v
RETRY (si échec, borné) ou TOOL RESULT structuré retourné au Harness
        |
        v
AUDIT            — OBSERVABILITY enregistre l'appel (paramètres, résultat, durée, permission)
```

### 8.2 Catalogue de départ

Les 83 schémas identifiés dans `core/tools.py` (V1) constituent le catalogue de capacités de référence — chacun devient un `Tool` avec un `capability_tag` explicite (ex : `document.generate`, `browser.navigate`, `pc.application_launch`, `calendar.create_event`). La logique métier actuellement inline dans certains `_dispatch_*` (ex : `_dispatch_display_info` qui contient sa propre recherche web) est extraite vers de vrais modules `devices`/services, le `Tool` en V2 ne fait plus que valider+déléguer+vérifier.

### 8.3 Exposition sélective au modèle

Le principe `core/session_mode.py` (gating par mode TALK/KNOW/ACT, **EXTRACT**) est généralisé : le Context Engine (pas un système de modes fixes) décide, pour CHAQUE appel modèle, quel sous-ensemble de `Tool` est pertinent selon l'objectif courant — c'est une fonction de `context.assemble()`, pas une liste statique par mode.

---

## 9. DEVICE AGENTS — détail complet

### 9.1 Interface générique

Voir `RAYA_V2_CONTRACTS.md` pour `Device`, `Capability`, `Command`, `Result`, `Health`. Principe : le Harness/Tools demande `application.launch(name="chrome")`, jamais `uiautomation.FindControl(...)`. Le Windows Agent choisit UIA vs shell vs registre selon ce qui marche pour ce cas précis (logique d'échelle héritée de `modules/pc_control/router.py`, **EXTRACT**).

### 9.2 Windows Agent (V1 → V2)

Mécanismes **EXTRACT** de `modules/pc_control/` : `elements.py`, `keyboard.py`, `mouse.py`, `clipboard.py`, `windows.py`, `applications.py`, `wait.py`, `vision_bridge.py`, plus `modules/appmanager/` (winget), `modules/apps/` (scan+lancement flou), `modules/system/*`, `modules/media/controller.py`, base `core/platform/windows.py`. Capacités exposées type : `application.launch/close/list`, `window.find/focus/move`, `input.click/type/scroll/drag`, `process.inspect/start/kill`, `file.read/write` (via filesystem local), `system.get_resources`, `package.search/install/uninstall`.

### 9.3 Browser Agent (V1 → V2)

Mécanismes **EXTRACT** de `modules/browser/session.py` (cycle de vie CDP, attache au VRAI navigateur utilisateur, jamais de profil miroir), `worker.py` (sérialisation thread Playwright), `controller.py` (navigate/click/type/read_page/fill_form + garde-fous champs sensibles). La logique de `recipes.py` (séquences figées par site) devient des CAPACITÉS composables que le Harness orchestre avec vérification/replanning à chaque étape, pas des scripts monolithiques par domaine.

### 9.4 Camera/Vision Agent (V1 → V2)

**ADAPT** de `modules/vision/{stream_manager,camera_registry,object_detection,ocr,face_recognition,analyze}.py`, `modules/camera/stream.py` (capture à la demande — bon modèle à garder), `modules/face/` (capture on-demand + `verify_is_owner()` comme primitive de permission). `presence_watcher.py` doit être **REBUILD** en capteur léger non-LLM (diff de frame simple → event `perception.presence_changed`), jamais en boucle d'analyse continue.

### 9.5 Agents futurs (non prioritaires V2 initial)

Linux Agent (base `core/platform/linux.py`, déjà présent), iOS Agent (base `modules/ios/api.py`, **ADAPT**). Non-goals explicites pour la Phase 4 initiale sauf besoin confirmé.

---

## 10. SAFETY — détail complet

### 10.1 Ce qui survit quasi tel quel

`modules/computeruse/safety.py` (**KEEP**) — signal STOP unifié multi-source (F12, Échap, commande vocale, appel programmatique), consulté par de très nombreux points de la boucle en V1 (7 sites rien que dans l'orchestrateur). Ce mécanisme devient la fondation du subsystem `safety` V2, avec deux ajouts :
1. Un **audit trail persistant** (absent en V1 — le STOP est vérifié mais pas systématiquement journalisé avec contexte).
2. Une **classification de risque généralisée** — V1 a `modules/pc_control/engine.py::_RISK` (table de risque locale au PC Control uniquement) ; V2 généralise ce principe à TOUTE capacité passant par `tools`, pas seulement PC.

### 10.2 Politique de confirmation

Reprise du principe blueprint §18 : actions sûres/réversibles à faible friction, actions à fort impact/destructives/sensibles avec permission renforcée. Précédents V1 à généraliser : `modules/browser/controller.py` (refus champs mot de passe/carte, warning URLs sensibles), `modules/appmanager/safety.py` (whitelist composants protégés contre désinstallation), `modules/face/manager.py::verify_is_owner()` (permission par présence physique vérifiée pour actions sensibles).

### 10.3 Règle absolue

Aucun canal (`interfaces`), aucun `device`, aucune `Task` ne possède sa propre logique de permission parallèle. Tous consultent `safety.check_permission()`. Le comportement V1 de `continuous_voice.py` (n'importe quel canal peut demander l'arrêt) est gardé, mais son MÉCANISME change : en V2 il publie un `Event` consommé par `safety` (jamais un appel direct à `safety.request_stop()`, voir §1.12/§1.14) — et il ne décide toujours pas lui-même QUOI annuler ni ne bypasse jamais une vérification de permission pour ses propres actions.

---

## 11. PERCEPTION — détail complet

### 11.1 Capteurs légers autorisés en continu

Fenêtre active/process au premier plan (`modules/context/monitor.py`, poll ~3s, **EXTRACT**), ressources système CPU/RAM/batterie (`modules/hud/monitor.py`, **EXTRACT**), USB/réseau/notifications (à construire, base `core/platform/`), géolocalisation à la demande plutôt qu'en continu (`modules/location/`, **KEEP** — déjà on-demand en V1).

### 11.2 Perception lourde — strictement on-demand

Vision (webcam/screenshot/analyse image), reconnaissance faciale, OCR — déclenchées UNIQUEMENT par une demande explicite remontant d'`attention`/`harness`/un `tool`. Aucun polling permanent vers un LLM. `modules/camera/stream.py` (capture on-demand JS↔Python, **EXTRACT**) est le bon modèle à généraliser ; `modules/vision/presence_watcher.py` (boucle daemon continue analysant la webcam, **REBUILD**) est l'exemple à ne PAS reproduire.

### 11.3 Interdits explicites (repris mot pour mot de la demande)

- Webcam permanente : **interdite**.
- Screen permanent : **interdit**.
- Micro permanent en transcription : **interdit**. Le VAD (détection d'activité vocale, léger, non-sémantique) peut tourner en continu — c'est un capteur léger (silence vs voix) ; la TRANSCRIPTION (Whisper) ne se déclenche que sur un segment de parole détecté, jamais en continu sur du silence.

---

## 12. INTERFACES — détail complet

### 12.1 Ordre d'implémentation (headless-first, repris du blueprint §4/§12)

CLI/API/test harness d'abord — suffisant pour valider tout le Core sans aucune UI. Web ensuite (ADAPT direct de `modules/web/server.py`, déjà bien découplé). Desktop/Mobile/Voice après, une fois le Core stable.

### 12.2 Contrat de câblage

```
Interface (CLI/Web/Desktop/Mobile/Voice)
        |
        v  InterfaceRequest { channel, session_id, input, attachments? }
        |
   HARNESS.handle_request()
        |
        v  HarnessState / events
        |
Interface affiche/vocalise   ← jamais l'inverse (Core → UI implementation directe interdite)
```

Contre-exemples V1 à corriger explicitement : `core/agent_state.py` pousse directement vers `ui.state` (à remplacer par un event consommé par l'interface Desktop) ; `modules/camera/stream.py` importe `ui.state` directement (à remplacer par le contrat `Device`/`Capability` générique) ; `modules/ghost/ghost_mode.py` appelle `core.llm` directement (à remplacer par un appel Harness standard avec un profil de requête "rapide").

---

## 13. EVENT BUS — mécanisme transversal

**Ajouté lors de la revue de cohérence finale (`RAYA_V2_ARCHITECTURE_FINAL_REVIEW.md` §1) — le concept d'`Event` était omniprésent (catalogue par subsystem en §1) mais son mécanisme de transport n'était pas explicité. Ce qui suit comble ce vide.**

L'EventBus n'est **pas un 17e subsystem métier** — c'est une infrastructure transversale, instanciée par `runtime/bootstrap.py` et injectée dans tous les subsystems qui publient ou consomment des `Event`. Comme `contracts/`, tout le monde peut en dépendre ; contrairement à un subsystem métier, il ne possède aucune logique de décision, aucune connaissance du domaine RAYA — il route des `Event` par `type`, rien de plus. **Règle absolue : l'EventBus ne devient jamais un second orchestrateur.** Il ne lit jamais `payload`, ne prend jamais de décision métier, ne retient jamais un event pour le "comprendre" — il livre, point.

### 13.1 API

```
EventBus {
  publish(event: Event) -> void
      # retourne immédiatement (fire-and-forget côté appelant) ; la livraison
      # aux abonnés se fait sur les threads/tâches propres du bus, jamais sur
      # le thread de l'appelant au-delà de la politique de backpressure (13.3)

  subscribe(event_type_pattern: string, handler: Callable[[Event], None],
            subscriber: string) -> SubscriptionHandle
      # event_type_pattern supporte un wildcard de préfixe, ex: "task.*"

  unsubscribe(handle: SubscriptionHandle) -> void
}
```

### 13.2 Propagation et correlation_id

Un `Event` conserve son `correlation_id` tout au long de sa vie sur le bus — l'EventBus ne le réécrit jamais. La livraison est **ordonnée par `(correlation_id, subscriber)`** (deux events de la même chaîne causale arrivent dans l'ordre chez un même abonné), mais **pas globalement ordonnée** entre des `correlation_id` différents — ce serait un goulot d'étranglement inutile et une source de couplage temporel involontaire entre sessions indépendantes.

**Cancellation :** l'EventBus ne filtre jamais un `Event` au prétexte que sa `Task`/session d'origine est annulée — il continue de le livrer. C'est la responsabilité de CHAQUE abonné (déjà spécifiée par subsystem en §1) de vérifier l'état pertinent avant d'agir. Un bus qui filtrerait par état métier deviendrait précisément le "second orchestrateur" à éviter.

### 13.3 Backpressure

Chaque abonné a une file d'entrée bornée. Deux politiques possibles, déclarées explicitement à l'abonnement (pas de défaut implicite silencieux) :

- **`drop_oldest`** — pour les abonnés best-effort où la perte est tolérable (`observability`, principalement — cohérent avec §1.16 "best-effort, jamais bloquant").
- **`block_publisher_with_timeout`** — pour les abonnés dont la perte d'un event serait dangereuse (`safety` en premier lieu : un `interface.stop_requested` ne doit jamais être silencieusement perdu). Le timeout est court (l'EventBus est in-process, pas un système distribué avec latence réseau) — un dépassement de timeout sur un abonné `block_publisher_with_timeout` doit lever une alerte `observability` de sévérité haute, pas juste logger et continuer.

### 13.4 Ce qui reste explicitement HORS de l'EventBus

- Aucune requête/réponse synchrone ne passe par l'EventBus (ça, c'est le rôle des appels directs autorisés par le graphe de dépendance — `harness.handle_request()`, `tools.call()`, etc.). L'EventBus sert exclusivement les communications asynchrones découplées (notifications, déclenchements cross-subsystem type STOP).
- La lecture de `safety.should_stop()` ne passe JAMAIS par l'EventBus (voir §1.12) — seule la mise à jour initiale du flag, déclenchée par un canal découplé, transite par un Event. Ceci préserve la garantie de latence synchrone de la vérification STOP.

---

## 14. IDEMPOTENCE / CRASH RECOVERY — exécution externe sûre

**Ajouté lors de la revue de cohérence finale (`RAYA_V2_ARCHITECTURE_FINAL_REVIEW.md` §3) — le mécanisme pause/resume/checkpoint (§3.2) ne suffisait pas à garantir qu'une action à EFFET EXTERNE (téléchargement, envoi, écriture fichier, action device) ne soit jamais rejouée aveuglément après un crash survenu entre son exécution et son checkpoint.**

### 14.1 Principe

Un checkpoint `Task`/`HarnessState` capture "où j'en suis dans le PLAN". Il ne garantit PAS, à lui seul, que la DERNIÈRE action à effet externe a bien été effectuée exactement une fois. Entre le moment où `tools.call()` déclenche un `Device.execute(Command)` et le moment où le `HarnessState` correspondant est checkpointé, un crash laisse le système dans un état où l'action a peut-être eu lieu, peut-être pas — et il est **interdit de deviner**.

### 14.2 Mécanisme

Voir `RAYA_V2_CONTRACTS.md` §18 pour le contrat `ExecutionRecord` complet. Résumé du flux :

```
1. harness/tools génère un operation_id STABLE pour CETTE action logique
   (le même operation_id est réutilisé à chaque retry de la MÊME action —
   ce n'est jamais un nouvel ID par tentative)
2. AVANT d'appeler le Device : persistence.save(ExecutionRecord{
       operation_id, execution_state=EXECUTING, idempotency_key, started_at
   })  — écriture synchrone, doit réussir avant l'étape 3
3. tools délègue au Device (Command porte operation_id + idempotency_key)
4. Device exécute, retourne un Result
5. persistence.save(ExecutionRecord{ ..., execution_state=COMPLETED|FAILED,
       completed_at })
```

Si un crash survient entre l'étape 2 et l'étape 5, le `ExecutionRecord` rechargé au redémarrage est trouvé en `EXECUTING` — **jamais interprété comme COMPLETED ni comme NOT_STARTED**, toujours comme `UNKNOWN` (voir contrat).

### 14.3 Règle de récupération pour un `ExecutionRecord` en état `UNKNOWN`

```
SI Tool.idempotent == true :
    → retry direct autorisé, avec le MÊME idempotency_key (le Device/provider
      externe déduplique nativement si l'API sous-jacente le supporte, ex:
      en-tête Idempotency-Key HTTP ; sinon le mécanisme du Device doit
      lui-même vérifier avant de rejouer, ex: "le fichier existe-t-il déjà
      avec la bonne taille/checksum ?")

SINON (Tool.idempotent == false) :
    → cognition.verify() est appelé AVANT toute décision, avec une
      vérification SANS EFFET DE BORD si le Device en expose une
      (ex: interroger world_state/le device pour une preuve que l'action a
      eu lieu — un fichier présent, un email dans "Envoyés", une fenêtre
      ouverte)
    → SI vérifiable et VERIFIED_SUCCESS : marquer COMPLETED, continuer
       normalement, ne JAMAIS réexécuter
    → SI vérifiable et VERIFIED_FAILURE : l'action n'a pas eu lieu, retry
       autorisé (c'est un cas NOT_STARTED de facto)
    → SI UNVERIFIABLE (aucun moyen de vérifier sans effet de bord) :
       ESCALADE à l'utilisateur via `cognition.resolve_ambiguity()` (cas
       "ambiguïté bloquante", §9 de l'architecture) — ne JAMAIS retry
       silencieusement, ne JAMAIS marquer COMPLETED par optimisme.
```

**Règle absolue : une action `idempotent=false` en état `UNKNOWN` et `UNVERIFIABLE` n'est JAMAIS rejouée automatiquement.** C'est un cas d'ambiguïté bloquante par définition — la petite question à poser à l'utilisateur est du type "je ne suis pas sûr que le fichier ait fini de se télécharger avant l'interruption, je vérifie/relance ?" plutôt qu'un choix silencieux dans un sens ou l'autre.

### 14.4 Propriétaire

`harness` orchestre la vérification et la décision (via `cognition`), `tools`/`devices` portent `operation_id`/`idempotency_key` de bout en bout sans les réinterpréter, `persistence` garantit que l'écriture de `execution_state=EXECUTING` (étape 2 ci-dessus) est durable AVANT le déclenchement de l'action externe — pas après, pas en best-effort.

---

**Fin de l'architecture technique.** Voir `RAYA_V2_CONTRACTS.md` pour les schémas de données exacts, `RAYA_V2_REPOSITORY_STRUCTURE.md` pour la traduction en arborescence de fichiers, `RAYA_V2_MIGRATION_PLAN.md` pour le séquencement, `RAYA_V2_ARCHITECTURAL_INVARIANTS.md` pour la liste d'invariants testables, `RAYA_V2_ARCHITECTURE_FINAL_REVIEW.md` pour la revue de cohérence qui a produit les §13/§14 et les clarifications STOP des §1.12/§1.14/§3.3/§10.3.
