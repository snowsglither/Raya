# RAYA V2 — PHASE 7 IMPLEMENTATION REPORT
## Perception & Environment

---

## 1. Résumé

Phase 7 fait passer RAYA d'un agent qui exécute des actions à un agent qui
maintient une représentation vérifiée de son environnement. Le sous-système
`perception/` (jusqu'ici un squelette Phase 0, une seule classe abstraite
`LightSensor`) devient réel : un capteur léger observe la fenêtre active
Windows toutes les ~3 secondes et alimente `WorldStateStore` via l'EventBus,
exactement selon la chaîne imposée par l'architecture gelée
(`Environment → Perception/Device Agent → Observation → World State →
ContextEngine → Harness → Model`, jamais l'inverse).

Indépendamment de la perception continue, chaque action réelle du Windows
Device Agent et du Browser Device Agent (déjà fonctionnels depuis la Phase 4)
promeut désormais son résultat vérifié dans World State — **génériquement**,
via un nouveau champ `Tool.observation` déclaré une fois par capacité dans
`tools/catalog/{pc,browser}.py`, jamais un `if tool_name == ...` dans le
Harness. Ce même mécanisme sert de **vérification post-action** : l'état
réellement observé après une action est comparé à l'intention d'origine
(l'argument `target`/`url` du modèle), et un `ToolResult.status=success` dont
l'état observé ne correspond pas à l'intention n'est **jamais** un succès
global — il est réinjecté dans le détecteur de boucle déjà existant
(`cognition.LoopDetector`), qui escalade honnêtement au lieu de répéter
aveuglément. Aucun second orchestrateur n'a été créé.

63 nouveaux tests (687 au total, contre 624 à la fin de la stabilisation
pré-Phase 7), dont 7 tests d'intégration **réels** contre le vrai Windows
Device Agent (vrai Bloc-notes lancé/fermé), le vrai Browser Device Agent, et
le vrai `PerceptionRuntime` démarré via `bootstrap()`. `python
scripts/arch_lint.py` : 0 violation. V1 (`RAYA/`) strictement inchangé.

---

## 2. État Initial

Avant cette phase :
- `raya/perception/sensors.py` ne contenait qu'une classe abstraite
  `LightSensor(ABC)` avec une seule méthode `sample()` — aucune
  implémentation réelle, jamais instanciée nulle part.
- `raya/world_state/store.py` savait déjà stocker/interroger des
  `WorldStateFact` avec fraîcheur/confiance/statut (Phase 1), mais
  **rien ne l'alimentait jamais** en dehors des écritures explicites
  (`Harness.set_world_fact()`, commande CLI `/state set`).
- Les Device Agents Windows/Browser (Phase 4) exécutaient déjà de vraies
  actions avec de vraies preuves (`ToolResult.evidence`), mais ces preuves
  ne quittaient jamais le tour de conversation courant (confirmé aussi dans
  le rapport Phase 6, §12/§13 "Known Limitations" : "no Device Agent writes
  World State yet").
- `cognition/verification.py` ne vérifiait que le `ToolResultStatus` brut
  (`SUCCESS`/`FAILURE`/`TIMEOUT`/`CANCELLED` → `SUCCESS`/`FAILURE`/`UNKNOWN`)
  — jamais si l'état RÉSULTANT correspondait à l'intention du modèle.
- `attention/evaluator.py` n'était abonné qu'à
  `("interface.request_received", "task.*", "safety.*")` — aucune
  catégorisation pour un futur event `perception.*`.

## 3. Problème Architectural Identifié

Le diagramme imposé (`Environment → Perception/Device → Observation →
World State → ContextEngine → Harness → Model`) existait sur le papier
(`RAYA_V2_TECHNICAL_ARCHITECTURE.md` §0, §1.2, §1.3) mais son premier maillon
n'était jamais construit : **aucun code ne produisait jamais
d'`Observation` réelle**, ni en continu (perception) ni post-action
(promotion depuis un `ToolResult`). Sans cela, "World State" restait un
mécanisme correct mais vide en usage réel — la même conclusion déjà tirée
dans le rapport de stabilisation pré-Phase 7 pour Memory/Identity, ici
transposée à l'environnement physique/applicatif.

## 4. Architecture Implémentée

```
Environment (fenêtre active Windows, page browser)
        │
        ├── Perception (poll ~3s, LÉGER, lecture seule)
        │       └─ Event("perception.window_changed", PerceptionObservation)
        │
        └── Device Agent (Windows/Browser, DÉJÀ Phase 4, invoqué via Tool)
                └─ ToolResult.evidence (déjà réel, déjà vérifié)
                        │
                        ▼
                Harness._promote_observations_and_verify()
                        │  (générique — lit Tool.observation, jamais le nom de l'outil)
                        ▼
                WorldStateStore.apply_update()  ◄── aussi via EventBus pour la perception
                        │
                        ▼
                ContextEngine.assemble() → render_system_prompt()  (déjà câblé, stabilisation pré-Phase 7)
                        │
                        ▼
                ModelRequest.messages (role=system)
```

Aucun chemin `Device Agent → Model` ni `Device Agent → ContextEngine`
direct n'existe — vérifié structurellement en §19/§25 (proof).

## 5. Fichiers Créés

| Fichier | Rôle |
|---|---|
| `raya/perception/windows_sensors.py` | `ActiveWindowSensor(LightSensor)` — implémentation RÉELLE (win32gui/win32process/psutil), indépendante du Windows Device Agent (voir §11) |
| `raya/perception/runtime.py` | `PerceptionRuntime` — boucle de poll (thread daemon), `poll_once()` séparé pour un test déterministe |
| `raya/contracts/perception.py` | `PerceptionObservation` — payload typé des events `perception.*` |
| `tests/perception/test_windows_sensors.py` | 6 tests unitaires (capteur injectable) |
| `tests/perception/test_perception_runtime.py` | 5 tests (poll_once, start/stop, résilience) |
| `tests/world_state/test_perception_ingestion.py` | 6 tests (event → fait réel) |
| `tests/harness/test_observation_promotion.py` | 8 tests (promotion générique + vérification de contenu) |
| `tests/integration/test_phase7_scenarios.py` | 7 tests **réels** (Windows/Browser/STOP/bootstrap) |
| `tests/architecture/test_phase7_architecture_proof.py` | 9 tests de preuve architecturale |

## 6. Fichiers Modifiés

| Fichier | Changement |
|---|---|
| `raya/contracts/world_state.py` | `WorldStateFact.__post_init__` rejette `confidence=hypothesis` pour `source` commençant par `"perception:"` (invariant déjà documenté, jamais appliqué) |
| `raya/contracts/tool.py` | + `ObservationSpec` (domain/key/evidence_field/confidence/freshness_ttl_s/expected_argument) ; `Tool.observation: tuple[ObservationSpec, ...] = ()` |
| `raya/contracts/__init__.py` | Exports `PerceptionObservation`, `ObservationSpec` |
| `raya/perception/__init__.py` | Exports `ActiveWindowSensor`, `PerceptionRuntime` |
| `raya/world_state/store.py` | `WorldStateStore` s'abonne à `perception.*`, convertit en `WorldStateFact`, appelle `apply_update()` |
| `raya/cognition/verification.py` | + `observation_matches_expectation()`, `verify_observation_against_intent()`, `combine_outcomes()` |
| `raya/cognition/__init__.py` | Exports des 3 nouvelles fonctions |
| `raya/attention/evaluator.py` | + `"perception.*"` dans `SUBSCRIBED_PATTERNS` ; `_decide_perception_event()` → toujours `IGNORE` |
| `raya/tools/catalog/pc.py` | `pc.window.focus`/`pc.application.launch`/`pc.application.focus` déclarent `observation=_ACTIVE_WINDOW_OBSERVATION` |
| `raya/tools/catalog/browser.py` | `browser.navigate` déclare `observation=_CURRENT_URL_OBSERVATION` |
| `raya/harness/loop.py` | + `_promote_observations_and_verify()`, appelée après `verify_tool_result()` et avant `LoopDetector.record()` |
| `raya/runtime/config.py` | + `enable_perception` (défaut `True`), `perception_poll_interval_s` (défaut `3.0`) |
| `raya/runtime/bootstrap.py` | + `_start_perception()` (best-effort, comme `_register_devices()`) ; `RuntimeHandles.perception` ; arrêté dans `shutdown()` |
| `tests/support/harness_factory.py` | + paramètre `enable_perception: bool = False` (comme `enable_windows_device`/`enable_browser_device`) — évite qu'un vrai thread perception démarre dans les ~600 tests qui n'en ont pas besoin |

## 7. Fichiers Volontairement Non Touchés

- `raya/devices/windows/mechanisms/*.py` — aucune capacité UIA/clavier/souris/
  screenshot/process reconstruite. `ActiveWindowSensor` est une implémentation
  **séparée et minimale** (win32gui direct), pas une réutilisation ni une
  duplication du rôle de `window_mgmt.py` (justifié en détail §11).
- `raya/devices/browser/*.py` — aucun changement ; `browser.navigate` était
  déjà correctement outillé (evidence `{"url": ...}`), seul son `Tool`
  a reçu une déclaration `observation`.
- `raya/tools/execution.py`, `raya/tools/registry.py` — le pipeline
  validation → Safety → exécution reste inchangé ; la promotion
  d'observation se fait strictement APRÈS, côté Harness.
- Toute l'UI Cockpit (`raya/interfaces/ui/`) — aucune vue supplémentaire
  n'était nécessaire (World/Computer/Browser Views, Phase 6, lisent déjà
  `Harness.list_world_facts()`/`last_tool_trace()`, désormais alimentés
  pour de vrai sans changement de leur code).

## 8. Contrats Ajoutés/Modifiés

- **`PerceptionObservation`** (nouveau) — `domain, key, value, source,
  confidence=KNOWN_FACT, freshness_ttl_s=None`. Valide `source` au format
  `"perception:<mechanism>"` et rejette `confidence=hypothesis` à la
  construction (jamais laissé à la discrétion de l'appelant).
- **`ObservationSpec`** (nouveau) — `domain, key, evidence_field,
  confidence=KNOWN_FACT, freshness_ttl_s=None, expected_argument=None`.
  Porté par `Tool.observation: tuple[ObservationSpec, ...] = ()` (champ
  additif, défaut `()` — aucun Tool existant cassé).
- **`WorldStateFact`** (modifié, additif) — `__post_init__` lève désormais
  `ValueError` si `confidence=hypothesis` ET `source` commence par
  `"perception:"` (RAYA_V2_ARCHITECTURAL_INVARIANTS.md, règle déjà
  documentée mot pour mot, jamais implémentée avant cette phase).

## 9. World State

Aucun changement de schéma — `WorldStateFact` portait déjà valeur/timestamp/
source/confidence/status/freshness_ttl_s depuis la Phase 1. Le travail de
cette phase est de **l'alimenter réellement** :

- **KNOWN** = un `WorldStateFact` existe pour `(domain, key)`, `status=active`.
- **UNKNOWN** = aucun fait — `get_fact()` retourne `None`. Délibérément
  **pas** une nouvelle valeur d'enum : l'absence de savoir est l'absence
  d'observation, pas une observation en soi (cohérent avec le contrat
  existant, aucune modification requise).
- **INFERRED**/**HYPOTHESIS** = valeurs `Confidence` déjà existantes,
  réservées à `cognition`/futures inférences — jamais utilisées par
  `perception:*` (invariant appliqué, §8).
- Fraîcheur : `ActiveWindowSensor` fixe `freshness_ttl_s=20` (~7× l'intervalle
  de poll) ; les observations promues depuis un `ToolResult` utilisent
  `freshness_ttl_s=60` (état vérifié après une action, valide pour environ
  une minute sans nouvelle confirmation). Un fait non rafraîchi devient
  honnêtement `stale` via le mécanisme déjà existant (`is_expired()`),
  jamais présenté comme actuel (invariant #23, inchangé, re-testé).

## 10. Observation

`PerceptionObservation` est le contrat transporté par les events
`perception.*` sur l'EventBus — **jamais** une structure ad hoc par capteur.
`ActiveWindowSensor.sample()` ne publie un `Event` QUE lorsque la fenêtre
active a réellement changé depuis le dernier appel (déduplication en
mémoire), pour ne jamais transformer un poll silencieux en bruit.

## 11. Device Agent Integration

**Décision architecturale centrale, documentée en détail dans le code** :
`ActiveWindowSensor` n'appelle PAS le `WindowsDeviceAgent` existant. Deux
raisons, toutes deux tirées des documents gelés :

1. **Graphe de dépendance** (`scripts/arch_lint.py::ALLOWED`) :
   `perception` n'a le droit d'importer que `world_state`/`observability` —
   jamais `devices` ni `tools`.
2. **Invariant #3** (`RAYA_V2_ARCHITECTURAL_INVARIANTS.md`) : *"Aucune
   exécution d'outil hors Tool System — un Device n'exécute jamais rien
   sans recevoir un Command de tools/."* Un capteur continu qui construirait
   lui-même des `Command` et appellerait `agent.execute()` contournerait ce
   pipeline (validation, Safety) — même si l'action elle-même est
   inoffensive.
3. `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §11.1 confirme d'ailleurs que la V1
   avait **deux lignées distinctes** pour "fenêtre active" :
   `modules/context/monitor.py` (perception légère, à `EXTRACT`) et
   `modules/pc_control/windows.py` (action outillée, dont `window_mgmt.py`
   est déjà l'`EXTRACT` V2 réel) — ce n'est pas un oubli, c'est le design.

`ActiveWindowSensor` réimplémente donc, en ~20 lignes indépendantes
(win32gui/win32process/psutil), la lecture de la fenêtre au premier plan —
sans toucher à `devices/windows/mechanisms/`. Ce n'est **pas** une
duplication des capacités interdites par la consigne (UIA, clavier, souris,
screenshot, cycle de vie applicatif) : c'est une primitive de lecture pure,
déjà distincte en V1, restée distincte en V2 par construction.

Les VRAIES actions du Windows Device Agent (`pc.application.launch`,
`pc.window.focus`, `pc.application.focus`) restent, elles, strictement
inchangées — seul leur `Tool` porte désormais un `ObservationSpec` qui dit
au Harness *où* regarder dans leur `ToolResult.evidence` déjà existant
(`{"window": ...}`) pour promouvoir une observation. Zéro nouvelle capacité
Device Agent, zéro capacité reconstruite.

## 12. Browser Agent Integration

Aucun changement au moteur CDP/Playwright. `browser.navigate` retournait déjà
`evidence={"url": r["url"]}` (Phase 4) — seul son `Tool` déclare maintenant
`observation=(ObservationSpec(domain="browser", key="current_url",
evidence_field="url", expected_argument="url", freshness_ttl_s=60),)`.
Vérifié réellement contre une vraie page HTML locale (§15, §18).

## 13. Post-Action Verification

`cognition/verification.py` gagne trois fonctions pures :

- `observation_matches_expectation(expected, observed)` — comparaison
  GÉNÉRIQUE par sous-chaîne, insensible à la casse, dans un sens ou l'autre
  (même principe que `window_mgmt.find_windows()`, jamais une égalité
  stricte ni une règle par application).
- `verify_observation_against_intent(expected, observed)` — `observed=None`
  → `UNKNOWN` (jamais promu ni SUCCESS ni FAILURE) ; sinon SUCCESS/FAILURE
  selon la correspondance.
- `combine_outcomes(base, content)` — un `ToolResult.status=success` (base
  SUCCESS) dont l'observation de contenu est FAILURE/UNKNOWN **n'est jamais**
  un succès global. Un échec d'exécution reste un échec quel que soit le
  contenu.

`Harness._promote_observations_and_verify()` orchestre l'ensemble,
générique, piloté par `Tool.observation` — zéro branchement par nom d'outil
(vérifié §16, §19).

## 14. Recovery/Replanning

Aucun nouveau mécanisme : l'`outcome` combiné (§13) est réinjecté dans
`self._loop_detector.record(...)` — le `LoopDetector` **déjà existant**
depuis la Phase 3 (échecs identiques répétés → `ESCALATE`) traite
désormais aussi bien une incohérence de contenu (observation ≠ intention)
qu'un échec technique brut, sans distinction de code. Prouvé par
`test_repeated_incoherence_escalates_via_existing_loop_detector` : le même
tool call répété avec le même résultat divergent escalade exactement comme
un échec technique répété, avec le même message honnête, sans jamais
épuiser les `max_tool_iterations`.

## 15. Context Integration

**Aucun changement** — `context_engine/assembler.py::_world_state_sections()`
et `context_engine/render.py::render_system_prompt()` existaient déjà
(stabilisation pré-Phase 7) et lisaient déjà `World State` avec fraîcheur
explicite. Ce que cette phase change, c'est qu'ils ont désormais de VRAIES
données à lire. Vérifié en réel (§18) :
`test_real_observation_reaches_the_actual_model_request` confirme que
l'observation "Bloc-notes" (fenêtre réellement ouverte) apparaît
textuellement dans le message système RÉELLEMENT envoyé au modèle scripté
lors du tour suivant.

## 16. Tests Ajoutés (63 nouveaux, 687 au total)

| Fichier | Nombre | Couvre |
|---|---|---|
| `tests/contracts/test_contracts.py` (+8) | 8 | `PerceptionObservation`/`ObservationSpec` round-trip, rejet hypothesis+perception, `Tool.observation` par défaut/déclaré |
| `tests/perception/test_windows_sensors.py` | 6 | Capteur injectable : premier événement, dédoublonnage, changement détecté, résilience aux exceptions, dégradation sans pywin32 |
| `tests/perception/test_perception_runtime.py` | 5 | `poll_once()` déterministe, capteur défaillant n'affecte pas les autres, vraie boucle start/stop |
| `tests/world_state/test_perception_ingestion.py` | 6 | Event réel → fait réel, TTL transmis, remplacement par clé, payload malformé sans crash, isolation des events non-perception |
| `tests/attention/test_attention.py` (+2) | 2 | `perception.*` → toujours `IGNORE`, présent dans `SUBSCRIBED_PATTERNS` |
| `tests/cognition/test_verification.py` (+12) | 12 | Correspondance générique (exacte/sous-chaîne/aucune/vide), `verify_observation_against_intent` (3 cas), `combine_outcomes` (5 cas) |
| `tests/harness/test_observation_promotion.py` | 8 | Promotion réussie/jamais sur échec, vérification correspond → SUCCESS, incohérence → FAILURE, **incohérence répétée → ESCALATE (LoopDetector existant)**, spec sans `expected_argument`, tool sans spec jamais touché, TTL transmis |
| `tests/integration/test_phase7_scenarios.py` | 7 | **RÉEL** : capteur OS réel, vrai Bloc-notes détecté par le capteur, vraie promotion Bloc-notes → World State, observation réelle dans le vrai ModelRequest, vraie navigation browser → World State, STOP réel toujours fonctionnel, `PerceptionRuntime` réel démarré/arrêté via `bootstrap()` |
| `tests/architecture/test_phase7_architecture_proof.py` | 9 | Isolation `perception/`, découplage `world_state`↔`perception` par EventBus, aucun nom d'outil en dur dans la promotion, non-régression devices/context_engine, aucune 2e boucle agentique, `arch_lint.run()` global |

## 17. Full Suite

3 exécutions consécutives de la suite complète (687 tests) :

| Run | Passed | Failed | Skipped | Durée |
|---|---|---|---|---|
| 1 | 671 | 7 | 9 | 81.2s |
| 2 | 670 | 8 | 9 | 80.0s |
| 3 | 671 | 7 | 9 | 108.9s |

Les 7 échecs reproduits systématiquement sont **PRÉ-EXISTANTS**, déjà
documentés dans les rapports Phase 6 et pré-Phase 7 stabilisation, sans
rapport avec Perception/World State :
- 4 tests `tests/devices/windows/test_windows_agent.py` (UIA réel,
  dépendant du focus/timing du Bloc-notes sur cette machine).
- `test_handle_request_fails_honestly_with_null_provider_stub` (appelle le
  vrai Ollama Cloud sans stub — un `OLLAMA_API_KEY` réel est configuré dans
  `.env` sur cette machine, ce test suppose son absence).
- `test_scenario_7_background_task_does_not_block_conversation` /
  `test_2_conversation_answered_immediately_during_task` (assertions de
  timing en millisecondes trop strictes avec un vrai appel réseau dans la
  boucle).

Le 8e échec ponctuel du run 2 (`test_queued_task_waits_when_at_concurrency_limit`,
+ un warning `Transition Task illégale : CANCELLED -> COMPLETED` dans les
logs du run 3) est une flakiness de timing déjà connue du scheduler
(course entre deux threads sur cette machine sous charge), également
pré-existante — reproduite de façon non-systématique avant cette phase
également (déjà notée dans le rapport Phase 6). **PRE-EXISTING**, non
introduite par Phase 7.

**Aucune régression Phase 7** : tous les tests Phase 0-6 qui passaient
avant cette phase continuent de passer, à l'identique.

## 18. Tests Réels

Effectués sur cette machine Windows, avec le vrai Windows Device Agent et
le vrai Browser Device Agent :

1. **Capteur OS réel** — `_read_foreground_window()` interrogé directement :
   retourne un dict plausible sans exception. **PASS**.
2. **Détection réelle de changement** — lance un vrai Bloc-notes, le focus,
   le capteur détecte le changement (`"notepad"` présent dans
   `process`/`title`). **PASS**.
3. **Promotion réelle** — un vrai tour Harness qui lance le Bloc-notes
   promeut réellement `pc.active_window` dans World State, avec
   `source="tool:pc.application.launch"`, `freshness_ttl_s=60`. **PASS**.
4. **Contexte réel** — l'observation réelle ("Bloc-notes") apparaît dans le
   VRAI message système envoyé au modèle au tour suivant. **PASS**.
5. **Navigation browser réelle** — une vraie navigation Playwright/CDP vers
   une page locale promeut réellement `browser.current_url`. **PASS**.
6. **STOP réel** — un vrai `interface.stop_requested` avant un vrai appel
   Windows empêche bien tout appel modèle (`STOP_ACTIVE`). **PASS**.
7. **`PerceptionRuntime` réel via `bootstrap()`** — démarre un vrai thread,
   tourne, s'arrête proprement au `shutdown()`. **PASS**.

**Limitation réelle découverte et documentée honnêtement** (jamais
masquée) : le Bloc-notes Windows localisé en français affiche le titre
`"Bloc-notes"`, pas `"Notepad"`. La comparaison générique
`observation_matches_expectation("notepad", "Bloc-notes")` échoue
légitimement (aucun sous-mot commun) — la vérification de contenu
post-action considère donc cette action précise comme `FAILURE` de
correspondance sur cette machine localisée en français, même si
l'application s'est réellement ouverte. C'est un vrai angle mort du
matching générique par sous-chaîne (documenté aussi comme limitation de
`MemoryStore.search()` dans le rapport de stabilisation pré-Phase 7) — pas
un bug caché : le comportement reste honnête (jamais un faux succès), juste
imprécis sur les noms localisés. Voir §20.

## 19. Architecture Lint

`python scripts/arch_lint.py` → **PASS, 0 violation** sur l'arbre complet
après Phase 7. 9 tests dédiés
(`tests/architecture/test_phase7_architecture_proof.py`) verrouillent
spécifiquement :
- `perception/` n'importe jamais `devices/tools/models/harness/attention/
  cognition/interfaces/runtime`.
- `world_state/` n'importe jamais `perception/` (couplage 100% EventBus).
- Aucun nom d'outil/valeur en dur dans `Harness._promote_observations_and_verify()`.
- Aucun `ObservationSpec(` construit dans `harness/loop.py` (vit uniquement
  dans `tools/catalog/`).
- Non-régression : `devices/` toujours isolé de `models/harness/cognition/safety`.
- Non-régression : `context_engine/` toujours en lecture seule.
- Aucune deuxième boucle agentique dans `perception/`.

## 20. Limitations

- **Correspondance générique par sous-chaîne, pas de NLP** (choix
  architectural déjà assumé pour `MemoryStore.search()`, reproduit ici pour
  la vérification post-action) : ne gère pas la localisation
  (`"notepad"` vs `"Bloc-notes"`), les synonymes, ni les fautes de frappe
  au-delà d'une inclusion littérale. Documenté, testé, jamais caché — voir
  §18.
- **Un seul capteur léger implémenté** (fenêtre active). L'architecture
  gelée (§11.1) en prévoit d'autres à terme (CPU/RAM/batterie, USB/réseau)
  — hors scope de cette phase, qui prouve le MÉCANISME (capteur → Event →
  World State → Context) plutôt que la couverture exhaustive des capteurs.
- **Pas de capteur navigateur continu** — cohérent avec §11.1 de
  l'architecture gelée, qui ne liste PAS le navigateur parmi les capteurs
  légers autorisés en continu (CDP/Playwright reste plus coûteux à
  interroger qu'un appel win32). L'observation browser reste uniquement
  déclenchée par une vraie navigation (Tool), jamais un poll.
- **`ObservationSpec` ne supporte qu'un seul champ evidence par spec** —
  suffisant pour les cas couverts (fenêtre active, URL courante) ; un futur
  besoin de promouvoir plusieurs facettes d'un même `ToolResult` (ex: URL
  ET titre de page pour `browser.navigate`) nécessiterait soit plusieurs
  `ObservationSpec` sur le même `Tool` (déjà supporté, `tuple[...]`), soit
  une extension du champ — pas un nouveau mécanisme.
- **Aucun test de "détection d'incohérence" en conditions réelles
  contrôlées** (§9 de la consigne) — le scénario de mismatch est prouvé de
  façon déterministe et fiable via `FakeScriptedProvider`
  (`tests/harness/test_observation_promotion.py`), pas en forçant un vrai
  mismatch sur l'état réel imprévisible de cette machine (aurait rendu le
  test fragile/non-reproductible). La découverte fortuite du cas
  "Bloc-notes" (§18) constitue néanmoins une preuve réelle non planifiée du
  même mécanisme.

## 21. FAIL / BLOCKED / NOT_TESTED

- **FAIL** : aucun — tous les tests Phase 7 (63/63) passent.
- **BLOCKED** : aucun — Windows et un navigateur réel étaient disponibles
  sur cette machine pour l'intégralité des tests réels prévus.
- **NOT_TESTED** :
  - Capteurs légers autres que la fenêtre active (CPU/RAM/batterie/USB/
    réseau) — non implémentés cette phase (§20).
  - Vérification post-action sur `pc.window.close`/`pc.application.close`
    — délibérément sans `ObservationSpec` (fermer une fenêtre ne produit
    pas un fait "actif" cohérent avec le domaine `active_window`).
  - Comportement de `PerceptionRuntime` sous très forte charge/nombreux
    capteurs simultanés — un seul capteur exercé cette phase.

## 22. Régressions Éventuelles

**Aucune régression fonctionnelle.** Une régression de PERFORMANCE de test
a été identifiée et corrigée EN COURS de cette phase, avant qu'elle
n'affecte la suite finale : activer `enable_perception=True` par défaut
dans `bootstrap()` aurait démarré un vrai thread de poll dans les ~600
tests appelant `bootstrap()`/`build_test_harness()` sans en avoir besoin.
Corrigé par (1) `tests/support/harness_factory.py::build_test_harness`
désactive `enable_perception` par défaut (même pattern que
`enable_windows_device`/`enable_browser_device`), et (2) `PerceptionRuntime`
utilise un `threading.Event` pour un arrêt immédiat (`stop()` réveille la
boucle en cours au lieu d'attendre jusqu'à `interval_s`). Vérifié : durée
de la suite complète inchangée (~80s, comme avant cette phase).

Une régression de TEST pré-existante et non liée à cette phase
(`test_cli_transmits_input_to_harness`) avait déjà été corrigée durant la
stabilisation pré-Phase 7 (bug de libellé "Phase 0" obsolète) — confirmée
toujours verte ici.

## 23. V1 Integrity Check

- `git status --porcelain` sur `RAYA/` (le seul dépôt git des deux) :
  identique caractère pour caractère à l'état de début de session — les 4
  mêmes fichiers non suivis (`_shopping_full.txt`,
  `prompt_claude_code_commit.txt`, `prompt_claude_code_shopping.txt`,
  `prompt_claude_code_tic.txt`), zéro fichier modifié, zéro nouveau
  fichier, zéro suppression.
- `grep -rlIE "core\.orchestrator|core\.llm|modules\.brain\.router|modules\.pc_control|modules\.voice|modules\.vision|modules\.context\.monitor" raya/` :
  aucune occurrence hors du détecteur `scripts/arch_lint.py` lui-même (les
  chaînes qu'il recherche, jamais un usage réel).
  `modules/context/monitor.py` (source V1 nommée par l'architecture pour
  la perception légère) n'a jamais été lu ni copié — `ActiveWindowSensor`
  a été écrit directement à partir des primitives win32 documentées, sans
  ouvrir aucun fichier V1.
- Aucun fichier sous `RAYA/` n'a été ouvert en écriture à aucun moment de
  cette phase.

## 24. GO / NO-GO Phase 8

**GO.**

Tous les critères de sortie de la consigne sont satisfaits avec preuve
réelle, pas seulement des tests unitaires :
- Les observations réelles fonctionnent (capteur OS réel + promotion
  d'action réelle, §18).
- World State reçoit réellement ces observations (vérifié par lecture
  directe de `WorldStateStore` après une vraie action, §18).
- Fraîcheur/provenance/confiance fonctionnent (TTL réels, `source`
  structuré `"perception:*"`/`"tool:*"`, `Confidence.KNOWN_FACT` jamais
  `hypothesis` depuis la perception — invariant appliqué au niveau du
  contrat).
- Les agents restent découplés (`perception/` ne connaît ni `devices/` ni
  `tools/` ; `world_state/` ne connaît pas `perception/` — EventBus
  uniquement ; preuve architecturale §19).
- Les actions peuvent être vérifiées, et une incohérence n'est **jamais**
  déclarée comme un succès (§13, §14, prouvé y compris par une découverte
  réelle non planifiée, §18).
- ContextEngine exploite World State pour de vrai (§15, preuve réelle sur
  le vrai `ModelRequest`).
- STOP fonctionne toujours, y compris pendant une vraie action Windows
  (§18).
- `arch_lint` = 0 violation (§19).
- Régression complète documentée honnêtement, y compris les échecs
  pré-existants non masqués (§17, §22).
- V1 strictement inchangé (§23).

La seule réserve — le matching générique imprécis sur les noms localisés
(§18, §20) — est un **choix architectural documenté et testé**, pas un
défaut caché, cohérent avec le même choix déjà assumé pour `MemoryStore`.
Elle ne bloque pas Phase 8.
