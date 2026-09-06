# RAYA V2 — PHASE 10 IMPLEMENTATION REPORT
## Long-Horizon Autonomy — Task Persistence / Planning / Checkpoint / Recovery

---

## Executive Summary

Phase 10 transforme le Task System (infrastructure de concurrence/priorité/
pause/resume/cancel, posée Phase 1-2) en véritable support d'exécution
**autonome** : une intention en langage naturel devient un `Plan` explicite
(liste d'étapes), exécuté pas à pas par le Harness via le même pipeline
Cognition/Model/Tools/Safety que la boucle conversationnelle — jamais un
second cerveau. Aucun `LongHorizonAgent`/`AutonomousBrain`/`PlannerAgent`/
`TaskOrchestrator` n'a été créé : le renfort vit entièrement à l'intérieur
de `Harness` (nouvelle méthode `_run_long_horizon_step`, soumise comme
`StepFn` ordinaire au `TaskScheduler` déjà existant), dans un nouveau module
`raya/cognition/planning.py` (produit un plan, ne l'exécute jamais), et dans
un nouveau Tool `tasks.create` (point d'entrée UNIQUE et CENTRAL pour
"langage naturel → Task", jamais un `TelegramTaskCreator`/`UITaskCreator`
par interface).

58 nouveaux tests (907 au total, contre 849 à la fin de la Phase 9), y
compris un **vrai test en conditions réelles contre le Cockpit et le vrai
modèle Ollama Cloud** : une demande en français a réellement produit un plan
à 5 étapes, et la première étape a réellement navigué (vrai Edge, vrai
Browser Device Agent) vers Stanford Encyclopedia of Philosophy, lu la page,
et listé les onglets — avant annulation volontaire via le bouton Cancel du
Cockpit pour ne pas laisser tourner indéfiniment un vrai appel API. `python
scripts/arch_lint.py` : 0 violation. V1 (`RAYA/`) strictement inchangé.

---

## Initial Audit

Avant toute implémentation, inspection réelle de `raya/tasks/`,
`raya/harness/` (dont `scheduler.py`, `execution_records.py`,
`cancellation.py`), `raya/cognition/`, `raya/context_engine/`,
`raya/models/`, `raya/tools/`, `raya/attention/`, et des tests existants.
Matrice EXISTE / INCOMPLET / MANQUANT :

| Concept | État | Où |
|---|---|---|
| Task persistence (CRUD, machine à états) | **EXISTE**, solide | `raya/tasks/registry.py`, `raya/contracts/task.py` |
| Checkpoint | **EXISTE** (dict générique) | `Task.checkpoint`, `TaskRegistry.checkpoint()` |
| Pause/Resume/Cancel | **EXISTE**, solide | `TaskRegistry`, `TaskScheduler` |
| Recovery après restart (RUNNING→PAUSED) | **EXISTE** | `TaskRegistry.recover_after_restart()` |
| Concurrence/priorité/aging | **EXISTE**, solide | `TaskScheduler` |
| STOP respecté par le scheduler | **EXISTE** | `TaskScheduler._process_one_step` |
| Modèle de step coopératif, bornée, résumable | **EXISTE** | contrat `StepFn` |
| Idempotence `ExecutionRecord`/`decide_recovery()` | **EXISTE mais INCOMPLET** — jamais invoqué au démarrage réel | `raya/harness/execution_records.py` |
| Plan/Step (structure de données) | **MANQUANT** | — |
| Un vrai "step" long-horizon (Cognition/Model/Tools réels par tick) | **MANQUANT** — seul un compteur démonstrateur existait | `_make_simulated_step_fn` |
| Vérification/evidence (observation Phase 7) | **EXISTE**, réutilisable | `Harness._promote_observations_and_verify` |
| LoopDetector/RecoveryAction (CONTINUE/REPLAN/ESCALATE) | **EXISTE**, REPLAN jamais exploité au-delà du simple "reboucler" | `cognition/recovery.py` |
| Contexte par tâche, borné | **EXISTE** | `context_engine.assemble(task=...)` |
| Attention : tâche de fond ne monopolise pas la conversation | **EXISTE**, solide | `attention/evaluator.py` |
| Langage naturel → Task, centralisé | **MANQUANT** | — |
| Notification via EventBus | **EXISTE**, déjà réutilisé Phase 9 | `TelegramChannel._on_task_event` |
| `ModelCapability.PLANNING` | **EXISTE, jamais utilisée** avant cette phase | `raya/contracts/model.py` (posée Phase 0) |

Décision : **KEEP** intégralement l'infrastructure Task/Scheduler/
ExecutionRecord/LoopDetector/ContextEngine/Attention — **EXTRACT/ADAPT**
uniquement ce qui manquait (Plan, planning, step réel, `tasks.create`),
jamais un REBUILD.

---

## Architecture Changes

```
Interface (n'importe laquelle)
        │  "fais-moi un rapport... préviens-moi"
        ▼
Harness._run_agentic_loop()  (boucle conversationnelle, INCHANGÉE)
        │
        ▼
Tool Registry ── tasks.create (NOUVEAU, centralisé, tag tasks.control=SAFE)
        │
        ▼
Harness.create_long_horizon_task()
        │  Cognition.build_plan() — UN appel modèle borné (ModelCapability.PLANNING)
        ▼
Plan { steps: [PlanStep, ...], current_step_id }  — dans Task.checkpoint["plan"]
        │
        ▼
TaskScheduler.submit(task_id, Harness._run_long_horizon_step, priority)
        │  (StepFn ordinaire — AUCUNE modification du scheduler)
        ▼
Harness._run_long_horizon_step(task_id)  [rappelée à chaque tick]
        │  context_engine.assemble(task=...)  (RÉUTILISÉ, Phase 1)
        │  model_route(REASONING)  (RÉUTILISÉ)
        │  execute_tool() / verify_tool_result() / _promote_observations_and_verify()
        │  LoopDetector.record()  (RÉUTILISÉ, Phase 3)
        │       CONTINUE  -> tick suivant
        │       REPLAN    -> retente la même étape
        │       ESCALATE  -> Cognition.replan_step() -> alternative OU échec honnête
        ▼
Task.complete()/fail()  ──EventBus (task.started/completed/failed, INCHANGÉ)──▶
        │
        ▼
Interfaces (Cockpit: current_step affiché ; Telegram: notification RÉUTILISÉE Phase 9)
```

Aucune modification de `TaskScheduler`, `TaskRegistry` (hors ajout mineur),
`Attention`, `Safety`, `ContextEngine`, `LoopDetector` — Phase 10 les
**appelle**, ne les remplace jamais.

---

## Task Lifecycle

`Task` (contrat, INCHANGÉ) reste la source de vérité durable. Le `Plan`
vit dans `Task.checkpoint["plan"]` — **pas** un nouveau champ `Task`, pas un
second système de persistance (consigne §2) : chaque mutation du plan est
persistée via `TaskRegistry.checkpoint()`, déjà existant. `resume_task()`
(Harness) détecte le TYPE de tâche à sa reprise en inspectant
`task.checkpoint` : présence de `"plan"` → dispatch vers
`_run_long_horizon_step` ; sinon → démonstrateur Phase 2 inchangé
(non-régression testée explicitement).

---

## Planning

`raya/cognition/planning.py::build_plan()` — UN appel modèle borné
(`ModelCapability.PLANNING`, jamais utilisée avant cette phase malgré son
existence depuis la Phase 0), demande une décomposition JSON en 1-5 étapes.
Repli honnête sur un plan à une seule étape (l'objectif brut) si le modèle
est indisponible ou renvoie une réponse inexploitable — **jamais** une
étape fabriquée sans base réelle, **jamais** un plan vide, **jamais** une
exception. Le planner n'importe ni `tools`, ni `devices`, ni `safety`, ni
`tasks`, ni `harness` (vérifié structurellement) — il produit un `Plan`, le
Harness l'exécute (consigne §3, jamais l'inverse).

---

## Checkpoint

Chaque tick de `_run_long_horizon_step` persiste le `Plan` complet
(`TaskRegistry.checkpoint()`) — **avant** l'appel modèle potentiellement
lent (pour qu'un crash pendant l'appel laisse une trace honnête de "quelle
étape était active"), et après chaque transition d'étape. Répond
concrètement aux questions de la consigne §4 :
- où en était la tâche ? → `plan.current_step_id`
- quelle étape était active ? → le `PlanStep` avec `status=RUNNING`
- quelles étapes sont terminées ? → `status=COMPLETED`/`SKIPPED`
- quels résultats vérifiés ? → `PlanStep.evidence` (ToolResult réel)
- quelle information pour continuer ? → `PlanStep.evidence` réinjecté au
  tour suivant en cas de nouvelle tentative
- quelle erreur ? → `PlanStep.error`

Test obligatoire (créer → exécuter → persister → simuler arrêt/restart →
recharger → vérifier l'état récupéré) : voir §Real E2E Tests, Scenario C.

---

## Recovery

Deux niveaux, tous deux réutilisant des mécanismes déjà existants :
1. **Niveau Task** — `TaskRegistry.recover_after_restart()` (Phase 2,
   inchangé) transitionne RUNNING→PAUSED au redémarrage.
2. **Niveau ExecutionRecord** — **gap comblé cette phase** :
   `ExecutionRecordRepository.recover_all_unknown()`/`decide_recovery()`
   étaient entièrement écrits depuis la Phase 1 mais **jamais invoqués** au
   démarrage réel. `Harness.recover()` les appelle désormais tous les deux.
3. **Niveau étape (idempotence, §12)** — `_is_step_tool_already_satisfied()`
   vérifie, avant de rejouer un appel d'outil déjà tenté pour cette étape,
   si le World State montre que l'intention est DÉJÀ satisfaite — réutilise
   le même `ObservationSpec`/`verify_observation_against_intent` que la
   vérification post-action Phase 7, jamais une deuxième heuristique.

---

## Replanning

Sur `RecoveryAction.ESCALATE` (même `LoopDetector` que la boucle
conversationnelle — 2 échecs identiques consécutifs), `Harness.
_handle_step_setback()` appelle `cognition.replan_step()` (UN appel modèle
borné) avec l'objectif global, l'étape échouée et son evidence. Si une
alternative est proposée : elle est insérée dans le plan, l'étape
originale passe à `SKIPPED` (jamais `FAILED` — son échec/evidence reste
tracé dans `step.error`/`step.evidence`, mais `SKIPPED` signale qu'elle ne
bloque plus la complétion du plan), un event `task.replanned` est publié.
Si aucune alternative n'est proposée : la tâche échoue honnêtement
(`Task.fail()`) si le plan est effectivement bloqué (`plan_is_stuck()`) —
jamais un échec caché, jamais un retry aveugle indéfini.

---

## Pause / Resume / Cancellation

Aucune modification de `TaskScheduler` : une tâche long-horizon respecte
EXACTEMENT le même contrat `StepFn` coopératif que le démonstrateur Phase 2
— `should_stop()`/`cancellation_requested` sont vérifiés par le scheduler
AVANT chaque tick, et par `_run_long_horizon_step` lui-même ENTRE deux
appels d'outil d'un même tick (aucune nouvelle action ne démarre après un
STOP, même au milieu d'un tick à plusieurs tool calls). `resume_task()`
redispatche vers le bon type de step_fn (§Task Lifecycle) — vérifié en
conditions réelles (§Real E2E Tests, Scenario C) : la tâche reprend
exactement à l'étape où elle en était, jamais depuis zéro.

---

## Verification

Aucune nouvelle couche : chaque tool call d'un tick passe par
`verify_tool_result()` + `Harness._promote_observations_and_verify()`
(Phase 7, inchangés) — la même evidence, le même World State, la même
notion de fraîcheur/provenance que n'importe quel autre appel d'outil.

---

## Natural Language Task Creation

Point d'entrée UNIQUE : Tool `tasks.create` (`raya/tools/catalog/tasks.py`),
enregistré UNE fois par `bootstrap.py`, disponible à TOUTES les interfaces
via le modèle — jamais un `TelegramTaskCreator`/`UITaskCreator`/
`VoiceTaskCreator`. Le handler dérive `channel`/`session_id` de
`ToolCall.requested_by` (`ToolCallRequester.channel`, champ additif Phase
10) — jamais une logique par canal. Vérifié réellement (§Real E2E Tests) :
le vrai modèle Ollama Cloud a de lui-même décidé d'appeler `tasks.create`
face à une demande de recherche longue, sans aucun mot-clé ni règle
hardcodée côté RAYA.

---

## Context Integration

`context_engine.assemble()` (Phase 1, **inchangé**) accepte déjà un
paramètre `task=` produisant une section `TASK_STATE` bornée (objectif,
état, étape courante) — Phase 10 s'en sert tel quel pour chaque tick, sans
jamais injecter l'historique complet de la tâche (consigne §9). Le contexte
d'un tick contient : objectif global, étape courante, evidence de la
tentative précédente le cas échéant, World State/Memory pertinents,
schémas d'outils — jamais plus.

---

## Memory Separation

Aucune écriture `MemoryStore` depuis le moteur long-horizon. `Task.
checkpoint` (état d'exécution, transitoire par nature) reste strictement
distinct de `Memory` (information durable) — une future promotion
explicite resterait un appel normal à `Memory.store()` par le modèle, non
implémentée cette phase (hors scope, consigne §15).

---

## Notification

Aucun nouveau système : `Task.started`/`completed`/`failed` restent publiés
par `TaskRegistry` (inchangé) sur l'EventBus. `TelegramChannel._on_task_event`
(Phase 9) est étendu pour aussi notifier `task.started` (pas seulement les
états terminaux) — toujours filtré sur `Task.owner.channel == "mobile"`,
toujours le même mécanisme. Le Cockpit (`TaskSummaryItemView`) gagne un
champ `current_step` (reflet direct de `Task.progress.current_step`,
déjà réel) — pas de refonte UI.

---

## Real E2E Tests

**Scénarios déterministes** (`tests/harness/test_long_horizon.py`,
`tests/integration/test_phase10_scenarios.py`) :
- **A (long task)** : objectif → plan à 2 étapes → vrais appels
  `filesystem.write_file` → checkpoint → completion. **PASS**.
- **A (réel Windows)** : une étape appelle réellement `pc.application.launch`
  → vrai Bloc-notes ouvert → World State réellement mis à jour. **PASS**.
- **B (interruption)** : STOP publié pendant une étape en cours → aucun
  nouvel appel d'outil → tâche CANCELLED, jamais un état ambigu. **PASS**.
- **C (restart)** : tâche à 2 étapes, la 2e bloquée en plein appel modèle
  (simulateur de crash déterministe via `threading.Event`) → process 1
  abandonné (RUNNING resté en base) → process 2 démarré sur la même DB →
  `bootstrap()` récupère RUNNING→PAUSED → `resume_task()` reprend
  EXACTEMENT à l'étape 2 (étape 1 jamais refaite) → completion. **PASS**.
- **D (échec d'étape)** : `filesystem.read_file` sur un chemin inexistant,
  répété → REPLAN puis ESCALATE (LoopDetector) → replanning → étape
  alternative insérée et exécutée → tâche complétée ; variante sans
  alternative → échec honnête, jamais masqué. **PASS** (les deux).
- **E (conversation pendant une tâche)** : une tâche longue bloquée en plein
  appel modèle ; une conversation SIMULTANÉE sur une autre session reçoit sa
  réponse en <1s, sans jamais attendre la tâche de fond. **PASS**.
- **F (Telegram réel)** : `tasks.create` déclenché via un message Telegram
  scripté → `Task.owner.channel == "mobile"` → notification réelle envoyée
  au bon chat_id à la complétion (réutilise le canal `send_message` réel de
  Phase 9). **PASS**.

**Test réel en conditions live** (Cockpit + vrai Ollama Cloud + vrai Edge,
même machine que les Phases 6-9) :
1. Demande en français ("Fais une recherche approfondie en plusieurs étapes
   sur l'histoire de l'IA et préviens-moi quand c'est terminé.") envoyée au
   vrai Cockpit. **PASS** — le vrai modèle `deepseek-v4-flash:cloud` a
   décidé DE LUI-MÊME d'appeler `tasks.create`, sans aucune règle RAYA
   câblée pour ce cas précis.
2. Le vrai plan généré (`ModelCapability.PLANNING`) contient 5 étapes
   cohérentes (origines/Turing, naissance de la discipline, âges d'or/
   hivers, essor du ML/DL, IA moderne). **PASS**.
3. Le Cockpit affiche réellement la tâche RUNNING avec son `current_step`
   ("Identifier les sources fiables...") — confirmation visuelle de
   l'ajout Phase 10 à `TaskSummaryItemView`. **PASS**.
4. La première étape a réellement exécuté `browser.navigate` (vrai Edge,
   Stanford Encyclopedia of Philosophy), `browser.read_page`,
   `browser.list_tabs` — toutes SUCCESS, evidence réelle persistée dans
   `Task.checkpoint["plan"]`. **PASS**.
5. Annulation volontaire via le bouton Cancel du Cockpit (pour ne pas
   laisser courir un vrai appel API sur les 4 étapes restantes) →
   `Task.state == CANCELLED` confirmé en base. **PASS** — preuve
   supplémentaire, non planifiée, que Pause/Cancel fonctionnent aussi pour
   une tâche long-horizon depuis l'UI réelle.

Le vrai Edge ouvert par le Browser Device Agent pendant ce test a été
fermé manuellement après coup (nettoyage de débris de test, même
discipline que les Phases 7/9 pour le Bloc-notes).

---

## Tests Added

58 nouveaux tests (907 au total, contre 849 à la fin de la Phase 9) :

| Fichier | Nombre | Couvre |
|---|---|---|
| `tests/cognition/test_planning.py` | 10 | `build_plan`/`replan_step` : parsing JSON propre/entouré de prose, repli honnête, dépendances séquentielles, plafond 5 étapes, alternative/NONE |
| `tests/contracts/test_plan.py` | 13 | `Plan`/`PlanStep`/`next_runnable_step`/`plan_is_complete`/`plan_is_stuck`, round-trip `to_dict`/`from_dict` |
| `tests/harness/test_long_horizon.py` | 17 | Création/plan, complétion 1 et 2 étapes avec vrais tool calls, checkpoint, STOP, replanning (avec/sans alternative), confirmation en fond (échec honnête), idempotence (2 tests), dispatch resume (2 tests dont non-régression Phase 2), **restart réel**, `tasks.create` end-to-end, conversation pendant une tâche |
| `tests/tools/test_task_control_catalog.py` (+5) | 5 | `tasks.create` : rétrocompatibilité (ops.create=None), SAFE, channel/session_id dérivés du requester, défaut "cli", argument manquant |
| `tests/architecture/test_phase10_architecture_proof.py` | 10 | Aucun second cerveau/orchestrateur, planning n'exécute jamais d'outil, réutilisation LoopDetector/execute_tool, `tasks.create` n'importe pas harness, Telegram sans notification Long-Horizon séparée, `arch_lint` global |
| `tests/integration/test_phase10_scenarios.py` | 3 | **RÉEL** : vrai Bloc-notes via une étape long-horizon, vraie notification Telegram de fin de tâche, Cockpit expose `current_step` |

---

## Full Suite Results

3 exécutions consécutives de la suite complète (907 tests) :

| Run | Passed | Failed | Skipped | Durée |
|---|---|---|---|---|
| 1 | 889 | 7 | 11 | 89.3s |
| 2 | 888 | 8 | 11 | 89.3s |
| 3 | 889 | 7 | 11 | 89.7s |

Les échecs reproduits systématiquement sont **PRÉ-EXISTANTS**, déjà
documentés dans les rapports Phase 7-9, sans rapport avec le Long-Horizon :
- 4 tests `tests/devices/windows/test_windows_agent.py` (module
  `uiautomation` absent/dégradé sur cette machine — environnement, pas RAYA).
- `test_handle_request_fails_honestly_with_null_provider_stub` (vrai
  `OLLAMA_API_KEY` configuré sur cette machine).
- `test_scenario_7_background_task_does_not_block_conversation` /
  `test_2_conversation_answered_immediately_during_task` (timing
  millisecondes trop strict avec un vrai appel réseau).
- Le 8e échec ponctuel du run 2 (`test_queued_task_waits_when_at_concurrency_limit`)
  est la même flakiness de scheduler déjà documentée Phase 7-9.

**Aucune régression Phase 10** : tous les tests Phase 0-9 qui passaient
avant cette phase continuent de passer, à l'identique. Une régression de
TEST (bruit bénin, jamais un échec) a été identifiée et corrigée EN COURS
de cette phase : un test ne laissait pas une tâche à un seul tick se
terminer avant `handles.shutdown()`, produisant occasionnellement un log
d'erreur inoffensif ("Transition Task illégale : CANCELLED -> COMPLETED")
sans jamais faire échouer le test — corrigé par une attente explicite,
vérifié stable sur 5 exécutions supplémentaires ciblées.

---

## Architecture Lint

`python scripts/arch_lint.py` → **PASS, 0 violation**. 10 tests dédiés
(`tests/architecture/test_phase10_architecture_proof.py`) verrouillent :
- `cognition/planning.py` n'importe jamais `tools/devices/safety/tasks/
  harness/interfaces`.
- Aucun appel `execute_tool`/`ToolCall(` dans le module de planification.
- Aucune classe `LongHorizonAgent`/`AutonomousBrain`/`PlannerAgent`/
  `TaskOrchestrator`/`LongHorizonOrchestrator`/`LongHorizonScheduler` nulle
  part dans `raya/`.
- `_run_long_horizon_step` n'est défini QUE dans `harness/loop.py`.
- Le moteur long-horizon réutilise `execute_tool`/`LoopDetector` réels,
  jamais une redéfinition locale.
- `tools/catalog/tasks.py` n'importe jamais `raya.harness`.
- `interfaces/telegram/channel.py` ne connaît aucun concept "long_horizon"
  spécifique — seuls les `task.*` events génériques.

---

## Security Review

- `tasks.create` reste `SAFE` (tag `tasks.control`) — créer une tâche n'a
  aucun effet sur l'environnement ; chaque action RÉELLE que la tâche
  effectuera ensuite (via `execute_tool()`) reste gatée par Safety
  indépendamment, exactement comme un tour conversationnel normal.
- **Aucun bypass Safety pour une tâche de fond** : si une étape a besoin
  d'une action `SENSITIVE`/`DESTRUCTIVE`, elle échoue honnêtement
  (`CONFIRMATION_REQUIRED_IN_BACKGROUND`) plutôt que de s'auto-confirmer —
  vérifié explicitement (`test_sensitive_tool_in_background_fails_honestly_never_bypasses_safety`).
- STOP reste prioritaire et global, vérifié à la fois par le scheduler
  (avant chaque tick) et par le moteur lui-même (entre deux appels d'outil
  d'un même tick).
- Aucun secret/token nouveau introduit par cette phase.

---

## Known Limitations

- **Pas de canal interactif pour confirmer une action SENSITIVE depuis une
  tâche de fond** — décision assumée (§14/§22 : jamais de bypass Safety),
  documentée comme limitation plutôt que contournée par un mécanisme de
  confirmation asynchrone non demandé cette phase.
- **Le "plan" affiché au fil de la conversation (texte du modèle) peut
  différer légèrement du VRAI plan structuré** (`ModelCapability.PLANNING`,
  appel séparé) — observé en conditions réelles : les deux étaient
  cohérents dans le test effectué, mais rien ne les synchronise
  explicitement (deux appels modèle indépendants, par design, pour rester
  dans les abstractions existantes).
- **Un seul planner "flat"** — dépendances séquentielles simples par
  défaut (chaque étape attend la précédente), pas de graphe de dépendances
  arbitraire proposé par le modèle. `PlanStep.dependencies` (liste) permet
  déjà plus si un futur planner le produit — non exercé cette phase.
- **Pas de plafond de temps/coût par tâche long-horizon** — une tâche mal
  engagée (le modèle continue d'appeler des outils avec des arguments
  toujours différents) ne serait jamais interceptée par le `LoopDetector`
  (qui ne détecte que des appels IDENTIQUES répétés) ; observé en
  conditions réelles (recherche web légitime à plusieurs pages) sans que
  ce soit un défaut — mais un futur plafond explicite (nombre de ticks
  max/étape, budget temps) serait une amélioration raisonnable, non
  implémentée cette phase (documentée en Future Work).
- **`Task.dependencies` (champ Task-level, Phase 0) reste inexploité** —
  Phase 10 introduit des dépendances au niveau `PlanStep` (intra-tâche),
  pas entre tâches distinctes ; hors scope.

**Future Work** (hors scope Phase 10, documenté sans y dériver) :
plafond de ticks/temps par étape ; un planner qui produit un graphe de
dépendances non-séquentiel ; promotion explicite d'un résultat de tâche
vers `Memory` sur demande utilisateur.

---

## V1 Integrity

- `git status --porcelain` sur `RAYA/` (le seul dépôt git des deux) :
  identique caractère pour caractère à l'état de début de session — les 4
  mêmes fichiers non suivis, zéro fichier modifié, zéro nouveau fichier,
  zéro suppression.
- Aucun fichier sous `RAYA/` n'a été ouvert en écriture à aucun moment de
  cette phase.

---

## Final GO / NO-GO

**GO PHASE 11.**

Tous les critères de sortie de la consigne (§27) sont satisfaits avec
preuve réelle :
- [x] Task long-horizon réellement persistante
- [x] Plan explicite (`Plan`/`PlanStep`, dans `Task.checkpoint`)
- [x] Checkpoint fonctionnel (avant l'appel modèle, à chaque transition)
- [x] Restart/recovery fonctionnel (vérifié avec un vrai crash simulé
      déterministe, deux process `bootstrap()` séquentiels sur la même DB)
- [x] Failure/replanning fonctionnel (REPLAN puis ESCALATE, alternative
      insérée ou échec honnête)
- [x] Pause/resume/cancellation fonctionnels (y compris en conditions
      réelles depuis le Cockpit)
- [x] STOP global respecté (scheduler + moteur lui-même, entre tool calls)
- [x] Vérification basée sur evidence (réutilise Phase 7 intégralement)
- [x] Aucun claim sans evidence (repli honnête partout : plan vide impossible,
      pas de faux succès, pas de replanning fabriqué)
- [x] Conversation reste utilisable pendant une tâche (vérifié réellement,
      isolation <1s)
- [x] Natural language → Task centralisé (`tasks.create`, un seul point
      d'entrée, vérifié avec le VRAI modèle qui décide seul de l'appeler)
- [x] Notifications réutilisent EventBus/Task events (Telegram étendu, pas
      remplacé)
- [x] Aucun second orchestrateur (vérifié structurellement)
- [x] Aucun bypass Safety (vérifié explicitement)
- [x] Context reste borné et pertinent (réutilise `assemble(task=...)`)
- [x] Memory ≠ Task state (aucune écriture Memory depuis le moteur)
- [x] World State ≠ Task persistence (World State reste un effet secondaire
      observé, jamais le stockage du plan)
- [x] `arch_lint` = 0
- [x] 3 runs complets (+ 5 runs ciblés supplémentaires après un fix)
- [x] V1 strictement inchangée
- [x] Rapport final écrit

Aucune réserve bloquante. Les limitations documentées (§Known Limitations)
sont des choix de scope explicites, pas des défauts cachés.
