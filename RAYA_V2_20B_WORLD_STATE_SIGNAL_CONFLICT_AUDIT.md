# RAYA V2 — Chantier 20B : World State Signal Conflict Audit

**Date :** 2026-09-15  
**Mode :** AUDIT ONLY — aucune modification de code  
**Trigger :** Scénario reproduit : ouvre Calculator → ouvre Notepad → écris "Bonjour" dans Notepad → ferme Notepad → RAYA ferme Calculator

---

## §1 — Résumé Exécutif

Le scénario de déclenchement révèle une confusion systémique sur la sémantique des signaux dans le World State. Lorsque l'utilisateur demande à RAYA de "fermer" après plusieurs interactions, le modèle ne sait pas **quel objet fermer** parce que :

1. `pc.active_window` est **écrasé par la perception** (poll 3s, TTL=20s) indépendamment des actions de l'utilisateur
2. Les outils PC de frappe (`pc.keyboard.type`, `pc.ui.type`) **n'enregistrent aucune observation** (`last_typed_target` inexistant pour PC)
3. La valeur de `active_window` change de **type** selon la source (outil→ `str`, perception→ `dict`)
4. Le modèle **ne voit jamais** la source ni l'horodatage — il reçoit un snapshot sans provenance

**Verdict :** L'architecture World State actuelle traite `active_window` comme un état OS (il l'est), mais la directive §18 l'utilise comme signal d'intention utilisateur (ce qu'il n'est pas). Cette confusion sémantique est la cause première du bug.

**Sévérité :** CRITIQUE — l'utilisateur voit RAYA exécuter une action destructrice sur le mauvais objet.

---

## §2 — Environnement Réel

| Composant | Valeur |
|-----------|--------|
| OS | Windows 11 Pro Education 10.0.26200 |
| Shell | PowerShell 5.1 |
| Python | projet `C:\Users\ruben\Desktop\Raya` |
| DB | SQLite via `raya/persistence/sqlite_backend.py` |
| Perception | `ActiveWindowSensor` — poll 3s, TTL=20s |
| Modèle Live | Ollama Cloud (clé disponible, longueur 90) |
| Applis testées | Calculator (`calc.exe`), Notepad (`notepad.exe`) |

---

## §3 — Scénarios Analysés

### Scénario A — Séquence complète de déclenchement (analyse code)

```
Tour 1 : pc.application.launch("calculator")
  → ObservationSpec: pc.active_window = "Calculatrice" (str, TTL=60s, source=tool)
  → Perception (3s): pc.active_window = {"title": "Calculatrice", "process": "calc.exe"} (dict, TTL=20s)

Tour 2 : pc.application.launch("notepad")
  → ObservationSpec: pc.active_window = "Sans titre - Bloc-notes" (str, TTL=60s, source=tool)
  → Perception (3s): pc.active_window = {"title": "Sans titre - Bloc-notes", "process": "notepad.exe"} (dict, TTL=20s)

Tour 3 : pc.keyboard.type("Bonjour") dans Notepad
  → observation=() — AUCUNE mise à jour World State
  → Perception (3s): pc.active_window = {"title": "Sans titre - Bloc-notes*", "process": "notepad.exe"}

Tour 4 : pc.window.close("notepad")
  → L'OS déplace le focus sur Calculator (ou Bureau)
  → Perception (3s): pc.active_window = {"title": "Calculatrice", "process": "calc.exe"} (dict, TTL=20s)
  → Le World State revient sur Calculator

Tour 5 : User: "ferme-le"
  → Le modèle consulte World State: pc.active_window = {'title': 'Calculatrice', ...}
  → §18 dit : active_window = "signal clairement dominant"
  → Modèle appelle pc.window.close("calculator") ← BUG
```

### Scénario B — Confirmation de l'écrasement de type (confirmé par code)

Le même clé `pc.active_window` reçoit des types différents selon la source :

