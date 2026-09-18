# RAYA V2 — Chantier 20C : PC Interaction Target + World State Semantic Consistency
## Rapport Final

**Date :** 2026-09-15  
**Auteur :** Claude (session Chantier 20C)  
**Statut global :** TERMINÉ

---

## §1 — STATUS

**TERMINÉ.**

Les quatre fixes prévus (A, B, C, D) ont été traités :
- Fix A (TERMINÉ) : `pc.last_interaction_target` via ObservationSpec pour `pc.keyboard.type` + `pc.ui.type`
- Fix B (TERMINÉ) : Unification du type de `pc.active_window` → `str` depuis toutes les sources
- Fix C (TERMINÉ) : §18 mis à jour pour distinguer OS focus vs intention d'interaction
- Fix D (NO-GO confirmé) : Exposition source/timestamp dans le prompt — délibérément non implémenté

13/13 nouveaux tests automatisés passent.  
747/750 tests de la suite globale passent (3 failures pré-existantes, non causées par 20C).

---

## §2 — AUDIT INITIAL

### Fichiers inspectés

| Fichier | Rôle | Problème détecté |
|---------|------|-----------------|
| `raya/tools/catalog/pc.py` | Déclarations des Tools PC | `pc.keyboard.type` et `pc.ui.type` avaient `observation=()` — aucun fait World State écrit |
| `raya/devices/windows/agent.py` | Handlers Windows Device | `_keyboard_type` ne capturait pas la fenêtre active ; `_ui_type` n'exposait pas `args["window"]` dans l'evidence |
| `raya/perception/windows_sensors.py` | Capteur léger foreground | Écrivait `value=current` (dict complet) pour `pc.active_window` |
| `raya/context_engine/render.py` | Rendu du prompt système | §18 ne distinguait pas `active_window` (focus OS passif) de la cible réelle d'interaction |
| `raya/contracts/__init__.py` | Contrats/types | Audit de `ObservationSpec`, `WorldStateFact`, `PerceptionObservation` — OK |
| `raya/harness/loop.py` | Boucle principale | `_promote_observations_and_verify` générique — aucun `if tool_name` — OK |

### Constat avant modification

1. **`pc.keyboard.type`** : `observation=()` → zéro fait écrit après une frappe clavier
2. **`pc.ui.type`** : `observation=()` → idem
3. **`_keyboard_type`** (agent.py) : retournait `evidence={"length": N}` — pas de champ `window`
4. **`_ui_type`** (agent.py) : retournait `evidence={"method": M}` — pas de champ `window`
5. **`ActiveWindowSensor`** : écrivait `value=current` (dict) alors que les outils écrivaient `value=str` — incohérence de type pour la même clé `pc.active_window`
6. **§18** (render.py) : citait `WS.active_window` comme signal dominant sans aucune réserve sur son caractère passif

---

## §3 — ROOT CAUSE

### Scénario reproducteur
```
ouvre Calculator → ouvre Notepad → écris "Bonjour" dans Notepad → ferme Notepad → "ferme-le"
→ RAYA ferme Calculator au lieu de Notepad
```

### Chaîne causale confirmée

**P0 (CONFIRMÉ) — Aucun `last_interaction_target` écrit après frappe :**
`pc.keyboard.type` avait `observation=()`. Après `écris "Bonjour"`, le World State ne contenait
aucune trace que RAYA avait interagi avec Notepad. Le modèle n'avait aucun signal d'intention.

**P1 (CONFIRMÉ, documenté, non modifiable) — Perception OS réécrit `active_window` passivement :**
Lorsque Notepad est fermé par l'OS, Windows restitue automatiquement le focus à Calculator.
Dans les ~3 secondes suivantes, `ActiveWindowSensor` publie `active_window = "Calculator"`.
Ce comportement est un fait OS réel — masquer ce signal serait une erreur (il reste utile).

**P2 (CONFIRMÉ) — Incohérence de type `pc.active_window` :**
- Source outil : `evidence["window"] = str` (titre fenêtre)
- Source perception : `value = dict {"title": ..., "process": ...}`
`verify_observation_against_intent` utilisait `str(value).strip().lower()` — fonctionnait par
chance sur les dicts (via `str(dict)`) mais produisait des représentations incohérentes
entre les deux sources pour la même clé World State.

