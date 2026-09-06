# RAYA V2 — ARCHITECTURAL INVARIANTS & TEST STRATEGY

**Statut :** Stratégie de test et liste d'invariants, prêtes pour revue.
**Sources :** `RAYA_V2_TECHNICAL_ARCHITECTURE.md`, `RAYA_V2_CONTRACTS.md`, `RAYA_V2_REPOSITORY_STRUCTURE.md` §20 (règle de dépendance), constats de l'audit (`RAYA_V2_AUDIT_REPORT.md` §9).

**Principe directeur, répété du cahier des charges :** pas de course aux 1000 tests artificiels. Cible **~150-200 tests V2 bien choisis**, contre ~1869 en V1 (993 `tests/` + 876 `brain/lab/`, mal répartis — 209 tests sur la 3D, non-goal V2, contre 43 tests, et les mauvais, sur le cerveau réellement actif). Chaque test doit prouver un comportement réel, pas gonfler un score.

---

## 1. Répartition cible (~150-200 tests)

| Catégorie | Nombre cible | Dont tests "live" (réels, non mockés) | Rationale |
|---|---|---|---|
| `contracts` | 20 | 0 | Round-trip sérialisation + invariants pour les 17 contrats — base, doit être exhaustif mais mécanique |
| `harness` | 20 | 0 (stubs modèle/tool) | Boucle nominale, cancellation, checkpoint, steering, replanning — cœur du système, priorité haute |
| `memory` | 15 | 0 | Lifecycle, isolation stricte de canal, intégrité de migration de données réelles |
| `world_state` | 10 | 0 | Freshness/TTL, superseded, requête par domaine |
| `attention` | 10 | 0 | 4 décisions selon facteurs, latence bornée sans appel modèle |
| `tasks` | 15 | 1 (long-running task réelle) | Lifecycle complet, pause/resume EXACT, dépendances, cancellation coopérative |
| `tools` | 15 | 0 | Discovery filtré, validation, permission gate, retry/timeout borné |
| `models` | 15 | 1 (Ollama Cloud live) | Routing par capability, fallback provider, contrat NON Anthropic-shaped (test négatif explicite) |
| `safety` | 15 | 0 | Risk classification, politique de confirmation, STOP (priorité absolue, multi-source), audit trail |
| `devices/windows` (PC) | 10 | 1 (action Windows réelle) | Mécanismes bas niveau + non-couplage à `models` |
| `devices/browser` | 10 | 1 (navigation réelle) | CDP/session/worker + non-couplage à `models` |
| `persistence` | 10 | 0 | Migrations idempotentes, bascule local/serveur avec repli |
| `concurrency` | 8 | 0 | Conversation + Task de fond en parallèle, pas de lock global |
| `recovery` | 8 | 1 (crash simulé mi-tâche + reprise) | Reprise depuis checkpoint, jamais depuis zéro silencieusement |
| `interfaces` (voice/web/cli) | 10 | 0 | Barge-in, propagation STOP par event, intégration web sans régression |
| `integration` (bout-en-bout) | 8 | 1 (scénario blueprint §29 complet) | Scénarios croisant plusieurs subsystems, dont le scénario de succès du blueprint |
| **Total** | **~199** | **~5 live** | Dans la fourchette cible 150-200 |

Les tests "live" sont marqués explicitement dans leur nom (`test_*_live.py` ou décorateur `@live`) et ne bloquent jamais la CI par défaut si l'environnement ne les permet pas (pas de clé API, pas de Windows, pas de navigateur disponible) — ils rapportent `BLOCKED`, jamais un faux `PASS` ni un `FAIL` qui casserait la CI pour une raison d'environnement.

## 2. Statuts obligatoires

```
PASS         — le comportement attendu est vérifié, assertion réelle réussie
FAIL         — le comportement attendu n'est pas observé
BLOCKED      — l'environnement de test a empêché la validation (pas de clé API,
                pas de Windows, pas de navigateur, pas de matériel) — jamais
                confondu avec PASS ni avec FAIL
NOT_TESTED   — délibérément non couvert à ce stade, avec raison explicite
```

**Règle absolue, reprise du cahier des charges :** ne jamais déclarer `PASS` quand une limitation d'environnement a empêché la validation réelle. Un rapport de test doit distinguer visiblement les `BLOCKED` des `PASS` — reprend la discipline déjà appliquée dans `RAYA_FINAL_AUDIT.md` en V1 ("Aucun faux PASS : tous les PASS correspondent à des assertions réelles qui ont réussi").

