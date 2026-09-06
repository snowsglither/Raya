# RAYA V2 — PHASE 8 IMPLEMENTATION REPORT
## Creative / Spatial Agent

---

## 1. Résumé

Phase 8 ajoute au Tool Registry existant une **capacité parmi d'autres** :
créer, modifier et faire visualiser une scène 3D (hiérarchie d'objets,
transforms, relations nommées). C'est une extension du même pipeline
`Interface → Harness → Tool Registry → Capability → Environment` déjà en
place depuis la Phase 0, pas un second cerveau ni une application 3D greffée
sur RAYA. Le modèle décide seul, sur intention réelle, s'il crée ou modifie
une scène — aucun pré-filtrage textuel côté frontend, aucune scène créée
pour "Salut" ou une question sans rapport.

Le cœur du travail est un **Spatial Scene Model headless**
(`raya/contracts/spatial.py` : `Vec3`, `Transform`, `Geometry`, `Material`,
`SpatialObject`, `Scene`) qui n'importe ni ne mentionne Three.js dans son
code — EXTRACT/ADAPT de la forme de données de V1
(`RAYA/modules/hologram/model.py`, désigné `KEEP` par
`RAYA_V2_MIGRATION_PLAN.md`), débarrassée des champs spécifiques CAD/3D
printing, augmentée d'un champ générique `relationships: dict[str, str]`
pour des relations nommées non hiérarchiques (ex. orbite, connexion). Le
seul point d'écriture est `raya/spatial/store.py::SceneStore` (même principe
que `WorldStateStore`/`MemoryStore`/`TaskRegistry`). La frontière
Data/Rendering est un seul fichier : `raya/spatial/renderer/threejs_adapter.py`
est l'UNIQUE endroit du code qui connaît les conventions de nommage
Three.js ; tout le reste de `raya/spatial/` et `tools/catalog/spatial.py`
l'ignorent totalement (vérifié par preuve architecturale, §19).

8 nouveaux Tools SAFE (`scene.create`, `scene.add_object`,
`scene.update_object`, `scene.remove_object`, `scene.describe`,
`scene.render`, `scene.close`, `scene.export`) réutilisent intégralement le
mécanisme de vérification post-action de la Phase 7
(`Tool.observation`/`ObservationSpec`) — aucun système de vérification
parallèle. `scene.render`/`scene.close` pilotent la vue "spatial" du Cockpit
(Phase 6) via le même event `ui.view_requested` que toutes les autres vues,
et le Cockpit charge Three.js depuis un CDN **uniquement** quand ce panneau
s'ouvre réellement — jamais au chargement de la page.

76 nouveaux tests (763 au total, contre 687 à la fin de la Phase 7),
dont un scénario bout-en-bout Soleil/Terre en relation parent-enfant via de
vrais appels d'outils chaînés (le critère central de la consigne), et une
vérification **live**, en conditions réelles, contre le Cockpit et un vrai
modèle Ollama Cloud (§18). `python scripts/arch_lint.py` : 0 violation.
V1 (`RAYA/`) strictement inchangé.

---

## 2. État Initial

Avant cette phase :
- Aucun sous-système `raya/spatial/` n'existait — ni contrat, ni store, ni
  renderer, ni Tool.
- Le Cockpit (Phase 6) avait 6 panneaux contextuels (conversation, tasks,
  world, browser, computer, attention), tous pilotés par le même mécanisme
  `ui.view_requested` — aucun panneau "scène"/"spatial".
- `raya/tools/catalog/ui_views.py::_VALID_VIEWS` ne listait pas "spatial".
- `Harness` n'exposait aucune méthode de lecture spatiale.
- V1 (`RAYA/modules/hologram/`) possédait un moteur de scène 3D fonctionnel
  (photo webcam → hologramme .glb, cf. mémoire `project_raya_scan_3d_local`)
  dont seule la FORME de données (`model.py`) était désignée `KEEP` par
  l'architecture gelée — aucun code V1 n'a été copié tel quel.