```python
# Outil pc.application.launch (raya/devices/windows/agent.py)
evidence = {"window": "Sans titre - Bloc-notes", "process": "notepad.exe"}
# ObservationSpec: evidence_field="window" → value = "Sans titre - Bloc-notes" (str)

# Perception (raya/perception/windows_sensors.py)
current = {"title": "Sans titre - Bloc-notes", "process": "notepad.exe"}
observation = PerceptionObservation(domain="pc", key="active_window", value=current)
# → value = {"title": ..., "process": ...} (dict)
```

Le modèle reçoit alternativement :
- `pc.active_window = 'Sans titre - Bloc-notes'` (après outil)
- `pc.active_window = {'title': 'Sans titre - Bloc-notes', 'process': 'notepad.exe'}` (après perception)

### Scénario C — Absence de last_typed_target PC (confirmé par code)

```python
# raya/tools/catalog/pc.py
("pc.keyboard.type", ..., PermissionLevel.SENSITIVE, "pc.interact", False, ()),  # observation=()
("pc.ui.type",       ..., PermissionLevel.SENSITIVE, "pc.interact", False, ()),  # observation=()

# vs browser.type
_LAST_TYPED_OBSERVATION = (
    ObservationSpec(domain="browser", key="last_typed_text",   ...),
    ObservationSpec(domain="browser", key="last_typed_target", ...),
)
```

Après `pc.keyboard.type("Bonjour")`, aucune trace de la cible dans le World State.

### Scénario D — Race Condition Perception (confirmé par code)

```
t=0    : pc.application.launch("notepad") → active_window="Sans titre - Bloc-notes" (TTL=60s)
t=3s   : Perception poll → active_window={"title":"Sans titre - Bloc-notes",...} (TTL=20s)
t=6s   : Perception poll → (même valeur, rafraîchie)
...
t=pc.window.close("notepad") : OS rend le focus à Calculator
t+3s   : Perception → active_window={"title":"Calculatrice","process":"calc.exe"} (TTL=20s)
         Le "dernier contexte significatif" est effacé avant que le modèle puisse l'utiliser
```

---

## §4 — Inventaire des Signaux World State

### Signaux écrits pour le domaine `pc`

| Signal | Source | Type valeur | TTL | Observations |
|--------|--------|------------|-----|--------------|
| `pc.active_window` | `tool:pc.application.launch` | `str` (titre fenêtre) | 60s | Via ObservationSpec, evidence_field="window" |
| `pc.active_window` | `tool:pc.window.focus` | `str` (titre fenêtre) | 60s | Même ObservationSpec |
| `pc.active_window` | `tool:pc.application.focus` | `str` (titre fenêtre) | 60s | Même ObservationSpec |
| `pc.active_window` | `perception:foreground_window` | `dict` {title, process} | 20s | Écrase le précédent |
| *(manquant)* | `tool:pc.keyboard.type` | — | — | Non enregistré |
| *(manquant)* | `tool:pc.ui.type` | — | — | Non enregistré |

### Signaux écrits pour le domaine `browser`

| Signal | Source | Type valeur | TTL |
|--------|--------|------------|-----|
| `browser.current_url` | `tool:browser.navigate` | `str` | 300s |
| `browser.last_typed_text` | `tool:browser.type` | `str` | 300s |
| `browser.last_typed_target` | `tool:browser.type` | `str` | 300s |
| `browser.last_clicked` | `tool:browser.click` | `str` | 300s |

**Asymétrie critique :** Browser enregistre `last_typed_target`; PC ne le fait pas.

---

## §5 — Analyse Temporelle

### Causalité et Ordonnancement

Le World State ne stocke pas les relations causales. `apply_update()` est un upsert pur :

```python
def apply_update(self, fact: WorldStateFact) -> WorldStateFact:
    with self._lock:
        self._backend.save(_COLLECTION, _row_id(fact.domain, fact.key), to_dict(fact))
    self._publish(fact, action="updated")
    return fact
```