## 3. Ce qui n'est PAS reporté depuis V1

- Les 209 tests `test_hologram_*` (V1) ne sont PAS reportés en volume — le moteur `modules/hologram/` est `KEEP`, un nombre de tests proportionné (~5-8, inclus dans `tools`) suffit à couvrir son contrat une fois exposé via `tools/catalog/hologram.py`.
- Les 876 tests `brain/lab/` ne sont PAS reportés — ils valident un rule-based mocké jamais connecté à un vrai modèle ; leur valeur est dans les CONCEPTS déjà extraits (`cognition/`), pas dans leur volume de test.
- `test_ollama_provider.py`/`test_ollama_live.py` (V1, 43 tests) ne sont pas reportés tels quels — ils testent `brain/models/ollama_provider.py`, jamais branché en prod V1 ; leur remplaçant V2 (`tests/models/`) teste le VRAI chemin (`models/providers/ollama_cloud.py`), pas un doublon orphelin.
- `test_web_platform.py` + `test_web_platform_v2.py` (V1, 305 tests, doublon "_v1"/"_v2" jamais résolu) ne sont pas reportés en volume — `tests/interfaces/` couvre le contrat `interfaces/web/` avec un nombre de tests proportionné à sa complexité réelle (déjà bien découplé en V1).

---

## 4. Liste des invariants architecturaux

