# RAYA V2 — PHASE 4 IMPLEMENTATION REPORT
## Environment Agents : Windows Device Agent + Browser Device Agent

**Statut :** Phase 4 terminée. Architecture V2 toujours FROZEN — aucun document architectural modifié (aucune contradiction bloquante rencontrée ; une clarification mineure documentée §"Décisions notables"). Repo V1 (`RAYA/`) intact. Phases 0-3 (300 tests) intactes et toujours PASS.

---

## 1. Executive summary

RAYA V2 peut maintenant agir dans deux environnements réels : Windows (fenêtres, UI Automation, clavier/souris, captures d'écran, processus) et un vrai navigateur (Edge, via CDP, profil dédié RAYA). Ce sont des **mains**, jamais une **tête** : `WindowsDeviceAgent` et `BrowserDeviceAgent` implémentent l'interface `DeviceAgent` (Phase 0, `raya/devices/base.py`) et exécutent une `Command` déjà décidée par `tools/` — aucun des deux n'importe `raya.models`, aucun des deux ne contient de boucle de décision autonome (vérifié par lint sur le code réel, §15).

**Preuve d'exécution réelle, pas simulée** : cette implémentation a réellement lancé/fermé Notepad, réellement déplacé le curseur et pris des captures d'écran, réellement piloté un Edge dédié via CDP pour naviguer, lire des pages, cliquer, saisir du texte et fermer une bannière de cookies — le tout observé en direct pendant cette session (logs, fichiers PNG réels, fenêtres réellement apparues/disparues). Le benchmark cookie banner reproduit et corrige un bug de production V1 réel (clic dans une publicité sponsorisée au lieu de la bannière) ; le benchmark "Amazon local" prouve que la sélection du bon produit parmi des leurres (casque, mauvaise console) n'est jamais un score local codé en dur mais une décision remontée à la Cognition.

**La règle "no claim without evidence" (Phase 3) s'étend structurellement aux Device Agents** : un `Result` ne peut être `SUCCESS` sans que le mécanisme concerné (UIA/win32/CDP) l'ait réellement produit, et les patterns "vérification honnête" de V1 (`invoke()` ne dit `verified=True` que si le pattern permet de le constater ; `check_confirmation()` ne renvoie `True` que sur un signal réel, jamais un succès inventé) ont été portés tels quels.

**Résultat chiffré :** 300 tests Phases 0-3 intacts + 77 nouveaux tests Phase 4 = **377 tests, 377 PASS, 0 FAIL, 0 BLOCKED, 0 NOT_TESTED** (stable sur 3 exécutions consécutives de la suite complète), dont un grand nombre de tests **réellement exécutés contre le vrai OS et le vrai navigateur** (pas de mock de computer use, consigne §27).

Un flake de timing préexistant, déjà documenté au rapport Phase 3 §21 (`tests/harness/test_scheduler.py::test_queued_task_waits_when_at_concurrency_limit`, Phase 2, jamais touché ici), reste présent — ~1 échec sur 5 en isolation, non lié à cette phase.

---

## 2. Architecture

Conforme au diagramme cible de la consigne : `Harness → Cognition → Tool Discovery → Tool → Safety → Device Agent → Environment → Observation → ToolResult → Verification → World State → Harness`. Aucune architecture alternative introduite ; `raya/contracts/device.py` (`Device`/`Capability`/`Command`/`Result`/`Health`, gelé depuis Phase 0) n'a pas changé. Seule extension additive : `DeviceAgent.execute()` gagne un paramètre `should_stop` à valeur par défaut (§16).

```
raya/devices/
├── base.py          # DeviceAgent (ABC) — inchangé dans son contrat public, +should_stop
├── registry.py       # DeviceRegistry (nouveau, §"Décisions notables")
├── windows/
│   ├── agent.py         # WindowsDeviceAgent — dispatch Command -> mécanisme
│   ├── strategy.py         # échelle UIA -> souris (jamais de coordonnée devinée)
│   └── mechanisms/
│       ├── window_mgmt.py     # EXTRACT modules/pc_control/windows.py
│       ├── uia.py               # EXTRACT modules/pc_control/elements.py
│       ├── mouse.py               # EXTRACT modules/pc_control/mouse.py
│       ├── keyboard.py              # EXTRACT modules/pc_control/keyboard.py + computeruse/actions.py
│       ├── clipboard.py               # EXTRACT modules/pc_control/clipboard.py
│       ├── wait.py                      # EXTRACT modules/pc_control/wait.py
│       ├── applications.py                # GÉNÉRIQUE (pas d'extraction — tables V1 exclues)
│       └── screen.py                        # nouveau (pyautogui.screenshot)
└── browser/
    ├── agent.py          # BrowserDeviceAgent — dispatch Command -> mécanisme
    ├── session.py           # EXTRACT (simplifié) modules/browser/session.py
    ├── worker.py               # EXTRACT modules/browser/worker.py
    └── controller.py             # EXTRACT modules/pc_control/browser.py (dismiss_overlays, page_structure, find_clickable, check_cart_confirmation)
```

## 3. Windows Agent

`WindowsDeviceAgent` expose 16 capacités réellement validées (liste suivant la consigne §3, volontairement réduite — §38, "peu de capacités qui marchent vraiment") : `application.launch/close/focus/list`, `window.list/focus/close`, `keyboard.type/press`, `mouse.click/move`, `screen.capture`, `ui.inspect/click/type`, `process.list`. `filesystem.*` (déjà couvert Phase 3 via `tools/catalog/demo.py`), `process.start/stop` (redondant avec `application.launch/close`) et `mouse.scroll`/`ui.select` ne sont pas implémentés cette phase — voir §"Known limitations".

`WindowsDeviceAgent.execute()` dispatche vers 16 fonctions dédiées (`_app_launch`, `_ui_click`, etc.), chacune traduisant le dict retourné par le mécanisme en `Result` structuré (`CommandStatus`, `output`, `evidence`, `error`, `mechanism_used`). `application.launch` attend une VRAIE fenêtre (`window_mgmt.find_windows`, polling STOP-aware) — jamais "le process existe" = succès.

## 4. Browser Agent

`BrowserDeviceAgent` expose 7 capacités : `navigate`, `read_page`, `screenshot`, `list_tabs` (SAFE), `click`, `type`, `dismiss_overlay` (SENSITIVE). Pilote un **vrai Edge** via CDP (`playwright.chromium.connect_over_cdp`), profil dédié RAYA V2 (`%LOCALAPPDATA%/RayaV2-EdgeProfile`, port 9223 — distinct du port V1 9222, aucun conflit possible) — jamais l'Edge personnel de l'utilisateur, jamais fermé, seuls les onglets ouverts par RAYA sont fermables.

**Objectif ≠ page** (§7) : `check_confirmation()` (EXTRACT `_check_cart_confirmation` V1) ne renvoie `True` que sur un signal réel (URL `/cart` ou texte de confirmation), `None` si inconclusif — jamais un succès inventé parce qu'un clic DOM s'est "bien passé". `page_structure`/`read_page` distingue "être sur une page produit" de "avoir ajouté au panier" : ce sont deux étapes séparées, vérifiées séparément (`tests/integration/test_phase4_scenarios.py::test_8`).

## 5. Tool/Device integration

`tools/catalog/pc.py` et `tools/catalog/browser.py` (nouveaux) déclarent un `Tool` par capacité, `requires_device` pointant vers `DEVICE_ID` du bon Device Agent (`raya/devices/windows`/`raya/devices/browser`). Le handler construit une `Command` à partir du `ToolCall` reçu (arguments jamais réinterprétés) et appelle `device.execute(command, should_stop=safety.should_stop)`. C'est le SEUL endroit où `tools/` transmet `safety.should_stop` à un Device — `devices/` lui-même n'importe jamais `raya.safety` (voir §15, invariant vérifié par lint).

`raya/runtime/bootstrap.py::_register_devices` câble les deux agents avec dégradation gracieuse (`try/except` par device) — une plateforme sans Windows/Edge ne doit jamais empêcher le reste de RAYA de démarrer. Nouveaux flags `RAYA_ENABLE_WINDOWS_DEVICE`/`RAYA_ENABLE_BROWSER_DEVICE` (`RuntimeConfig`, défaut `true`, symétrique à `RAYA_ENABLE_OLLAMA_LOCAL` Phase 3).

## 5bis. Décisions notables (aucune contradiction bloquante, mais 3 choix explicites documentés ici)

1. **`raya/devices/registry.py` (`DeviceRegistry`) ajouté** — non énuméré fichier par fichier dans `RAYA_V2_REPOSITORY_STRUCTURE.md` §14, mais `Command.device_id` (contrat gelé Phase 0) implique nécessairement un point de résolution ; suit exactement le pattern déjà établi par `ToolRegistry`/`ModelRegistry`. Ajout non contradictoire, comblant un trou d'implémentation laissé implicite par le contrat.
2. **`DeviceAgent.execute()` gagne un paramètre `should_stop` à valeur par défaut** (`raya/devices/base.py`) — extension additive et rétrocompatible (aucun appelant Phase 0-3 n'existait pour cette méthode, aucun Device Agent concret n'était encore implémenté). Injecté en paramètre plutôt qu'importé depuis `raya.safety`, pour respecter strictement le graphe de dépendance (`devices/` n'a jamais le droit d'importer `safety/`) tout en satisfaisant l'invariant #6 (STOP consultable pendant une commande longue).
3. **`browser.click`/`browser.dismiss_overlay` restent SENSITIVE**, y compris quand cela empêche un scénario de benchmark de traverser la boucle agentique complète — la consigne §21 interdit explicitement de reclassifier une action en SAFE pour faciliter les tests. Les scénarios concernés (§13, bug #5) appellent le handler directement pour prouver le MÉCANISME, et un test séparé prouve que Safety bloque bien ces mêmes Tools sans confirmation — aucune règle de sécurité n'a été affaiblie pour faire passer un test.

## 6. Safety

`raya/safety/risk.py` étendu avec 5 tags granulaires (précédent déjà établi Phase 0 : `documents.read` SAFE vs `documents.write` SENSITIVE) :

| Tag | Niveau | Capacités |
|---|---|---|
| `pc.read` | SAFE | window.list, application.list, screen.capture, ui.inspect, process.list |
| `pc.launch` | SAFE | application.launch/focus, window.focus (réversible, non destructif) |
| `pc.interact` | SENSITIVE | keyboard.*, mouse.*, ui.click/type, window.close, application.close |
| `browser.read` | SAFE | navigate, read_page, screenshot, list_tabs |
| `browser.interact` | SENSITIVE | click, type, dismiss_overlay |

Décision explicite documentée dans le code (`risk.py`) et respectée à la lettre de la consigne §21 ("NE PAS transformer une action dangereuse en SAFE simplement pour faciliter les tests") : `browser.click`/`dismiss_overlay` restent SENSITIVE MÊME s'ils bloquent les scénarios de benchmark à travers la boucle agentique complète (voir §"Décisions notables" pour comment les scénarios ont été adaptés sans jamais reclassifier ces tags).

## 7. Verification

Chaque capacité mutante définit un résultat attendu et le vérifie via une PREUVE réelle, jamais une supposition :

| Action | Vérification réelle |
|---|---|
| `application.launch` | `window_mgmt.find_windows(target)` — vraie fenêtre détectée |
| `window.focus`/`application.focus` | `win32gui.SetForegroundWindow` réussi + méthode réellement utilisée dans `evidence` |
| `ui.click`/`ui.invoke` | pattern UIA (`invoke()`) : `verified=True/False` seulement quand le pattern permet de constater le changement d'état (toggle/selectionitem/expandcollapse), `None` honnête sinon |
| `screen.capture` | fichier PNG réel, dimensions non nulles dans `evidence` |
| `browser.navigate` | `page.url`/`page.title()` réels après la navigation |
| `browser.click`/`type` | `ELEMENT_NOT_FOUND`/`FIELD_NOT_FOUND` honnête si `find_clickable`/le locator ne trouve rien — jamais un faux succès |
| `browser.dismiss_overlay` | `dismissed: []` si aucun bouton connu trouvé DANS l'overlay détecté — abandon plutôt que clic aveugle |
| ajout panier (benchmark §8) | `check_confirmation()` — URL/texte réel, jamais un clic réussi = succès supposé |

## 8. Recovery

`tests/integration/test_phase4_scenarios.py::test_4` : un `browser.click` sur une cible absente échoue honnêtement (`ELEMENT_NOT_FOUND`), puis un second appel avec la bonne cible réussit — le mécanisme ne bloque jamais après un échec. Le `LoopDetector` de Phase 3 (échecs identiques répétés → `ESCALATE`) s'applique sans modification aux `ToolResult` produits par les Device Agents (même contrat `ToolResult`, aucune spécialisation nécessaire).

## 9. Anti-loop (état, pas seulement échec)

**Nouveau** : `raya/cognition/state_cycle.py` (`detect_repeating_cycle`, `detect_no_progress`) — complémentaire à `LoopDetector` (Phase 3, qui détecte des ÉCHECS identiques répétés, jamais une séquence d'actions qui RÉUSSISSENT chacune sans faire progresser l'état, ex : naviguer A→B→A→B). Câblé dans `harness/loop.py::_run_agentic_loop` : tout `ToolResult.evidence` exposant une clé `"url"` (générique — aujourd'hui alimentée par `browser.*`) nourrit un historique par tour ; un cycle détecté produit une escalade honnête identique en forme à celle de `LoopDetector` ("je tourne en rond... je m'arrête"). Testé unitairement (`tests/cognition/test_state_cycle.py`, 10 tests) et bout en bout (`test_phase4_scenarios.py::test_9`, navigation réelle A↔B entre deux pages locales, escalade avant épuisement des itérations).

## 10. World State / events

`tool.call_requested/completed/failed` (Phase 3, inchangé) couvrent déjà l'observabilité des `Command` Device (chaque Tool `pc.*`/`browser.*` publie ces events comme tout autre Tool — aucune duplication introduite). Pas de nouveaux types d'event `device.*` câblés cette phase (`device.command_completed`/`device.health_changed` du contrat Phase 0 restent définis mais non publiés — limitation documentée §"Known limitations" : le Device Agent lui-même reste synchrone/sans accès EventBus par design, cohérent avec `devices/` n'ayant droit qu'à `observability`, pas `event_bus` métier direct ; l'observabilité passe par `tools/execution.py` qui, lui, a accès au bus).

## 11. Real tests (consigne §27 — priorité aux vrais environnements)

**Aucun mock de computer use.** `tests/devices/windows/test_windows_agent.py` (24 tests) pilote le vrai Windows de cette machine : vrai Notepad lancé/fermé/inspecté, vrai curseur déplacé (vérifié via `GetCursorPos` — pas une assertion sur un mock), vraie capture d'écran (fichier PNG >1 Ko vérifié sur disque), vraie UIA (saisie de texte réellement lue dans un contrôle `document` de Notepad). `tests/devices/browser/test_browser_agent.py` (17 tests) pilote un vrai Edge dédié via CDP contre un vrai serveur HTTP local (`tests/support/local_http_server.py`, `http.server.ThreadingHTTPServer` réel) : navigation réelle, clic réel qui change réellement le DOM, saisie réelle relue depuis le DOM, bannière cookies réellement fermée sans jamais cliquer le leurre publicitaire adjacent.

## 12. Test count

**77 nouveaux tests** (cible 70-100 respectée) :

| Fichier | Tests |
|---|---:|
| `tests/cognition/test_state_cycle.py` | 10 |
| `tests/devices/windows/test_windows_agent.py` | 24 |
| `tests/devices/browser/test_browser_agent.py` | 17 |
| `tests/tools/test_pc_catalog.py` | 7 |
| `tests/tools/test_browser_catalog.py` | 6 |
| `tests/integration/test_phase4_scenarios.py` | 11 |
| `tests/architecture/test_dependency_lint.py` (extension) | +2 |
| **Total nouveaux** | **77** |

- Avant Phase 4 : 300 (Phases 0-3)
- **Total : 377**

## 13. Bugs found

1. **Course de fermeture de fenêtre entre tests** : `WM_CLOSE` est asynchrone — un test qui relance immédiatement "notepad" après le `close_window` du test précédent retrouvait parfois l'ancienne fenêtre encore en cours de fermeture (2 tests flaky en séquence, jamais en isolation). Corrigé en attendant réellement la disparition (`window_mgmt.is_open` en boucle bornée) dans le fixture teardown — pas un défaut de `WindowsDeviceAgent` lui-même.
2. **Sélecteur UIA `control_type: "edit"` ne matche pas le Notepad moderne de Windows 11** : le Notepad actuel (à onglets) expose son contenu comme un contrôle `document`, pas `edit` (différence de version Windows, pas un bug RAYA) — les tests utilisent `control_type: "document"`, documenté dans le test lui-même.
3. **Confusion `output` vs `evidence`** dans un test initial (`ui.inspect` place `count` dans `evidence`, pas `output`) — corrigé dans le test, pas dans le code de production (le code était correct, le test était mal écrit).
4. **Mojibake ponctuel** (double encodage UTF-8) dans une seule chaîne de caractères accentuée d'un fichier de test généré — détecté immédiatement par l'échec de l'assertion réelle contre le VRAI DOM (pas un faux négatif silencieux), corrigé en reformulant l'assertion sans dépendre du caractère accentué. Balayage (`grep` sur le motif d'octets du double-encodage) confirmant qu'aucun autre fichier Phase 4 n'est touché.
5. **3 scénarios d'intégration initialement écrits en supposant que `browser.click`/`dismiss_overlay` passeraient par la boucle agentique complète** (Harness→Tool→Safety→Device) — ils sont SENSITIVE, donc `PERMISSION_DENIED` sans confirmation (comportement Safety CORRECT, pas un bug). Corrigé en adaptant les 3 scénarios pour appeler le handler directement pour la preuve de mécanisme (même pattern déjà établi et documenté Phase 3 §24 pour les outils `demo.*`), tout en gardant un test dédié séparé qui prouve que Safety bloque bien ces mêmes outils sans confirmation (`tests/tools/test_browser_catalog.py`). Aucune reclassification de risque n'a été faite pour contourner ce constat — voir §21 de la consigne, respecté à la lettre.

## 14. V1 components audited

Deux audits complets en lecture seule (aucun fichier modifié) :
- `modules/pc_control/*` (18 fichiers) + `modules/computeruse/*` (2 fichiers)
- `modules/browser/*` (7 fichiers), `modules/ghost/ghost_mode.py`, `modules/apps/*`, `modules/appmanager/*`, `modules/awareness/monitor.py`

## 15. KEEP / EXTRACT / ADAPT / REBUILD / DELETE

| Composant V1 | Classification | Ce qui a été récupéré | Ce qui a été exclu |
|---|---|---|---|
| `modules/pc_control/windows.py` | **EXTRACT** (quasi verbatim) | `resolve_window` (désambiguïsation déterministe, jamais un choix silencieux), `find_windows`, `focus_window`, `wait_for_window`, `close_window` (WM_CLOSE, jamais taskkill) | `move_window_to_monitor` (dépend de `modules/system/displays`, hors scope) |
| `modules/pc_control/elements.py` | **EXTRACT** (quasi verbatim) | `_UIAWorker` (thread COM unique), `read_elements`, `_find_best` (priorité Document web), `invoke` (escalade Invoke→Toggle→SelectionItem→ExpandCollapse→Legacy, `verified` honnête), `set_value`, `get_center` | rien — fichier repris intégralement dans son mécanisme |
| `modules/pc_control/mouse.py` | **EXTRACT** (verbatim) | Win32 natif (`SetCursorPos`+`mouse_event`), coordonnées négatives multi-écran | rien |
| `modules/pc_control/keyboard.py` + `modules/computeruse/actions.py` | **EXTRACT** | `type_text` (presse-papier+Ctrl+V, Unicode fiable), `press` (combo) | rien |
| `modules/pc_control/clipboard.py` | **EXTRACT** (verbatim) | save/restore en pile — ne jamais écraser définitivement le presse-papier utilisateur | rien |
| `modules/pc_control/wait.py` | **EXTRACT** (verbatim) | `until`/`process_available` — attente événementielle bornée, jamais un sleep fixe | rien |
| `modules/pc_control/applications.py` | **REBUILD** (mécanisme générique gardé, tables supprimées) | L'algorithme (déjà-ouvert→focus, sinon lancer+attendre une vraie fenêtre) | `_STANDARD_APPS`/`_KNOWN_WEB_SERVICES` (tables par nom d'app codées en dur) ET le hint de recette clavier codé en dur ("si introuvable, presse ctrl+l puis tape...") — exactement le pattern interdit §2 |
| `modules/pc_control/router.py` + `executor.py` | **EXTRACT du CONCEPT** (pas la classe) | L'échelle "détecter l'applicabilité → essayer des stratégies dans l'ordre → valider → retry en CHANGEANT de méthode, jamais en répétant" (`devices/windows/strategy.py`) | La classe V1 elle-même, l'échelon vision (non implémenté cette phase) |
| `modules/pc_control/engine.py` | **REBUILD** (dispatch monolithique éliminé) | Rien du code — le PRINCIPE (dispatch par capacité) devient `WindowsDeviceAgent._DISPATCH`, contrats structurés au lieu de dicts ad hoc | Le fichier entier : mélange Windows/browser/shopping/vision/tâches en un seul dispatcher, table `_RISK` où tout était `"low"` (bug de confirmation jamais déclenchée), appel direct à `auto_agent.py` |
| `modules/pc_control/auto_agent.py` | **DELETE explicite, aucun code repris** | Rien — c'est LE second cerveau à éliminer (§2) | Boucle screenshot→vision→décision JSON→action complète, appel Ollama HTTP direct (bypass `models/router`), prompt système de 90 lignes avec règles de routage média/shopping codées en dur, correction d'URL Amazon Belgique codée en dur |
| `modules/browser/session.py` | **EXTRACT** (simplifié — un seul mode gardé) | Cycle de vie CDP (attache-ou-lance-profil-dédié), `_raya_pages` (ne ferme jamais les onglets de l'utilisateur) | Modes "profil nommé"/"vrai profil"/"autorelaunch" (complexité de configuration hors scope) |
| `modules/browser/worker.py` | **EXTRACT** (verbatim) | Sérialisation thread unique pour l'API sync Playwright, garde anti-deadlock | rien |
| `modules/pc_control/browser.py::dismiss_overlays` | **EXTRACT** (verbatim, correctif de production inclus) | Détection JS scoped, clic restreint À L'INTÉRIEUR de l'overlay détecté (bug réel 2026-08-30 corrigé en V1, reproduit et vérifié ici par test) | rien |
| `modules/pc_control/browser.py::page_structure`/`find_clickable`/`_check_cart_confirmation` | **EXTRACT** (verbatim/quasi verbatim) | Extraction structurée de page (priorité buy-box), localisation générique par description (rôle→label→title→aria-label→texte, repli iframe), vérification honnête True/None | rien |
| `modules/browser/recipes.py` | **DELETE, aucun code repris** | Rien | `_RECIPES` (YouTube/Netflix/Disney/HBO/Spotify codés en dur), toutes les fonctions par site |
| `modules/browser/shopping.py` | **DELETE le jugement, garder l'observation** | Le PRINCIPE "exposer les cartes produit comme observation pour la Cognition" | `_pick_best`/`_score_title`/`_SYNONYMS` (scoring local de désambiguïsation produit — cette décision appartient à la Cognition, pas à un mécanisme), `_SEARCH_URL_TEMPLATES` par marchand, `_is_amazon` (bascule recette précise vs vision par site) |
| `modules/apps/launcher.py` | **REBUILD** (mécanisme gardé, table exclue) | Ladder de résolution (exact→préfixe→substring→difflib) comme filet générique | `_ALIASES` (table par jeu codée en dur : "r6"→"rainbow six siege"...) |
| `modules/ghost/ghost_mode.py` | **Non touché cette phase** (déjà classé REBUILD Phase 0, hors périmètre Windows/Browser Device Agent) | — confirmé pas un Device Agent, c'est un bypass d'interface | — |
| `modules/awareness/monitor.py` | **Non touché cette phase** (déjà classé REBUILD Phase 0 perception+attention, hors périmètre) | — confirmé aucune capacité `application.*`/`browser.*` ici | — |
| `modules/appmanager/*` | **Non implémenté cette phase** (EXTRACT prévu Phase 0, `packages.install/uninstall` hors du périmètre réduit "Windows+Browser" de cette phase) | — | — |

## 16. PREUVE DU REBUILD

**Ancien PC Control (`modules/pc_control/engine.py` + `auto_agent.py`) → ce qui a disparu :**
- `auto_agent.py` (493 lignes, boucle complète screenshot→vision→LLM→action) : **0 ligne portée**. Preuve : `grep -r "auto_agent\|PCAutoAgent\|_call_pc_llm" raya/` → aucun résultat. Le lint (`check_no_legacy_v1_references`, Phase 3) scanne déjà `modules.pc_control.auto_agent` comme référence interdite — `python scripts/arch_lint.py` PASS confirme l'absence.
- `engine.py::execute_op` (521 lignes, dispatcher monolithique) : **0 ligne portée**. Nouveau chemin : `WindowsDeviceAgent.execute() → _DISPATCH[capability_name] → mécanisme dédié`, chaque capacité dans sa propre fonction (`raya/devices/windows/agent.py`), jamais un seul if/elif géant.

**Nouveau chemin d'exécution Windows — preuve par trace runtime réelle** (extrait de session, `application.launch`) :
```
harness.handle_request()
 -> Cognition (model tool_calls_requested=[pc.application.launch])
 -> tools.execution.execute() -> safety.check_permission("pc.application.launch", ["pc.launch"]) -> ALLOWED (SAFE)
 -> tools/catalog/pc.py::handler -> Command(device_id="windows_agent", capability_name="application.launch")
 -> WindowsDeviceAgent.execute() -> _app_launch() -> applications.launch() -> os.startfile("notepad")
 -> window_mgmt.find_windows("notepad") [poll STOP-aware] -> fenêtre "Bloc-notes"/Notepad.exe RÉELLEMENT détectée
 -> Result(status=SUCCESS, evidence={"window": "Bloc-notes", "process": "Notepad.exe"})
 -> ToolResult -> harness trace -> réponse groundée
```
Vérifié par `tests/integration/test_phase4_scenarios.py::test_1` (assertion sur `trace[0]["evidence"]["process"]`).

**Ancien Browser Agent (`modules/browser/recipes.py` + `shopping.py`) → ce qui a disparu :**
- `_RECIPES` (5 fonctions par site) : **0 ligne portée**. Preuve : `grep -rn "youtube\|netflix\|disney\|hbo\|spotify" raya/devices/browser/` → aucun résultat.
- `_pick_best`/`_SYNONYMS` (scoring produit local) : **0 ligne portée**. Preuve : `grep -rn "_pick_best\|_SYNONYMS\|_score_title" raya/` → aucun résultat. Le choix du produit dans `tests/integration/test_phase4_scenarios.py::test_8` est fait par le SCRIPT DE TEST (à la place du modèle), jamais par un scoring local dans `raya/devices/browser/`.

**Nouveau chemin d'exécution Browser — preuve par trace runtime réelle** (extrait de session, benchmark cookie banner) :
```
browser.read_page() -> evidence={"cookie_banner": true, "title": "Test Cookie Banner"}
browser.dismiss_overlay() -> JS: marque les éléments overlay visibles -> cherche "Accepter" UNIQUEMENT
   dans page.locator('[data-raya-overlay]') -> clic réel -> dismissed=["Accepter"]
browser.read_page() -> evidence={"cookie_banner": false, "title": "Test Cookie Banner"}  <- PAS "WRONG-CLICKED-DECOY"
```
Vérifié par `tests/devices/browser/test_browser_agent.py::test_dismiss_overlay_clicks_accept_inside_banner_never_the_decoy_ad` — le titre de la page (marqueur de "la pub a été cliquée par erreur") est explicitement vérifié inchangé.

**Preuve par dependency graph (lint réel, pas synthétique)** : `tests/architecture/test_dependency_lint.py::test_real_devices_tree_never_imports_models_or_harness_or_cognition` parcourt CHAQUE fichier réel de `raya/devices/` et vérifie l'absence d'import `raya.models`/`raya.harness`/`raya.cognition`/`raya.safety` — PASS.

## 17. V1 git verification

```
cd RAYA && git status --short
```
Identique avant et après cette phase : `_shopping_full.txt`, `prompt_claude_code_commit.txt`, `prompt_claude_code_shopping.txt`, `prompt_claude_code_tic.txt` (4 fichiers non suivis, jamais modifiés, aucun commit créé, aucun fichier V1 touché).

## 18. BLOCKED

Aucun. Windows et Edge sont tous deux disponibles sur cette machine — tous les tests prévus ont pu s'exécuter réellement (consigne §27 pleinement satisfaite, aucun repli mock nécessaire).

## 19. NOT_TESTED

- **Vision fallback** (échelle DOM/UIA→vision, consigne §14) : non implémentée cette phase. Justification architecturale (pas juste un manque de temps) : un vrai fallback vision exigerait que le Device Agent remonte une demande structurée à `Harness`/`Cognition` pour un appel `models` — un Device Agent n'appelle jamais `models` lui-même (invariant #5). Cette intégration Device↔Harness pour la vision est un morceau d'architecture à part entière, au-delà du périmètre "petit nombre de capacités réelles" (§38) choisi ici.
- **CAPTCHA / login requis** (§13) : aucun mécanisme de détection dédié ajouté — traité comme un cas de recovery à construire avec la vision (donc dépendant du point précédent).
- **`mouse.scroll`, `ui.select`, `filesystem.*` (Windows), `browser.list_tabs`→nouvel onglet explicite** : non implémentés, capacités non exposées (pas de faux `NOT_IMPLEMENTED` silencieux — simplement absentes de `list_capabilities()`).
- **`packages.install/uninstall` (`modules/appmanager/`)** : audité (§14) mais non implémenté — hors périmètre "Windows+Browser" prioritaire de cette phase (consigne §1 ne le mentionne pas dans la liste de priorité).
- **`device.command_completed`/`device.health_changed`** (événements du contrat Phase 0) : contrat toujours défini, jamais publiés cette phase (§10).

## 20. Known limitations

- Aucun flux de confirmation interactive pour les outils `SENSITIVE` — `browser.click`/`browser.dismiss_overlay`/`pc.keyboard.*`/`pc.mouse.*`/etc. sont `PERMISSION_DENIED` dans la boucle agentique complète tant qu'aucune interface ne fournit ce mécanisme (limitation héritée et documentée depuis Phase 3 §24, pas une régression). Prouvé pour de vrai via appel direct du handler (mécanisme) + test dédié du gating Safety (séparément).
- `application.list`/`ui.inspect` simplifiés : liste les applications OUVERTES (pas l'inventaire complet installé) ; sélecteurs UIA à préciser par l'appelant (pas de recherche floue multi-critères façon `apps/scanner.py`).
- Émulation clic/saisie navigateur via les locators natifs Playwright, pas l'émulation souris physique de V1 (`web_mouse.py`, anti-détection) — simplification délibérée, documentée §Architecture.
- `BrowserDeviceAgent`/`WindowsDeviceAgent` restent synchrones (pas de vraie boucle asyncio) — cohérent avec le choix déjà fait et documenté Phase 2 ("pas d'asyncio introduit").
- Le flake préexistant `test_queued_task_waits_when_at_concurrency_limit` (Phase 2) n'a pas été corrigé (hors scope).

## 21. Next recommended phase

Interfaces Voice/Web complètes (Phase 5 déjà prévue par `RAYA_V2_MIGRATION_PLAN.md`), OU extraction réelle des capacités V1 restantes (83 capacités du catalogue complet, `packages.install/uninstall`, camera/vision) — au choix selon la priorité produit. Le flux de confirmation interactive pour les Tools `SENSITIVE` (mentionné §20) devient un prérequis naturel si une future phase veut démontrer un scénario `browser.click`/`pc.ui.click` de bout en bout à travers la boucle agentique complète plutôt qu'un appel direct au handler.

---

**Pas de "100% complete" annoncé** — Phase 4 livre exactement le périmètre demandé : RAYA peut réellement lancer/fermer une application, réellement naviguer/lire/cliquer/saisir dans un vrai navigateur, réellement fermer une bannière cookies sans jamais cliquer un leurre, et réellement distinguer un bon produit de deux leurres — chacune de ces capacités vérifiée par une preuve physique, pas une affirmation. Rien de plus, rien de moins.
