# RAYA V2 — MIGRATION PLAN

**Statut :** Plan de séquencement, prêt pour revue. **Aucune suppression, aucun déplacement, aucune migration n'est exécutée à cette étape** — ce document planifie, il n'agit pas.
**Sources :** `RAYA_V2_MIGRATION_MAP.md` (verdicts par composant), `RAYA_V2_TECHNICAL_ARCHITECTURE.md`, `RAYA_V2_REPOSITORY_STRUCTURE.md`, `RAYA_V2_CONTRACTS.md`.

**Note sur le séquençage :** le blueprint (§27) propose 7 phases (0 à 6, Voice et Interfaces séparées). Ce plan en compresse 6, en fusionnant Voice et Interfaces web/desktop/mobile dans une seule Phase 5 finale — les deux dépendent d'un Harness déjà stable et n'ont pas de dépendance croisée entre elles qui justifierait de les garder séparées à ce niveau de granularité.

**Convention pour ce document :** la colonne "composants V1 à terme supprimés" **identifie** ce qui deviendra obsolète une fois la phase terminée et validée — elle ne déclenche AUCUNE suppression réelle. La suppression effective de code V1 n'a lieu qu'après validation explicite de l'utilisateur, phase par phase, jamais automatiquement.

---

## Phase 0 — Contrats d'architecture & squelette de repo

