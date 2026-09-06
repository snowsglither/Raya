# RAYA V1 → V2 — MIGRATION MAP

**Statut :** Audit terminé, aucune migration effectuée. Aucun fichier V1 modifié/supprimé/déplacé.
**Méthode :** grep des imports réels, lecture du code, vérification git history pour les composants "disparus". Classification KEEP / EXTRACT / ADAPT / REBUILD / DELETE par composant, conforme à la section 25 du blueprint.
**Document compagnon :** `RAYA_V2_AUDIT_REPORT.md` (executive summary, architecture, contrats, ordre de migration, risques).

---

## Légende

- **KEEP** — architecture + implémentation survivent telles quelles.
- **EXTRACT** — une capacité est bonne, mais son architecture actuelle non ; on prélève le mécanisme/la connaissance, pas le système entier.
- **ADAPT** — le composant peut être adapté aux contrats V2 sans réécriture complète.
- **REBUILD** — le concept est utile, l'implémentation doit être entièrement reconstruite.
- **DELETE** — legacy, doublon, mort, ou incompatible avec V2.

---

## Tableau récapitulatif global (~80 composants audités)

| # | Composant | Emplacement V1 | Verdict | Destination V2 |
|---|---|---|---|---|
| 1 | `core/orchestrator.py` | core/ | **REBUILD** | harness (+ fragments memory/attention/safety/models/interfaces) |
| 2 | `core/tools.py` | core/ | **EXTRACT** | tools (catalogue 83 capacités) |
| 3 | `core/llm.py` | core/ | **REBUILD** | models |
| 4 | `core/session_mode.py` | core/ | **EXTRACT** | attention, context |
| 5 | `core/agent_state.py` | core/ | **DELETE** | vocabulaire d'états seulement → tasks |
| 6 | `core/bootstrap.py` | core/ | **ADAPT** | runtime (ops), Linux-first |
| 7 | `core/platform/` | core/ | **ADAPT** | devices |
| 8 | `core/agent/`, `core/devices/`, `core/remote/`, `core/wol/`, `core/gitops/`, `core/routing/`, `core/storage/` | core/ (source supprimé, **récupérable via `git show 308492a^:<chemin>`**) | **EXTRACT** (depuis git history) | devices (Device Registry), persistence, tasks (task_router.py) |
| 9 | `core/deploy/updater.py` (SafeUpdater) | core/ (récupérable via git history) | **EXTRACT** | runtime/persistence (déploiement sûr dry-run) |
| 10 | `brain/core/brain.py` (+Context/Memory/TaskManager/Scheduler/Planner/DecisionEngine) | brain/ | **EXTRACT** | harness (forme boucle), tasks |
| 11 | `brain/models/` (ModelProvider, OllamaProvider, registry, routing) | brain/ | **EXTRACT** | models (meilleure graine) |
| 12 | `brain/cognition/` (ambiguity, entities, intent, prioritization, reasoning, recovery, verification) | brain/ | **EXTRACT** | cognition |
| 13 | `brain/learning/`, `brain/skills/` | brain/ | **DELETE** | rien (stubs vides) |
| 14 | `brain/lab/` (876 tests) | brain/ | **DELETE** | rien (philosophie de test seulement) |
| 15 | `modules/brain/router.py` (BrainMessages) | modules/ | **DELETE** (contrat) / **EXTRACT** (heuristique routing code) | models |
| 16 | `modules/appmanager/` (winget) | modules/ | **EXTRACT** | devices, safety |
| 17 | `modules/apps/` (scanner + launcher flou) | modules/ | **EXTRACT** | devices |
| 18 | `modules/async_engine/registry.py` (TaskRegistry) | modules/ | **ADAPT** | tasks (meilleure base Task Actor actuelle) |
| 19 | `modules/awareness/monitor.py` | modules/ | **REBUILD** | perception (sources) + attention (politique) |
| 20 | `modules/browser/` session.py, worker.py, controller.py | modules/ | **EXTRACT** | devices (Browser Agent — hands) |
| 21 | `modules/browser/recipes.py`, `shopping.py` | modules/ | **REBUILD** | harness (logique agentique par-dessus les hands) |
| 22 | `modules/calendar/`, `modules/drive/`, `modules/gcontacts/`, `modules/gtasks/` | modules/ | **ADAPT** | tools (connecteur Google unifié) |
| 23 | `modules/camera/stream.py` | modules/ | **EXTRACT** | devices, perception (on-demand) |
| 24 | `modules/computeruse/safety.py` (STOP unifié) | modules/ | **KEEP** | safety |
| 25 | `modules/computeruse/actions.py` | modules/ | **EXTRACT** | devices (input.* primitives) |
| 26 | `modules/context/monitor.py` | modules/ | **EXTRACT** | perception |
| 27 | `modules/context/dnd.py` | modules/ | **EXTRACT** | attention |
| 28 | `modules/context/recent_work.py` | modules/ | **EXTRACT** | context (pattern assemblage conditionnel + cache) |
| 29 | `modules/context_bridge/bridge.py` | modules/ | **ADAPT** | memory (Conversation Memory, accès cross-canal contrôlé) |
| 30 | `modules/conversations/` (store.py, backend.py, remote.py) | modules/ | **ADAPT** | memory + persistence (meilleur précédent local/serveur du repo) |
| 31 | `modules/documents/` | modules/ | **EXTRACT** | tools (document.extract/generate) |
| 32 | `modules/drive/` | modules/ | voir #22 | tools |
| 33 | `modules/face/` | modules/ | **EXTRACT** | devices (vision.identify_person), safety (verify_is_owner) |
| 34 | `modules/files/` (upload_pipeline.py) | modules/ | **EXTRACT** | tools, safety (validation upload) |
| 35 | `modules/gcontacts/` | modules/ | voir #22 | tools |
| 36 | `modules/ghost/ghost_mode.py` | modules/ | **REBUILD** | interfaces (idée gardée, bypass du Harness supprimé) |
| 37 | `modules/gtasks/` | modules/ | voir #22 | tools |
| 38 | `modules/hologram/` (engine.py, model.py, store.py) | modules/ | **KEEP** | tools (generation.hologram) |
| 39 | `modules/hud/monitor.py` | modules/ | **EXTRACT** | perception, world_state (system conditions) |
| 40 | `modules/identity/store.py` | modules/ | **ADAPT** | memory (Experience/Personal Memory) |
| 41 | `modules/ios/api.py` | modules/ | **ADAPT** | interfaces (client mobile) |
| 42 | `modules/judgment/store.py` | modules/ | **EXTRACT** | cognition/memory (position durable) |
| 43 | `modules/location/` | modules/ | **KEEP** | perception/world_state |
| 44 | `modules/mail/` | modules/ | **ADAPT** | tools (capability email) |
| 45 | `modules/media/controller.py` | modules/ | **KEEP** | devices |
| 46 | `modules/meet/agent.py` | modules/ | **REBUILD** (différé, non-goal V2 initial) | interfaces |
| 47 | `modules/memory/store.py` | modules/ | **REBUILD** (implémentation) | memory (concept Personal Memory gardé) |
| 48 | `modules/mobile/api.py` | modules/ (source supprimé du repo, **récupérable via `git stash show -p stash@{0} -- modules/mobile`**, 437 lignes) | **ADAPT** (à réévaluer — remplacé en pratique par modules/ios/) | interfaces |
| 49 | `modules/models/manager.py` (cascade 3D) | modules/ | **ADAPT** | tools (generation, intent-driven §22) |
| 50 | `modules/notes/` | modules/ | **KEEP** | memory/tools |
| 51 | `modules/pc_control/router.py` | modules/ | **EXTRACT** | devices (échelle stratégies DOM→UIA→vision) |
| 52 | `modules/pc_control/{elements,keyboard,mouse,clipboard,windows,applications,wait,vision_bridge}.py` | modules/ | **EXTRACT** | devices (Windows Agent — hands) |
| 53 | `modules/pc_control/engine.py` (dispatch+policy) | modules/ | **REBUILD** | safety (table de risque) + tools (dispatch) séparés |
| 54 | `modules/pc_control/auto_agent.py` | modules/ | **REBUILD** — **second cerveau à éliminer** | harness + tasks + models (aucun code repris) |
| 55 | `modules/planning/detector.py`, `plan_store.py` | modules/ | **ADAPT** | cognition (pré-filtre déterministe) + tasks (plan → Task Actor) |
| 56 | `modules/raya_state/state.py` | modules/ | **REBUILD** (implémentation) | world_state (concept "action récente cross-canal" gardé) |
| 57 | `modules/reminders/scheduler.py` | modules/ | **ADAPT** (parsing) + **REBUILD** (scheduling) | tasks (Task Actor "reminder") |
| 58 | `modules/system/*` | modules/ | **KEEP** | devices (Windows Agent, system.*) |
| 59 | `modules/tasks/` (scheduler.py, store.py) | modules/ (source supprimé du repo, **récupérable via `git stash show -p stash@{0} -- modules/tasks`**, 267 lignes) | **EXTRACT** (à réévaluer — matière brute pour Task Actors) | tasks |
| 60 | `modules/utils/*` (calculatrice, convertisseur, météo) | modules/ | **KEEP** | tools |
| 61 | `modules/vision/stream_manager.py`, `camera_registry.py`, `object_detection.py`, `ocr.py`, `face_recognition.py`, `analyze.py` | modules/ | **ADAPT** | devices + models (capability vision) |
| 62 | `modules/vision/presence_watcher.py` | modules/ | **REBUILD** — **violation §6 confirmée** | perception (capteur léger non-LLM) ou suppression |
| 63 | `modules/voice/` (continuous_voice.py, stt.py, tts*.py) | modules/ | **REBUILD** | interfaces (specs comportementales seulement) |
| 64 | `modules/web/server.py` | modules/ | **ADAPT** | interfaces (déjà bien découplé, changer juste le point d'appel) |
| 65 | `config/features.py` | config/ | **DELETE** (fichier) / pattern KEEP | safety (permissions par capability) |
| 66 | `config/paths.py` | config/ | **KEEP** | persistence |
| 67 | `config/personality.py` | config/ | **EXTRACT** | context (contenu) + cognition (règles de ton) |
| 68 | `tools/ensure_deps.py` | tools/ | **KEEP** | tooling dev, hors boundaries |
| 69 | `tools/test_voices.py` | tools/ | **ADAPT** | interfaces (voice, outil de dev) |
| 70 | `ui/` (raya_hud_v5.html, bridge.py, state.py) | ui/ | **REBUILD** (différé — headless-first) | interfaces |
| 71 | `requirements-core.txt` | racine | **KEEP** | déjà la base headless Linux-first §4/§23 |
| 72 | `requirements.txt` (résidus `anthropic`, `openai`) | racine | **DELETE** (résidu, à vérifier avant suppression réelle) | — |
| 73 | Suite de tests (~993 tests `tests/` + 876 `brain/lab/`) | tests/, brain/lab/ | Majoritairement **DELETE** en tant que code ; **EXTRACT** en tant que spec comportementale | voir Test Migration Plan (rapport principal §9) |

---

## Détail des composants les plus critiques

### 1. `core/orchestrator.py` — LE monolithe

```
Current location: core/orchestrator.py (2583 lignes)
Current responsibility: boucle principale, agentic loop, TTS (_GlobalSpeaker), parsing
  faux tool-calls texte, détection STOP/mode continu, classification canal, persistance+
  broadcast des tours, historiques par canal, confirmations en attente, modes TALK/KNOW/ACT
  par canal, init client LLM, écoute événements tâches async, appels UI directs.
Real callers: main.py, raya_cli.py, modules/web/server.py, 11 fichiers de tests.
Real dependencies: core.llm, core.tools, core.session_mode, config.personality,
  modules.apps.scanner, ui.state (import direct — couplage UI dans "core").
What it actually does: son __init__ seul détient memory (historiques), safety
  (pending_actions), attention/cognition (modes par canal), model layer (client LLM),
  tasks (listener), interfaces (speaker TTS) — sans aucune frontière entre ces
  responsabilités.
Existing tests: test_raya_interaction.py, test_realtime_interaction.py,
  test_final_audit.py, test_full_isolation.py, test_reconstruction.py,
  test_ollama_live.py, test_planning_integration.py, test_web_platform(_v2).py —
  large couverture COMPORTEMENTALE, mais aucun test n'assume une séparation
  harness/cognition/tasks : ils valident le monolithe tel quel.
Useful capabilities: isolation stricte des canaux (voice/ios/chat jamais mélangés,
  invariant à préserver) ; détection STOP prioritaire ; filet de rattrapage pour les
  faux tool-calls texte émis en clair par des modèles gratuits (capacité défensive
  réelle face à Ollama Cloud, à reproduire).

V2 decision: REBUILD
What the V2 replacement must provide: Agentic Harness (blueprint §8) ÉCLATÉ —
  boucle d'exécution + cancellation + checkpoint → Harness ; historiques → Memory
  (Conversation Memory) ; pending_actions → Safety ; modes TALK/KNOW/ACT →
  Attention/Context Engine ; client LLM → Model Layer ; TTS/UI push → Interfaces
  (clients du Core, jamais couplés dedans). Les invariants comportementaux
  (isolation canal, STOP prioritaire, anti-faux-tool-call) sont des EXIGENCES de
  conception du nouveau Harness, pas du code repris.
V2 destination: harness (primaire) ; fragments vers memory, attention, safety,
  models, interfaces
```

### 2. Les trois "cerveaux" — un seul actif, aucun réutilisable tel quel

| | `core/llm.py` | `brain/` (racine) | `modules/brain/router.py` |
|---|---|---|---|
| Actif en prod ? | **OUI — seul cerveau réel** | NON, jamais câblé (876 tests isolés) | OUI, appelé par core/llm.py |
| Rôle | Client Ollama + traducteur contrat Anthropic↔Ollama + routing | Boucle cognitive complète rule-based (Context/Memory/TaskManager/Scheduler/Planner/DecisionEngine) | Adaptateur de messages format Anthropic → Ollama + routing code |
| Problème | Contrat interne façon Anthropic (interdit blueprint §16), 9 modules le contournent en l'appelant directement | Rule-based, aucune connexion réelle à Ollama/capacités, jamais éprouvé | Nom trompeur (n'est PAS le "Brain" cognitif), shims "compat tests" figés |
| Verdict | REBUILD | EXTRACT (forme de la boucle + contrats) | DELETE (contrat) / EXTRACT (heuristique routing) |
| Ce qui a de la valeur | Connaissances opérationnelles Ollama (think=False, done_reason mapping) | Dataclasses (BrainEvent, TaskStatus, VerificationResult), boucle plan→exécute→vérifie→recover, `brain/models/` (ModelProvider par capability — meilleure graine Model Layer), `brain/cognition/` (ambiguïté bloquante/non-bloquante, verification tri-état) | Heuristique "routage par nature de tâche" |

