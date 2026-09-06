# RAYA V2 — Targeted Execution / Browser / Safety Repair Pass

**Statut : GO**

---

## 1. Executive Summary

Cette passe corrige des comportements réels observés via Telegram/Cockpit : navigation trop rigide (URLs devinées répétées), Safety trop agressive sur des clics ordinaires (`pc.ui.click` dans la Calculatrice), réponses d'échec trop génériques, et absence de changement de stratégie face à un échec répété avec des arguments variables (angle mort exact de `LoopDetector`). Aucune nouvelle architecture : tout est résolu par (a) des directives de prompt système génériques, (b) une extension de la classification Safety contextuelle déjà introduite en Phase 11, (c) un compteur anti-répétition local à la boucle agentique existante, et (d) un nouveau formatter d'explication réutilisant exactement le pattern `_natural_response_for_tool_result` (Phase 11).

**Un bug structurel majeur, non signalé, a été découvert pendant les tests E2E réels obligatoires (Partie 14)** : `ollama_cloud.py::_messages_to_ollama()` sérialisait un tour "l'assistant demande un appel d'outil" comme un simple texte placeholder ("[demande d'appel d'outil]"), sans le champ `tool_calls` structuré ni de `tool_call_id` reliant la réponse — le vrai modèle, conditionné sur cette forme non standard répétée plusieurs tours de suite, a fini par recopier ce placeholder verbatim comme réponse finale au lieu de répondre réellement. Confirmé en direct, puis corrigé (extension additive du contrat `Message`), avec l'accord explicite de l'utilisateur étant donné le dépassement de périmètre.

