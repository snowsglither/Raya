# RAYA V2 — PHASE 2 IMPLEMENTATION REPORT
## Attention + Persistent Task Actors + Concurrency + Interruption + Scheduling Foundation

**Statut :** Phase 2 terminée. Architecture V2 toujours FROZEN — aucun document architectural modifié. Repo V1 (`RAYA/`) intact. Phase 0 (56 tests) et Phase 1 (99 tests) intactes et toujours PASS.

---

## 1. Executive summary

RAYA V2 sait maintenant gérer plusieurs activités à la fois et décider — de façon déterministe, sans modèle — ce qui mérite son attention. Un `AttentionEngine` réel classe les événements (requêtes utilisateur, événements de tâches, événements safety) en `PROCESS_NOW`/`BACKGROUND`/`INTERRUPT`/`IGNORE`, filtre agressivement le bruit (les ticks de progression ne spamment jamais l'utilisateur), et ne décide jamais **comment** agir — c'est toujours le Harness. Un `TaskScheduler` réel (pool de threads borné, file de priorité avec aging anti-starvation) fait tourner plusieurs Task Actors concurrents, avec pause/resume/cancel coopératifs, checkpoint throttlé, et une reprise honnête après crash (RUNNING → PAUSED, jamais COMPLETED par optimisme, y compris pour le mécanisme d'idempotence `ExecutionRecord` hérité de Phase 1). La conversation reste strictement indépendante des tâches de fond dans les deux sens — démontré, pas seulement affirmé.

**Deux vrais bugs de concurrence ont été trouvés et corrigés pendant l'implémentation** (détail §22-23), dont un bug critique qui rendait Attention totalement muette sur les événements de tâches réels (silencieusement avalé par l'EventBus) — trouvé uniquement parce que les tests d'intégration exercent le VRAI système, pas des mocks.

**Résultat chiffré :** 155 tests Phase 0+1 intacts + 60 nouveaux tests Phase 2 = **215 tests, 215 PASS, 0 FAIL, 0 BLOCKED, 0 NOT_TESTED**, stables sur 3 exécutions consécutives de la suite complète.

---

## 2. Architecture implemented

Conforme au diagramme cible de la consigne (§2) : `Interfaces → Harness → {Attention, Task Actors, Context}`, avec World State/Persistence en dessous. **Aucune architecture alternative introduite** — Cognition/Tools/Devices restent des placeholders honnêtes (Phase 3/4). Un seul écart mérite mention : le **scheduler vit dans `harness/`, pas `tasks/`** — décision justifiée §5 ci-dessous, cohérente avec la consigne §27 elle-même ("Adapter le Harness pour gérer... concurrent task execution").

## 3. Attention implementation

`raya/attention/evaluator.py` — `AttentionEvaluator` (logique pure, testable sans bus : un `Event` en entrée, une `AttentionDecision` en sortie, zéro I/O) + `AttentionEngine` (s'abonne à l'EventBus sur `interface.request_received`, `task.*`, `safety.*` — **jamais `"*"`**, republie `attention.decision_made`). `raya/attention/policy.py` — `FocusTracker` (session → task_id en focus, en mémoire uniquement, jamais persisté comme donnée métier).

**Dépendances vérifiées** (lint + tests dédiés) : `attention/` ne dépend que de `world_state`, `tasks` (lecture seule), `observability`, `event_bus` — jamais `harness`/`models`/`tools`/`devices`/`interfaces`.

## 4. Attention scoring/decision logic

Déterministe, documentée dans le code, sans LLM ni ML :

| Événement | Décision | Logique |
|---|---|---|
| `interface.request_received` | PROCESS_NOW | toujours — conversation ≠ task execution (§7) |
| `task.created/ready/started/checkpoint` | IGNORE | routine, non exploitable |
| `task.paused/resumed/cancelled` | BACKGROUND | informatif |
| `task.recovered` | PROCESS_NOW | un crash mérite d'être signalé |
| `task.completed` | PROCESS_NOW si focus, sinon BACKGROUND | `user_relevance` dérivée du focus + priorité |
| `task.failed` | INTERRUPT (CRITICAL) / PROCESS_NOW (HIGH ou focus) / BACKGROUND (sinon) | urgence proportionnelle à la priorité |
| `task.progress` | BACKGROUND uniquement au franchissement d'un palier (25/50/75/100%) non encore notifié, IGNORE sinon | anti-spam §49, testé sur 100 ticks consécutifs |

`AttentionFactors` (urgency/importance/novelty/confidence/cost/user_relevance) peuplés à chaque décision, toujours dans `[0,1]`.

## 5. Task Actor implementation

`TaskRegistry` (`raya/tasks/registry.py`) reste **strictement la couche données/lifecycle** (comme Phase 1), étendue avec `report_progress()` (bon marché, EventBus only, aucune écriture SQLite), `request_cancellation()` (signal coopératif distinct de `cancel()`), `mark_ready()`, et 3 nouveaux types d'événements catalogués (`task.ready`, `task.cancel_requested`, `task.recovered` — extension additive du catalogue fermé, justifiée §14). `raya/tasks/priority.py` fournit LOW/NORMAL/HIGH/CRITICAL comme constantes nommées sur l'`int` déjà défini par le contrat — **aucun champ de contrat modifié**.

## 6. Scheduler implementation

`raya/harness/scheduler.py` — `TaskScheduler`, vit dans `harness/` (pas `tasks/`) : décision architecturale explicite, pas un oubli — `tasks/` n'a pas accès à `safety` (nécessaire pour `should_stop()`) et la consigne §27 assigne explicitement "concurrent task execution" au Harness. Modèle : **pool fixe de N threads worker + file de priorité en mémoire**, choix documenté dans le code (pas d'asyncio introduit, cohérent avec les patterns déjà utilisés Phase 0/1).