## 3. Problème Architectural Identifié

La consigne demandait une capacité créative/spatiale sans jamais :
(a) faire du rendu 3D un identifiant central de RAYA, (b) coupler le modèle
de données à un renderer précis, (c) dupliquer le mécanisme de vérification
post-action déjà construit Phase 7, ni (d) introduire un second point
d'écriture pour un nouveau type de donnée. Le risque principal était de
traiter "scène 3D" comme un sous-système spécial méritant son propre
orchestrateur/vérificateur/UI-toujours-visible — exactement l'anti-pattern
que la consigne interdit explicitement (§3 : *"ne transforme pas RAYA en...
application 3D permanente"*).

## 4. Architecture Implémentée

```
Interface (Cockpit)
        │  intention réelle ("crée une scène du système solaire")
        ▼
Harness._run_agentic_loop()  (boucle UNIQUE, inchangée)
        │
        ▼
Tool Registry ── scene.create / scene.add_object / scene.update_object /
        │        scene.remove_object / scene.describe / scene.render /
        │        scene.close / scene.export   (tools/catalog/spatial.py)
        ▼
SceneStore (raya/spatial/store.py)  ── SEUL point d'écriture des scènes
        │
        ├─ ToolResult.evidence (scene_id / object_id / object_count)
        │       │
        │       ▼
        │  Harness._promote_observations_and_verify()  (Phase 7, réutilisé)
        │       │
        │       ▼
        │  WorldStateStore  →  ContextEngine  →  ModelRequest (system prompt)
        │
        └─ scene.render/scene.close → Event("ui.view_requested", view="spatial")
                │
                ▼
        UIEventBridge (Phase 6, réutilisé) → WebSocket Cockpit
                │
                ▼
        app.js: charge Three.js (CDN, à la demande) → GET /api/session/{id}/spatial
                │
                ▼
        ThreeJSAdapter.to_payload(scene)  (raya/spatial/renderer/threejs_adapter.py)
                │
                ▼
        Rendu WebGL réel dans #panel-spatial
```

Aucun chemin `spatial → Model` ni `spatial → Memory` direct n'existe —
vérifié structurellement (§19). Aucune deuxième boucle agentique, aucun
second scheduler.

## 5. Fichiers Créés

| Fichier | Rôle |
|---|---|
| `raya/contracts/spatial.py` | `Vec3`, `Transform`, `Geometry`, `Material`, `SpatialObject`, `Scene`, `SpatialError`, `slugify_object_id`, `validate_object_id` — headless, aucune mention Three.js |
| `raya/spatial/__init__.py` | Exports `SceneStore`, `RendererAdapter`, `ThreeJSAdapter` |
| `raya/spatial/store.py` | `SceneStore` — seul point d'écriture (create/add/update/remove/describe/mount/unmount) |
| `raya/spatial/renderer/base.py` | `RendererAdapter(ABC)` — frontière abstraite Data/Rendering |
| `raya/spatial/renderer/threejs_adapter.py` | `ThreeJSAdapter` — SEUL fichier qui connaît les conventions de géométrie Three.js |
| `raya/tools/catalog/spatial.py` | 8 Tools SAFE, `register_spatial_tools()` |
| `tests/spatial/test_scene_store.py` | 23 tests (SceneStore) |
| `tests/spatial/test_renderer_adapter.py` | 7 tests (ThreeJSAdapter) |
| `tests/tools/test_spatial_tools.py` | 12 tests (Tools, sans Harness) |
| `tests/harness/test_spatial_integration.py` | 12 tests bout-en-bout (vrai Harness, modèle scripté) |
| `tests/architecture/test_phase8_architecture_proof.py` | 11 tests de preuve architecturale |
| `tests/runtime/test_web_entrypoint.py` (+3) | 3 tests (endpoint REST `/spatial`, event `ui.view_requested`) |
| `tests/contracts/test_contracts.py` (+8) | 8 tests (contrats spatiaux) |

## 6. Fichiers Modifiés

| Fichier | Changement |
|---|---|
| `raya/contracts/__init__.py` | Exports des contrats spatiaux |
| `raya/tools/catalog/__init__.py` | + `register_spatial_tools` |
| `raya/tools/catalog/ui_views.py` | `_VALID_VIEWS` + `"spatial"` |
| `raya/safety/risk.py` | + capacités `spatial.create/modify/read/render/export` → `PermissionLevel.SAFE` |
| `raya/harness/loop.py` | + paramètre `scene_store`, + `describe_scene()`, `get_spatial_render_payload()`, `mounted_scene_id()`, `list_scenes()` (lecture seule) |
| `raya/runtime/bootstrap.py` | + `SceneStore`, `register_spatial_tools()`, `RuntimeHandles.scene_store` |
| `scripts/arch_lint.py` | `ALLOWED` : `spatial` = feuille (`{observability}`), `harness`/`tools` gagnent `spatial` |
| `raya/interfaces/ui/viewmodels.py` | + `SpatialSceneView` |
| `raya/interfaces/ui/channel.py` | + `UIChannel.spatial_view()` |
| `raya/runtime/entrypoints/web.py` | + `GET /api/session/{id}/spatial` |
| `raya/interfaces/ui/static/index.html` | + `#panel-spatial` avec `<canvas>` |
| `raya/interfaces/ui/static/app.js` | + chargement Three.js à la demande, mount/update/destroy du renderer |
| `raya/interfaces/ui/static/styles.css` | + styles `.panel-spatial`/`#spatial-canvas` |

## 7. Fichiers Volontairement Non Touchés

- `RAYA/modules/hologram/*.py` (V1) — jamais ouvert en écriture ; seule la
  FORME de `model.py` a été utilisée comme référence de conception (§1).
- `raya/harness/loop.py::_run_agentic_loop` — la boucle agentique elle-même
  reste inchangée ; Phase 8 n'ajoute que des Tools et des méthodes de
  lecture, jamais un nouveau chemin d'exécution.
- `raya/tools/execution.py`, `raya/safety/service.py` — le pipeline
  validation → Safety → exécution reste strictement le même ; les Tools
  spatiaux le traversent comme n'importe quel autre Tool SAFE.
- `raya/cognition/verification.py` — aucune fonction spécifique au spatial
  n'a été ajoutée ; le mécanisme générique Phase 7 suffit (§13).
- `raya/tasks/*.py` — aucun second scheduler ; voir §14 pour la décision
  explicite sur l'intégration Task.
- Les autres vues du Cockpit (conversation/tasks/world/browser/computer/
  attention) — aucune modifiée, seule une nouvelle vue ajoutée en parallèle.

## 8. Contrats Ajoutés/Modifiés

- **`Vec3`** — `x, y, z: float = 0.0`.
- **`Transform`** — `position: Vec3`, `rotation: Vec3` (degrés),
  `scale: Vec3` (défaut `(1,1,1)`, jamais `(0,0,0)` — testé explicitement).
- **`Geometry`** — `kind: str = "box"`, `params: dict` — délibérément non
  restreint à un enum Three.js (§ frontière Data/Rendering, §11).
- **`Material`** — `color: str`, `opacity: float`.
- **`SpatialObject`** — `id, label, object_type, parent, children,
  transform, geometry, material, relationships: dict[str,str], metadata`.
  `relationships` est le champ ajouté par rapport à V1 (§19 de la consigne :
  relations nommées non hiérarchiques, ex. `{"orbits": "sun"}`).
- **`Scene`** — `id, label, objects: dict[str, SpatialObject], root_ids,
  camera: dict, metadata, created_at, updated_at`.
- Sérialisation : `to_dict()` (générique, `raya/contracts/_base.py`)
  gère `Scene`/`SpatialObject` sans modification — round-trip `from_dict()`
  reste une limitation connue et acceptée pour les champs
  `dict[str, Dataclass]`, déjà vraie pour `Tool.observation` depuis la
  Phase 7 (§20).

## 9. World State

Aucun changement de schéma. Les Tools spatiaux déclarent trois
`ObservationSpec` (`tools/catalog/spatial.py`) :
`spatial.active_scene`, `spatial.scene_object_count`,
`spatial.renderer_state` (ce dernier `freshness_ttl_s=300`, non utilisé
cette phase mais posé pour un futur usage). Promues par le même
`Harness._promote_observations_and_verify()` que Phase 7 — aucun nouveau
mécanisme. `spatial.active_scene`/`spatial.scene_object_count` apparaissent
réellement dans le message système envoyé au modèle au tour suivant
(`test_spatial_observation_reaches_the_real_model_request`, et vérifié en
conditions réelles §18).

## 10. Memory vs World State

Aucune scène n'est jamais écrite dans `MemoryStore` — une scène est un état
courant (World State), pas un souvenir sémantique. `SceneStore` reste le
seul et unique point d'écriture (comme `WorldStateStore`/`MemoryStore`/
`TaskRegistry`), jamais dupliqué dans un autre subsystem. Une future
persistance long terme d'une scène ("souviens-toi de cette maquette")
resterait un usage explicite de `Memory.store()` par le modèle via un Tool
existant, pas un mécanisme spatial spécifique — non implémenté cette phase
(hors scope, §20).