**P3 (CONFIRMÉ) — §18 désignait `active_window` comme signal dominant sans réserve :**
Après P0+P1, le modèle recevait `pc.active_window = "Calculator"` (focus passif, après fermeture
Notepad) comme seul signal, et §18 le traitait comme intention dominante → ferme Calculator.

---

## §4 — CHANGEMENTS EXACTS

### 4.1 `raya/tools/catalog/pc.py`

**Ajout** du tuple `_LAST_INTERACTION_OBSERVATION` :
```python
# Chantier 20C : dernière cible d'interaction PC réelle
_LAST_INTERACTION_OBSERVATION = (
    ObservationSpec(domain="pc", key="last_interaction_target", evidence_field="window",
                     freshness_ttl_s=300),
)
```

**Modification** de `pc.keyboard.type` : `observation=()` → `observation=_LAST_INTERACTION_OBSERVATION`

**Modification** de `pc.ui.type` : `observation=()` → `observation=_LAST_INTERACTION_OBSERVATION`

**Pourquoi ici :** `tools/catalog/` est le seul endroit où les `ObservationSpec` sont déclarées —
invariant architectural : jamais de `if tool_name` dans `harness/loop.py`. La promotion générique
`_promote_observations_and_verify` récupère le champ `evidence["window"]` automatiquement.

### 4.2 `raya/devices/windows/agent.py`

**Modification de `_keyboard_type`** — capture la fenêtre active AVANT de taper :
```python
def _keyboard_type(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    # Capture the foreground window BEFORE typing — this is the interaction target
    active_raw = window_mgmt.get_active_window()
    active_info = active_raw.get("active") if active_raw.get("status") == "ok" else None
    ok = keyboard.type_text(command.arguments["text"])
    if not ok:
        return _fail(command, "TYPE_FAILED", "échec de la saisie clavier", "clipboard_paste")
    evidence: dict = {"length": len(command.arguments["text"])}
    if active_info:
        evidence["window"] = active_info["title"]
    return _ok(command, {"typed": True}, evidence, "clipboard_paste")
```
**Pourquoi ici :** L'evidence doit être produite par le Device Agent (seul à avoir accès à
`window_mgmt` pour connaître la fenêtre OS réelle). Le champ `evidence["window"]` est ensuite
lu par l'ObservationSpec via `evidence_field="window"`.

**Modification de `_ui_type`** — expose `args["window"]` dans l'evidence :
```python
# Avant :
return _ok(command, {"typed": True, "mode": mode}, {"method": r["method"]}, r["method"])

# Après :
return _ok(command, {"typed": True, "mode": mode}, {"method": r["method"], "window": args["window"]}, r["method"])
```
**Pourquoi ici :** `pc.ui.type` reçoit toujours `args["window"]` (requis par le schéma) — c'est
la cible déclarée par le modèle, précise et directe (pas besoin d'interroger l'OS).

### 4.3 `raya/perception/windows_sensors.py`

**Modification de `ActiveWindowSensor.sample()`** — Fix B : passe de dict à str :
```python
# Avant :
observation = PerceptionObservation(
    domain="pc", key="active_window", value=current,  # dict {"title":..., "process":...}
    source=_SOURCE, freshness_ttl_s=_DEFAULT_FRESHNESS_TTL_S,
)

# Après :
observation = PerceptionObservation(
    domain="pc", key="active_window", value=current["title"],  # str "Notepad"
    source=_SOURCE, freshness_ttl_s=_DEFAULT_FRESHNESS_TTL_S,
)
```
**Pourquoi ici :** C'est la seule source qui écrivait encore un dict. Unifier vers str ici est
moins risqué que de modifier tous les consommateurs d'outils (qui produisent déjà des str).

### 4.4 `raya/context_engine/render.py`

**Modification du bloc §18** — ajout de la distinction OS focus vs interaction intent.

