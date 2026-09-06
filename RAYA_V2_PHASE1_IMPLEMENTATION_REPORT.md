# RAYA V2 — PHASE 1 IMPLEMENTATION REPORT
## Harness + World State + Memory + Context Engine + Persistence + Safety minimal

**Statut :** Phase 1 terminée. Architecture V2 toujours FROZEN — aucun document architectural modifié. Repo V1 (`RAYA/`) intact (`git status` identique à avant cette session). Un conflit environnemental (pas architectural) a été trouvé et résolu en Phase 0 (package placé dans `RayaV2/`, pas `RAYA/`) ; ce choix est reconduit sans changement.

---

## 1. Executive summary

RAYA V2 dispose maintenant d'une fondation agentique réelle et persistante : World State, Memory et Tasks survivent à un redémarrage complet du process via un backend SQLite réel (transactions, rollback testé, reopen testé) ; le Context Engine fait un vrai ranking/filtrage/budget déterministe (plus un passthrough) ; le Harness assemble un contexte réel, invoque le Model Layer via l'abstraction, et persiste ses changements ; les Task Actors ont un lifecycle complet avec checkpoint/pause/resume/cancel et une recovery honnête après crash simulé (RUNNING → PAUSED, jamais COMPLETED par optimisme) ; le mécanisme d'idempotence (`ExecutionRecord`) est implémenté et testé indépendamment de tout vrai Tool ; STOP reste centralisé et interrompt une tâche de fond réelle. Le CLI démontre tout cela avec 5 commandes (`/state /memory /tasks /context /events`) en plus du dialogue et de STOP.

**Ce que Phase 1 n'est PAS :** ni Cognition, ni Attention, ni Tool catalog, ni Device Agent réels — le Model Layer reste un stub honnête (`NullProvider`). Rien de tout cela n'a été implémenté prématurément, conformément au périmètre §2 de la consigne.

**Résultat chiffré :** 56 tests Phase 0 intacts + 99 nouveaux tests Phase 1 = **155 tests, 155 PASS, 0 FAIL, 0 BLOCKED, 0 NOT_TESTED** sur tout ce qui était dans le périmètre de cette phase. Un vrai bug de production a été trouvé et corrigé pendant l'implémentation (thread de tâche de fond non arrêté proprement à `shutdown()`) — détaillé en §12.

---

## 2. What was implemented

- **Persistence** : `SqliteBackend` réel (WAL, transactions, rollback, `save_batch` atomique), `InMemoryBackend` conservé pour les tests rapides, migrations idempotentes versionnées.
- **World State** : `WorldStateStore` persistant — create/update/invalidate/expire/retrieve_fact/retrieve_by_domain/retrieve_relevant, freshness paresseuse (TTL) + explicite, events `world_state.updated`.
- **Memory** : `MemoryStore` persistant — write/get/update_lifecycle/correct (avec `supersedes`), isolation stricte de canal, ranking déterministe (lifecycle + correspondance mots-clés + récence), events `memory.entry_written/updated`.
- **Context Engine** : assemblage réel multi-sources (system_rules, task_state, conversation_history, world_state avec freshness, memory, tool schemas via discovery), ranking déterministe, budget de tokens réellement respecté (sections optionnelles jamais en excès), provenance sur chaque section.
- **Tasks** : `TaskRegistry` persistant — lifecycle complet, checkpoint, pause/resume/cancel/complete/fail, `recover_after_restart()` (RUNNING → PAUSED au crash).
- **ExecutionRecord** : `ExecutionRecordRepository` + `decide_recovery()` implémentant exactement la règle d'idempotence du contrat (RETRY si idempotent, sinon vérification obligatoire, UNVERIFIABLE → escalade, jamais de retry aveugle).
- **Safety** : inchangée dans son mécanisme (déjà réelle depuis Phase 0), maintenant exercée sur une vraie tâche de fond (thread réel, pas un mock).
- **Harness** : contexte réel, invocation modèle via l'abstraction, méthodes explicites `create_memory/set_world_fact/create_task/pause_task/resume_task/cancel_task/checkpoint_task/start_background_task`, `recover()` appelé au boot, `shutdown()` propre (nouveau — voir §12).
- **Event Repository** (`observability/event_store.py`) : persistance curatée des events significatifs (task/harness/safety/memory/world_state/runtime), distincte d'`ObservabilityTracer` (qui reste en mémoire pour tout écouter, Phase 0).
- **CLI** : `/state`, `/memory`, `/tasks`, `/context`, `/events` en plus de STOP et du dialogue.
- **Configuration** : `db_path`, `context_budget_tokens`, `world_state_default_ttl_s`, `memory_search_limit` ajoutés à `RuntimeConfig`.

---

## 3. Files created