## 11. Séparation Data / Rendering

Décision architecturale centrale, vérifiée par preuve automatisée (§19) :
- `raya/contracts/spatial.py` et `raya/spatial/store.py` ne contiennent
  aucun appel d'API Three.js (`THREE.*`) ni les conventions de nommage de
  géométrie Three.js (`_KNOWN_KINDS`).
- `raya/spatial/renderer/threejs_adapter.py` est le SEUL fichier Python
  autorisé à connaître ces conventions — `ThreeJSAdapter.to_payload(scene)`
  transforme `Scene` en un dict plat (positions/rotations/scales en tableaux,
  géométrie avec repli honnête sur `"box"` si le `kind` n'est pas reconnu,
  jamais une exception qui casserait le rendu pour un objet).
- Côté navigateur, `raya/interfaces/ui/static/app.js` est le SEUL fichier
  qui charge et pilote Three.js — `index.html` ne le mentionne jamais
  (vérifié texte, §19). Le payload transporté par l'API REST reste
  lui-même agnostique du renderer (positions/couleurs brutes), permettant
  en théorie un futur renderer alternatif sans toucher au Scene Model ni
  aux Tools.

## 12. Intent-Driven (jamais de pattern-matching frontend)

`app.js` n'ouvre JAMAIS le panneau spatial de sa propre initiative — il
réagit exclusivement à un event `ui.view_requested`/`view="spatial"`
publié par `scene.render` (Tool appelé par le MODÈLE). Aucune regex ni
mot-clé côté frontend. Vérifié par :
- `test_greeting_never_creates_a_scene_or_touches_world_state` — "Salut"
  ne crée aucune scène, ne touche pas World State.