**Conséquence :** La dernière écriture gagne, sans notion de "qui a la priorité sémantique".

### Fenêtre de Vulnérabilité

```
Outil écrit (TTL=60s)
  ↓
≤3s : Perception écrase (TTL=20s)
  ↓
20s : Fait perception expiré (STALE)
  ↓
60s : Fait outil original également expiré

→ Pendant les 20-60s post-close, AUCUN signal actif ne représente
  "l'intention de l'utilisateur au moment de la frappe"
```

### Timestamps dans le Rendu Contexte

```python
# raya/context_engine/render.py — SectionKind.WORLD_STATE
lines.append(f"Observed environment state [{freshness}]: {c.get('domain')}.{c.get('key')} = {c.get('value')!r}")
```

Le modèle voit :
```
Observed environment state [active]: pc.active_window = {'title': 'Calculatrice', 'process': 'calc.exe'}
```

Le modèle **ne voit jamais** :
- La source (`tool:pc.application.launch` vs `perception:foreground_window`)
- L'horodatage exact
- Le TTL restant
- L'historique des valeurs précédentes

---

## §6 — Conflits de Signaux Identifiés

### Conflit 1 — Type Inconsistency (CONFIRMÉ)

| Source | Valeur dans WS |
|--------|---------------|
| Outil `pc.application.launch` | `'Sans titre - Bloc-notes'` (str) |
| Perception `foreground_window` | `{'title': 'Sans titre - Bloc-notes', 'process': 'notepad.exe'}` (dict) |

Les deux écrivent `pc.active_window`. Quand le modèle lit `pc.active_window`, il peut recevoir soit un `str` soit un `dict` selon l'ordre des événements — comportement non-déterministe.

### Conflit 2 — Sémantique EVENT vs STATE (CONFIRMÉ)

`active_window` est un **état OS** (qui a le focus système à cet instant). Mais §18 le traite comme une **intention utilisateur** ("cible de l'interaction"). Ces deux notions divergent dès qu'une action change le focus (fermeture de fenêtre, pop-up système, clic accidentel).

### Conflit 3 — TTL Asymétrique (CONFIRMÉ)

- Outil : TTL=60s → "ce que l'utilisateur a ouvert reste actif 1 minute"
- Perception : TTL=20s → "le focus OS se périme en 20s"

La perception peut réécrire un signal outil en 3s, puis expirer en 20s, laissant le WS avec un fait outil périmé à 60s — le modèle pense que "l'utilisateur travaille dans Notepad" alors que Notepad est fermé depuis 25s.

### Conflit 4 — Absence last_typed_target PC (CONFIRMÉ)

Quand l'utilisateur dit "ferme-le" après avoir tapé dans une app, le modèle ne sait pas "ce que le modèle a tapé EN DERNIER dans quelle cible PC" — contrairement au browser où `last_typed_target` persiste 300s.

---

## §7 — Résultats E2E Réels