## 7. Concurrency model

**Coopératif par step** : un `step_fn` exécute EXACTEMENT une unité de travail et retourne `done: bool` — ce n'est PAS une boucle agentique (aucun `while True: call model` — §28 respecté et vérifié par lint). Le scheduler rappelle le step suivant au tour d'après, ce qui garantit `max_concurrent_tasks` réellement respecté (vérifié par test : 5 tâches, limite 2, jamais plus de 2 steps simultanés observés).

## 8. Priority/fairness

Priorité effective = `priority + min(temps_attente_s × 0.5, 50)` (aging). Une tâche LOW ne peut jamais rester bloquée indéfiniment derrière un flot de tâches HIGH — testé en soumettant 15 tâches HIGH en continu pendant qu'une tâche LOW attend : elle finit par s'exécuter (`test_fairness_low_priority_eventually_runs_no_starvation`).

## 9. Cancellation

Coopérative à deux niveaux : `request_cancellation()` (RUNNING, potentiellement en plein step — le scheduler finalise au prochain point de contrôle) vs `cancel()` direct (PENDING/PAUSED, rien d'actif dessus, finalisation immédiate sûre). `Harness.cancel_task()` choisit automatiquement le bon chemin selon l'état.

## 10. STOP integration

Chemin inchangé (`Interface → interface.stop_requested Event → EventBus → Safety → should_stop()`). Le scheduler vérifie `should_stop()` dans `_process_one_step` (avant CHAQUE step) — **pas** dans `_pop_next` (bug trouvé et corrigé, §22). Testé avec 3 tâches concurrentes, répété 3 fois consécutivement.

## 11. Checkpointing

Throttlé : `report_progress()` (chaque step, bon marché) vs `checkpoint()` (tous les 2 steps ou au dernier — écriture SQLite réelle). Un compteur de step en mémoire (par closure, une par tâche) est la source de vérité en fonctionnement normal ; il n'est réamorcé depuis le checkpoint persistant qu'une seule fois, au premier appel — bug de conception initial trouvé et corrigé (§23).

## 12. Recovery

`recover_after_restart()` (Phase 1, événement Phase 2 dédié `task.recovered`) transitionne RUNNING→PAUSED au boot. **Nouveau en Phase 2** : `Harness.resume_task()` détecte si le `step_fn` a été perdu (process précédent) et le ré-enregistre, reprenant exactement au dernier `step_index` persisté — testé bout en bout (`test_recovered_task_can_actually_resume_and_complete`), pas seulement au niveau de l'état.

## 13. Harness modifications

`Harness` possède maintenant `attention: AttentionEngine`, `focus: FocusTracker`, et un `TaskScheduler` interne. S'abonne à `attention.decision_made` (abonné critique, `BLOCK_PUBLISHER_WITH_TIMEOUT`) : sur `INTERRUPT`, met en pause la tâche en focus de la session concernée — **Attention ne fait jamais l'interruption elle-même** (§40, vérifié par test). Une seule boucle agentique demeure (`handle_request`), aucune seconde boucle nulle part (vérifié par lint : règle "second-agent-loop").

## 14. Persistence changes

Aucun changement de schéma SQLite (toujours la table générique `kv_store` de Phase 1). Aucune migration nécessaire.

## 15. EventBus integration

Inchangé. Nouveaux types d'événements circulent dessus (`task.progress` à haute fréquence mais sans écriture disque, `attention.decision_made`) sans que l'EventBus lui-même ne connaisse la moindre logique métier (vérifié : `event_bus/` n'importe que `raya.contracts`).

## 16. CLI additions

`/tasks create <objectif> [low|normal|high|critical]`, `/tasks inspect <id>`, `/attention` (journal des dernières décisions). `/tasks list/pause/resume/cancel` étendus (affichage priorité). Toujours zéro logique métier dans `repl.py` — vérifié après avoir **corrigé une violation réelle** (§22).

## 17. Architecture lint changes

- `ALLOWED["harness"]` complété avec `"attention"` (déjà autorisé par `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §1.5, oubli de la table Phase 0/1 corrigé, pas un changement d'architecture).
- 13 nouveaux tests Groupe F : Attention→Harness/Models/Tools détectés, Tasks→Models/Devices détectés, scheduler sans accès provider direct, **régression exacte du bug CLI→tasks** figée en test permanent, EventBus sans logique métier.

## 18. Tests added

**60 nouveaux tests** (cible 55-90) :

| Fichier | Tests |
|---|---|
| `tests/attention/test_attention.py` | 16 |
| `tests/tasks/test_task_actors_phase2.py` | 13 |
| `tests/harness/test_scheduler.py` | 7 |
| `tests/stop/test_scheduler_stop.py` | 4 |
| `tests/harness/test_recovery_phase2.py` | 4 |
| `tests/architecture/test_dependency_lint.py` (extension) | +8 |
| `tests/integration/test_phase2_scenarios.py` | 8 |
| **Total nouveaux** | **60** |

## 19. Integration scenarios

Les 8 scénarios exigés (§44), système réel complet (vrai SQLite, vrai EventBus, vraie Safety, vrai Scheduler, vraie Attention, vrai Harness — §45), tous PASS :

| # | Scénario | Statut |
|---|---|---|
| 1 | Deux tâches concurrentes, progressent et terminent | **PASS** |
| 2 | Conversation répondue immédiatement pendant une tâche | **PASS** |
| 3 | HIGH préférée, LOW finit quand même par s'exécuter | **PASS** |
| 4 | Pause = zéro progression, resume = reprise réelle | **PASS** |
| 5 | Cancel coopératif, état CANCELLED | **PASS** |
| 6 | Attention classe (routine=IGNORE), échec CRITICAL=INTERRUPT+pause focus | **PASS** |
| 7 | STOP arrête 3 tâches, persistance relue depuis un backend indépendant | **PASS** |
| 8 | Crash recovery : jamais COMPLETED par erreur, checkpoint valide | **PASS** |

## 20. Test totals

- Avant Phase 2 : 155 (56 Phase 0 + 99 Phase 1)
- Ajoutés Phase 2 : 60
- **Total : 215**

## 21. PASS / FAIL / BLOCKED / NOT_TESTED

| Domaine | PASS | FAIL | BLOCKED | NOT_TESTED |
|---|---:|---:|---:|---:|
| Phase 0 (contracts/event_bus/runtime/harness/lint/stop/world_state) | 56 | 0 | 0 | 0 |
| Phase 1 (persistence/world_state/memory/context/tasks/harness/integration) | 99 | 0 | 0 | 0 |
| Phase 2 — Attention | 16 | 0 | 0 | 0 |
| Phase 2 — Task Actors | 13 | 0 | 0 | 0 |
| Phase 2 — Concurrency/Scheduler | 7 | 0 | 0 | 0 |
| Phase 2 — STOP | 4 | 0 | 0 | 0 |
| Phase 2 — Recovery | 4 | 0 | 0 | 0 |
| Phase 2 — Architecture | 8 | 0 | 0 | 0 |
| Phase 2 — Intégration (8 scénarios) | 8 | 0 | 0 | 0 |
| **Total** | **215** | **0** | **0** | **0** |
| Ollama réel | — | — | — | **NOT_TESTED** (hors scope explicite, inchangé depuis Phase 1) |

Stable sur 3 exécutions consécutives de la suite complète + 3 répétitions dédiées du scénario STOP multi-tâches.

## 22. Bugs found

1. **CLI important `raya.tasks` directement** (violation Interfaces→Harness) — trouvé par le lint architectural lui-même dès la première exécution après écriture du code.
2. **Compteur de step dérivé du checkpoint throttlé** — le step restait bloqué à 1 indéfiniment (le checkpoint persisté ne bougeant qu'une fois sur deux, chaque appel recalculait "checkpoint+1" = la même valeur). Trouvé par un test d'intégration réel (pas un mock).
3. **`_pop_next` du scheduler bloquait TOUTE dépilation dès que `should_stop()` était actif** — empêchant les tâches déjà en file d'être dépilées pour être annulées ; STOP ne finalisait jamais rien. Trouvé par `test_scenario_5_stop_via_cli_event_interrupts_task`.
4. **`TaskEvent.payload` est un `TaskEventPayload` (dataclass typé), pas un `dict`** — `AttentionEvaluator` faisait `.get()` dessus comme sur un dict, levait une `AttributeError` **silencieusement avalée par l'EventBus** (`except Exception: pass` dans `_Subscription._deliver_loop`, une protection Phase 0 légitime qui a ici masqué un vrai bug). Résultat : Attention ne traitait JAMAIS un seul événement de tâche réel, alors que tous les tests unitaires (payload en `dict` construit à la main) passaient. Trouvé uniquement parce que le scénario d'intégration 6 utilisait le VRAI pipeline `TaskRegistry → EventBus → Attention`, pas une construction d'`Event` synthétique.
5. **Test fragile basé sur un `time.sleep(0.02)` fixe** (`test_queued_task_waits_when_at_concurrency_limit`) — a échoué de façon intermittente sous charge (suite complète), jamais en isolation. Pas un bug de production — un défaut de qualité de test. Corrigé en remplaçant le sleep par une synchronisation `threading.Event` déterministe (le step A signale explicitement son démarrage, bloque jusqu'à ce que le test ait vérifié B, plutôt que d'espérer qu'un délai suffise). Suite complète stable sur 5 exécutions consécutives après correction.

## 23. Bugs fixed

Les 5 listés ci-dessus, tous corrigés à la racine (pas de contournement) :
1. `Harness.priority_from_name/priority_to_name` (passthrough) remplace l'import direct dans le CLI.
2. Compteur de step en mémoire (par closure), réamorcé depuis le checkpoint persistant une seule fois — pas à chaque appel.
3. `_pop_next` dépile toujours ; c'est `_process_one_step` (déjà correct) qui décide cancellation vs exécution.
4. `evaluate()` normalise `event.payload` via `to_dict()` avant tout accès — fonctionne uniformément pour un `dict` (tests) ou un `TaskEventPayload` (système réel).
5. Synchronisation par `threading.Event` remplace le `sleep()` fixe dans le test concerné.

Chaque bug a un test de régression dédié dans la suite (pas seulement corrigé silencieusement). Validation finale : 5 exécutions consécutives de la suite complète (215/215), toutes stables.

## 24. Known limitations

- `start_background_task`/le démonstrateur simulé reste à UN seul type de tâche (pas un exécuteur générique — Phase 2 assumé, Phase 3+ généralisera).
- `resume_task()` après un vrai crash suppose le nombre de steps par défaut si la tâche a été créée avec un `total_steps` personnalisé (limitation documentée dans le code, acceptable pour un démonstrateur).
- Aging de fairness est un mécanisme simple (linéaire, plafonné) — suffisant pour éviter la famine à l'échelle de Phase 2, pas un ordonnanceur de production.
- `AuditTrail` (Safety) toujours en mémoire, non persisté (inchangé depuis Phase 1, non exigé).

## 25. Technical debt

- Le bug #4 (§22) révèle une fragilité de conception plus large : `Event.payload` générique (`dict`) et `TaskEvent.payload` typé (`TaskEventPayload`) coexistent sur le même bus sans garde-fou au niveau du contrat lui-même — seulement corrigé au point d'usage (Attention). Un futur consommateur générique du bus pourrait retomber dans le même piège. Recommandation Phase 3+ : soit unifier `Event.payload` pour toujours être un `dict` sérialisé même pour `TaskEvent` (changement de contrat, pas fait ici sans nécessité absolue), soit documenter cette règle de façon plus visible dans `RAYA_V2_CONTRACTS.md`.

## 26. What remains for Phase 3

Cognition réelle (raisonnement/ambiguïté connectés à un vrai modèle), Tool System réel (catalogue + exécution), Model Layer avec un vrai provider Ollama Cloud. Le Context Engine (Phase 1) sait déjà lire `tools.discovery` en lecture seule — rien à changer côté Context pour brancher un vrai catalogue. Le `TaskScheduler` peut recevoir de vrais step_fn (Tool-based) sans changement de contrat.

## 27. Confirmation

**Aucune fonctionnalité Phase 3+ n'a été implémentée** : pas de Cognition réelle, pas de catalogue d'outils réel, pas de Device Agent, pas de provider Ollama réel, pas de Voice/Web/Desktop. Chaque fois qu'une limite Phase 2 a été atteinte (ex : que faire d'un résultat de tâche terminée sans vraie interface de notification push), la réponse a été un mécanisme d'observation minimal (`/attention`, journal borné) plutôt qu'une anticipation de Phase 3.

---

**Pas de "100% complete" annoncé** — Phase 2 livre exactement le périmètre demandé : RAYA peut maintenant être occupée à quelque chose tout en restant disponible pour autre chose, avec une fondation de concurrence, priorité, interruption et recovery honnête. Rien de plus, rien de moins.