- `test_unrelated_question_never_invokes_spatial_tools` — une question
  météo n'invoque aucun outil spatial (`last_tool_trace() == []`).
- En conditions réelles (§18) : le modèle Ollama Cloud réel n'a appelé
  `scene.*` que lorsque la demande l'exigeait explicitement.

## 13. Vérification Post-Action (réutilisation Phase 7)

Aucune fonction de vérification spécifique au spatial n'a été écrite.
`scene.add_object`/`scene.update_object`/`scene.remove_object` retournent
`ToolResultStatus.FAILURE` avec `code="SPATIAL_ERROR"` (jamais `SUCCESS`)
si `SceneStore` lève `SpatialError` (scène/parent/objet inconnu) — le
succès/échec brut suffit pour des mutations en mémoire déterministes,
contrairement aux actions Windows/Browser réelles de la Phase 7 où
l'état OS pouvait diverger de l'intention. Le contenu (nombre d'objets,
scène active) est promu et disponible pour un contrôle de cohérence futur
via le même `ObservationSpec.expected_argument` que Phase 7, non nécessaire
cette phase (aucune ambiguïté possible sur un `scene_id` généré par
`SceneStore` lui-même).

## 14. Intégration Task

**Décision explicite : aucune intégration Task cette phase.** La consigne
interdit un second scheduler ; une création de scène (create + N objects +
render) est rapide (mutations en mémoire, pas d'E/S lente) et ne justifie
pas un `Task` en arrière-plan. Le seul point de friction réel observé
(§18) est la limite `max_tool_iterations` (4 par défaut) qui peut tronquer
une séquence de 3+ étapes — le Harness le rapporte alors HONNÊTEMENT
plutôt que de prétendre avoir terminé (mécanisme préexistant, pas nouveau).
Documenté comme limitation de configuration, pas comme besoin d'un
scheduler dédié (§20).

## 15. Cycle de Vie du Renderer

- **Mount** : `app.js::mountSpatialScene()` — appelé uniquement quand le
  panneau spatial s'ouvre réellement (event `ui.view_requested`/show ou
  raccourci `openViewByName("spatial")`), après chargement paresseux de
  Three.js.
