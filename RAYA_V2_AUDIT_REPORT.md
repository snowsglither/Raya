# RAYA V1 → V2 — AUDIT ARCHITECTURAL & RAPPORT DE MIGRATION

**Statut :** Analyse terminée. Aucun code modifié, supprimé, renommé ou déplacé.
**Source de vérité architecture cible :** `RAYA_V2_ARCHITECTURAL_BLUEPRINT.md` (même dossier).
**Document compagnon :** `RAYA_V2_MIGRATION_MAP.md` — tableau complet des ~80 composants audités avec verdict KEEP/EXTRACT/ADAPT/REBUILD/DELETE.
**Méthode :** 4 audits parallèles avec grep des imports réels (pas seulement l'arborescence), lecture de code, vérification git history pour les composants apparemment "morts". Repo audité : `C:\Users\ruben\OneDrive\Bureau\RAYA`.

---

## 1. EXECUTIVE SUMMARY

RAYA V1 est un système **fonctionnel mais monolithique**. Il fait réellement ce que le README annonce (voix, contrôle Windows, navigateur, mémoire, 3D), mais toute la logique de décision vit dans un seul fichier de 2583 lignes (`core/orchestrator.py`) qui mélange exactement les responsabilités que le blueprint exige de séparer : mémoire, sécurité, attention, model layer, tasks et interfaces dans une seule classe.

**Trois découvertes structurantes :**

1. **Trois systèmes appelés "cerveau" coexistent, un seul est actif.** `core/llm.py` est le seul réellement utilisé en production — et son contrat interne est calqué sur le format Anthropic (`type="tool_use"`, `stop_reason`), un artefact de migration que le blueprint interdit explicitement de perpétuer (§16). `brain/` (racine, 876 tests) est un prototype rule-based jamais câblé. `modules/brain/router.py` n'est qu'un adaptateur de format nommé de façon trompeuse.

2. **Quatre composants possèdent leur propre "second cerveau" autonome, hors de tout Harness central** : `modules/pc_control/auto_agent.py` (boucle vision→décision→action complète avec appel Ollama direct), `modules/ghost/ghost_mode.py` (bypass total de l'orchestrateur), `modules/meet/agent.py`, et `modules/voice/continuous_voice.py` (décide seul d'annuler des tâches). C'est l'anti-pattern central que le blueprint interdit ("Brain and hands are decoupled", "do not recreate a new monolith") — et il apparaît quatre fois indépendamment, pas une.

3. **Le repo contient de la bonne matière première, mal exposée.** Plusieurs mécanismes bas niveau sont solides et directement réutilisables comme "hands" : session CDP/navigateur (`modules/browser/session.py`, `worker.py`, `controller.py`), primitives Windows (`modules/pc_control/{elements,keyboard,mouse,windows,applications}.py`), le STOP unifié multi-source (`modules/computeruse/safety.py`), la persistance conversations avec routing local/serveur (`modules/conversations/`), le moteur de scène 3D (`modules/hologram/`). Et quelques instincts architecturaux déjà alignés blueprint existent en embryon : `core/session_mode.py` (gating dynamique des outils), `modules/planning/detector.py` (pré-filtre déterministe avant LLM), `modules/context/dnd.py` (politique d'interruption), `modules/awareness/monitor.py` (politique d'attention urgence/mode), `brain/cognition/ambiguity.py` (ambiguïté bloquante/non-bloquante).

**Verdict global :** aucun système de décision existant (orchestrateur, brain/, auto_agent, continuous_voice) ne survit tel quel. Tous sont **REBUILD**. Mais la majorité des capacités bas niveau (Windows, navigateur, documents, mémoire persistante, sécurité STOP, 3D) sont **KEEP/EXTRACT/ADAPT** — la migration porte sur la reconstruction du cerveau/harness, pas sur la réécriture des mains.

Deux découvertes secondaires importantes : (a) plusieurs modules signalés comme "code source disparu" (infra distribuée `core/agent`, `core/devices`, `core/remote`, etc., et `modules/tasks`, `modules/mobile`) sont en réalité **récupérables via git history/stash**, pas perdus ; (b) la suite de tests V1 (~993 tests `tests/` + 876 `brain/lab/`, soit ~1869) est disproportionnée par rapport à la cible blueprint (~150-200, §28) et mal répartie — 209 tests sur la 3D (non-goal V2 explicite) contre seulement 43 tests (les mauvais) sur le cerveau réellement utilisé.

---

## 2. CURRENT ARCHITECTURE

### 2.1 Flow de démarrage réel (3 modes, 1 seul cerveau)

```
MODE BUREAU (main.py / RAYA.vbs → launch_silencieux.bat)
  main.py
    → core.bootstrap.run_startup_checks()
    → ui.app.launch(raya.start)         [pywebview, ui/static/raya_hud_v5.html]
    → core.orchestrator.Orchestrator()  ← instancie core/llm.py (PAS brain/ racine)
        Orchestrator.start()
          ├─ démarre modules.web.server.start(orchestrator, port=5005)  [thread]
          ├─ démarre la boucle voix (STT/TTS) si VOICE_ALWAYS_ON        [thread]
          └─ démarre les watchers (calendar, awareness, hud...)        [threads]
  => Desktop + Web local + Voix tournent TOUS sur la MÊME instance Orchestrator.

MODE TERMINAL (raya_cli.py / commande `raya`)
  raya_cli.py
    → core.orchestrator.Orchestrator()   ← même cerveau, MAIS PAS .start()
    → boucle REPL : appelle directement raya.process(text, conversation_id)
  => Pas de web server, pas de voix, pas de watchers. Juste le cœur agentique.
  RAYA_ISOLATED=1 coupe context_bridge (pas de fouille des autres conversations).

MODE WEB (http://127.0.0.1:5005)
  N'est PAS un entry point indépendant — modules/web/server.py est démarré EN THREAD
  par Orchestrator.start() (donc actif seulement si le mode Bureau tourne). Chaque
  route Flask appelle orchestrator.process(text, conversation_id=cid) — bon exemple
  de découplage "Interfaces are clients of the Core" à suivre en V2.

AUTRES ENTRY POINTS (hors flow principal)
  raya_hotkey.py  → listener Ctrl+Alt+R, spawn raya.bat en subprocess
  raya_ghost.py   → modules.ghost.ghost_mode.GhostMode().run() — BYPASSE
                     complètement l'Orchestrator, appelle core.llm directement
```

**Le cerveau réellement actif est `core/llm.py`.** `brain/` (racine, 876 tests) n'est importé nulle part en production — confirmé par grep exhaustif, seul `brain/lab/runner.py` (son propre harnais de test) s'y réfère.

### 2.2 Les vrais "decision centers"

Il n'y en a pas un, mais **cinq**, indépendants les uns des autres :

| Decision center | Portée | Appel modèle | Passe par l'Orchestrateur ? |
|---|---|---|---|
| `core/orchestrator.py` | Central, nominal | via `core/llm.py` | — (c'est lui) |
| `modules/pc_control/auto_agent.py` | Contrôle PC autonome | Ollama HTTP direct (`OLLAMA_PC_MODEL`) | **Non** |
| `modules/ghost/ghost_mode.py` | Overlay hotkey rapide | `core.llm.build_client()` single-turn | **Non** |
| `modules/meet/agent.py` | Agent vocal Google Meet | LLM propre (non audité en détail) | **Non** |
| `modules/voice/continuous_voice.py` | Décisions stop/annulation | Aucun appel LLM, mais décide seul et appelle `safety.request_stop()`/`REGISTRY.cancel_all()` directement | **Non** |

`core/orchestrator.py` lui-même n'est pas un vrai "cerveau" séparé de ses "mains" — son `__init__` détient à la fois les historiques (mémoire), les `pending_actions` (sécurité), les modes TALK/KNOW/ACT par canal (attention/cognition), le client LLM (model layer), l'écoute des tâches de fond (tasks), et le speaker TTS (interfaces) — sans frontière entre ces responsabilités.

### 2.3 Où se trouve concrètement chaque fonction du blueprint aujourd'hui

| Fonction blueprint | Où ça vit en V1 aujourd'hui | Qualité de l'implémentation |
|---|---|---|
| Orchestration/dispatch | `core/orchestrator.py` (monolithe) | Fonctionnel mais tout-en-un |
| Gestion des outils | `core/tools.py` (83 schémas, dispatch()) | Registre correct, logique métier parfois inline |
| Appel modèle | `core/llm.py` (contrat Anthropic-shaped) + appels directs depuis 9 autres modules | Fonctionne, mais mal abstrait et contourné |
| Gestion des tâches | `modules/async_engine/registry.py` (TaskRegistry) | Meilleure base actuelle, incomplète (pas de persistance/priorité/dépendances) |
| Mémoire | `modules/memory/store.py` (faits JSON), `modules/conversations/store.py` (SQLite) | Fonctionnel, sans confidence/provenance/lifecycle formalisés |
| Contexte / état environnement | `modules/raya_state/state.py` (journal 50 actions), `modules/context/monitor.py` (fenêtre active) | Embryon correct de World State, très sous-implémenté |
| Contrôle PC | `modules/pc_control/` (mécanismes solides + `auto_agent.py` = second cerveau) | Mains excellentes, tête à jeter |
| Navigateur | `modules/browser/` (CDP solide + `recipes.py` = logique agentique figée par site) | Mains excellentes, logique à reconstruire |
| Voix | `modules/voice/continuous_voice.py` (runtime autonome complet) | Comportements corrects, architecture à reconstruire |
| Interfaces ↔ Core | `modules/web/server.py` (bon découplage) vs `modules/ghost/ghost_mode.py` (bypass total) | Incohérent — un bon exemple, un mauvais |

---

## 3. DEPENDENCY MAP

### 3.1 Graphe d'imports principal (confirmé par grep)

```
main.py, raya_cli.py
    └─→ core.orchestrator.Orchestrator
            ├─→ core.llm                    (client modèle — SEUL cerveau actif)
            │       └─→ modules.brain.router.BrainMessages  (adaptateur Anthropic→Ollama)
            ├─→ core.tools                  (registre 83 outils + dispatch)
            │       └─→ ~20 imports directs modules/* + dizaines différés par _dispatch_*
            ├─→ core.session_mode           (classify_mode / tools_for_mode TALK-KNOW-ACT)
            ├─→ config.personality          (system prompt statique par mode)
            ├─→ modules.apps.scanner
            └─→ ui.state                    (⚠ couplage UI direct depuis "core")

core.llm  ←── appelé DIRECTEMENT (hors Orchestrator) par :
    modules/files/upload_pipeline.py
    modules/ghost/ghost_mode.py             (⚠ bypass total de l'Orchestrator)
    modules/meet/agent.py                   (⚠ bypass total)
    modules/memory/consolidation.py
    modules/vision/analyze.py
    modules/pc_control/auto_agent.py        (⚠ en réalité appelle Ollama en HTTP direct,
                                                pas via core.llm — double implémentation)
    modules/pc_control/vision_bridge.py

modules/async_engine/registry.py (TaskRegistry)
    └─→ modules.computeruse.safety           (cancel() déclenche request_stop si tool="pc_control")
    ← utilisé par : core.orchestrator, core.tools, modules/voice/continuous_voice.py

modules/computeruse/safety.py (STOP unifié — le système le plus proprement centralisé du repo)
    ← consulté par : core.orchestrator (7 sites), modules/async_engine/registry.py,
      modules/pc_control/{auto_agent,executor,keyboard,task}.py,
      modules/voice/continuous_voice.py

modules/voice/continuous_voice.py
    └─→ modules.async_engine.REGISTRY.cancel_all()   (⚠ appel direct, contourne l'Orchestrator)
    └─→ modules.computeruse.safety.request_stop()    (⚠ idem)

modules/conversations/store.py
    └─→ config.paths
    modules/conversations/backend.py
    └─→ routing optionnel vers serveur central distant (RAYA_SERVER_URL), repli local auto

modules/context_bridge/bridge.py
    └─→ modules.conversations.store, modules.documents.pdf

modules/browser/{controller,session,worker}.py
    └─→ Playwright (CDP)
    ← utilisé par : modules/pc_control/browser.py, modules/pc_control/engine.py (recipes/shopping)

core/agent_state.py (état global singleton process-wide)
    └─→ ui.state, modules.web.events    (⚠ push direct, couplage interfaces↔core)

modules/camera/stream.py
    └─→ ui.state   (⚠ couplage direct à une seule UI, WebView2)
```

### 3.2 Couplages/anti-patterns majeurs identifiés

1. **9 points d'appel directs à `core.llm`** en dehors de l'orchestrateur — le "Model Layer" actuel n'est le seul point de passage de nulle part ailleurs que dans son propre nom.
2. **`modules/pc_control/auto_agent.py` réimplémente son propre client Ollama HTTP** au lieu d'utiliser `core.llm` — double implémentation non synchronisée.
3. **`brain/models/ollama_provider.py` et `core/llm.py` sont deux implémentations Ollama distinctes**, jamais reliées — le meilleur candidat pour le Model Layer (`brain/models/`, interface par capability) n'est jamais utilisé par le système qui appelle réellement Ollama.
4. **`ui.state` est importé directement depuis `core/agent_state.py` et `modules/camera/stream.py`** — le principe "Interfaces are clients of the Core" est violé dans le sens inverse (le "core" pousse vers l'UI au lieu que l'UI consulte le Core).
5. **`modules/voice/continuous_voice.py` court-circuite `core/orchestrator.py`** pour annuler des tâches — la logique de décision "quand stopper" vit dans le canal voix lui-même plutôt que dans une couche Attention/Safety partagée.
6. **4 implémentations quasi identiques** du pattern auth/client/manager/operations pour Google (`calendar/`, `drive/`, `gcontacts/`, `gtasks/`) — duplication structurelle, pas fonctionnelle.
7. **Infra distribuée supprimée du répertoire de travail mais présente en git history** (commit `308492a`, "retour au local pas le choix") — `core/agent/`, `core/devices/`, `core/remote/`, `core/wol/`, `core/gitops/`, `core/deploy/`, `core/routing/`, `core/storage/`. `modules/tasks/` et `modules/mobile/` sont dans un `git stash@{0}` jamais dépilé.

Détail complet composant par composant : voir `RAYA_V2_MIGRATION_MAP.md`.

---

## 4. COMPONENT MIGRATION TABLE

Le tableau complet (~80 composants, avec pour chacun : current responsibility / real callers / real dependencies / tests existants / capacités utiles / problèmes architecturaux / verdict / ce qui survit / destination V2) est dans le document séparé **`RAYA_V2_MIGRATION_MAP.md`**, pour ne pas dupliquer ~150 Ko de détail ici. Synthèse par verdict :

| Verdict | Nombre approx. | Exemples représentatifs |
|---|---|---|
| **KEEP** | ~8 | `modules/computeruse/safety.py`, `modules/hologram/`, `modules/system/*`, `modules/utils/*`, `modules/media/controller.py`, `modules/notes/*`, `modules/location/`, `config/paths.py`, `requirements-core.txt` |
| **EXTRACT** | ~30 | `core/tools.py` (catalogue), `brain/models/`, `brain/cognition/`, mécanismes `modules/pc_control/*`, `modules/browser/{session,worker,controller}.py`, `modules/documents/`, `modules/files/`, `modules/hud/`, `modules/apps/`, `modules/appmanager/`, `config/personality.py` (contenu), infra distribuée récupérable via git |
| **ADAPT** | ~18 | `core/bootstrap.py`, `core/platform/`, `modules/async_engine/registry.py`, `modules/conversations/`, `modules/context_bridge/`, `modules/calendar`+`drive`+`gcontacts`+`gtasks`, `modules/identity/`, `modules/ios/`, `modules/mail/`, `modules/web/server.py`, `modules/planning/`, `modules/models/manager.py` |
| **REBUILD** | ~14 | `core/orchestrator.py`, `core/llm.py`, `modules/pc_control/auto_agent.py`, `modules/pc_control/engine.py`, `modules/browser/recipes.py`, `modules/voice/*`, `modules/awareness/monitor.py`, `modules/vision/presence_watcher.py`, `modules/memory/store.py` (implémentation), `modules/raya_state/state.py` (implémentation), `modules/ghost/ghost_mode.py`, `modules/reminders/scheduler.py` (scheduling), `ui/` |
| **DELETE** | ~10 | `core/agent_state.py`, `brain/learning/`, `brain/skills/`, `brain/lab/`, `modules/brain/router.py` (contrat), `config/features.py` (fichier), `core/deploy/` (résidu .pyc racine, distinct du commit récupérable), `deploy/` (vide), résidus `anthropic`/`openai` dans requirements.txt, majorité de la suite de tests actuelle |

---

## 5. V2 TARGET ARCHITECTURE

Architecture cible reprise du blueprint (§3, §13), annotée avec les décisions de migration concrètes issues de l'audit :

```
                         INTERFACES
        CLI (Phase 0-4) / Web (ADAPT modules/web/server.py) / Voice (REBUILD,
        specs modules/voice) / Desktop (REBUILD différé, ui/) / Mobile (ADAPT
        modules/ios/api.py) / iOS Shortcuts bridge
                              |
                              v
                         ATTENTION
        Politique d'interruption/urgence/mode (REBUILD, inspiré de
        modules/awareness/monitor.py + modules/context/dnd.py) — décide
        seule si une tâche voix peut être annulée, remplace la décision
        actuellement prise en autonomie par continuous_voice.py
                              |
                              v
                    AGENTIC HARNESS
        REBUILD complet. Remplace core/orchestrator.py. Forme de boucle
        inspirée de brain/core/brain.py (plan→exécute→vérifie→
        SUCCESS/UNKNOWN/FAILURE→recover→replan, EXTRACT les dataclasses).
        AUCUNE boucle agentique concurrente ne doit subsister ailleurs
        (élimine auto_agent.py, ghost_mode.py, meet/agent.py comme
        décideurs autonomes).
        |                     |                     |
        v                     v                     v
    COGNITION               TASKS                CONTEXT
    EXTRACT de              ADAPT de              EXTRACT de
    brain/cognition/         modules/async_engine/  modules/context/
    (ambiguïté bloquante/    registry.py (Task/     recent_work.py
    non-bloquante,           statuts/cancel/        (assemblage
    verification tri-état,   listeners), + ajouter   conditionnel +
    recovery) + core/        persistance/priorité/   cache) + config/
    session_mode.py          dépendances/pause-      personality.py
    (gating outils)          resume réels            (contenu)
        |                     |                     |
        +---------------------+---------------------+
                              |
                              v
                         WORLD STATE
        REBUILD implémentation, EXTRACT concept de modules/raya_state/
        state.py (journal actions cross-canal) + modules/context/monitor.py
        (fenêtre active) + modules/hud/monitor.py (CPU/RAM/batterie) +
        modules/location/. Schéma fact/timestamp/source/confidence/
        freshness/status à créer (absent en V1).
                              |
                 +------------+------------+
                 |                         |
                 v                         v
              MEMORY                  ENVIRONMENT
        REBUILD implémentation,        Device Agents :
        EXTRACT/ADAPT de :
        - modules/memory/store.py      WINDOWS AGENT
          (Personal Memory)            EXTRACT modules/pc_control/{router,
        - modules/conversations/       elements,keyboard,mouse,clipboard,
          (Conversation Memory,        windows,applications,wait,vision_
          meilleur précédent           bridge}.py + modules/appmanager/ +
          local/serveur du repo)       modules/apps/ + modules/system/* +
        - modules/identity/store.py    modules/media/controller.py +
          (Experience Memory)          core/platform/ (base OS)
        - modules/judgment/store.py
          (rule/policy memory)         BROWSER AGENT
        Lifecycle candidate→active→    EXTRACT modules/browser/{session,
        confirmed→aging→obsolete       worker,controller}.py — REBUILD
        à créer (absent en V1)         recipes.py comme plans Harness

                                        CAMERA/VISION AGENT
                                        ADAPT modules/vision/{stream_
                                        manager,camera_registry,object_
                                        detection,ocr,face_recognition}.py
                                        + modules/camera/, modules/face/
                                        REBUILD presence_watcher.py
                                        (violation §6 continuous vision)
```

**Model Layer transversal** — REBUILD complet du contrat (aucun des trois "cerveaux" actuels ne sert de base au format), mais **EXTRACT `brain/models/interface.py`** (ModelProvider par capability : generate/reason/plan/classify/vision) comme point de départ conceptuel, étendu avec streaming, tool-calling natif RAYA, et routing multi-critères (capability/coût/latence/contexte/VRAM — aujourd'hui seul un routing "code vs général" existe dans `modules/brain/router.py`).

**Safety** — le seul subsystem transversal déjà quasi conforme au blueprint : `modules/computeruse/safety.py` (STOP unifié multi-source F12/Échap/voix/programmatique, bien testé) est **KEEP** quasi tel quel comme fondation.

**Server/Remote** — `modules/conversations/backend.py` (routing local↔serveur central avec repli honnête) est le meilleur précédent existant pour la boundary `persistence`/serveur central (blueprint §23). L'infra distribuée supprimée (`core/remote/`, `core/devices/`, `core/wol/`) est **récupérable via git** et peut servir de base au Device Registry si le besoin est confirmé.

---

## 6. CONTRACTS TO DEFINE

Pour chaque contrat ci-dessous : forme proposée informée par ce qui existe déjà de solide en V1 (à ne pas repartir de zéro sans regarder) + ce que le blueprint exige en plus.

### WorldState
```
{
  domain: "pc" | "app" | "window" | "process" | "browser" | "device" | "system" | ...
  fact: <valeur structurée>
  timestamp: iso8601
  source: "perception:foreground_window" | "perception:presence_sensor" | ...
  confidence: known_fact | inferred | hypothesis
  freshness: <ttl ou horodatage de péremption>
  status: active | stale | superseded
}
```
Base V1 à regarder : `modules/raya_state/state.py` (journal d'actions — bon *use case*, schéma à créer), `modules/context/monitor.py` (fenêtre active, capteur léger continu conforme §6), `modules/hud/monitor.py` (CPU/RAM/batterie).

### Event
```
{
  id, type, timestamp, source, payload, correlation_id (lie l'event à un Task/Session)
}
```
Aucun précédent V1 formel — actuellement des callbacks directs (`set_fire_callback`, `set_dispatch_callback` dans `modules/awareness/monitor.py`). À construire from scratch, mais s'inspirer du découplage callback déjà présent.

### Memory
```
{
  id, type: fact | preference | rule | experience,
  channel_scope: shared | voice | chat | ios,
  content, confidence, provenance, created_at, updated_at,
  lifecycle: candidate | active | confirmed | aging | obsolete
}
```
Base V1 : `modules/memory/store.py` (facts + correction/obsolescence — flag booléen à remplacer par vrai lifecycle), `modules/identity/store.py` (écriture explicite uniquement, bornée, dédoublonnée — déjà aligné §11 "user corrections have high priority"), `modules/context_bridge/bridge.py` (isolation stricte de canal, read-only, anti path-traversal — modèle de sécurité à reprendre tel quel), `modules/conversations/store.py` (schéma SQLite avec migrations propres, meilleure base structurelle).

### Attention
```
{
  event_ref, urgency, importance, novelty, confidence, cost,
  current_focus_task_id, decision: process_now | background | interrupt | ignore
}
```
Meilleur précédent V1 : `modules/awareness/monitor.py` (whitelist d'actions sûres exécutées directement vs pending_action, hold-and-flush par mode idle/game/call/coding, bypass si urgent) et `modules/context/dnd.py`. Concept correct, implémentation monolithique à décomposer.

### Task
```
{
  id, objective, state, priority, context, progress, dependencies[],
  created_at, updated_at, checkpoint, cancellation_token, result, error, owner
}
```
Base V1 : `modules/async_engine/registry.py` (Task/TaskContext — statuts PENDING→RUNNING→SUCCESS|FAILED|CANCELLED, cancel coopératif, listeners, `resume_info` = embryon de checkpoint) + `modules/pc_control/task.py` (journal d'actions/métriques) + `brain/core/brain.py::TaskManager/Scheduler` (dataclasses TaskStatus, VerificationResult SUCCESS/UNKNOWN/FAILURE). À ajouter : persistance réelle (tout est en mémoire process en V1, perdu au redémarrage), priorité, dépendances entre tâches, pause/resume réel (pas seulement resume-from-scratch).

### Harness
Pas de contrat de données à proprement parler — c'est le runtime. Forme de boucle à reprendre de `brain/core/brain.py::_execute_task` (stratégie → observation → SUCCESS/UNKNOWN/FAILURE → alternative → retry borné), étendue avec les invariants comportementaux de `core/orchestrator.py` (isolation canal stricte, STOP prioritaire consulté à chaque étape, filet de rattrapage anti-faux-tool-call texte).

### Tool
```
{
  name, description, schema (input/output), permission_level, timeout,
  retryable, capability_tags[]  # ex: ["filesystem", "network", "destructive"]
}
```
Base V1 : les 83 schémas de `core/tools.py` sont un bon catalogue de CAPACITÉS à réextraire (le mécanisme de dispatch, lui, doit être reconstruit — logique métier actuellement mêlée au dispatch dans plusieurs `_dispatch_*`).

### Model
```
RAYA Model Request { capability: reasoning|vision|coding|classification|...,
                      messages, tools?, context_budget, constraints }
      → Model Registry → Router (capability/coût/latence/contexte/local-cloud)
      → Provider (Ollama Cloud | Ollama Local | futur)
      → RAYA Model Response { content, tool_calls?, usage, provider_meta }
```
**Explicitement PAS façon Anthropic** (`type="tool_use"`, `stop_reason` bannis comme contrat permanent, §16). Base V1 : `brain/models/interface.py` (ModelProvider par capability — meilleure graine), connaissances opérationnelles à préserver de `core/llm.py` (`think=False` requis pour deepseek-v4-flash, mapping `done_reason="length"→"max_tokens"`).

### Context
Pas un contrat de données statique mais une fonction : `assemble_context(task, budget) → ranked_context`. Précédent V1 : `modules/context/recent_work.py` (assemblage conditionnel + cache TTL 30s, injecté SEULEMENT si pertinent) — meilleur exemple existant du principe "ne pas tout injecter à chaque appel".

### Permission
```
{ action_risk: safe|sensitive|destructive, requires_confirmation: bool,
  granted_by: user|policy|physical_presence, audit_trail: [...] }
```
Base V1 : `modules/pc_control/engine.py::_RISK` (table de risque statique), `modules/computeruse/safety.py` (STOP), `modules/face/manager.py::verify_is_owner()` (permission par présence physique vérifiée, fail-secure), `modules/browser/controller.py` (refus champs mot de passe/carte, warning URLs sensibles).

### Device
```
{ identity, status, capabilities[], availability, health, permissions }
```
Aucun registre formel en V1 (l'infra `core/devices/registry.py` existait et a été supprimée — récupérable via git, commit `308492a^`). À reconstruire, potentiellement en repartant de ce code historique comme base plutôt que de zéro.

### Interface
```
{ interface_id, channel_type: cli|web|voice|mobile|desktop,
  session_id, → calls Harness.handle_request() UNIQUEMENT }
```
Meilleur exemple V1 à suivre : `modules/web/server.py` (chaque route appelle `orchestrator.process()`, jamais de logique propre). Contre-exemple à corriger : `modules/ghost/ghost_mode.py` (bypass total).

---

## 7. MIGRATION ORDER

Reprise de l'ordre du blueprint (§27), avec les cibles d'extraction concrètes identifiées par cet audit :

### Phase 0 — Contrats d'architecture
Définir les 12 contrats ci-dessus. Aucune dépendance code, juste les schémas/interfaces. Décider explicitement du nommage pour éviter la confusion "3 brains" identifiée dans cet audit.

### Phase 1 — Harness + World State + Memory + Context
- Harness : nouvelle boucle inspirée de `brain/core/brain.py` (forme) + invariants de `core/orchestrator.py` (isolation canal, STOP).
- World State : nouveau schéma, alimenté par les capteurs légers déjà existants (`modules/context/monitor.py`, `modules/hud/monitor.py`) reconnectés en perception plutôt qu'en push direct UI.
- Memory : nouveau schéma avec lifecycle, migrer les DONNÉES existantes de `modules/memory/store.py` (facts JSON) et `modules/conversations/store.py` (SQLite conversations — **données utilisateur réelles, migration prudente requise**, voir Risques §8).
- Context Engine : nouveau, inspiré du pattern `modules/context/recent_work.py`.

### Phase 2 — Attention + Task Actors
- Task Actors : étendre `modules/async_engine/registry.py` (le composant le plus proche du concept) avec persistance/priorité/dépendances/pause-resume réel, plutôt que repartir de zéro.
- Attention : nouveau subsystem, politique reprise de `modules/awareness/monitor.py` (whitelist/hold-flush/urgent-bypass) et `modules/context/dnd.py`, mais séparé de la perception (aujourd'hui mélangés dans le même fichier).

### Phase 3 — Cognition + Tool System + Model Layer
- Cognition : EXTRACT `brain/cognition/` (ambiguïté, verification tri-état, recovery) comme spec, connecté à un vrai modèle (V1 est rule-based pur).
- Tool System : nouveau registre/dispatch, catalogue de départ = les 83 schémas de `core/tools.py`, logique métier sortie vers de vrais Device Agents.
- Model Layer : nouveau contrat natif RAYA (PAS Anthropic-shaped), base conceptuelle `brain/models/interface.py`, connaissances opérationnelles Ollama préservées de `core/llm.py`.

### Phase 4 — Environment Agents
- Windows Agent : EXTRACT les mécanismes `modules/pc_control/*` (hors auto_agent.py/engine.py), `modules/appmanager/`, `modules/apps/`, `modules/system/*`, `modules/media/controller.py`, base `core/platform/`.
- Browser Agent : EXTRACT `modules/browser/{session,worker,controller}.py`, REBUILD la logique de `recipes.py` comme plans orchestrés par le Harness plutôt que scripts figés par site.
- Camera/Vision Agent : ADAPT `modules/vision/*` (hors presence_watcher.py), `modules/camera/`, `modules/face/`.
- **Point de vigilance** : c'est à cette phase que les quatre "seconds cerveaux" (auto_agent, ghost_mode, meet/agent, continuous_voice) doivent être formellement éliminés en tant que décideurs — leurs mécanismes bas niveau migrent en Device Agents, leur logique de décision disparaît au profit du Harness générique.

### Phase 5 — Realtime Voice
REBUILD complet, specs comportementales extraites de `modules/voice/continuous_voice.py` (VAD, barge-in avec anti-écho calibré, phrases stop/annulation, timeout, streaming TTS interruptible) — tous déjà validés par les tests `test_barge_in.py`, `test_tts_router.py`, portions de `test_final_audit.py`. Le nouveau runtime doit émettre des événements vers Attention/Harness au lieu de décider seul.

### Phase 6 — Interfaces / Remote UX
- Web : ADAPT `modules/web/server.py` quasi tel quel (déjà bien découplé), changer uniquement le point d'appel vers le nouveau Harness.
- Mobile : ADAPT `modules/ios/api.py`.
- Desktop : REBUILD `ui/` en client mince du Harness (actuellement couplé directement à l'Orchestrator via `ui/bridge.py`).
- Ghost overlay : REBUILD l'idée (hotkey → input flottant → presse-papier) en la faisant passer par le Harness, jamais en bypass.

---

## 8. RISKS

### Risques architecture
- **Recréer un nouveau monolithe sous un autre nom** — le blueprint le dit explicitement (§25) : le risque n°1 est de reproduire `core/orchestrator.py` avec des noms de fichiers différents. Point de vigilance particulier sur le Harness (Phase 1) qui a la surface la plus large.
- **Les "seconds cerveaux" survivent silencieusement** — `auto_agent.py`, `ghost_mode.py`, `continuous_voice.py` fonctionnent aujourd'hui de façon autonome. Pendant une migration progressive, il est facile de les laisser tourner "en attendant" pendant que le nouveau Harness se construit à côté — et de ne jamais vraiment les démanteler parce qu'ils "marchent". Nécessite une décision explicite de coupure, pas une dépréciation implicite.
- **Confusion de nommage "brain"** — 3 systèmes déjà nommés "brain"/"cerveau" dans V1 (brain/ racine, modules/brain/, le mot dans les logs `core/llm.py`). Choisir un vocabulaire V2 sans ambiguïté dès la Phase 0 pour éviter de recréer la confusion pendant l'implémentation.

### Risques données
- **`modules/conversations/store.py` (SQLite) contient l'historique réel des conversations de l'utilisateur** — toute migration de schéma (ajout de lifecycle/confidence/provenance) doit préserver ces données sans perte. Migration à traiter comme une vraie migration de base de données (versionnée, réversible), pas une réécriture.
- **`modules/memory/store.py` (facts.json) et `modules/identity/store.py`** contiennent des apprentissages accumulés sur l'utilisateur (préférences, corrections) — même risque, volume plus faible mais valeur élevée (perdre ces données = RAYA "oublie" l'utilisateur).
- **`memory/faces/` (visages enregistrés pour reconnaissance faciale)** — données biométriques, à traiter avec la même prudence que les credentials au sens sécurité, indépendamment de la migration architecturale.

### Risques dépendances
- **9 points d'appel directs à `core.llm`** hors orchestrateur (voir §3.2) — chacun doit être explicitement retracé et reconnecté au nouveau Model Layer ; en rater un signifie qu'un module continue à parler à l'ancien contrat Anthropic-shaped en silence.
- **`modules/pc_control/auto_agent.py` a son propre client Ollama HTTP** distinct de `core.llm` — double surface à migrer, pas une seule.
- **Infra distribuée : décision à prendre, pas seulement technique.** Le commit `308492a` ("retour au local pas le choix") suggère un choix délibéré de revenir à une architecture locale — avant de récupérer `core/remote/`, `core/devices/`, `core/wol/` depuis git history pour le futur Device Registry/Server, il faut comprendre POURQUOI ce retour en arrière a eu lieu (contrainte technique ? décision produit ? voir Questions §10).

### Risques régression
- La suite de tests actuelle (~1869 tests au total) valide des invariants comportementaux réels et durement acquis : isolation stricte des canaux voix/chat/ios, priorité absolue du STOP, anti-narration ("je vais cliquer..."), barge-in avec anti-écho calibré. Si le Harness V2 ne reproduit pas ces invariants avec la même rigueur, ce sont des régressions UX silencieuses (l'utilisateur les remarquera avant que les tests ne les attrapent, puisque les tests V1 ne couvriront pas le nouveau code).
- **Coordination multi-canal actuelle non triviale** — `modules/raya_state/state.py` (actions cross-canal visibles) et l'isolation stricte de `modules/context_bridge/bridge.py` sont deux mécanismes qui s'équilibrent prudemment (partage explicite vs isolation par défaut). Une reconstruction naïve de World State/Memory pourrait soit tout partager (fuite d'isolation) soit tout cloisonner (perte du "RAYA sait ce qui vient de se passer ailleurs").

---

## 9. TEST MIGRATION PLAN

### Constat chiffré
- `tests/` : ~993 tests répartis sur ~25 fichiers.
- `brain/lab/` : 876 tests (framework de scénarios mockés, jamais exécutés contre un vrai environnement).
- **Total ~1869**, contre une cible blueprint de ~150-200 tests réels d'intégration (§28).

### Répartition disproportionnée identifiée
| Fichier(s) | Tests | Jugement |
|---|---|---|
| `test_web_platform.py` + `test_web_platform_v2.py` | 305 | Le suffixe "_v2" jamais nettoyé à côté du "_v1" suggère un doublon non résolu, forte suspicion de sur-permutation |
| `test_hologram_*` (5 fichiers) | 209 | Disproportionné : la 3D avancée est un non-goal explicite du V2 initial (blueprint §30) |
| `test_ollama_provider.py` + `test_ollama_live.py` | 43 | Testent `brain/models/ollama_provider.py` (le cerveau ORPHELIN), pas `core/llm.py` (le cerveau réellement utilisé) — couverture trompeuse |
| `brain/lab/` | 876 | Testent une boucle rule-based en mock, jamais connectée à un vrai modèle ni de vraies capacités |

### Ce qui a une vraie valeur de migration (spec comportementale, pas code)
- `test_realtime_interaction.py` (115), `test_raya_interaction.py` (99), `test_final_audit.py` (106) : couvrent STOP prioritaire, barge-in, TTS interruptible — invariants à reproduire à l'identique en V2, à réécrire comme tests d'intégration Harness.
- `test_channel_isolation.py` + `test_full_isolation.py` (76) : isolation stricte des canaux — invariant central de la Memory V2 (Conversation Memory, channel_scope).
- `test_barge_in.py`, `test_tts_router.py` : specs comportementales Voice à reporter en Phase 5.

### Ce qui n'a aucune valeur de migration directe
- Tout ce qui teste l'implémentation de composants classés REBUILD/DELETE (orchestrator, auto_agent, brain/lab/, contrat Anthropic-shaped de core/llm.py) — le comportement testé disparaît avec le code.
- `test_hologram_*` : à conserver en volume minimal (le moteur `modules/hologram/` est KEEP), pas besoin de porter les 209 tests.

### Plan concret
1. Ne PAS porter les tests un par un. Extraire d'abord la LISTE des invariants comportementaux qu'ils prouvent (STOP, isolation, barge-in, anti-narration, undo/redo hologramme) — travail déjà largement fait par cet audit et par `RAYA_FINAL_AUDIT.md` existant.
2. Écrire les nouveaux tests d'intégration V2 contre ces invariants, sur la nouvelle architecture (Harness réel, pas de mocks de complaisance).
3. Utiliser les statuts PASS/FAIL/BLOCKED/NOT_TESTED explicitement (blueprint §28) — en particulier pour tout ce qui touche à de vraies actions Windows/navigateur, ne jamais déclarer PASS si l'environnement de test a empêché la validation réelle.
4. Cible réaliste : 150-200 tests, concentrés sur Harness/Tasks/Memory/Safety/Model routing/intégration réelle — pas de réplique des 209 tests hologramme ni des 876 tests mock de brain/lab/.

---

## 10. QUESTIONS / AMBIGUITIES

Décisions qui ne peuvent pas être déduites du repo + blueprint seuls :

1. **Pourquoi l'infra distribuée (`core/remote/`, `core/devices/`, `core/wol/`, `core/gitops/`) a-t-elle été supprimée le 2026-08-14** (commit "retour au local pas le choix") ? Contrainte technique rencontrée, changement de priorité produit, ou juste un nettoyage temporaire ? Cette réponse détermine si le Device Registry V2 (blueprint §19) et le serveur central (§23) doivent repartir de ce code récupéré via git, ou si un choix délibéré de rester 100% local doit être respecté et documenté comme contrainte V2 permanente.

2. **`modules/mobile/api.py` (dans le git stash, jamais dépilé) vs `modules/ios/api.py` (actif aujourd'hui)** — `mobile` a-t-il été abandonné intentionnellement au profit d'`ios`, ou simplement oublié dans un stash ? Ça change le verdict de EXTRACT à DELETE pur et simple.

3. **Le mode Ghost (`modules/ghost/ghost_mode.py`, hotkey overlay rapide, bypass total du Harness)** — est-ce une fonctionnalité que tu veux vraiment garder en V2 (réponse ultra-rapide sans contexte/historique, par design) ? Si oui, le Harness V2 doit-il exposer un mode "stateless/fast-path" explicite, ou est-ce que Ghost doit simplement accepter la latence d'un appel Harness complet ?

4. **`modules/meet/agent.py` (agent vocal autonome Google Meet)** — priorité réelle pour toi ? Le blueprint classe la voix réaliste comme Phase 5 (après le Core stable), mais Meet est un canal encore plus spécialisé — à traiter en Phase 5 également, ou complètement hors scope V2 initial (comme la 3D avancée) ?

5. **Ampleur du Model Router souhaitée dès la Phase 3** — le blueprint liste des critères riches (capability/coût/latence/contexte/VRAM/modalité). V1 n'a qu'un routing binaire "code vs général". Veux-tu un router simple (2-3 règles, capability-based) pour valider l'architecture d'abord, ou le système de scoring multi-critères complet dès le départ ?

6. **Statut des 4 connecteurs Google dupliqués** (`calendar/drive/gcontacts/gtasks`) — factoriser en un connecteur générique fait partie de la Phase 3/4 (Tool System) ou est-ce un chantier séparé, non prioritaire pour valider le Core ?

7. **Combien de la suite de tests actuelle acceptes-tu de perdre "brutalement"** — le plan §9 recommande de ne PAS porter les 209 tests hologramme ni les 876 tests brain/lab/ tels quels. Confirmation explicite souhaitée avant qu'un futur chantier d'implémentation ne les supprime.

---

*Fin du rapport. Aucune action de code n'a été effectuée. Prochaine étape suggérée (à valider avec toi avant tout démarrage) : trancher les questions ci-dessus, puis attaquer la Phase 0 (contrats) du plan de migration.*