Texte clé ajouté (extrait) :
```
"WS.pc.last_interaction_target (the PC window/app RAYA most recently typed into); "
...
"Critical distinction: WS.active_window reflects the current OS foreground focus "
"and can change PASSIVELY without any user intention (e.g. Windows moves focus to "
"Calculator after Notepad is closed). When WS.pc.last_interaction_target is present "
"and differs from WS.active_window, prefer last_interaction_target for resolving "
"'close it'/'use it'/'do something with it' references to the thing RAYA was just "
"working with. Only treat active_window as the dominant referent when no "
"last_interaction_target exists or when the user explicitly names the foreground window;"
```
**Pourquoi ici :** La directive §18 est l'unique point d'instruction sémantique pour le modèle
sur comment résoudre les référents. Mettre ce raisonnement dans le prompt-système (et non dans
le code) préserve l'invariant "le Device Agent ne décide pas, le modèle décide".

---

## §5 — `pc.active_window` : type final et justification

**Type final : `str` (titre de la fenêtre uniquement)**

### Justification

| Critère | str (titre seul) | dict {"title", "process"} |
|---------|-----------------|--------------------------|
| Cohérence multi-source | ✅ Outils + perception unifiés | ❌ Perception divergeait |
| Rendu lisible dans le prompt | ✅ "Bloc-notes" direct | ❌ `str({"title": "...", "process": "..."})` verbeux |
| `verify_observation_against_intent` | ✅ Substring check trivial | ⚠️ Fonctionnait par coïncidence |
| Consommateurs existants | ✅ Zéro break (tous utilisent déjà str) | ❌ 2 tests à corriger |
| Contenu sensible | ✅ Titre de fenêtre, jamais un secret | ✅ Idem |

**Choix du titre seul (pas du process) :** Le modèle raisonne en termes de noms de fenêtres
("Bloc-notes", "Calculator") — pas d'exécutables (`notepad.exe`, `calc.exe`). Le titre est
ce que l'utilisateur voit ; le process n'est utile qu'au Device Layer (déjà accessible via
`window_mgmt`). Le process reste disponible dans `_read_foreground_window()` pour un usage futur
sans que le World State ne le stocke redondamment.

---

## §6 — `pc.last_interaction_target` : sémantique, source, TTL

### Définition sémantique

`pc.last_interaction_target` = **la fenêtre/application dans laquelle RAYA a effectivement
tapé du texte lors de son dernier appel à `pc.keyboard.type` ou `pc.ui.type`**.

Ce fait répond à : "sur quoi est-ce que RAYA était en train de travailler activement ?"
Il est distinct de `pc.active_window` qui répond à : "quelle est la fenêtre au premier plan OS
en ce moment ?" (peut changer passivement).

### Sources et propagation

| Outil | Comment la cible est obtenue | Champ evidence |
|-------|------------------------------|----------------|
| `pc.keyboard.type` | `window_mgmt.get_active_window()` AVANT la frappe | `evidence["window"] = active_info["title"]` |
| `pc.ui.type` | `args["window"]` (déclaré par le modèle, requis) | `evidence["window"] = args["window"]` |

L'ObservationSpec (`evidence_field="window"`) lit ce champ et appelle
`_promote_observations_and_verify` de manière générique — aucun `if tool_name` dans Harness.

### TTL = 300 secondes

**Justification du TTL 300s (vs 20s perception, 60s active_window) :**
- 20s (perception) : TTL minimal pour tolérer les cycles de poll manqués. Trop court pour
  une intention d'interaction humaine.
- 60s (active_window outil) : Assez pour un lancement ; insuffisant si l'utilisateur réfléchit
  30 secondes avant de dire "ferme-le".
- 300s (last_interaction_target) : 5 minutes — assez pour une session de travail sur une app
  unique, sans que des changements de focus passifs (alt-tab involontaire) effacent la cible.

### Preuve que la cible est réellement résolue (jamais inventée)

- `_keyboard_type` : appelle `window_mgmt.get_active_window()` AVANT d'envoyer la frappe —
  la fenêtre lue est celle qui reçoit les touches au moment de l'action réelle.