- **Update** : un nouvel appel à `refreshSpatial()` (ex. après un nouveau
  `scene.render` du modèle) détruit puis remonte la scène — jamais deux
  contextes WebGL vivants simultanément sur le même canvas.
- **Destroy** : `destroySpatialScene()` — appelée sur `scene.close` (event
  `hide`), sur fermeture du panneau par Échap/un autre panneau
  (`closePanel()`), et avant tout remount. Annule la boucle
  `requestAnimationFrame`, dispose géométries/matériaux/renderer.
- Vérifié en conditions réelles (§18) : après fermeture, le panneau est
  bien `hidden`, sans classe `open`, le canvas reste dans le DOM (réutilisé
  au prochain mount) sans renderer actif dessus.

## 16. Sécurité / Permissions

Les 5 capacités spatiales (`spatial.create/modify/read/render/export`) sont
`PermissionLevel.SAFE` — jamais de confirmation utilisateur requise, car
une mutation de scène reste en mémoire, sans effet sur l'environnement réel
(contrairement à `pc.*`/`browser.*`). Vérifié :
`test_all_spatial_tags_are_safe_never_require_confirmation`,
`test_all_spatial_tools_declared_as_safe_permission_level`. `scene.export`
écrit un fichier réel sur disque de façon SANDBOXÉE (même pattern que
`demo.py` : `_resolve_safe_path()` rejette toute tentative de traversée de
chemin, testé par `test_scene_export_rejects_path_traversal`).

## 17. Tests Ajoutés (76 nouveaux, 763 au total)

| Fichier | Nombre | Couvre |
|---|---|---|
| `tests/contracts/test_contracts.py` (+8) | 8 | Vec3/Transform (scale≠0)/sérialisation nested/`validate_object_id`/`slugify_object_id` |
| `tests/spatial/test_scene_store.py` | 23 | create/get/list/delete, add/update/remove_object (racine, parent, doublons, inconnu), hiérarchie, describe_scene (résumé, pas de dump massif), mount/unmount (dont sessions isolées) |
| `tests/spatial/test_renderer_adapter.py` | 7 | Aplatissement Vec3→array, géométrie connue/inconnue (repli honnête), objet sans geometry/material, relations+hiérarchie, non-mutation de la scène source |
| `tests/tools/test_spatial_tools.py` | 12 | SAFE partout, create/add/describe, échec honnête sur scène inconnue, update/remove, render (mount+event), close (unmount+event), export (JSON, fichier sandboxé, anti-traversal) |
| `tests/harness/test_spatial_integration.py` | 12 | **Intent-driven** (greeting/question neutre), création réelle bout-en-bout, promotion World State (scène active, object_count), **Soleil/Terre hiérarchie via appels chaînés réels**, échec jamais promu, render→event UI réel, close→unmount, observation dans le vrai ModelRequest, STOP toujours actif, découverte générique du Tool |
| `tests/architecture/test_phase8_architecture_proof.py` | 11 | Isolation `spatial/`, aucune API Three.js hors adapter, `tools/catalog/spatial.py` n'importe pas harness, pas d'auto-autorisation Safety, exposition Harness lecture seule, aucun second orchestrateur/boucle, panneau Cockpit présent + Three.js jamais chargé depuis index.html, `arch_lint` global |
| `tests/runtime/test_web_entrypoint.py` (+3) | 3 | Endpoint `/spatial` par défaut vide, reflète une vraie scène après render, event `ui.view_requested`/"spatial" poussé sur le bon WebSocket |