**Point clé :** aucun des trois ne devient LE Model Layer ou LE Harness V2 tel quel. `brain/models/interface.py` (ModelProvider.generate/reason/plan/classify/vision, orienté capability) est la meilleure graine conceptuelle pour le Model Layer — mais reste un doublon jamais branché à `core/llm.py`, qui réimplémente son propre client Ollama en parallèle sans jamais l'utiliser.

### 3. Les "seconds cerveaux" cachés — anti-pattern répété

Le blueprint interdit explicitement qu'un composant périphérique possède sa propre boucle de raisonnement autonome hors Harness ("Brain and hands are decoupled", "avoid recreating a new monolith"). Ce pattern apparaît **quatre fois** dans V1, indépendamment de l'orchestrateur :

| Composant | Ce qu'il fait en autonomie | Appel modèle |
|---|---|---|
| `modules/pc_control/auto_agent.py` | Boucle screenshot→vision→décision JSON→action complète, son propre system prompt (90 lignes), son propre historique glissant | Appel Ollama HTTP direct (`OLLAMA_PC_MODEL`), sans passer par `brain/` ni `core/orchestrator.py` |
| `modules/ghost/ghost_mode.py` | Bypass total de l'orchestrateur — pas d'historique, pas d'outils, pas de mémoire, pas de STOP | `core.llm.build_client()` en appel single-turn brut |
| `modules/meet/agent.py` | Agent vocal autonome (loopback→VAD→STT→LLM→TTS→VB-Cable) pour Google Meet | Appel LLM propre, non audité en détail (hors scope V2 initial) |
| `modules/voice/continuous_voice.py` | Décide seul quand annuler des tâches et déclenche `safety.request_stop()` directement | N'appelle pas de LLM lui-même, mais court-circuite la couche de décision centrale |