```
raya/persistence/sqlite_backend.py
raya/persistence/migrations.py
raya/harness/execution_records.py
raya/observability/event_store.py
raya/context_engine/ranking.py
raya/context_engine/tokens.py
.gitignore

tests/persistence/test_sqlite_backend.py
tests/world_state/test_world_state.py
tests/memory/test_memory.py
tests/context_engine/test_context.py
tests/tasks/test_tasks.py
tests/harness/test_execution_records.py
tests/harness/test_harness_phase1.py
tests/integration/test_integration_scenarios.py
tests/conftest.py
```

## 4. Files modified

```
raya/persistence/backend.py       (+ delete, save_batch, transaction)
raya/persistence/__init__.py      (export SqliteBackend)
raya/world_state/store.py         (réécrit : persistant, CRUD complet)
raya/memory/store.py              (réécrit : persistant, ranking, isolation renforcée)
raya/tasks/registry.py            (réécrit : persistant, recover_after_restart)
raya/context_engine/assembler.py  (réécrit : vrai ranking/budget/provenance)
raya/harness/loop.py              (context réel, méthodes CLI, shutdown() — voir §12)
raya/harness/__init__.py          (export ExecutionRecordRepository/decide_recovery)
raya/observability/__init__.py    (export EventStore)
raya/runtime/config.py            (+ db_path, context_budget_tokens, etc.)
raya/runtime/bootstrap.py         (SqliteBackend réel, EventStore, recover() au boot, shutdown correct)
raya/models/router.py             (bug Phase 0 corrigé — voir rapport Phase 0 §5)
raya/interfaces/cli/repl.py       (commandes /state /memory /tasks /context /events)
raya/runtime/entrypoints/cli.py   (passe event_store à run())
.env.example                       (nouvelles variables documentées)
tests/world_state/test_freshness.py (constructeurs mis à jour vers PersistenceBackend explicite)
tests/architecture/test_dependency_lint.py (+5 tests memory/world_state/persistence)
```

Aucun fichier de `docs/` (les 9 documents d'architecture) n'a été modifié.

## 5. Architecture changes

**Aucune.** Toutes les frontières, tous les contrats de `RAYA_V2_CONTRACTS.md` sont respectés tels quels (aucun champ ajouté aux contrats gelés — `ExecutionRecord` était déjà défini en Phase 0). La règle de dépendance de `RAYA_V2_REPOSITORY_STRUCTURE.md` §20 est vérifiée automatiquement (lint étendu avec 5 tests, tous PASS sur le vrai code).

## 6. Persistence design

Une seule table générique `kv_store(collection, item_id, payload_json, updated_at)` sert TOUS les repositories (world_state/memory/tasks/execution_records/events) — aucun repository ne connaît de SQL, seulement `PersistenceBackend.save/load/query/delete/save_batch`. `save_batch` est le primitif transactionnel (tout ou rien, testé par rollback explicite). WAL activé, verrou applicatif (`RLock`) pour l'usage concurrent raisonnable de Phase 1. Migrations versionnées (`schema_meta.version`), idempotentes (testé : rejouer n'applique rien deux fois).

## 7. World State implementation

Clé composite (domain, key), upsert en place (jamais un journal), `status` ACTIVE/STALE/SUPERSEDED, freshness paresseuse (TTL calculé à la lecture) + explicite (`expire_fact`). Traçabilité via events `world_state.updated` persistés par l'Event Repository, pas via un historique de lignes dans la table de faits elle-même (choix documenté, garde le contrat World State conforme à "pas un journal").

## 8. Memory implementation

Isolation stricte de canal vérifiée dans les deux sens (chat-private jamais visible en voice et inversement, shared visible partout — testé explicitement, scénario exact demandé §7.1). Ranking déterministe et documenté (poids lifecycle + occurrences de mots-clés significatifs [≥4 lettres, pas de NLP] + petit bonus de récence qui décroît). `correct()` implémente `supersedes` + priorité utilisateur (ancienne entrée → `obsolete`).

## 9. Context Engine implementation

Sources : system_rules (toujours), task_state (si Task fourni), conversation_history (dernières entrées `MemoryLayer.CONVERSATION`), world_state (avec freshness obligatoire — invariant Phase 0 toujours vérifié), memory (hors conversation), tool schemas (via `tools.discovery`, lecture seule — jamais `tools.execution`, vérifié par lint). Budget appliqué par `ranking.trim_to_budget()` : sections obligatoires toujours incluses, sections optionnelles triées par score et ajoutées tant que le budget le permet — jamais dépassé pour l'optionnel. Estimateur de tokens isolé dans un seul fichier (`~4 caractères/token`, documenté comme remplaçable).

## 10. Task/checkpoint implementation