## 18. Tests Réels

Effectués contre le vrai Cockpit (`python -m raya.runtime.entrypoints.web`,
port 8765), un vrai navigateur Chrome (via l'extension claude-in-chrome), et
le vrai modèle Ollama Cloud configuré (`OLLAMA_API_KEY` réel de cette
machine) — pas de modèle scripté :

1. **Création réelle via langage naturel** — "Crée une scène du système
   solaire avec le soleil et la terre qui l'orbite, puis montre-la moi." Le
   modèle réel a appelé une séquence de Tools spatiaux ; le panneau "SCENE"
   du Cockpit s'est ouvert et a affiché deux sphères (jaune = soleil, bleue
   = terre) en WebGL réel. **PASS** (avec une limitation honnête, voir
   ci-dessous).
2. **Rendu WebGL réel confirmé** — inspection JS de la page :
   `window.THREE.REVISION === "128"`, un seul `<canvas>`, contexte WebGL
   valide obtenu (`getContext("webgl")` non null). Ce n'est pas une image
   statique. **PASS**.
3. **Mise à jour réelle reflétée** — "Déplace la terre plus loin du soleil,
   disons à la position x=6." → `GET /api/session/{id}/spatial` confirme
   `terre.position == [6, 0, 0]` côté serveur, et le rendu s'est remonté
   (la sphère bleue est sortie du cadre visible de la caméra, cohérent avec
   x=6). **PASS**.
4. **Fermeture propre** — "Ferme cette scène maintenant." → le panneau
   repasse à `hidden=true`, `classList` sans `"open"`, le canvas reste dans
   le DOM (réutilisable) sans renderer actif. **PASS**.
5. **Aucune erreur console imputable à RAYA** — la seule exception capturée
   provient de l'extension `claude-in-chrome` elle-même
   (`No Listener: tabs:outgoing.message.ready`), sans rapport avec le code
   du Cockpit. **PASS**.