Chaque invariant est testable automatiquement, soit par lint statique (analyse d'imports), soit par grep/scan de code, soit par un test runtime. Sévérité : **BLOQUANT** (doit faire échouer la CI, aucune exception), **SURVEILLÉ** (alerte mais peut être temporairement toléré avec justification explicite en commentaire de code, revu périodiquement).

| # | Invariant | Mécanisme de vérification | Sévérité |
|---|---|---|---|
| 1 | **Aucune deuxième boucle agentique** en dehors de `harness/loop.py` — aucun fichier hors `harness/` ne définit un cycle observation→décision→action avec appel modèle interne | Scan statique : aucun fichier hors `harness/` et `models/` n'importe à la fois `models` ET orchestre une boucle `while`/récursion sur son propre résultat | **BLOQUANT** |
| 2 | **Aucun appel modèle direct hors Model Layer** — tout accès à un provider (Ollama ou futur) passe par `models/` | Lint d'import : seul `models/providers/*` a le droit d'importer la bibliothèque HTTP vers l'API du provider | **BLOQUANT** |
| 3 | **Aucune exécution d'outil hors Tool System** — un `Device` n'exécute jamais rien sans recevoir un `Command` de `tools/` | Lint d'import : `devices/` n'importe jamais `harness/`/`cognition/` directement, seulement `contracts/` | **BLOQUANT** |
| 4 | **Aucune UI importée par le Core** — `interfaces/` n'est jamais importée par `harness/cognition/tasks/tools/models/safety/devices/world_state/memory/context_engine/attention/perception` | Lint d'import : règle de dépendance stricte descendante (`RAYA_V2_REPOSITORY_STRUCTURE.md` §20) | **BLOQUANT** |
| 5 | **Aucun Device Agent ne possède son propre cerveau** — `devices/*` n'importe jamais `models/` | Lint d'import + test d'intégration explicite (grep CI) | **BLOQUANT** |
| 6 | **STOP toujours prioritaire** — `safety.should_stop()` est vérifié avant chaque appel modèle, chaque `ToolCall`, chaque `Command` device, avec une latence de propagation bornée mesurée | Test runtime : déclenche STOP pendant une boucle Harness en cours, mesure le délai jusqu'à arrêt effectif | **BLOQUANT** |
| 7 | **Conversation et Task de fond mutuellement indépendantes** — aucune conversation ne bloque une Task de fond, aucune Task de fond ne bloque une conversation | Test de concurrence dédié (`tests/concurrency/`), absence de lock global vérifiée par inspection + test de charge à deux sessions simultanées | **BLOQUANT** |
| 8 | **World State != Memory** — aucune structure de données partagée entre `world_state/` et `memory/`, contrats `WorldStateFact`/`MemoryEntry` strictement distincts | Revue de contrat statique (le contrat lui-même les distingue), test qui vérifie qu'écrire dans l'un n'affecte jamais l'autre | **BLOQUANT** |
| 9 | **Context Engine sélectionne, ne persiste jamais** — `context_engine/` n'écrit jamais dans `memory/` ou `world_state/`, lecture seule strictement | Lint d'import : `context_engine/` n'a accès qu'aux méthodes de lecture des stores | **BLOQUANT** |
| 10 | **Modèle != Runtime** — `models/` ne connaît aucun concept de `Task`/`Session`/`HarnessState` | Lint d'import : `models/` n'importe jamais `contracts/task.py` ni `contracts/harness.py` | **BLOQUANT** |
| 11 | **Secrets jamais dans le code** — aucune clé API, token, credential en dur dans le repo | Scan automatique (pattern de clés connues, entropie élevée) en pre-commit/CI, `.env`/`credentials/` jamais committés | **BLOQUANT** |
| 12 | **Webcam/screen/micro non analysés en permanence** — `perception/sensors/*` n'appelle jamais `models`, `perception/capture/*` n'est invoqué que sur demande explicite tracée par `correlation_id` | Lint d'import (`perception/sensors/` n'importe jamais `models`) + test runtime vérifiant qu'aucune capture lourde ne se déclenche sans requête explicite en amont | **BLOQUANT** |
| 13 | **Un seul point d'écriture par type de donnée** — un seul `Model Registry`, un seul `Task Registry`, un seul `Tool Registry`, une seule `Memory Store` par process (pas de duplication façon "3 brains" V1) | Revue d'architecture + test d'instanciation unique (pattern singleton contrôlé via `runtime/bootstrap.py`) | **BLOQUANT** |
| 14 | **Aucun partage cross-canal sans permission explicite** — seule `MemoryEntry.channel_scope == shared` traverse les canaux automatiquement | Test runtime : une entrée `channel_scope=voice` n'apparaît jamais dans une requête faite depuis `channel_scope=chat` sans passer par `memory/channel_bridge.py` explicitement | **BLOQUANT** |
| 15 | **Un `ToolResult` ne ment jamais** — `status=success` implique `error=null` et vice versa ; `evidence` renseignée pour `permission_level: sensitive\|destructive` quand techniquement possible | Validation de contrat (invariant déclaré dans `contracts/tool.py`), test négatif | **BLOQUANT** |
| 16 | **Un `Task` terminal ne redevient jamais actif** — `COMPLETED`/`FAILED`/`CANCELLED` sont des états finaux | Validation de transition d'état dans `tasks/actor.py`, test négatif explicite (tentative de réactivation refusée) | **BLOQUANT** |
| 17 | **Toute action `destructive` exige `granted_by` non-null** — aucune auto-approbation silencieuse pour le risque le plus élevé | Validation de contrat (`contracts/permission.py`), test négatif | **BLOQUANT** |
| 18 | **`correlation_id` propagé de bout en bout** — un `Event`/`Task`/`ToolCall`/`ModelRequest` issu d'une même chaîne causale porte le même `correlation_id` | Test d'intégration : reconstruction de timeline complète depuis `observability.trace(correlation_id)` sur un scénario bout-en-bout | **SURVEILLÉ** (utile au debug, pas un risque de sécurité/données) |
| 19 | **Aucune boucle de retry infinie** — chaque mécanisme de retry (Harness, Tools, Cognition recovery) a une borne explicite, testée | Test runtime : simuler un échec permanent, vérifier l'arrêt après N tentatives avec remontée honnête | **BLOQUANT** |
| 20 | **Interfaces ne parlent qu'au Harness** — aucune interface n'importe `models/tools/devices/memory/world_state/tasks/safety` directement | Lint d'import (règle de dépendance §20 de `RAYA_V2_REPOSITORY_STRUCTURE.md`) | **BLOQUANT** |
| 21 | **Le chemin STOP depuis une Interface passe toujours par un Event, jamais par un appel direct à `safety`** — une interface peut PUBLIER `interface.stop_requested`, elle n'importe jamais `safety` (ajouté lors de la revue de cohérence finale, résout la contradiction identifiée entre l'invariant #20 et le besoin de la voix de déclencher STOP) | Lint d'import (`interfaces/` n'importe jamais `safety`) + test runtime (STOP déclenché depuis un stub d'interface via Event atteint `safety.should_stop()==true` en dessous d'un seuil de latence mesuré) | **BLOQUANT** |
| 22 | **Une action externe non-idempotente et non-vérifiable après crash n'est jamais rejouée ni marquée réussie silencieusement** — `ExecutionRecord` en `UNKNOWN`+`UNVERIFIABLE` déclenche toujours une escalade utilisateur (ajouté lors de la revue de cohérence finale, contrat `ExecutionRecord` §18 de `RAYA_V2_CONTRACTS.md`) | Test runtime : simuler un crash entre écriture `EXECUTING` et `COMPLETED` sur un tool `idempotent=false` sans mécanisme de vérification, vérifier qu'aucun retry automatique ni faux `COMPLETED` ne se produit | **BLOQUANT** |
| 23 | **Un `WorldStateFact` `stale`/`superseded` n'atteint jamais le modèle sans son statut de fraîcheur explicite** — toute `Context.sections[i]` de `kind=world_state` porte un `freshness.status`/`freshness.as_of` non-null (ajouté lors de la revue de cohérence finale, `RAYA_V2_CONTRACTS.md` §14) | Validation de schéma (`Context` refusé à la construction si une section `world_state` a `freshness=null`) + test runtime : assembler un `Context` à partir d'un `WorldStateFact` expiré (`status=stale`), vérifier que `freshness.status="stale"` apparaît bien dans la section correspondante, jamais omis ni présenté comme `active` | **BLOQUANT** |