**Aucun de ces quatre ne doit survivre comme moteur de décision en V2.** Leurs comportements utiles (voir sections dédiées ci-dessus et rapport principal) deviennent des spécifications pour le Harness + Task Actors + Model Layer génériques, jamais des boucles dupliquées.

### 4. Infra distribuée — récupérable via git, pas perdue

L'audit initial signalait `core/agent/`, `core/devices/`, `core/remote/`, `core/wol/`, `core/gitops/`, `core/deploy/`, `core/routing/`, `core/storage/` comme "orphelins, code source disparu". **Vérification git effectuée :** ce code a été supprimé délibérément dans le commit `308492a` ("retour au local pas le choix", 2026-08-14) — probablement un choix conscient de revenir à une architecture locale plutôt qu'un accident. Il reste entièrement récupérable :

```bash
git show 308492a^:core/remote/cloudflare.py
git show 308492a^:core/devices/registry.py
git show 308492a^:core/routing/task_router.py
# etc. — tous les fichiers listés dans le tableau récapitulatif (#8) sont dans ce commit parent
```

`core/deploy/updater.py` (SafeUpdater — mise à jour git avec dry-run, vérification dirty-repo, tests avant déploiement, jamais d'auto-redémarrage) est en particulier un bon composant à **EXTRACT** pour la boundary `persistence`/déploiement du futur serveur central (blueprint §23).

De même, `modules/tasks/` (scheduler.py 233 lignes, store.py 34 lignes) et `modules/mobile/api.py` (437 lignes) ne sont pas dans un commit normal mais dans **`git stash@{0}`** (2026-08-09, jamais dépilé/nettoyé) :

```bash
git stash show -p stash@{0} -- modules/tasks/scheduler.py
git stash show -p stash@{0} -- modules/mobile/api.py
```

**Recommandation :** avant de conclure "rien à extraire" sur un module qui semble n'avoir que des `.pyc` orphelins, vérifier systématiquement `git log --all` et `git stash list` — ce cas s'est produit deux fois dans cet audit.

---

## Doublons / recoupements de nommage détectés (aucun n'est un vrai chevauchement de code)

| Paire | Nature réelle | Résolution V2 |
|---|---|---|
| `brain/` (racine) vs `modules/brain/` vs le mot "cerveau" dans les logs | 3 systèmes complètement différents, aucun chevauchement de code, confusion de nommage pure | Un seul concept "Model Layer" nommé sans ambiguïté en V2 |
| `modules/apps/` vs `modules/appmanager/` | `apps` = scan/lancement d'apps déjà installées ; `appmanager` = installation/désinstallation via winget — complémentaires | Noms clarifiés : `apps.launch` vs `packages.install` |
| `modules/context/` vs `modules/context_bridge/` | `context` = perception+attention+context engine embryonnaire (3 fichiers sans rapport entre eux) ; `context_bridge` = accès mémoire cross-canal en lecture seule | Boundaries séparées : perception / attention / context / memory |
| `modules/memory/` vs `modules/raya_state/` | Déjà séparés au niveau fichier (facts JSON vs journal d'actions FIFO), mais aucun schéma formel confidence/provenance/freshness | World State (raya_state) vs Memory (memory/store.py) formalisés séparément |
| `calendar/` + `drive/` + `gcontacts/` + `gtasks/` | Vrai doublon STRUCTUREL — 4x le même pattern auth/client/manager/operations pour 4 APIs Google | Connecteur Google générique unique, paramétré par service |
| `modules/hologram/` vs `modules/models/` (3D) | Probablement complémentaires (rendu de scène vs sources de génération 3D) — non vérifié en détail, à croiser lors de l'implémentation | tools (generation.hologram vs generation.model_source) |

---

## Violations de principes blueprint confirmées dans V1

1. **Second cerveau autonome hors Harness** (×4 — voir section dédiée ci-dessus).
2. **Perception continue interdite** — `modules/vision/presence_watcher.py` fait de l'analyse webcam en boucle daemon permanente (`threading.Thread` en continu), exactement ce que le blueprint §6 interdit ("do not continuously inspect webcam content").
3. **Contrat modèle façon Anthropic comme fondation** — `core/llm.py` et `modules/brain/router.py` (BrainMessages) encodent en dur un format `_anthropic_to_ollama_messages`/`type="tool_use"`/`stop_reason`, exactement l'anti-pattern interdit §16.
4. **World State et Memory non formalisés** — séparés au niveau fichier mais sans schéma fact/confidence/freshness/source/status (§5) ni lifecycle candidate→active→confirmed→aging→obsolete (§11) ; `memory/store.py` n'a qu'un flag booléen `obsolete`.
5. **Task Actors absents** — aucun composant V1 n'a id/objective/state/priority/checkpoint/cancellation/dependencies/pause/resume réels (§12). `modules/async_engine/registry.py` est la meilleure base (statuts, cancel coopératif, listeners, resume_info) mais sans persistance, priorité, ni dépendances entre tâches.
6. **Interfaces couplées directement au Core** — `core/agent_state.py` (état global singleton) pousse directement vers `ui.state`/`modules.web.events` ; `modules/camera/stream.py` importe `ui.state` directement ; `modules/ghost/ghost_mode.py` appelle `core.llm` en bypass total de l'orchestrateur. Le principe "Interfaces are clients of the Core" (§3) est violé à plusieurs endroits, alors même que `modules/web/server.py` (le serveur web) le RESPECTE correctement (bon contre-exemple à suivre).
7. **Système prompt statique géant** — `config/personality.py` injecte un bloc de texte fixe par mode (Talk/Know/Act) plutôt qu'une sélection dynamique — anti-pattern que le Context Engine (§10) doit remplacer.

---

*Fin de la migration map. Voir `RAYA_V2_AUDIT_REPORT.md` pour l'architecture cible détaillée, les contrats à définir, l'ordre de migration et les risques.*