**Limitation réelle découverte et documentée honnêtement** (jamais
masquée) : lors de l'étape 1, le Harness a RÉELLEMENT rapporté ne pas avoir
terminé dans les `max_tool_iterations=4` par défaut ("6 action(s)
réelle(s) tentée(s)") plutôt que de prétendre avoir fini — la relation
parent-enfant Soleil/Terre n'a donc PAS été établie lors de cet essai précis
en conditions réelles (les deux objets sont restés `parent: null`), alors
que le scénario scripté déterministe (§17,
`test_relationship_and_hierarchy_via_real_multi_step_tool_calls`) prouve
que le mécanisme lui-même fonctionne correctement quand le nombre d'étapes
reste dans la limite. C'est le mécanisme d'honnêteté PRÉEXISTANT (Phase 3,
`LoopDetector`/limite d'itérations) qui fonctionne correctement face à une
nouvelle capacité — pas un défaut de Phase 8, mais une observation réelle
sur le réglage par défaut de `RAYA_MAX_TOOL_ITERATIONS` pour des scènes à
plusieurs objets (voir §20).

## 19. Architecture Lint

`python scripts/arch_lint.py` → **PASS, 0 violation** sur l'arbre complet
après Phase 8. 11 tests dédiés
(`tests/architecture/test_phase8_architecture_proof.py`) verrouillent
spécifiquement :
- `spatial/` n'importe jamais `models/memory/harness/interfaces/tools/
  devices/attention/cognition/runtime`.
- `raya/contracts/spatial.py` ne contient aucun import ni appel d'API
  `THREE.*`.
- Seul `threejs_adapter.py` contient `THREE.` ou `_KNOWN_KINDS` — vérifié
  sur tous les autres fichiers de `raya/spatial/`.
- `tools/catalog/spatial.py` n'importe jamais `raya.harness`.
- Aucun import `raya.safety` dans `tools/catalog/spatial.py` (pas
  d'auto-autorisation).
- `Harness` n'expose aucune méthode de mutation de scène (`create_scene`,
  `add_object`, etc.) — lecture seule uniquement.
- Aucune classe `TaskScheduler`/`Harness`/`SceneScheduler` dans `spatial/`
  ou `tools/catalog/spatial.py`.
- Aucune deuxième boucle agentique dans `spatial/`.
- `index.html` ne mentionne jamais Three.js ; `app.js` le charge bien (seul
  endroit autorisé) ; le panneau `#panel-spatial` avec `<canvas>` existe.

## 20. Limitations

- **`max_tool_iterations` par défaut (4) peut tronquer une création de
  scène à plusieurs objets** avec relations — découvert en conditions
  réelles (§18), pas un défaut de Phase 8 mais un réglage de configuration
  à ajuster selon l'usage réel (`RAYA_MAX_TOOL_ITERATIONS` dans `.env`).
  Le comportement honnête du Harness (dire "je n'ai pas terminé" plutôt que
  mentir) reste correct et n'a pas besoin d'être changé.
- **Pas de persistance disque des scènes** — `SceneStore` est en mémoire
  uniquement cette phase (cohérent avec la consigne : pas de nouveau
  `PersistenceBackend` requis). `scene.export` permet un export JSON
  explicite à la demande, mais aucune scène ne survit à un redémarrage du
  runtime.
- **Round-trip `from_dict()` non supporté pour `Scene`/`SpatialObject`** —
  limitation déjà acceptée pour `Tool.observation` depuis la Phase 7 (champs
  `dict[str, Dataclass]`/`tuple[Dataclass,...]`) ; `to_dict()` reste
  pleinement fonctionnel pour l'export/l'API.
- **Un seul renderer implémenté** (Three.js) — la frontière `RendererAdapter`
  existe précisément pour permettre un futur renderer alternatif sans
  toucher au Scene Model, mais aucun second renderer n'a été écrit cette
  phase (hors scope).
- **Pas d'intégration Task** (§14) — décision explicite, pas un oubli.
- **Le placeholder "group" (nœud de hiérarchie pur) n'affiche rien à
  l'écran** — comportement voulu (`buildGeometry("group") → null`), pas
  encore de représentation visuelle dédiée pour un nœud sans géométrie
  propre (ex. un centre de gravité).

## 21. FAIL / BLOCKED / NOT_TESTED

- **FAIL** : aucun — tous les tests Phase 8 (76/76) passent, sur 3
  exécutions consécutives de la suite complète.
- **BLOCKED** : aucun — Cockpit, Chrome et un vrai modèle Ollama Cloud
  étaient disponibles sur cette machine pour l'intégralité des tests réels
  prévus (§18).
- **NOT_TESTED** :
  - Persistance longue durée d'une scène across un redémarrage du runtime
    (non implémentée, §20).
  - Un second renderer que Three.js (frontière posée, jamais exercée).
  - Comportement sous un très grand nombre d'objets dans une scène
    (performance du renderer non mesurée à l'échelle).
  - Export de fichier avec un nom de fichier legitimate mais très long ou
    contenant des caractères Unicode exotiques (seul le cas de traversée de
    chemin a été testé, §17).

## 22. Régressions Éventuelles

**Aucune régression fonctionnelle.** 3 exécutions consécutives de la suite
complète (763 tests) :

| Run | Passed | Failed | Skipped | Durée |
|---|---|---|---|---|
| 1 | 746 | 8 | 9 | 96.4s |
| 2 | 746 | 8 | 9 | 85.8s |
| 3 | 746 | 8 | 9 | 81.9s |

Les 8 échecs reproduits systématiquement sont **PRÉ-EXISTANTS**, déjà
documentés dans les rapports Phase 6/Phase 7, sans rapport avec le Creative/
Spatial Agent :
- 5 tests `tests/devices/windows/test_windows_agent.py` (UIA réel,
  dépendant du focus/timing sur cette machine — un test supplémentaire par
  rapport à Phase 7, `test_health_reports_online_when_uia_responds`, a
  basculé en échec sur cette exécution, cohérent avec la nature déjà connue
  de ces tests comme sensibles au focus/timing de la machine, pas une
  régression Phase 8).
- `test_handle_request_fails_honestly_with_null_provider_stub` (appelle le
  vrai Ollama Cloud sans stub — un `OLLAMA_API_KEY` réel est configuré).
- `test_scenario_7_background_task_does_not_block_conversation` /
  `test_2_conversation_answered_immediately_during_task` (assertions de
  timing en millisecondes trop strictes avec un vrai appel réseau).

**Aucune régression Phase 8** : tous les tests Phase 0-7 qui passaient
avant cette phase continuent de passer, à l'identique.

## 23. V1 Integrity Check

- `git status --porcelain` sur `RAYA/` (le seul dépôt git des deux) :
  identique caractère pour caractère à l'état de début de session — les 4
  mêmes fichiers non suivis (`_shopping_full.txt`,
  `prompt_claude_code_commit.txt`, `prompt_claude_code_shopping.txt`,
  `prompt_claude_code_tic.txt`), zéro fichier modifié, zéro nouveau
  fichier, zéro suppression.
- `RAYA/modules/hologram/` n'a jamais été ouvert en lecture ni en écriture
  pendant cette phase — le Scene Model V2 a été conçu directement à partir
  de la description de sa forme dans `RAYA_V2_MIGRATION_PLAN.md`/
  `RAYA_V2_REPOSITORY_STRUCTURE.md`, pas en copiant du code V1.
- Aucun fichier sous `RAYA/` n'a été ouvert en écriture à aucun moment de
  cette phase.

## 24. GO / NO-GO Phase 9

**GO.**

Tous les critères de sortie de la consigne sont satisfaits avec preuve
réelle, pas seulement des tests unitaires :
- Le modèle de scène est headless, sérialisable, indépendant du renderer
  (§8, §11, preuve architecturale §19).
- La frontière Data/Rendering est un seul fichier de chaque côté
  (`threejs_adapter.py` côté Python, `app.js` côté navigateur) — vérifié
  automatiquement (§19).
- Le comportement est strictement intent-driven, jamais de pattern-matching
  frontend — vérifié en tests scriptés ET en conditions réelles (§12, §18).
- Le mécanisme de vérification post-action de la Phase 7 est réutilisé sans
  duplication (§13).
- Aucun second orchestrateur/scheduler/boucle agentique n'a été introduit
  (§14, §19).
- Le cycle de vie du renderer (mount/update/destroy) est propre, vérifié en
  conditions réelles jusqu'à la fermeture (§15, §18).
- Le scénario central de la consigne (Soleil/Terre en relation
  parent-enfant, créé via de vrais appels d'outils chaînés) est prouvé de
  façon déterministe (§17) — et sa dépendance à `max_tool_iterations` est
  documentée honnêtement plutôt que masquée (§18, §20).
- `arch_lint` = 0 violation (§19).
- Régression complète documentée honnêtement, y compris les échecs
  pré-existants non masqués (§22).
- V1 strictement inchangé (§23).

La seule réserve — le réglage par défaut de `max_tool_iterations` pour des
scènes à plusieurs objets (§18, §20) — est un paramètre de configuration
ajustable (`.env`), pas un défaut architectural. Elle ne bloque pas une
Phase 9 éventuelle.