- `_ui_type` : utilise `args["window"]` (le sélecteur de fenêtre que le modèle a passé,
  vérifié par UIA comme existant avant la frappe — sinon `WINDOW_NOT_FOUND`).
- Si `window_mgmt.get_active_window()` échoue (machine non-Windows, Win32 indisponible) :
  `evidence["window"]` n'est pas ajouté → `ObservationSpec` ne produit rien → aucun fait
  incorrect écrit (dégradation honnête, jamais une invention).

---

## §7 — §18 : nouvelle règle de dominance

### Règle complète implémentée dans `render.py`

**Priorité de résolution des référents :**
1. Environnement observé : URL active/site, dernier contenu tapé, dernier élément cliqué,
   **`WS.pc.last_interaction_target` (app/fenêtre dans laquelle RAYA a tapé, TTL=300s)**
2. Résultats des outils dans la conversation courante
3. Échanges conversationnels récents
4. Tâches de fond actives
5. Mémoire générale

**Distinction critique (ajoutée par Chantier 20C) :**

> `WS.active_window` reflète le focus OS courant et peut changer PASSIVEMENT sans intention
> de l'utilisateur (ex : Windows restitue le focus à Calculator après fermeture de Notepad).
> Quand `WS.pc.last_interaction_target` est présent ET diffère de `WS.active_window`,
> préférer `last_interaction_target` pour résoudre "ferme-le" / "utilise-le" / "fais quelque
> chose avec" comme référence à la chose sur laquelle RAYA était en train de travailler.
> Traiter `active_window` comme référent dominant uniquement quand aucun `last_interaction_target`
> n'existe ou quand l'utilisateur nomme explicitement la fenêtre au premier plan.

### Application au scénario déclencheur

| État World State | Avant 20C | Après 20C |
|-----------------|-----------|-----------|
| `pc.active_window` | "Calculator" (OS focus passif) | "Calculator" (inchangé) |
| `pc.last_interaction_target` | — (inexistant) | "Bloc-notes" (TTL=300s) |
| §18 résout "ferme-le" vers | "Calculator" ❌ | "Bloc-notes" ✅ |

---

## §8 — TESTS

### Nouveaux tests (`tests/tools/test_chantier20c_pc_interaction_target.py`)

| # | Nom | Type | Résultat |
|---|-----|------|---------|
| T4 | `test_last_interaction_target_ttl_300s` | Unit | ✅ PASS |
| T5a | `test_active_window_str_from_tool_observation` | Unit | ✅ PASS |
| T5b | `test_active_window_str_from_perception` | Unit | ✅ PASS |
| T6a | `test_keyboard_type_has_last_interaction_observation` | Unit | ✅ PASS |
| T6b | `test_ui_type_has_last_interaction_observation` | Unit | ✅ PASS |
| T6c | `test_harness_promotes_last_interaction_target_declaratively` | Integration | ✅ PASS |
| T7a | `test_directive_18_mentions_last_interaction_target` | Unit | ✅ PASS |
| T7b | `test_directive_18_warns_active_window_is_os_focus` | Unit | ✅ PASS |
| T8 | `test_rendered_context_exposes_both_signals_distinctly` | Unit | ✅ PASS |
| T11 | `test_browser_last_typed_target_still_declared` | Regression | ✅ PASS |
| T12 | `test_typed_text_not_stored_as_last_interaction_target` | Security | ✅ PASS |
| T1 (real) | `test_real_keyboard_type_writes_last_interaction_target` | E2E Windows | ✅ PASS |
| T5 (real) | `test_real_active_window_str_type_after_launch` | E2E Windows | ✅ PASS |

**Total nouveaux : 13/13 PASS**

### Tests de régression corrigés

| Fichier | Test | Problème (causé par Fix B) | Correction |
|---------|------|---------------------------|-----------|
| `tests/perception/test_windows_sensors.py` | `test_event_published_on_first_real_observation` | `assert event.payload["value"] == {"title": "Notepad", ...}` (dict attendu) | → `assert event.payload["value"] == "Notepad"` (str) |
| `tests/perception/test_windows_sensors.py` | `test_event_published_when_window_changes` | `first.payload["value"]["title"]` (accès dict) | → `first.payload["value"]` (str directe) |
| `tests/integration/test_phase7_scenarios.py` | `test_real_sensor_detects_change_when_notepad_becomes_active` | `event.payload["value"]["process"]` (accès dict) | → `assert isinstance(val, str)` + substring check |