Lifecycle complet avec transitions validées par le contrat (`Task.transition_to`, déjà défini Phase 0). Checkpoint = dict libre (jamais de chain-of-thought). `start_background_task()` est un **démonstrateur** (thread réel, 5 steps) des mécaniques pause/resume/STOP/checkpoint — explicitement pas un exécuteur générique (ça, c'est Phase 2 avec Attention + Task Actors complets). `recover_after_restart()` : tout Task RUNNING retrouvé au boot passe PAUSED, jamais réactivé silencieusement, jamais réputé COMPLETED.

## 11. Safety implementation

Mécanisme inchangé depuis Phase 0 (déjà réel : STOP unifié via EventBus, `should_stop()` synchrone). Phase 1 l'exerce sur une vraie charge : un thread de tâche de fond réel vérifie `should_stop()` à chaque itération et s'auto-annule proprement (testé en intégration, scénario 5).

## 12. Harness changes

Boucle nominale enrichie d'un vrai `context_engine.assemble()` (au lieu du passthrough Phase 0). Nouvelles méthodes explicites pour les actions demandées par une interface (pas par un modèle inexistant — honnête). **Bug trouvé et corrigé pendant les tests** : les threads de `start_background_task` n'étaient pas arrêtés à `shutdown()`, provoquant un `sqlite3.ProgrammingError` sporadique dans un thread après fermeture du backend (détecté via `PytestUnhandledThreadExceptionWarning` en exécutant la suite complète — pas caché). Corrigé par `Harness.shutdown()` (annule les tâches actives + `join()` avec timeout), appelé par `RuntimeHandles.shutdown()` **avant** `backend.close()`. Vérifié stable sur 3 exécutions complètes consécutives sans avertissement.

## 13. CLI changes

`/state set|get`, `/memory add|list`, `/tasks create|list|pause|resume|cancel`, `/context`, `/events`, en plus de `/stop`/`stop` et du dialogue existant. Chaque handler ne fait que parser des arguments et appeler une méthode du Harness — zéro logique métier dans `repl.py` (vérifié par lecture, pas seulement déclaré).

## 14. V1 migration status

**NOT_TESTED / BLOCKED — décision de scope explicite, pas un échec caché.**

Inspection réelle effectuée (lecture directe, pas de supposition) : `modules/conversations/store.py` V1 a un schéma SQLite réel (`conversations`, `messages`, `app_state`, `projects`, `attachments`) — mappable en principe vers `memory/conversation` V2 via une migration additive de colonnes (`lifecycle`/`confidence`/`provenance` avec défauts). `modules/memory/store.py` V1 utilise `facts.json` (champs `fact`/`obsolete`) — mappable vers `MemoryEntry{type=fact}`.

**Raison précise du NOT_TESTED :** le budget de cette phase a été consacré à la fondation de persistance elle-même (SQLite réel, World State, Memory, Context, Tasks, ExecutionRecord, Safety, Harness, CLI, 99 tests) — écrire ET tester un script de migration réel avec vérification de checksums est un sous-projet à part entière (`RAYA_V2_MIGRATION_PLAN.md` le prévoit comme travail dédié, scripts `--dry-run` obligatoires). Aucune donnée V1 n'a été touchée, lue en écriture, ni copiée. Aucun faux succès de migration n'est rapporté.

## 15. Tests added

**99 nouveaux tests** (cible annoncée : 80-120) :

| Fichier | Tests |
|---|---|
| `tests/persistence/test_sqlite_backend.py` | 12 |
| `tests/world_state/test_world_state.py` | 14 |
| `tests/memory/test_memory.py` | 16 |
| `tests/context_engine/test_context.py` | 12 |
| `tests/tasks/test_tasks.py` | 15 |
| `tests/harness/test_execution_records.py` | 11 |
| `tests/harness/test_harness_phase1.py` | 7 |
| `tests/integration/test_integration_scenarios.py` | 7 |
| `tests/architecture/test_dependency_lint.py` (extension) | +5 |
| **Total nouveaux** | **99** |

## 16. Full test results

```
155 tests collected
155 passed, 0 failed, 0 error
3 exécutions consécutives, 0 flakiness, 0 warning
```

Répartition : 56 Phase 0 (contracts 15, event_bus 7, runtime/bootstrap 3, runtime/cli 3, harness/loop 4, architecture 15→20, stop 5, world_state/freshness 4) + 99 Phase 1.

## 17. Integration scenarios

Les 7 scénarios demandés (§18) sont implémentés en tests réels bout-en-bout (vrai SQLite, vrais threads, vrai EventBus — aucun mock) :

| # | Scénario | Statut |
|---|---|---|
| 1 | Restart persistence (memory+world_state+task+checkpoint survivent) | **PASS** |
| 2 | Context construction (pertinent inclus, non-pertinent exclu, stale marqué, budget respecté) | **PASS** |
| 3 | Channel isolation (chat/voice/shared, double sens) | **PASS** |
| 4 | Pause/resume (aucune progression pendant la pause, reprise puis complétion) | **PASS** |
| 5 | STOP (Event CLI → EventBus → Safety → tâche annulée) | **PASS** |
| 6 | Crash recovery (Task jamais COMPLETED par erreur, ExecutionRecord EXECUTING→UNKNOWN) | **PASS** |
| 7 | Conversation non bloquée par une tâche de fond (réponse <200ms pendant une tâche de ~250ms) | **PASS** |

## 18. PASS / FAIL / BLOCKED / NOT_TESTED table

| Domaine | PASS | FAIL | BLOCKED | NOT_TESTED |
|---|---:|---:|---:|---:|
| Contracts (Phase 0) | 15 | 0 | 0 | 0 |
| EventBus (Phase 0) | 7 | 0 | 0 | 0 |
| Runtime/CLI (Phase 0+1) | 6 | 0 | 0 | 0 |
| Harness (Phase 0+1) | 22 | 0 | 0 | 0 |
| Architecture/Lint | 20 | 0 | 0 | 0 |
| STOP/Safety | 5 | 0 | 0 | 0 |
| World State | 18 | 0 | 0 | 0 |
| Memory | 16 | 0 | 0 | 0 |
| Context Engine | 12 | 0 | 0 | 0 |
| Tasks | 15 | 0 | 0 | 0 |
| Persistence | 12 | 0 | 0 | 0 |
| Intégration (7 scénarios) | 7 | 0 | 0 | 0 |
| **Total** | **155** | **0** | **0** | **0** |
| Migration V1 (hors tests) | — | — | — | **1 item BLOCKED** (§14, raison exacte donnée) |
| Ollama réel | — | — | — | **NOT_TESTED** (hors scope explicite §19, NullProvider utilisé partout) |

## 19. Known limitations

- Ranking Memory/Context : mots-clés + poids déterministes documentés, pas de recherche sémantique (attendu, explicitement demandé ainsi).
- `start_background_task` est un démonstrateur à un seul type de tâche simulée, pas un exécuteur générique de Task Actors (Phase 2).
- `AuditTrail` (Safety) reste en mémoire, non persisté (pas exigé par les critères d'acceptation Phase 1).
- Concurrence SQLite : verrou applicatif simple (`RLock`), suffisant pour un runtime local mono-process — pas conçu pour du multi-process.
- Le budget de Context Engine peut, en théorie, être dépassé par les sections **obligatoires** seules (system_rules/task_state) si un appelant fournit un `budget_tokens` absurdement petit — dans ce cas `Context.__post_init__` lève une erreur explicite plutôt que de mentir sur la consommation (comportement voulu, documenté dans le code).

## 20. Technical debt

- Migration V1 → V2 : script à écrire (Phase 2 ou dédiée), voir §14.
- `ExecutionRecordRepository` n'est pas encore appelé par un vrai pipeline `tools.execute()` (normal, Tools réels = Phase 3) — le mécanisme est prouvé isolément par 11 tests, pas encore intégré en bout de chaîne.
- Pas de vraie politique de confirmation Safety riche (toujours celle, minimale, de Phase 0) — attendu, "Safety minimal Phase 1" explicitement demandé.

## 21. Exact remaining work

Pour Phase 2 (Attention + Task Actors complets) : généraliser `start_background_task` en un vrai executor Task Actor piloté par Attention ; brancher `ExecutionRecordRepository` dans un vrai pipeline d'exécution ; enrichir la politique de dépendances entre Tasks (le champ existe, non exploité activement en Phase 1).

## 22. Recommendation for Phase 2

Attaquer **Attention + Task Actors** en s'appuyant directement sur `TaskRegistry` (déjà persistant, déjà testé) — ne pas le refaire. Le démonstrateur `start_background_task` de Phase 1 peut servir de base concrète (déjà pause/resume/STOP/checkpoint réels) à généraliser plutôt qu'à jeter. Le Context Engine est prêt à recevoir de vrais Tools (Phase 3) sans changement de contrat.

---

## Compteurs demandés

- **Tests avant Phase 1 :** 56
- **Tests ajoutés :** 99
- **Total :** 155
- **PASS :** 155
- **FAIL :** 0
- **BLOCKED :** 0 (tests) / 1 (item hors-tests : migration V1, raison donnée §14)
- **NOT_TESTED :** 0 (tests) / 1 (Ollama réel, hors scope explicite Phase 1)

**Pas de "100% complete" annoncé** — Phase 1 livre exactement le périmètre demandé (fondation persistante, pas d'intelligence), avec ses limites documentées ci-dessus.