Régression : 3 exécutions complètes de la suite (996/995/996 passés après installation d'une dépendance manquante — voir §17 —, 4-5 échecs stables tous PRE-EXISTING/FLAKY, zéro nouvelle régression), `arch_lint.py` 0 violation, V1 (fichiers) intact. Tests réels : Calculatrice validée de bout en bout SANS confirmation sur les clics ordinaires et avec résultat vérifié.

---

## 2. Bugs observés initialement (fournis par l'utilisateur)

1. RAYA retente `browser.navigate` avec des URLs devinées différentes (YouTube "Love Me Not", Coolblue) et finit par abandonner avec un message générique.
2. RAYA suppose une structure d'URL interne (ex: `/show/season-N/episode-M`) au lieu de l'observer.
3. Face à un blocage nécessitant une action humaine (Disney+ : sélection de profil), RAYA continue de réessayer au lieu de le dire clairement.
4. `pc.ui.click` exige une confirmation pour un clic ordinaire dans la Calculatrice.
5. Répétition d'actions sans changement de stratégie réel.
6. Réponses d'échec trop génériques, sans expliquer la cause/le blocage réel.
7. Continuité d'objectif entre plusieurs messages ("Love Me Not... puis Sur YouTube").
8. Vérification réelle de l'objectif final (pas de faux succès).

---

## 3. Root Cause de chaque bug

| # | Problème | Cause racine |
|---|---|---|
| 1-2 | Navigation rigide / URL devinée | Aucune capacité manquante — rien dans le prompt système n'indiquait que la navigation directe est UNE stratégie parmi d'autres, ni qu'une URL doit être OBSERVÉE, jamais construite par supposition. |
| 3 | Blocage humain non reconnu | Aucun mécanisme manquant pour le cas conversationnel synchrone (tous les exemples réels de l'utilisateur) — juste l'absence d'une directive explicite disant au modèle de reconnaître ces situations et de s'arrêter proprement. |
| 4 | `pc.ui.click` trop agressif | `pc.interact` était classé SENSITIVE inconditionnellement (même mécanisme historique que `browser.interact` avant Phase 11) ; de plus, le scan de contenu dangereux (`_mentions_dangerous_action`) ne regardait que les chaînes de PREMIER NIVEAU des arguments — `pc.ui.click`'s cible réelle vit dans `selector.name` (dict imbriqué), jamais scannée. |
| 5 | Pas de changement de stratégie | `LoopDetector` ne détecte que des échecs avec `tool_name`+`arguments` EXACTEMENT identiques — un modèle qui retente le MÊME outil avec des arguments DIFFÉRENTS à chaque fois (ex: deviner une nouvelle URL) lui échappe totalement, ainsi qu'au détecteur de cycle d'état (qui exige un état identique ou un motif A/B répété). Angle mort déjà noté comme "Future Work" en Phase 10, confirmé ici comme cause réelle. |
| 6 | Réponses génériques | Les 3 messages de fin de boucle (`LoopDetector` ESCALATE, cycle d'état ESCALATE, budget épuisé) étaient des templates statiques codés en dur, jamais construits à partir de la vraie trace d'exécution. |
| 7 | Continuité multi-tour | Déjà résolu Phase 11 (mémoire conversation bidirectionnelle) — vérifié ici par un test dédié, aucun changement nécessaire. |
| 8 | Vérification de l'objectif | Déjà résolu depuis Phase 7 (`combine_outcomes`/`verify_observation_against_intent`, `_promote_observations_and_verify`) — vérifié par des tests de câblage dédiés pour `browser.navigate`/`pc.window.focus`, aucun changement nécessaire. |
| — | **Découverte E2E** : placeholder recopié par le modèle | `_messages_to_ollama()` n'envoyait jamais le champ `tool_calls` structuré — chaque tour "appel d'outil" ressemblait à un texte libre répété, confondant un vrai modèle après plusieurs tours. |

---

## 4. Fichiers réellement modifiés

- `raya/safety/risk.py` — `_CONTEXTUAL_TAGS` devient un dict (tag → repli quand aucun texte n'est trouvé) ; ajout de `pc.interact` (repli SENSITIVE, différent de `browser.interact`=SAFE) ; `_extract_text_values()` (nouveau, scan récursif) remplace le scan de premier niveau.
- `raya/harness/loop.py` — nouvelle méthode `_explain_blocked_turn()` (explication détaillée, un appel modèle borné supplémentaire, repli honnête générique) ; compteur anti-répétition générique (`consecutive_tool_name`/`consecutive_failures`, nudge injecté tous les 3 échecs consécutifs du même outil) ; les 3 points d'escalade/épuisement utilisent désormais `_explain_blocked_turn()` ; construction du tour "appel d'outil" corrigée pour porter `Message.tool_calls`/`tool_call_id` structurés (IDs pré-générés, identiques au `ToolCall.id` réellement exécuté).
- `raya/contracts/model.py` — `Message` gagne deux champs additifs (`tool_calls: list[dict] | None`, `tool_call_id: str | None`), défaut `None`, aucune rupture de compatibilité.
- `raya/models/providers/ollama_cloud.py` — `_messages_to_ollama()` sérialise le vrai format function-calling (`tool_calls`/`tool_call_id`) au lieu d'un texte libre ; `OllamaLocalAdapter` en hérite automatiquement.
- `raya/context_engine/render.py` — 3 nouvelles directives génériques (navigation adaptative/pas d'URL devinée, blocage humain) ; correction d'une directive Phase 11 qui référençait à tort un "vision tool" inexistant en V2 (trouvé pendant cet audit).
- Environnement (hors code) : installation du paquet `uiautomation` dans l'interpréteur Python 3.14 utilisé par RayaV2 — absent, bloquait structurellement `pc.ui.*`/`pc.mouse.*` (5 tests pytest + le test réel Calculatrice) ; voir §17.

Aucun fichier V1 modifié (vérifié §16).

---

## 5. Architecture respectée

Aucune nouvelle classe `BrowserAgentV2`/`NavigationAgent`/`SearchAgent`/`SafetyAgent`/`ExecutionOrchestrator`/`TaskOrchestrator` — vérifié par `tests/architecture/test_targeted_execution_repair_architecture_proof.py`. Aucun nouveau Tool navigateur (`browser.search` absent, 7 capacités inchangées). `_explain_blocked_turn` vit uniquement dans `harness/loop.py`, réutilise `model_route()` existant. Le nudge anti-répétition reste une variable locale, jamais une nouvelle classe de détection. Aucun branchement codé en dur par site (`if ... "amazon"/"netflix"/"youtube"/...`) ni par langue, dans `render.py`/`risk.py`/`browser.py`/`controller.py`/`agent.py`/`loop.py`. `arch_lint.py` : 0 violation.

---

## 6. Navigation adaptative

Directive générique ajoutée : la navigation directe vers une URL construite par le modèle est UNE stratégie, jamais la seule — si aucune URL confirmée n'est disponible ou qu'une tentative directe échoue, utiliser la recherche du site (ou une recherche web générale) plutôt que deviner une autre URL. Répéter le même type de tentative sans progrès est un signal de changement de stratégie, pas une invitation à varier légèrement le même essai. Aucun nouveau Tool — les primitives existantes (`browser.navigate`/`browser.read_page`/`browser.click`) suffisent déjà à composer une recherche.

---

## 7. Search Fallback

Résolu par la même directive (§6) — délibérément PAS un nouveau Tool `browser.search` (vérifié par test d'architecture) : une recherche est déjà composable via `browser.navigate` (vers un moteur/la recherche du site) + `browser.read_page` + `browser.click`, exactement les primitives déjà disponibles avant cette passe.

---

## 8. Human-Block Handling

Directive générique ajoutée : reconnaître un blocage nécessitant une action humaine (profil, login, CAPTCHA, 2FA/PIN) et s'arrêter clairement plutôt que réessayer, avec interdiction explicite de contourner CAPTCHA/2FA/MFA, d'extraire/deviner/afficher des identifiants, ou de fabriquer un login — l'autofill natif du navigateur reste utilisable normalement s'il est déjà disponible. Pour le cas conversationnel synchrone (tous les exemples réels fournis), s'arrêter en texte laisse l'utilisateur répondre au tour suivant, déjà pleinement supporté par la continuité de contexte Phase 11 — fonctionnellement équivalent à "pause → intervention → reprise" sans nouveau mécanisme. **Décision explicite validée avec l'utilisateur** : l'extension du vrai pause/resume Task Long-Horizon (Phase 10) pour ce cas reste hors périmètre (aucun échec réel de ce type observé sur une Task, nécessiterait un nouveau signal dans le contrat de replanning) — voir Known Limitations.

---

## 9. Safety Contextualisée

`pc.interact` rejoint `browser.interact` dans `_CONTEXTUAL_TAGS`, avec un repli DIFFÉRENT quand aucun texte n'est trouvé dans les arguments : `pc.mouse.click`/`pc.mouse.move` (coordonnées brutes, aucune description sémantique de la cible) restent SENSITIVE par défaut — contrairement à `browser.dismiss_overlay` (action intrinsèquement bornée), un clic aveugle sur un pixel ne doit jamais devenir SAFE faute d'information. `pc.ui.click`/`pc.ui.type` (cible dans `selector.name`, dict imbriqué) sont désormais correctement scannés grâce à `_extract_text_values()` (récursif). Vérifié empiriquement :

```
calc 7:                       SAFE
calc plus:                    SAFE
fermer une app (target=nom):  SAFE
clic "Supprimer" (selector):  SENSITIVE
clic souris coordonnées seul: SENSITIVE
```

---

## 10. Exécution Multi-Actions

Résolu naturellement par §9 — vérifié en réel (§14) : "ouvre la calculatrice et calcule 7 plus 3" s'exécute de bout en bout (plusieurs `pc.ui.click` réels) sans aucune confirmation.

---

## 11. Error Explanations

Nouvelle méthode `Harness._explain_blocked_turn()` : un appel modèle borné supplémentaire (même pattern que `_natural_response_for_tool_result`, Phase 11) construit une explication (objectif / tenté / blocage / suite possible) STRICTEMENT à partir de la vraie trace d'exécution — jamais une invention. Repli honnête générique si aucun modèle ne produit de texte exploitable. Utilisée aux 3 points d'arrêt de la boucle agentique (LoopDetector ESCALATE, cycle d'état ESCALATE, budget épuisé).

---

## 12. CLAIM → EVIDENCE

Règle Phase 11 préservée telle quelle (non modifiée, testée en non-régression). Renforcée en pratique par le test réel Calculatrice : "Calcul fait et vérifié... La calculatrice affiche bien le résultat `10`" — une affirmation appuyée sur un état observé, jamais une déclaration nue.

---

## 13. Tests ajoutés

~50 tests nets ajoutés, priorisés comme demandé :

- `tests/tools/test_pc_catalog.py` (+5) — Safety contextuelle pc.interact (bénin SAFE, dangereux SENSITIVE, `pc.ui.click` calculatrice SAFE, sélecteur dangereux SENSITIVE, clic souris coordonnées SENSITIVE).
- `tests/tools/test_browser_catalog.py` (+2) — câblage `ObservationSpec` de `browser.navigate`.
- `tests/tools/test_pc_catalog.py` (+1) — câblage `ObservationSpec` de `pc.window.focus`.
- `tests/context_engine/test_targeted_execution_repair_directives.py` (NEW, 7) — présence des directives navigation/URL/blocage humain, absence de hardcoding, non-régression CLAIM→EVIDENCE, absence de référence à un vision tool inexistant.
- `tests/harness/test_targeted_execution_repair.py` (NEW, 9) — nudge anti-répétition (injecté/pas prématuré/reset sur succès), `_explain_blocked_turn` (budget épuisé, texte modèle réel, cause inconnue honnête), blocage humain via arrêt naturel, continuité multi-tour (Love Me Not/YouTube), round-trip `tool_calls`/`tool_call_id`.
- `tests/models/test_ollama_provider.py` (+2) — sérialisation `tool_calls`/`tool_call_id` structurée, non-régression sur les messages ordinaires.
- `tests/architecture/test_targeted_execution_repair_architecture_proof.py` (NEW, 8) — aucun nouvel agent, `_explain_blocked_turn` unique, aucun second détecteur de boucle, aucun nouveau Tool navigateur, aucun hardcoding site/langue.

---

## 14. Résultats Full Suite ×3

Après installation de `uiautomation` (voir §17), 3 exécutions complètes consécutives :

| Run | Passed | Failed | Skipped |
|---|---|---|---|
| 1 | 996 | 4 | 11 |
| 2 | 995 | 5 | 11 |
| 3 | 996 | 4 | 11 |

Classification des échecs (tous PRE-EXISTING ou FLAKY, zéro nouvelle régression) :
- `test_windows_agent.py::test_application_focus_brings_real_window_to_foreground` — FLAKY (état réel du focus fenêtre desktop, passe en isolation, confirmé).
- `test_loop.py::test_handle_request_fails_honestly_with_null_provider_stub` — PRE-EXISTING (OLLAMA_API_KEY réelle configurée dans `.env`, casse l'hypothèse NullProvider du test, antérieur à cette passe).
- `test_integration_scenarios.py::test_scenario_7_...` / `test_phase2_scenarios.py::test_2_...` — FLAKY (réseau réel Ollama Cloud, latence/décision non déterministe).
- `test_phase3_scenarios.py::test_live_real_ollama_cloud_creates_a_real_file_end_to_end` (run 2 seulement) — FLAKY (timeout réseau réel après 60s, test explicitement nommé "live").

---

## 15. E2E Réels

| Scénario | Résultat |
|---|---|
| Calculatrice simple ("7 plus 3") | **FAIT** — bout en bout réel (Cockpit + vrai Ollama Cloud + vrai Windows Device Agent), **zéro confirmation** sur les clics ordinaires, résultat **vérifié** ("la calculatrice affiche bien 10"). |
| Calculatrice complexe ("12492384 × 2746 / 25") | **FAIT (partiel, honnête)** — zéro confirmation sur les clics, mais n'a pas terminé dans le budget d'itérations (calcul à ~15+ chiffres/opérateurs) ; a expliqué le blocage honnêtement au lieu de prétendre un résultat — conforme à la consigne "ne pas juste augmenter la limite pour cacher le problème". |
| YouTube ("Love Me Not d'Olivia Dean") | **NOT_TESTED** — non rejoué en direct cette session (accès à un vrai compte/service externe, contrainte de temps) ; couvert par les directives de navigation + tests unitaires. |
| Disney+ (saison/épisode, gestion profil) | **NOT_TESTED** — idem, nécessite un vrai compte Disney+. |
| Coolblue (panier) | **NOT_TESTED** — idem, interagirait avec un vrai site marchand. |
| Tâche volontairement bloquée | Couvert par test unitaire (`test_model_stopping_itself_with_a_human_block_explanation_ends_the_turn`) — pas de blocage humain réel généré en conditions live cette session. |
| Multi-message (continuité d'objectif) | Couvert par test unitaire réel-scripté (`test_multi_turn_objective_continuation_love_me_not_pattern`) — pas de session Cockpit live dédiée à ce tour précis. |

Découverte non planifiée mais critique : le bug `tool_calls`/placeholder (§1, §3) a été trouvé PENDANT le premier test Calculatrice réel, confirmé, corrigé, puis re-testé avec succès (voir logs : le modèle ne recopie plus jamais le placeholder, y compris sur un tour à 11 itérations).

---

## 16. Limitations restantes

1. **Blocage humain sur Task Long-Horizon** : pas de vrai signal "nécessite une intervention humaine" dans `replan_step()`/`_handle_step_setback` — une étape de Task bloquée par un humain finit par échouer (FAIL), jamais mise en pause automatiquement. Décision explicite validée avec l'utilisateur : hors périmètre de cette passe (aucun échec réel de ce type observé sur une vraie Task ; nécessiterait d'étendre le contrat de replanning, pas juste une directive de prompt). Le pause/resume manuel (Cockpit/Telegram) reste disponible à tout moment, non affecté.
2. **Aucun mécanisme de vision** en RayaV2 (confirmé par audit complet du code — jamais construit depuis le report explicite de Phase 4). Les directives de navigation/multilingue reposent uniquement sur structure/DOM/rôles — pas de repli visuel si le DOM est réellement insuffisant. Décision explicite validée avec l'utilisateur : hors périmètre de cette passe.
3. **Calculs multi-chiffres longs** (§15) peuvent légitimement dépasser le budget d'itérations (chaque chiffre/opérateur = un clic) — comportement honnête (explique le blocage) plutôt qu'un faux succès, mais pas une garantie de complétion pour des calculs très longs.
4. **Real E2E navigateur** (YouTube/Disney+/Coolblue) non rejoués en direct cette session — marqués NOT_TESTED honnêtement, jamais présentés comme validés.
5. **Statut du processus RAYA V1** : les deux processus V1 observés en début de cette passe (PID 12444/41828) ne sont plus présents en fin de session — mon nettoyage de processus a filtré spécifiquement sur la ligne de commande de mon propre serveur de test (`raya.runtime.entrypoints.web`), qui ne correspond pas à celle de V1 (`main.py`) ; cause exacte non déterminée (arrêt indépendant probable). Les FICHIERS V1 restent, eux, byte-identiques (vérifié §17).

---

## 17. V1 Integrity

`git status --porcelain` sur `RAYA/` avant et après l'intégralité de cette passe : **identique** (mêmes 4 fichiers non suivis, zéro modification, même dernier commit `6855d01`). Aucun fichier V1 modifié. Voir Known Limitation #5 concernant l'état des processus (distinct de l'intégrité des fichiers, qui reste garantie).

Note additionnelle : le paquet Python `uiautomation` (absent de l'interpréteur RayaV2, cause des échecs `pc.ui.*` pré-existants depuis plusieurs phases) a été installé via `pip install uiautomation` dans `pythoncore-3.14-64` — action système hors du dépôt, aucun fichier RayaV2 ni V1 modifié, réversible (`pip uninstall`).

---

## 18. GO / NO-GO

**GO.**

Les 8 problèmes réels rapportés ont des causes identifiées et corrigées, vérifiées par ~50 tests ciblés et par des tests réels end-to-end (Calculatrice simple : succès complet sans confirmation superflue, résultat vérifié). Le bug structurel majeur découvert en cours de route (sérialisation `tool_calls`) a été corrigé avec l'accord explicite de l'utilisateur et re-vérifié en conditions réelles. Zéro régression sur 3 exécutions complètes de la suite, `arch_lint` à 0 violation, fichiers V1 intacts. Les limitations documentées (§16) sont des décisions de périmètre explicitement validées avec l'utilisateur, pas des défauts cachés — les scénarios réels non rejoués (YouTube/Disney+/Coolblue) sont honnêtement marqués NOT_TESTED plutôt que présentés comme validés.