### Suite globale

```
3 failed, 747 passed in 173.18s
```

Les 3 failures sont **pré-existantes** (non causées par Chantier 20C) :
1. `test_handle_request_fails_honestly_with_null_provider_stub` — effectue de vrais appels réseau au modèle
2. `test_recovered_task_can_actually_resume_and_complete` — sensible au timing, défaut intermittent
3. `test_config_root_is_derived_from_file_location_not_hardcoded` — spécifique à la machine (override `data_dir` dans `.env`)

---

## §9 — REAL E2E

### Environnement

- Machine : Windows 11 Pro Education 10.0.26200
- Python : 3.12 (venv activé)
- pywin32 : installé (win32gui, win32process disponibles)
- Notepad : disponible (Bloc-notes Windows localisé en français)

### Scénarios exécutés

#### E2E 1 — `test_real_keyboard_type_writes_last_interaction_target` (PASS)

**Séquence :**
1. `pc.application.launch(target="notepad")` → Notepad lancé
2. `time.sleep(0.5)` → fenêtre active = Notepad
3. `pc.keyboard.type(text="Bonjour")`
4. Vérification `evidence["window"]` contient titre Notepad
5. Promotion manuelle en World State via ObservationSpec
6. `ws.retrieve_fact("pc", "last_interaction_target")` → fact présent, TTL=300s

**Résultat : PASS**  
`evidence["window"]` = "Sans titre - Bloc-notes" (titre localisé français)  
`fact.value` = "Sans titre - Bloc-notes", `fact.source` = "tool:pc.keyboard.type"

#### E2E 2 — `test_real_active_window_str_type_after_launch` (PASS)

**Séquence :**
1. `pc.application.launch(target="notepad")`
2. Vérification `ObservationSpec(key="active_window").evidence_field` → lit str depuis tool
3. `ActiveWindowSensor().sample()` → vérification `event.payload["value"]` est str

**Résultat : PASS**  
Outil : `evidence["window"]` = str ✅  
Perception : `event.payload["value"]` = str ✅  
Cohérence de type confirmée depuis les deux sources.

#### E2E 3 — Scénario bout-en-bout via `test_phase7_scenarios.py` (PASS)

`test_real_notepad_launch_promotes_real_observation_into_world_state` existant :  
- `pc.application.launch(target="notepad")` → `world_state.get_fact("pc", "active_window")`
- `fact.value` = str, `fact.source` = "tool:pc.application.launch" ✅

#### Scénarios non exécutés (environnement partiel)