| | |
|---|---|
| **Nouveaux composants** | `raya/contracts/*` (17 contrats de `RAYA_V2_CONTRACTS.md` en dataclasses/pydantic), squelette complet de `raya/` (dossiers vides avec `__init__.py` + docstring de responsabilité), `pyproject.toml` (groupes de dépendances `[core]`/`[windows]`/`[voice]`/`[vision]`/`[3d]`), configuration de lint d'imports (règle de dépendance §20 de `RAYA_V2_REPOSITORY_STRUCTURE.md`), CI minimale (lint + tests contrats). |
| **Composants V1 utilisés (spec comportementale)** | `RAYA_FINAL_AUDIT.md` (invariants déjà validés en V1 — liste de départ pour `RAYA_V2_ARCHITECTURAL_INVARIANTS.md`). |
| **Composants V1 extraits** | Forme des dataclasses de `brain/core/brain.py` (`BrainEvent`, `TaskStatus`, `VerificationResult`) comme référence de conception pour `contracts/task.py`/`contracts/event.py` — **inspiration de forme, pas de code copié**. |
| **Composants V1 adaptés** | `config/paths.py` → `runtime/config.py` (résolution de chemins de données, `RAYA_DATA_DIR`). |
| **Composants V1 à terme supprimés** | Aucun — trop tôt, rien ne remplace encore de fonctionnalité réelle. |
| **Dépendances** | Aucune dépendance externe nouvelle au-delà de `pydantic` (ou équivalent dataclasses+validation) et d'un outil de lint d'imports (`import-linter`). |
| **Tests nécessaires** | `tests/contracts/` — sérialisation/désérialisation round-trip sans perte pour chacun des 17 contrats ; validation des invariants déclarés (ex : `Task` refuse une transition d'état illégale, `ModelResponse.finish_reason` refuse une valeur hors énumération native) ; un test négatif qui introduit délibérément un import interdit (ex : `devices` important `models`) et vérifie que le lint le bloque. |
| **Critère de réussite** | Les 17 contrats s'importent et se sérialisent sans erreur ; le squelette de repo s'importe sans dépendance circulaire ; le lint d'import échoue sur le cas de test négatif volontaire. |

---

## Phase 1 — Fondation : Harness minimal + World State + Memory + Context + STOP

| | |
|---|---|
| **Nouveaux composants** | `harness/loop.py` (boucle Architecture §3.1, avec un `ModelProvider` STUB et un `Tool` STUB — pas encore de vrais outils/modèles), `harness/session.py`, `world_state/store.py`, `memory/store.py` + `conversation.py` + `personal.py` + `experience.py` + `lifecycle.py`, `context_engine/assembler.py` (version simple : historique + faits récents, sans ranking sophistiqué), `persistence/sqlite_backend.py` + `file_backend.py`, `safety/stop.py` (STOP minimal — c'est la fondation la moins chère à porter, et le Harness en a besoin dès sa première ligne pour être testable en cancellation), `perception/sensors/window.py` + `resources.py` (capteurs légers, alimentent world_state dès cette phase), `interfaces/cli/repl.py` (client minimal — premier vrai consommateur du Harness, headless). |
| **Composants V1 utilisés (spec comportementale)** | Isolation stricte de canal (`test_channel_isolation.py`, `test_full_isolation.py`), priorité absolue du STOP (`test_final_audit.py` catégorie A/B). |
| **Composants V1 extraits** | `brain/core/brain.py` (forme de boucle plan→exécute→vérifie→SUCCESS/UNKNOWN/FAILURE→recover, **EXTRACT** de la forme, pas du mock) ; `modules/context/recent_work.py` (pattern d'assemblage conditionnel + cache TTL, **EXTRACT**) ; `modules/computeruse/safety.py` (**EXTRACT réel** — c'est du code, pas seulement un concept, il est déjà propre) ; `modules/context/monitor.py` + `modules/hud/monitor.py` (**EXTRACT** capteurs légers). |
| **Composants V1 adaptés** | `modules/conversations/store.py` → `memory/conversation.py` (**migration de données réelle**, voir §Données) ; `modules/memory/store.py` → `memory/personal.py` (migration de données réelle) ; `modules/identity/store.py` → `memory/experience.py` (migration de données réelle) ; `modules/context_bridge/bridge.py` → `memory/channel_bridge.py` (logique d'isolation adaptée au nouveau schéma). |
| **Composants V1 à terme supprimés** | `modules/raya_state/state.py` (remplacé fonctionnellement par `world_state/store.py`, une fois ce dernier validé) ; les historiques par canal internes à `core/orchestrator.py` (remplacés par `memory/conversation.py` consultée via `HarnessState.history_ref`). |
| **Dépendances** | Phase 0 complète (contrats, lint). |
| **Tests nécessaires** | `tests/harness/` (boucle complète avec stubs : assemble→model stub→tool stub→verify→continue/complete, cancellation via STOP interrompt une boucle en cours) ; `tests/memory/` (lifecycle candidate→obsolete, isolation stricte de canal, **test de migration** rejouant un export réel — anonymisé — de `modules/conversations/store.py` V1 et vérifiant l'intégrité ligne à ligne) ; `tests/world_state/` (freshness/TTL, superseded, requête par domaine) ; `tests/persistence/` (migrations de schéma idempotentes, rejouables sans perte) ; `tests/safety/test_stop.py` (STOP multi-source, priorité absolue, non réinjecté après interruption — invariants déjà validés en V1 à reproduire à l'identique). |
| **Critère de réussite** | Un scénario "question/réponse simple, sans outil" tourne de bout en bout via `interfaces/cli/repl.py` → `harness/loop.py` avec un modèle stub, l'échange est persisté réellement dans `memory/conversation.py` (SQLite) et **survit à un redémarrage du process** ; un `world_state` fact expire correctement selon son TTL ; STOP interrompt immédiatement une boucle Harness stub en cours d'exécution. |

---

## Phase 2 — Attention + Task Actors

| | |
|---|---|
| **Nouveaux composants** | `attention/evaluator.py` + `policy.py` + `interruption.py`, `tasks/registry.py` + `actor.py` + `dependencies.py`. |
| **Composants V1 utilisés (spec comportementale)** | Politique hold-and-flush/urgent-bypass de `modules/awareness/monitor.py` ; politique Ne-Pas-Déranger de `modules/context/dnd.py`. |
| **Composants V1 extraits** | `modules/async_engine/registry.py` (**EXTRACT réel** — `Task`/`TaskContext`, statuts, cancellation coopérative, listeners : c'est le composant le plus proche du concept Task Actor, il est porté et étendu, pas réinventé). |
| **Composants V1 adaptés** | `modules/planning/detector.py` + `plan_store.py` → pré-filtre déterministe dans `cognition/prioritization.py` (Phase 3) + un plan devient un `Task` avec `dependencies` entre étapes ; `modules/reminders/scheduler.py` (parsing FR des dates, **ADAPT** de la logique de parsing uniquement) → un `Task` de type `reminder`. |
| **Composants V1 à terme supprimés** | `modules/async_engine/registry.py` (une fois `tasks/registry.py` opérationnel et strictement supérieur : persistance réelle, priorité, dépendances) ; `modules/reminders/scheduler.py` (la boucle daemon elle-même — le parsing de dates FR survit ailleurs) ; `modules/tasks/` (dossier déjà vide en V1, rien à faire). |
| **Dépendances** | Phase 1 (world_state, persistence, memory stables). |
| **Tests nécessaires** | `tests/tasks/` (lifecycle complet PENDING→RUNNING→PAUSED→RUNNING→COMPLETED, **pause/resume réel avec reprise exacte au checkpoint** — pas depuis zéro, cancellation coopérative, détection de cycle dans les dépendances) ; `tests/attention/` (les 4 décisions selon les facteurs déclarés, latence bornée en millisecondes SANS appel réseau/modèle) ; `tests/concurrency/test_background_task_concurrency.py` (**invariant central** : une session de conversation répond pendant qu'une Task de fond distincte s'exécute, sans lock global bloquant l'une pour l'autre). |
| **Critère de réussite** | Une `Task` de fond mise en pause reprend exactement à l'étape suivant son dernier checkpoint (vérifié par assertion sur le contenu du checkpoint, pas seulement sur l'état) ; `attention.evaluate()` répond en dessous d'un seuil de latence fixé sans jamais invoquer `models` ; le test de concurrence Task/conversation passe sans deadlock ni faux du sérialisation. |

---

## Phase 3 — Cognition + Tool System + Model Layer + Safety complète

| | |
|---|---|
| **Nouveaux composants** | `cognition/intent.py` + `ambiguity.py` + `verification.py` + `recovery.py` + `prioritization.py` ; `tools/registry.py` + `discovery.py` + `validation.py` + `execution.py` + `catalog/*` (documents, calendar, files, hologram, notes, mail, utils dans un premier temps — sans device réel encore, voir Phase 4) ; `models/registry.py` + `router.py` + `providers/base.py` + `providers/ollama_cloud.py` + `providers/ollama_local.py` ; `safety/risk.py` + `permissions.py` + `audit.py` (complètent `safety/stop.py` de Phase 1). |
| **Composants V1 utilisés (connaissances opérationnelles)** | `think=False` requis pour `deepseek-v4-flash:cloud` ; mapping `done_reason="length"` → `finish_reason: truncated` ; catalogue des 83 schémas d'outils de `core/tools.py` comme liste de référence des capacités à réexposer. |
| **Composants V1 extraits** | `brain/cognition/{ambiguity,verification,recovery,prioritization,entities,intent,reasoning}.py` (**EXTRACT réel** — les concepts ET une bonne partie de la logique heuristique sont directement portables, à connecter à un vrai modèle plutôt qu'au mock `LabEnv`) ; `brain/models/interface.py` (**EXTRACT** — `ModelProvider` par capability est la meilleure graine du Model Layer) ; `core/session_mode.py` (**EXTRACT** — logique de gating dynamique des outils, portée dans `cognition/intent.py` + utilisée par `context_engine` pour filtrer `available_tools`) ; `modules/brain/router.py` (**EXTRACT** l'heuristique "routage par nature de tâche" uniquement, comme une règle parmi d'autres de `models/router.py` — **DELETE** le contrat `BrainMessages` lui-même) ; `modules/appmanager/safety.py`, `modules/pc_control/engine.py::_RISK`, `modules/browser/controller.py` (garde-fous champs sensibles/URLs), `modules/face/manager.py::verify_is_owner()` (**EXTRACT** — précédents concrets pour généraliser `safety/risk.py`). |
| **Composants V1 adaptés** | `modules/documents/*`, `modules/files/upload_pipeline.py`, `modules/hologram/*` (moteur **KEEP** quasi tel quel derrière le contrat Tool), `modules/notes/*`, `modules/utils/*`, `modules/calendar`+`drive`+`gcontacts`+`gtasks` (unifiés derrière un connecteur Google générique), `modules/mail/*`. |
| **Composants V1 à terme supprimés** | `core/llm.py` (contrat Anthropic-shaped entier) ; `brain/` racine en totalité (`core/brain.py`, `learning/`, `skills/`, `lab/` — les parties utiles ont déjà été extraites vers `cognition/`/`models/`) ; `modules/brain/router.py` (le contrat `BrainMessages`) ; `core/tools.py` (le fichier monolithique 4521 lignes — remplacé par `tools/catalog/*` éclaté). |
| **Dépendances** | Phase 1 (`context_engine` pour filtrer les tools/mémoire exposés au modèle), Phase 2 (`tasks` pour les tools qui créent du travail de fond). |
| **Tests nécessaires** | `tests/models/` (routing par capability, fallback multi-provider, **test négatif explicite** vérifiant qu'aucun champ `type="tool_use"`/`stop_reason` façon Anthropic n'apparaît dans `ModelResponse`) ; `tests/tools/` (discovery filtré par capability_tags — jamais "tous les outils" —, validation de schéma stricte, `ToolCall` refusé si `safety.check_permission()` refuse) ; `tests/cognition/` (ambiguïté bloquante pose la plus petite question, ambiguïté non-bloquante continue, vérification tri-état) ; `tests/safety/` (action `destructive` sans `granted_by` toujours refusée, tout refus est audité) ; `tests/integration/test_ollama_cloud_live.py` (vrai appel réseau Ollama Cloud, `capability=reasoning`, marqué explicitement comme test "live" — statut `BLOCKED` honnête si la clé API n'est pas configurée en CI, jamais un faux PASS). |
| **Critère de réussite** | Un `ToolCall` complet (discovery → validation → permission → exécution simulée → vérification → audit) fonctionne de bout en bout ; un `ModelRequest` avec `capability=coding` route vers le bon `ModelDescriptor` ; aucune couche au-dessus de `models/providers/` ne voit jamais un format spécifique à Ollama ou à un ancien SDK. |

---

## Phase 4 — Environment Agents (Windows + Browser + Camera)

| | |
|---|---|
| **Nouveaux composants** | `devices/base.py`, `devices/windows/agent.py` + `mechanisms/*` + `strategy.py`, `devices/browser/agent.py` + `session.py` + `worker.py` + `controller.py`, `devices/camera/agent.py` + `vision_ops.py`, `perception/capture/*` (camera/screen à la demande), `perception/sensors/presence.py` (capteur léger reconstruit), `tools/catalog/pc.py` + `browser.py` (déclarations reliant les capabilities aux Device Agents). |
| **Composants V1 utilisés (connaissances)** | Échelle de stratégies DOM→UIA→vision déjà validée en V1 (base de `devices/windows/strategy.py`). |
| **Composants V1 extraits** | `modules/pc_control/{elements,keyboard,mouse,clipboard,windows,applications,wait,vision_bridge}.py` (**EXTRACT réel**) ; `modules/pc_control/router.py` (**EXTRACT** → `devices/windows/strategy.py`) ; `modules/appmanager/manager.py`+`winget.py`, `modules/apps/{launcher,scanner}.py` (**EXTRACT réel**) ; `modules/system/*`, `modules/media/controller.py` (**EXTRACT réel**) ; `core/platform/windows.py` (**ADAPT/EXTRACT** base) ; `modules/browser/{session,worker,controller}.py` (**EXTRACT réel** — cycle de vie CDP, sérialisation thread, primitives navigate/click/type/read_page + garde-fous sensibles) ; `modules/vision/{stream_manager,camera_registry,object_detection,ocr,face_recognition,analyze}.py`, `modules/camera/stream.py`, `modules/face/{manager,encoder,database}.py` (**EXTRACT/ADAPT réel**). |
| **Composants V1 adaptés** | `modules/browser/recipes.py` (la SÉQUENCE d'étapes par site, pas le mécanisme CDP) → réécrite comme définitions de tools multi-étapes dans `tools/catalog/browser.py`, orchestrées par le Harness avec vérification/replanning à chaque étape plutôt qu'en script figé. |
| **Composants V1 à terme supprimés** | `modules/pc_control/auto_agent.py` (**suppression totale, aucun code repris** — c'est le second cerveau à éliminer, sa capacité "PC autonome" devient une `Task` normale exécutée par le Harness) ; `modules/pc_control/engine.py` (dispatch+policy — remplacé par `tools/execution.py` + `safety/risk.py`) ; `modules/vision/presence_watcher.py` (version boucle-daemon-continue-avec-LLM, remplacée par le capteur léger `perception/sensors/presence.py`) ; `modules/pc_control/vision_bridge.py` (remplacé par un appel `models` capability=vision normal, orchestré par le Harness). |
| **Dépendances** | Phase 3 complète (Tools/Safety/Models doivent exister pour que les Device Agents soient invoqués avec permission checks et appels vision réels, pas mockés). |
| **Tests nécessaires** | `tests/devices/windows/` (**test réel, pas mocké** — lancer une application connue, vérifier via `world_state` que la fenêtre est apparue) ; `tests/devices/browser/` (**test réel** — navigation vers une page connue, extraction de contenu, marqué "live") ; test d'intégration "recette multi-étapes" (ex : recherche+lecture d'une page) exécutée comme séquence de `ToolCall` avec vérification à chaque étape, capable de replanifier si une étape échoue ; test confirmant qu'aucun `Device Agent` n'appelle jamais `models` directement (grep automatisé en CI, pas seulement une revue manuelle). |
| **Critère de réussite** | "Lancer une application" et "naviguer sur un site puis vérifier le résultat" fonctionnent de bout en bout réellement (Harness→Tools→Devices, aucune boucle de décision locale au Device Agent) ; le capteur de présence émet un event sans jamais invoquer un modèle ; le grep CI "aucun import `models` dans `devices/`" passe. |

---

## Phase 5 — Realtime Voice + Interfaces (Web / Desktop / Mobile)

| | |
|---|---|
| **Nouveaux composants** | `interfaces/voice/{vad,transcription,synthesis,channel}.py` (**REBUILD complet** de l'architecture, connecté à `attention`+`harness` plutôt qu'autonome), `interfaces/web/{server,routes}.py`, `interfaces/mobile/ios_bridge.py`, `interfaces/desktop/app.py` (optionnel selon priorité, peut être différé au-delà de cette phase sans bloquer le reste). |
| **Composants V1 utilisés (spec comportementale, contrat comportemental à reproduire à l'identique)** | VAD (Silero) + barge-in avec anti-écho calibré + phrases de stop/annulation + timeout de silence + streaming TTS interruptible + mode micro ouvert — tous déjà validés par `test_barge_in.py`, `test_tts_router.py`, portions de `test_final_audit.py`/`test_realtime_interaction.py`. |
| **Composants V1 extraits** | Mécanismes purs de `modules/voice/{stt,tts_kokoro,tts_router}.py` (appels Whisper/Kokoro eux-mêmes, **EXTRACT** — ce ne sont pas des "cerveaux", ce sont des mécanismes de transcription/synthèse) ; VAD Silero (mécanisme). |
| **Composants V1 adaptés** | `modules/web/server.py` (**ADAPT quasi direct** — déjà bien découplé en V1, seul le point d'appel change de `orchestrator.process()` vers `harness.handle_request()`) ; `modules/ios/api.py` (**ADAPT**). |
| **Composants V1 à terme supprimés** | `modules/voice/continuous_voice.py` (la LOGIQUE DE DÉCISION — remplacée par emission d'events consommés par `attention`/`harness`, plus jamais d'appel direct à `async_engine`/`safety` depuis le canal voix) ; `modules/ghost/ghost_mode.py` (bypass — remplacé par `interfaces/cli` avec `HarnessRequest.profile=fast_minimal_context`, ou un nouveau petit client équivalent qui respecte le contrat) ; `ui/` (ancien HUD pywebview — remplacé quand `interfaces/desktop` est prêt, peut coexister en fallback tant que ce n'est pas le cas) ; `modules/meet/agent.py` (hors scope V2 initial, à traiter en canal `interfaces` spécialisé plus tard si confirmé prioritaire). |
| **Dépendances** | Phase 1 à 4 stables (Harness complet, Devices opérationnels si la voix doit déclencher des actions PC/navigateur). |
| **Tests nécessaires** | Tests comportementaux voix portés en intégration réelle (barge-in interrompt bien la synthèse en cours, STOP vocal atteint `safety.stop()` via un `Event`, jamais via un appel direct au registre de tâches) ; test d'intégration web (une session `interfaces/web` complète via SSE, sans régression fonctionnelle perceptible côté utilisateur par rapport à V1). |
| **Critère de réussite** | L'interface web fonctionne sur le nouveau Harness sans régression fonctionnelle visible ; barge-in et STOP vocal conservent exactement les mêmes garanties qu'en V1, mesurées par les mêmes scénarios de test, mais sans qu'aucune logique de décision ne vive plus dans le canal voix lui-même. |

---

## Données existantes — ce qui doit être MIGRÉ, pas recréé

Principe directeur : **le code peut être reconstruit, les données utilisateur sont précieuses.** Chaque migration de données ci-dessous doit être **additive/versionnée, jamais destructive**, testée d'abord sur une COPIE du fichier réel, avec vérification d'intégrité (comptage de lignes, checksum) avant/après, et le fichier V1 original reste intact et non supprimé jusqu'à confirmation explicite de l'utilisateur que la bascule est validée.

| Donnée V1 | Emplacement | Destination V2 | Méthode | Risque |
|---|---|---|---|---|
| **Conversations** | `modules/conversations/store.py` — SQLite (probablement sous `memory/` dans le repo V1, résolu via `config/paths.py`) | `memory/conversation.py` — même moteur SQLite, schéma étendu | **Migration de schéma additive** : `ALTER TABLE`/nouvelles colonnes pour `lifecycle`/`confidence`/`provenance` avec valeurs par défaut sur les lignes existantes (`lifecycle=confirmed`, `confidence=known_fact`) — jamais de `DROP`/réécriture complète de table | **Élevé** — historique réel de conversations de l'utilisateur. Migration testée sur copie d'abord, obligatoire. |
| **Faits mémorisés** | `modules/memory/store.py` — `facts.json` | `memory/personal.py` — table structurée | Script d'import un-shot : chaque entrée JSON → `MemoryEntry{type=fact, lifecycle=confirmed, confidence=known_fact}` sauf entrées marquées `obsolete=true` en V1 → `lifecycle=obsolete` directement. `facts.json` original archivé, jamais supprimé. | **Élevé** — connaissances accumulées sur l'utilisateur. |
| **Ajustements comportementaux (identity)** | `modules/identity/store.py` | `memory/experience.py` | Import direct — la contrainte V1 "écriture explicite uniquement, bornée, dédoublonnée" est déjà compatible avec le contrat `MemoryEntry` (`provenance: "user:explicit_correction"`, `lifecycle=confirmed`). | **Moyen** — volume faible mais valeur élevée (RAYA "oublie" l'utilisateur si perdu). |
| **Notes** | `modules/notes/*` — store JSON | `tools/catalog/notes.py` (backing store conservé quasi tel quel, `modules/notes` étant **KEEP**) | Copie directe, pas de transformation de schéma nécessaire dans un premier temps. | **Faible-Moyen.** |
| **Rappels/tâches en attente** | `modules/reminders/scheduler.py` (store propre à ce module — **à vérifier précisément l'emplacement réel au moment de l'implémentation**, l'audit n'a pas confirmé de fichier de données dédié séparé du code) | `tasks/registry.py` — chaque rappel en attente devient un `Task{type=reminder, state=PENDING, checkpoint={due_at, message}}` | Script d'import qui préserve l'horaire dû exact ; à exécuter et vérifier manuellement (liste des rappels avant/après) avant toute désactivation de l'ancien scheduler. | **Moyen** — un rappel manqué est visible immédiatement par l'utilisateur, bonne détectabilité si la migration échoue. |
| **Visages enregistrés** | `memory/faces/{Nom}/` — embeddings + photos | `devices/camera/` — store dédié, format conservé | Copie de fichiers directe, **traitée avec la même prudence que des credentials** (données biométriques) — pas de transformation de format sans nécessité. | **Élevé au sens confidentialité**, faible au sens technique (copie simple). |
| **Tokens OAuth (Gmail/Calendar/Drive/Contacts/Tasks)** | `credentials/`, `mail_token_default.json`, `token.json`... | Reste hors du repo, chemin résolu par `runtime/config.py` | **Pas une migration de données applicative** — ces fichiers ne changent pas de format, seule leur résolution de chemin doit continuer à fonctionner à l'identique. Ne jamais les committer, ne jamais les régénérer sans nécessité. | **Élevé au sens sécurité** — à traiter comme tel indépendamment de la migration d'architecture. |

**Recommandation d'exécution (pour la future phase d'implémentation, pas maintenant) :** chaque migration de données ci-dessus doit être un script séparé, versionné, exécuté manuellement avec confirmation, jamais un effet de bord silencieux du démarrage de l'application V2. Un script de migration doit toujours pouvoir être exécuté en mode `--dry-run` qui rapporte ce qu'il FERAIT sans rien écrire, avant tout run réel — même principe que `core/deploy/updater.py` (`apply=False` par défaut, **EXTRACT** de ce principe de sécurité).

---

*Fin du plan de migration. Voir `RAYA_V2_ARCHITECTURAL_INVARIANTS.md` pour la stratégie de test transversale et les invariants à vérifier automatiquement à chaque phase.*