### 4.1 Comment ces invariants préviennent spécifiquement les anti-patterns identifiés en V1

| Anti-pattern V1 constaté | Invariant(s) qui l'empêche en V2 |
|---|---|
| `modules/pc_control/auto_agent.py` (second cerveau, appel Ollama direct) | #1, #2, #5 |
| `modules/ghost/ghost_mode.py` (bypass total du Harness) | #2, #20 |
| `modules/voice/continuous_voice.py` (décide seule d'annuler des tâches) | #6, #21 (STOP reste levable par tout canal via Event, mais jamais par appel direct à `safety` ; la décision de QUOI annuler passe par #7/#20) |
| `core/orchestrator.py` (monolithe mémoire+safety+attention+modèle+tasks+UI) | #4, #8, #9, #10, #13, #20 |
| `core/llm.py` (contrat Anthropic-shaped comme fondation) | #2, #10, et le test négatif explicite en `tests/models/` |
| `modules/vision/presence_watcher.py` (analyse webcam continue) | #12 |
| `core/agent_state.py` / `modules/camera/stream.py` (couplage direct à `ui.state`) | #4, #20 |
| `_process_lock` global de `core/orchestrator.py` (conversation bloque tâche de fond) | #7 |
| 3 systèmes nommés "brain" (confusion) | #13, et l'absence délibérée du mot "brain" dans `RAYA_V2_REPOSITORY_STRUCTURE.md` |

---

## 5. Intégration à la CI

- Les invariants **BLOQUANT** de type lint/scan statique s'exécutent à chaque commit, avant même les tests unitaires (rapides, pas de dépendance réseau).
- Les tests par catégorie (§1) s'exécutent en suite complète à chaque PR, hors tests `live`.
- Les tests `live` (~5) s'exécutent sur une cadence séparée (nightly ou sur déclenchement manuel), jamais comme condition bloquante de merge — leur `BLOCKED` en environnement CI standard (pas de Windows/navigateur/clé API) est un statut normal, pas un échec.
- Rapport de test final toujours présenté avec la répartition PASS/FAIL/BLOCKED/NOT_TESTED explicite par catégorie, jamais un simple pourcentage agrégé qui masquerait des `BLOCKED` derrière un score flatteur — c'est exactement le défaut à ne pas reproduire de la suite V1 (`test_ollama_provider.py` testait le mauvais cerveau et affichait pourtant un score PASS élevé et trompeur).

---

*Fin du document. Ceci clôt la série de conception architecturale RAYA V2 : `RAYA_V2_TECHNICAL_ARCHITECTURE.md`, `RAYA_V2_CONTRACTS.md`, `RAYA_V2_REPOSITORY_STRUCTURE.md`, `RAYA_V2_MIGRATION_PLAN.md`, `RAYA_V2_ARCHITECTURAL_INVARIANTS.md`.*