**Note :** Les scénarios E2E réels avec le vrai Ollama (clé disponible) n'ont pas été exécutés dans cette passe d'audit pour éviter des actions destructrices involontaires (fermetures d'applications sur la machine de développement). Les findings ci-dessus sont tous confirmés par analyse statique du code source et exécution des scripts de diagnostic (sans interaction OS).

**Preuve code-based suffisante :**

```python
# Confirmation type inconsistency (script de diagnostic exécuté)
# Tool fact value type: str → 'Sans titre - Bloc-notes' (TTL=60s)
# Perception fact value type: dict → {'title': 'Calculatrice', 'process': 'calc.exe'} (TTL=20s)
# Same key: pc.active_window

# Confirmation rendering (script de diagnostic exécuté)
# Model sees (tool): Observed environment state [active]: pc.active_window = 'Sans titre - Bloc-notes'
# Model sees (perc): Observed environment state [active]: pc.active_window = {'title': 'Calculatrice', ...}
# Source NOT rendered. Timestamp NOT rendered.
```

---

## §8 — Contexte Reçu par le Modèle

### Format de rendu actuel (WORLD_STATE section)

```
Observed environment state [active]: pc.active_window = {'title': 'Calculatrice', 'process': 'calc.exe'}
Observed environment state [active]: browser.current_url = 'https://example.com'
```

### Ce que le modèle NE voit PAS

```
# Absent du rendu :
source = "perception:foreground_window"   # ← qui a écrit ça ?
timestamp = "2026-09-15T14:32:05Z"       # ← il y a combien de temps ?
freshness_ttl_s = 20                      # ← combien de temps reste-t-il valide ?
previous_value = 'Sans titre - Bloc-notes' # ← qu'est-ce qu'il y avait avant ?
```

### Directive §18 reçue par le modèle (extrait de raya/context_engine/render.py)

```
WS.pc.active_window → clearly dominant signal for PC interactions
```

Cette directive est correcte pour l'état OS actuel, mais elle ne distingue pas :
- "signal dominant pour savoir où taper **maintenant**"
- "signal dominant pour savoir sur quoi **agir ensuite**"

Ces deux cas divergent précisément dans le scénario de déclenchement (fermeture = action sur le Notepad qu'on vient de fermer).

---

## §9 — Décisions Modèle Analysées

### Reconstruction du raisonnement modèle (scénario de déclenchement)

```
Tour 5 — User: "ferme-le"
  World State reçu:
    pc.active_window = {'title': 'Calculatrice', 'process': 'calc.exe'} [active]
  
  Directive §18: active_window = signal clairement dominant
  
  Référent "le" → ambigu (Notepad fermé ou Calculator ouvert?)
  Règle §18 → active_window est dominant → Calculator est actif
  Décision → fermer Calculator ← INCORRECT
```

### Pourquoi le modèle ne peut pas faire mieux

1. `pc.last_typed_target` n'existe pas → le modèle ne sait pas "j'ai tapé dans Notepad"
2. La source de `active_window` n'est pas visible → le modèle ne peut pas distinguer "l'utilisateur a ouvert Calculator" de "Calculator a repris le focus après fermeture de Notepad"
3. §18 dit que `active_window` est dominant → le modèle l'applique correctement selon les règles données, mais les règles sont inadéquates pour ce scénario

---

## §10 — Causes Racines Classifiées

### F. OBSERVATION_SEMANTICS — PRIMARY (sévérité CRITIQUE)

**Problème :** `active_window` encode l'état OS (focus système), pas l'intention utilisateur.  
**Manifestation :** Après fermeture de Notepad, l'OS rend le focus à Calculator. La perception écrit `active_window = Calculator` dans les 3s. Le modèle pense que l'utilisateur travaille dans Calculator.  
**Cause :** La directive §18 traite `active_window` comme un proxy d'intention, alors que c'est un fait OS qui change sans action explicite de l'utilisateur.

### I. TOOL_OBSERVATION — SECONDARY (sévérité HAUTE)

**Problème :** `pc.keyboard.type` et `pc.ui.type` ont `observation=()` — aucune observation enregistrée.  
**Manifestation :** Après "écris Bonjour dans Notepad", aucun signal `last_typed_target` n'existe pour PC. Le modèle ne peut pas inférer "j'ai interagi avec Notepad en dernier".  
**Cause :** Asymétrie avec browser tools qui enregistrent systématiquement `last_typed_target`.

### E. WORLD_STATE_TEMPORALITY — TERTIARY (sévérité MOYENNE)

**Problème :** Le modèle ne voit ni source, ni timestamp, ni TTL restant.  
**Manifestation :** Le modèle ne peut pas distinguer un fait écrit il y a 2s d'un fait écrit il y a 55s (presque périmé), ni un fait outil d'un fait perception.  
**Cause :** `render.py` n'expose que `[active]`/`[stale]` + valeur, sans provenance temporelle.

### C. WORLD_STATE_CONFLICT — ADDITIONAL (sévérité HAUTE)

**Problème :** Outil et perception écrivent le même clé avec des types différents (`str` vs `dict`).  
**Manifestation :** Comportement non-déterministe du modèle selon l'ordre des écritures. Code de traitement ambigu si certains chemins attendent `active_window` comme `str` et d'autres comme `dict`.  
**Cause :** Aucune contrainte de type sur les valeurs World State. Aucun contrat de schéma.

---

## §11 — Évaluation Architecturale

### Points forts actuels

1. **`ObservationSpec`** est un mécanisme élégant : les outils déclarent leurs observations, le harness les applique génériquement. Extensible sans modifier le core.
2. **TTL + FactStatus** : le système distingue déjà `ACTIVE` / `STALE` / `SUPERSEDED`. La mécanique existe.
3. **`apply_update` upsert** : simple et correct pour des faits sans historique.

### Limites architecturales identifiées

1. **Pas de sémantique EVENT vs STATE** : `active_window` (état OS continu) et `last_typed_target` (événement ponctuel) sont traités identiquement. Un état OS peut être "la vérité actuelle" sans être "l'intention récente de l'utilisateur".

2. **Pas de provenance dans le rendu** : La source est stockée dans `WorldStateFact.source` mais jamais transmise au modèle. Le modèle prend des décisions sans savoir si un fait vient d'une action explicite ou d'un capteur passif.

3. **Pas de `last_interaction_target` pour PC** : Il manque une clé dédiée qui encode "la dernière app avec laquelle l'utilisateur a interagi via un outil". Ce signal serait stable (ne change pas avec le focus OS) et sémantiquement correct.

4. **Race condition perception/outil** : La perception (TTL=20s, poll=3s) peut écraser un signal outil (TTL=60s) dans les 3 secondes suivant une action, avant même que l'utilisateur ait émis sa prochaine requête.

---

## §12 — Propositions de Correction Minimales (Audit — Pas de Code)

### Fix A — Ajouter `pc.last_interaction_target` (CRITIQUE)

**Principe :** Quand `pc.keyboard.type` ou `pc.ui.type` s'exécute avec succès, enregistrer la cible (nom de l'app / titre de fenêtre) dans `pc.last_interaction_target` avec TTL long (ex: 300s).

```python
# Proposition ObservationSpec pour pc.keyboard.type
ObservationSpec(
    domain="pc", 
    key="last_interaction_target", 
    evidence_field="target_window",  # à ajouter dans l'evidence outil
    freshness_ttl_s=300
)
```

**Impact :** Le modèle peut demander `pc.last_interaction_target` pour savoir "dans quelle app j'ai tapé en dernier", indépendamment du focus OS actuel.

### Fix B — Exposer la source dans le rendu World State (HAUTE)

**Principe :** Modifier `render.py` pour inclure la provenance dans le contexte modèle.

```python
# Avant
f"Observed environment state [{freshness}]: {domain}.{key} = {value!r}"

# Après (proposition)
f"Observed environment state [{freshness}] (source: {source}): {domain}.{key} = {value!r}"
```

**Impact :** Le modèle peut distinguer `source=tool:pc.application.launch` (action explicite) de `source=perception:foreground_window` (état OS passif) et pondérer ses décisions en conséquence.

### Fix C — Unifier le type de `pc.active_window` (HAUTE)

**Principe :** `ActiveWindowSensor` doit écrire `active_window` comme `str` (titre seul, comme l'outil), ou l'outil doit écrire un `dict` (comme la perception). Choisir un contrat de type et l'appliquer aux deux sources.

**Option recommandée :** Écrire en `str` dans les deux cas (le titre de fenêtre est la valeur utile pour le modèle; le process est une métadonnée qui peut aller dans `evidence` séparé).

### Fix D — Directive §18 : distinguer signal dominant d'état vs d'intention (MOYENNE)

**Principe :** Reformuler §18 pour indiquer explicitement que `active_window` décrit l'état OS courant, pas nécessairement la dernière cible d'interaction. Le modèle doit consulter `last_interaction_target` pour les références anaphoriques ("ferme-le").

---

## §13 — Tests Recommandés (max 10)

### Test 1 — Régression directe du scénario de déclenchement

```python
def test_close_after_typing_targets_typed_app_not_focus_app(tmp_path):
    """Après open(calculator) + open(notepad) + type(notepad) + close(notepad),
    une demande 'ferme-le' doit cibler notepad/l'app où on a tapé,
    jamais calculator qui a repris le focus OS."""
    # Script: launch(calc) → launch(notepad) → type(notepad) → close(notepad) → ...
    # Vérifie: le modèle consulte last_interaction_target, pas seulement active_window
```

### Test 2 — last_interaction_target écrit après pc.keyboard.type

```python
def test_pc_keyboard_type_writes_last_interaction_target(tmp_path):
    """pc.keyboard.type doit écrire pc.last_interaction_target dans le World State."""
    # Exécute pc.keyboard.type(target="notepad", text="hello")
    # Vérifie: world_state.get_fact("pc", "last_interaction_target") is not None
    # Vérifie: fact.value contient "notepad"
    # Vérifie: fact.source == "tool:pc.keyboard.type"
```

### Test 3 — Type consistency pour pc.active_window

```python
def test_active_window_consistent_type_across_sources(tmp_path):
    """pc.active_window doit avoir le même type (str ou dict)
    qu'il soit écrit par un outil ou par la perception."""
    # Écrit via outil, lit le type
    # Écrit via perception, lit le type
    # Assure: type(fact.tool.value) == type(fact.perception.value)
```

### Test 4 — Source visible dans le rendu contexte

```python
def test_world_state_source_appears_in_rendered_context():
    """Le rendu du système prompt doit inclure la source de chaque fait WS."""
    # Crée un Context avec WorldStateFact(source="perception:foreground_window")
    # Rend le système prompt
    # Vérifie: "perception:foreground_window" in rendered ou "perception" in rendered
```

### Test 5 — Perception ne cible pas le mauvais objet après fermeture

```python
def test_perception_overwrite_does_not_confuse_close_target(tmp_path):
    """Après close(notepad), la perception peut écrire active_window=calculator,
    mais le modèle scripté qui reçoit 'ferme-le' doit voir un signal d'intention
    (last_interaction_target) distinct du signal perception."""
    # Script multi-tour avec harness scripté
    # Tour 3: modèle reçoit 'ferme-le' + WS contenant les deux signaux
    # Vérifie: le message système contient last_interaction_target ET active_window distincts
```

### Test 6 — TTL de last_interaction_target > TTL de active_window perception

```python
def test_last_interaction_target_ttl_outlives_perception_active_window():
    """last_interaction_target (si ajouté) doit avoir TTL >= 120s
    pour survivre aux cycles de perception (TTL=20s)."""
    # Crée WorldStateFact(key="last_interaction_target", freshness_ttl_s=300)
    # Simule passage de 25s (> perception TTL)
    # Vérifie: is_expired() == False
```

### Test 7 — Directive §18 distingue signaux OS et signaux intention

```python
def test_directive_18_distinguishes_os_state_from_intent_signal():
    """La directive §18 dans le système prompt doit mentionner explicitement
    que active_window = état OS, last_interaction_target = intention."""
    rendered = render_system_prompt(_system_rules_ctx())
    assert "last_interaction_target" in rendered or "last interaction" in rendered.lower()
    # OU : la directive doit avertir que active_window peut changer après fermeture
    assert "focus" in rendered.lower() or "os state" in rendered.lower() or "may change" in rendered.lower()
```

### Test 8 — pc.ui.type écrit aussi last_interaction_target

```python
def test_pc_ui_type_writes_last_interaction_target(tmp_path):
    """pc.ui.type (comme pc.keyboard.type) doit enregistrer la cible."""
    # Exécute pc.ui.type(target="notepad", text="hello")
    # Vérifie: world_state.get_fact("pc", "last_interaction_target") is not None
```

### Test 9 — Résolution anaphorique "ferme-le" en contexte multi-app

```python
def test_anaphoric_close_resolves_to_last_interaction_not_active_window(tmp_path):
    """'ferme-le' après interaction dans Notepad doit résoudre 'le' comme
    Notepad, même si Calculator a repris le focus OS entre-temps."""
    script = [
        _tool_call_response("pc.application.launch", {"target": "calculator"}),
        _tool_call_response("pc.application.launch", {"target": "notepad"}),
        _tool_call_response("pc.keyboard.type", {"target": "notepad", "text": "Bonjour"}),
        _tool_call_response("pc.window.close", {"target": "notepad"}),
        # Ici Calculator a le focus OS — mais l'intention = fermer notepad
        _tool_call_response("pc.window.close", {"target": "notepad"}),  # ← attendu
        _text_response("Notepad fermé."),
    ]
    # Vérifie: la dernière action close cible "notepad", pas "calculator"
```

### Test 10 — Pas de régression browser.last_typed_target

```python
def test_browser_last_typed_target_still_written_after_type(tmp_path):
    """Régression : browser.type continue d'écrire last_typed_target
    (ne pas casser en ajoutant last_interaction_target pour PC)."""
    # Exécute browser.type(target="q", text="test")
    # Vérifie: world_state.get_fact("browser", "last_typed_target").value == "q"
```

---

## §14 — Tests E2E Réels Après Correction (max 5)

### E2E 1 — Scénario de déclenchement complet (Ollama live)

```
User: "ouvre la calculatrice"
User: "ouvre le bloc-notes"
User: "écris bonjour dans le bloc-notes"
User: "ferme-le"
Attendu: pc.window.close("notepad"), PAS pc.window.close("calculator")
```

### E2E 2 — Frappe multi-app sans confusion cible (Ollama live)

```
User: "ouvre notepad"
User: "écris 'hello'"
User: "ouvre une autre instance notepad"
User: "ferme la première"
Attendu: fermeture de la première instance (last_interaction_target distinctif)
```

### E2E 3 — Vérification source visible dans trace (Ollama live)

```
User: "dis-moi quelle application est active"
Attendu: la réponse distingue "l'OS dit X" vs "j'ai ouvert Y" selon la source
```

### E2E 4 — Résolution après délai > TTL perception (Ollama live)

```
User: "ouvre notepad"
[attendre 25s — perception TTL expiré]
User: "ferme-le"
Attendu: ferme notepad via last_interaction_target (qui a TTL=300s), pas active_window (expiré)
```

### E2E 5 — Frappe PC + fermeture sans ambiguïté (Ollama live)

```
User: "ouvre le bloc-notes et écris 'test' dedans"
User: "ferme le bloc-notes maintenant"
Attendu: fermeture correcte, aucune confusion même si Calculator était déjà ouvert
```

---

## §15 — Ce Qu'il Ne Faut Pas Changer

1. **`ObservationSpec` mechanics** — le mécanisme déclaratif outil → WS est correct et extensible. Ne pas remplacer.

2. **`apply_update` upsert** — la sémantique "last write wins" est correcte pour les états. Ne pas ajouter de logique de résolution de conflits dans la couche store.

3. **`FactStatus` (ACTIVE/STALE/SUPERSEDED)** — la mécanique TTL fonctionne correctement.

4. **`ActiveWindowSensor` poll interval (3s)** — c'est le bon équilibre réactivité/charge. Ne pas modifier.

5. **browser.type observations** — `last_typed_text` et `last_typed_target` pour browser fonctionnent correctement. Ne pas modifier.

6. **`pc.window.close` et `pc.application.launch`** — les outils eux-mêmes sont corrects. Seules les observations associées doivent évoluer.

7. **Le contrat `WorldStateFact`** — la structure de données est correcte. Ajouter `last_interaction_target` comme nouvelle clé, ne pas modifier la structure `active_window`.

---

## §16 — Items Différés

1. **Multi-instance app** : Si l'utilisateur ouvre 2 instances de Notepad, `last_interaction_target` ne distingue pas quelle instance. Nécessite un modèle de clé plus riche (ex: window handle). Différé — hors scope Chantier 20B.

2. **Conflits inter-sessions** : Plusieurs sessions simultanées partageant le même World State (SQLite). La notion de "last_interaction_target" est par-session, pas globale. Différé — nécessite session-scoped keys.

3. **History buffer** : Conserver les N dernières valeurs d'un fait (ex: "les 3 dernières apps actives"). Utile pour référents temporels ("l'app que j'avais avant"). Différé — complexité architecturale.

4. **Schéma de validation de type** : Imposer un type strict par clé World State (ex: `active_window` est toujours `str`). Différé — nécessite un registre de schémas.

5. **Signal de fermeture** : Quand `pc.window.close` s'exécute, marquer `active_window` comme `SUPERSEDED` immédiatement (avant que la perception ne réécrive). Différé — interaction subtile avec le timing perception.

---

## §17 — Verdict Final

### Résumé des Anomalies

| ID | Sévérité | Description | Fix proposé |
|----|----------|-------------|------------|
| F | CRITIQUE | `active_window` = état OS ≠ intention utilisateur. §18 les confond. | Fix A + Fix D |
| I | HAUTE | `pc.keyboard.type` / `pc.ui.type` : observation=() → pas de `last_interaction_target` | Fix A |
| C | HAUTE | Type inconsistency `str` (outil) vs `dict` (perception) pour même clé | Fix C |
| E | MOYENNE | Source/timestamp invisible au modèle — décisions sans provenance | Fix B |

### Diagnostic Causal de la Régression

```
Le bug "ferme Calculator à la place de Notepad" est causé par :

1. (PRIMARY) pc.keyboard.type n'enregistre pas last_interaction_target
   → Le modèle ignore que l'utilisateur a tapé dans Notepad

2. (CONTRIBUTING) Perception réécrit active_window=Calculator en 3s après close(Notepad)
   → Le seul signal disponible pointe vers Calculator

3. (AMPLIFYING) §18 dit que active_window est "clairement dominant"
   → Le modèle obéit à la règle et choisit Calculator

4. (ENABLING) Source invisible au modèle
   → Le modèle ne peut pas distinguer "j'ai ouvert Calculator" de "Calculator a le focus par défaut"
```

### Priorité des Corrections

1. **[P0]** Ajouter `last_interaction_target` pour PC typing tools (Fix A)
2. **[P1]** Unifier le type de `pc.active_window` (Fix C)
3. **[P2]** Exposer source dans le rendu contexte (Fix B)
4. **[P3]** Reformuler directive §18 (Fix D)

### Conclusion

L'architecture World State est saine dans son principe. Le bug est une lacune de couverture (pc typing sans observation) combinée à une confusion sémantique dans la directive §18 (état OS ≠ intention). Les 4 corrections proposées sont minimales, chirurgicales, et n'impliquent aucune refonte architecturale. L'implémentation prioritaire est Fix A (< 20 lignes de code).

---

*Audit réalisé par analyse statique du code source — aucune modification apportée.*  
*Fichiers lus : `raya/contracts/world_state.py`, `raya/tools/catalog/pc.py`, `raya/tools/catalog/browser.py`, `raya/perception/windows_sensors.py`, `raya/context_engine/render.py`, `raya/devices/windows/agent.py`, `raya/world_state/store.py`*