- **E2E Ollama live** (requiert Ollama en cours d'exécution avec modèle chargé) : non exécuté —
  Ollama non actif dans l'environnement de test. La directive §18 est vérifiée par les tests
  automatisés T7a, T7b, T8 (prompt généré vérifiable sans LLM).
- **E2E multi-app (Calculator → Notepad → "ferme-le" avec vrai LLM)** : nécessite Ollama live —
  BLOCKED environnement, non FAIL implémentation (voir §11 Limitations).

---

## §10 — RÉGRESSIONS

### Avant Chantier 20C (baseline)

```
3 failed, 734 passed
```
(3 failures pré-existantes identiques, comptage différent car certains tests n'existaient pas)

### Après Chantier 20C

```
3 failed, 747 passed
```

**+13 nouveaux tests PASS, 0 nouvelle failure.**

### Détail des impacts Fix B sur les tests existants

Fix B (perception → str) a causé **3 failures de tests existants** immédiatement corrigées :

| Test | Failure type | Fix appliqué |
|------|-------------|-------------|
| `test_event_published_on_first_real_observation` | AssertionError (str ≠ dict) | Assertion mise à jour → str |
| `test_event_published_when_window_changes` | TypeError (str non subscriptable) | Assertion mise à jour → str |
| `test_real_sensor_detects_change_when_notepad_becomes_active` | TypeError (str non subscriptable) | Assertion mise à jour → str |

Ces 3 tests avaient des assertions qui dépendaient du type `dict` — désormais incorrectes après
Fix B. Les corrections sont triviales et reflètent le nouveau comportement attendu.

---

## §11 — LIMITATIONS

### L1 — Validation du scénario déclencheur sans LLM réel

Le bug original ("RAYA ferme Calculator au lieu de Notepad") requiert un LLM réel pour être
reproduit et vérifié end-to-end avec le nouveau comportement. La correction a été validée par :
- T8 (rendu du prompt : les deux signaux sont distincts et correctement étiquetés)
- T7a/T7b (§18 contient la distinction explicite)
- T6c (promotion générique via ObservationSpec fonctionne)
- T1 real (evidence["window"] réel sur vraie machine Windows)

La confiance dans la correction est élevée mais la preuve E2E avec LLM reste théorique pour
l'environnement de test (Ollama non actif).

### L2 — Cas `_keyboard_type` sans fenêtre active

Si `window_mgmt.get_active_window()` retourne un statut d'erreur (ex : Win32 indisponible),
`active_info` est None et `evidence["window"]` n'est pas ajouté. L'ObservationSpec ne produit
alors rien (dégradation honnête). Dans ce cas, aucun `last_interaction_target` n'est écrit —
le modèle se rabat sur `active_window` (comportement pré-20C). C'est le comportement correct :
mieux vaut aucun fait qu'un fait incorrect.

### L3 — `pc.keyboard.press` (non couvert)

`pc.keyboard.press` (combinaisons de touches : Ctrl+S, Enter, etc.) n'écrit pas de
`last_interaction_target` — il n'a pas été modifié dans ce chantier. Justification : une
pression de touches est généralement un modificateur ou une action dans la fenêtre courante,
mais l'action de référence pour "sur quoi RAYA travaillait" reste le dernier `keyboard.type`.
Si un scénario critique nécessite `keyboard.press` comme marqueur d'interaction, c'est un
chantier séparé.

### L4 — TTL statique (300s non configurable)

Le TTL de 300s est hardcodé dans `_LAST_INTERACTION_OBSERVATION`. Pour une session de
travail très longue (> 5 minutes entre l'interaction et la commande de fermeture), le fait
peut expirer. Configurer ce TTL serait une amélioration future (non demandée par 20C).

### L5 — Localisation Windows (français vs anglais)

`evidence["window"]` contient le titre localisé ("Sans titre - Bloc-notes", "Calculatrice").
`verify_observation_against_intent` fait une vérification substring case-insensitive — cela
fonctionne pour les appariements directs, mais un modèle anglophone qui pense "Notepad" ne
trouvera pas "Bloc-notes" par substring. Limitation documentée aussi dans les scénarios Phase 7.
Ce n'est pas un bug de Chantier 20C — la localisation Windows est un problème orthogonal.

---

## §12 — V1 INTEGRITY

**V1 STRICTEMENT INTACTE.**

Aucun fichier sous `raya_v1/` ou dans tout autre composant V1 n'a été touché dans ce chantier.

Fichiers modifiés (V2 uniquement) :
- `raya/tools/catalog/pc.py` ✅ V2
- `raya/devices/windows/agent.py` ✅ V2
- `raya/perception/windows_sensors.py` ✅ V2
- `raya/context_engine/render.py` ✅ V2
- `tests/tools/test_chantier20c_pc_interaction_target.py` ✅ nouveau fichier
- `tests/perception/test_windows_sensors.py` ✅ V2 tests
- `tests/integration/test_phase7_scenarios.py` ✅ V2 tests

Contraintes de sécurité de la session respectées :
- Aucun achat / checkout / message envoyé à des personnes réelles
- Aucune suppression de données
- Aucun compte modifié
- Aucun contournement de CAPTCHA / 2FA
- Aucun mot de passe exposé
- Browser : SAFE uniquement (navigation/lecture)
- PC : actions réversibles uniquement (lancer/fermer Notepad, taper du texte)

---

*Rapport généré à la fin de la session Chantier 20C — 2026-09-15*
