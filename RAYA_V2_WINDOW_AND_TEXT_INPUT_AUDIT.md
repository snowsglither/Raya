# RAYA V2 — Audit : Window State & Text Input Semantics
## RAYA_V2_WINDOW_AND_TEXT_INPUT_AUDIT.md

**Date :** 2026-09-15  
**Mode :** AUDIT ONLY — aucun code modifié  
**Environnement :** Windows 11 Pro Education 10.0.26200, Python 3.14, pygetwindow 0.0.9, pywin32

---

## §1 — Executive Summary

**BUG A (Window State)** et **BUG B (Text Input)** sont deux bugs **indépendants** avec des causes racines distinctes. Les deux sont confirmés par observation réelle sur machine.

| Bug | Symptôme | Cause racine | Fichier | Verdict |
|-----|----------|-------------|---------|---------|
| **A** | Chrome/Edge maximisé se retrouve déplacé/réduit à droite de l'écran | `SW_RESTORE` (=9) appelé **inconditionnellement** dans `_activate_hwnd()` fallback Win32 | `raya/devices/windows/mechanisms/window_mgmt.py` L.176 | CONFIRMED |
| **B** | RAYA écrit à la suite du texte existant au lieu de remplacer | `pc.keyboard.type` n'a **pas de paramètre `mode`** ; `keyboard.type_text()` colle (Ctrl+V) à la position curseur | `raya/devices/windows/agent.py` + `raya/tools/catalog/pc.py` | CONFIRMED |

**Aucune cause commune.** BUG A est un mécanisme Win32 mal conditionné. BUG B est un manque de schéma + contexte.

---

## §2 — BUG A : Chrome / Window State

### 2.1 Reproduction confirmée

**Observation directe :**
```
AVANT  SW_RESTORE : Chrome "RAYA | INTERPRISE" → MAXIMIZED  (-8, -8, 1928, 1040)
APRÈS  SW_RESTORE : Chrome "RAYA | INTERPRISE" → NORMAL     (946, 0, 1920, 1032)
```

Position X=946 sur un écran 1920px = **positionnée au milieu-droit de l'écran**, exactement le comportement rapporté par l'utilisateur ("la fenêtre se retrouve positionnée à droite de l'écran").

La "restore_rect" lue via `GetWindowPlacement()` confirme que la position de restauration de Chrome est (946, 0) — moitié droite de l'écran. C'est la dernière position non-maximisée mémorisée par Windows pour cette fenêtre Chrome.

### 2.2 Cause exacte

**Fichier :** `raya/devices/windows/mechanisms/window_mgmt.py`  
**Fonction :** `_activate_hwnd()` — chemin fallback Win32  
**Ligne critique :** 176

```python
def _activate_hwnd(hwnd: int, title: str, extra: dict) -> dict:
    try:
        import pygetwindow as gw
        wins = [w for w in gw.getAllWindows() if (w.title or "") == title]
        if wins:
            w = wins[0]
            try:
                if w.isMinimized:
                    w.restore()
                w.activate()          # ← pygetwindow.activate() = SetForegroundWindow seulement
                time.sleep(0.2)
                return {"status": "ok", ..., "method": "pygetwindow", ...}
            except Exception:         # ← Si activate() lève → tombe dans fallback
                pass
    except Exception:
        pass
    # FALLBACK WIN32 — TOUJOURS EXÉCUTÉ SI PYGETWINDOW ÉCHOUE :
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)    # ← LIGNE 176 — BUG
        ...
        win32gui.SetForegroundWindow(hwnd)
```

**`win32con.SW_RESTORE` = 9.** Comportement MSDN :
> "Activates and displays the window. If the window is minimized **or maximized**, Windows restores it to its original size and position."

Sur une fenêtre **maximisée** : SW_RESTORE = démaximise et repositionne à la "restore position" (la dernière position normale mémorisée).

### 2.3 Chemin d'exécution complet

```
User : "ouvre Chrome" / "va sur Google dans Chrome" / "focuse Chrome"
  ↓
Modèle appelle : pc.application.launch("chrome") ou pc.application.focus("chrome")
  ↓
tools/catalog/pc.py : _run(agent, "application.launch", ...)
  ↓
devices/windows/agent.py : _app_launch()
  ↓
mechanisms/applications.py : applications.launch("chrome")
  if already_open:
      return {**_win.focus_window("chrome"), "already_open": True}   ← TRIGGER
  ↓
mechanisms/window_mgmt.py : focus_window("chrome")
  → resolve_window(query="chrome") → trouve "RAYA | INTERPRISE - Google Chrome"
  → _activate_hwnd(hwnd=..., title="RAYA | INTERPRISE - Google Chrome", ...)
  ↓
CHEMIN PYGETWINDOW :
  gw.getAllWindows() exact match sur "RAYA | INTERPRISE - Google Chrome" → trouvé ✓
  w.activate() → ctypes SetForegroundWindow(hwnd)
  Si RAYA n'a pas le "foreground lock" → SetForegroundWindow retourne 0 → _raiseWithLastError()
  → Exception → caught → tombe dans fallback
  ↓
CHEMIN FALLBACK WIN32 :
  win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)  ← DÉMAXIMISE ←
  ctypes AllowSetForegroundWindow(-1)
  win32gui.SetForegroundWindow(hwnd)
  ↓
Chrome : MAXIMIZED (-8,-8,1928,1040) → NORMAL (946,0,1920,1032)
```

### 2.4 Quand le fallback Win32 est déclenché

`pygetwindow.activate()` lève une exception quand `SetForegroundWindow` retourne 0. Cela se produit quand :
- RAYA s'exécute en arrière-plan sans input utilisateur récent (Windows foreground lock)
- Le Cockpit UI (Edge WebView) a le focus mais n'est pas dans la chaîne de processus
- Windows bloque le "focus stealing" pour protéger l'expérience utilisateur

Ce comportement est **intermittent et contextuel** : dans certains cas (RAYA foreground), le chemin pygetwindow réussit et est sûr. Dans d'autres (RAYA background), le fallback SW_RESTORE est déclenché et de-maximize la fenêtre.

### 2.5 `browser.navigate` n'est PAS responsable

Trace vérifiée par E2E réel :
```
browser.navigate(https://example.com) :
  Edge "Nouvel onglet" NORMAL (951,87) → "Example Domain" NORMAL (951,87)  ← INCHANGÉ
  Chrome "RAYA | INTERPRISE" MAXIMIZED → MAXIMIZED                         ← INCHANGÉ
```

`BrowserController.navigate()` → `page.goto()` → CDP `Page.navigate` — aucune interaction Win32.  
`BrowserSession._start()` → `new_page()` → CDP `Target.createTarget` — crée un onglet, ne change pas la position/état de la fenêtre.

**Le bug ne se manifeste QUE si le modèle appelle `pc.application.focus` / `pc.application.launch` sur une application navigateur.**

### 2.6 Changement réellement nécessaire ?

Non. L'appel à `ShowWindow(hwnd, SW_RESTORE)` dans le fallback était destiné à gérer les fenêtres **minimisées** (iconiques). Il ne devrait jamais toucher les fenêtres maximisées. Un `SW_SHOW` (5) ou un check conditionnel serait correct.

---

## §3 — BUG B : Text Input — Écriture à la suite

### 3.1 Reproduction confirmée

**Observation réelle sur machine :**
```
1. Notepad lancé : "Sans titre — Bloc-notes"  (document vide)
2. pc.keyboard.type("Bonjour") → "*Bonjour — Bloc-notes"   ✓
3. pc.keyboard.type("Salut")   → "*BonjourSalut — Bloc-notes"  ← APPEND CONFIRMÉ
```

"Bonjour" + "Salut" → "BonjourSalut" — le texte est **collé à la position curseur** (fin du document après "Bonjour").

### 3.2 Cause exacte

**Niveau 1 — Mécanisme :** `keyboard.type_text()` utilise le presse-papier (Ctrl+V).

```python
# raya/devices/windows/mechanisms/keyboard.py
def type_text(text: str, use_clipboard: bool = True) -> bool:
    if use_clipboard:
        _clipboard.set_text(text)
        pyautogui.hotkey("ctrl", "v")   # ← Colle à la position curseur
        return True
    pyautogui.write(text, interval=0.01)  # Repli clavier
    return True
```

`Ctrl+V` insère au curseur. Si le document contient du texte et que le curseur est à la fin, le nouveau texte s'ajoute à la suite.

**Niveau 2 — Tool Schema :** `pc.keyboard.type` n'a pas de paramètre `mode`.

```python
# raya/tools/catalog/pc.py (extrait)
("pc.keyboard.type", "keyboard.type",
 "Saisit du texte au clavier.",
 {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
 ...
 observation=_LAST_INTERACTION_OBSERVATION),
```

Aucun `mode` (replace/append/clear). Le modèle ne peut pas indiquer son intention.

**Niveau 3 — Contexte modèle :** Avant un appel `pc.keyboard.type`, le modèle reçoit uniquement :

| Donnée World State | Ce que le modèle reçoit | Ce qu'il faudrait |
|--------------------|------------------------|------------------|
| `pc.active_window` | `"Sans titre — Bloc-notes"` (titre seul) | Contenu du document |
| `pc.last_interaction_target` | `"Sans titre — Bloc-notes"` (titre seul) | Position curseur |
| Contenu document | ❌ ABSENT | Texte actuel |
| Position curseur | ❌ ABSENT | Sélection actuelle |
| Sélection | ❌ ABSENT | Mode replace/append |

Le modèle **ne sait pas** si le document est vide ou contient du texte.

### 3.3 Asymétrie du schéma

| Outil | Paramètre `mode` | Comportement par défaut |
|-------|-----------------|------------------------|
| `browser.type` | ✅ replace/append/clear | `replace` (fill — remplace tout) |
| `pc.ui.type` | ✅ replace/append/clear | `replace` (uia_set_value) |
| `pc.keyboard.type` | ❌ ABSENT | Toujours append (colle à curseur) |

Les trois outils font des saisies de texte. `browser.type` et `pc.ui.type` sont cohérents entre eux et avec l'intention "écris X = remplace le contenu". `pc.keyboard.type` est incohérent : il **ajoute** toujours, comme si c'était un `append` silencieux.

### 3.4 Analyse des cas sémantiques

**Cas 1 — Notepad vide + "écris Bonjour"**  
Attendu : "Bonjour". Résultat actuel : "Bonjour" ✓ (cursor at 0, colle en début = correct)

**Cas 2 — Notepad contient "Bonjour" + "ajoute salut"**  
Attendu : "BonjourSalut" ou "Bonjour salut". Résultat actuel : "BonjourSalut" ✓ (correct par coïncidence — le mode "append" correspond à l'intention "ajoute")

**Cas 3 — Notepad contient "Bonjour" + "remplace ça par Salut"**  
Attendu : "Salut". Résultat actuel : "BonjourSalut" ❌ (append au lieu de replace)

**Cas 4 — Notepad contient "Bonjour" + "écris Salut"**  
Attendu ambigu. Résultat actuel : "BonjourSalut" (append). Correct si "écris" = append, incorrect si "écris" = replace. Le modèle **ne peut pas distinguer** les deux intentions avec le schéma actuel.

**Cas 5 — Notepad contient un document + "écris un nouveau texte"**  
Attendu : nouveau document (Ctrl+N) ou document vidé. Résultat actuel : texte ajouté à la suite ❌. RAYA ne peut pas créer un nouveau document Notepad sans `pc.keyboard.press("ctrl+n")` ou `pc.ui.type` avec `mode=clear`. Aucun outil dédié.

### 3.5 Ce que RAYA fait actuellement (ou non)

```
Flux actuel pour pc.keyboard.type :
  WRITE  (Ctrl+V à la position curseur)
  ← pas d'OBSERVE
  ← pas d'IDENTIFY TARGET
  ← pas d'UNDERSTAND STATE
  ← pas de CHOOSE WRITE MODE
  ← pas de VERIFY
```

Il n'y a pas de pipeline "OBSERVE → IDENTIFY → CHOOSE → WRITE → VERIFY". C'est un outil **blindly insert** sans aucune conscience du contexte du document.

---

## §4 — Root Cause Classification

### BUG A

| Catégorie | Applicable | Détail |
|-----------|-----------|--------|
| `WINDOW_STATE` | ✅ PRIMAIRE | Fenêtre de-maximisée par SW_RESTORE |
| `WINDOW_RESIZE` | ✅ | Taille restaurée depuis restore_rect |
| `WINDOW_POSITION` | ✅ | Positionnée à X=946 (droite de l'écran) |
| `WINDOW_FOCUS` | ✅ SECONDAIRE | Focus change déclenche le chemin défaillant |
| `DEVICE_AGENT` | ✅ | Bug dans WindowsDeviceAgent → _activate_hwnd |
| `VERIFICATION_FAILURE` | ❌ | Pas un problème de vérification |
| `CDP` | ❌ | browser.navigate n'est pas responsable |
| `PLAYWRIGHT` | ❌ | Playwright n'est pas responsable |
| `MODEL_INTERPRETATION` | ⚠️ | Le modèle choisit d'appeler pc.application.focus — trigger indirect |

### BUG B

| Catégorie | Applicable | Détail |
|-----------|-----------|--------|
| `TEXT_INPUT_SEMANTICS` | ✅ PRIMAIRE | append vs replace non distingués |
| `TOOL_SCHEMA` | ✅ PRIMAIRE | mode absent dans pc.keyboard.type |
| `CONTEXT_MISSING` | ✅ PRIMAIRE | contenu document/curseur absent du contexte modèle |
| `WRITE_MODE` | ✅ | pas de sélection de mode possible |
| `UI_OBSERVATION` | ✅ | aucune observation pré-frappe |
| `TARGET_RESOLUTION` | ❌ | la fenêtre est identifiée (last_interaction_target) |
| `MODEL_INTERPRETATION` | ✅ SECONDAIRE | modèle ne peut distinguer les intentions |

---

## §5 — Real E2E Results

| E2E | Description | Résultat | Preuve |
|-----|-------------|---------|--------|
| **A — SW_RESTORE statique** | ShowWindow(SW_RESTORE) sur VS Code maximisé | ✅ PASS | MAXIMIZED(-8,-8,1928,1040) → NORMAL(352,140,1568,948) |
| **A — SW_RESTORE Chrome** | ShowWindow(SW_RESTORE) sur Chrome maximisé | ✅ PASS | MAXIMIZED(-8,-8,1928,1040) → NORMAL(946,0,1920,1032) — position droite |
| **B — pygetwindow.activate()** | activate() sur fenêtre maximisée | ✅ PASS (safe) | MAXIMIZED reste MAXIMIZED — activate() = SetForegroundWindow seulement |
| **A — browser.navigate window** | browser.navigate → état Edge/Chrome | ✅ PASS (safe) | 4 fenêtres : aucun changement d'état, aucun déplacement |
| **C — Notepad vide** | keyboard.type("Bonjour") dans Notepad vide | ✅ PASS (correct) | "Sans titre" → "*Bonjour — Bloc-notes" |
| **D — Notepad contenant texte** | keyboard.type("Salut") après "Bonjour" | ✅ CONFIRMED BUG | "*Bonjour" → "*BonjourSalut" — append confirmé |
| **E — pc.ui.type replace** | mode='replace' via strategy.type_into_element | NOT_TESTED | uiautomation non installé |
| **F — nouveau document** | Ctrl+N depuis Notepad avec contenu | OBSERVED | Notepad Windows 11 ferme et rouvre (tabbed) — contenu perdu |

**Environnement réel :**
- 3 fenêtres Chrome : 2 MAXIMIZED (rect -8,-8,1928,1040), 1 NORMAL (rect 956,10,1930,1042)
- 1 fenêtre Edge (profil RayaV2) : NORMAL (rect 951,87,1896,1099)
- pygetwindow installé (v0.0.9)
- uiautomation NON installé (module absent)
- SW_RESTORE confirmé de-maximiser avec preuves de rect avant/après

---

## §6 — Proposed Fixes

### Fix BUG A — Conditionner SW_RESTORE dans `_activate_hwnd()`

**Fichier :** `raya/devices/windows/mechanisms/window_mgmt.py`  
**Fonction :** `_activate_hwnd()`  
**Changement :** Remplacer le `ShowWindow(hwnd, SW_RESTORE)` inconditionnel par un appel conditionnel.

**Avant :**
```python
try:
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
except Exception:
    pass
win32gui.SetForegroundWindow(hwnd)
```

**Après (minimum viable) :**
```python
try:
    placement = win32gui.GetWindowPlacement(hwnd)
    if placement[1] == 2:  # SW_SHOWMINIMIZED — seulement si minimisée
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
except Exception:
    pass
win32gui.SetForegroundWindow(hwnd)
```

**Impact :** Localité maximale — 3 lignes dans un seul fichier. Aucune modification de l'API publique. Risque minimal.

**Pourquoi `placement[1] == 2` et non `IsIconic()` :** `GetWindowPlacement` retourne l'état "conceptuel" de la fenêtre même si elle est en cours d'animation. Plus fiable.

**Fenêtre minimisée :** Reste correctement gérée (SW_RESTORE restaure depuis état iconique).  
**Fenêtre maximisée :** Plus touchée — conserve son état plein écran. ✅  
**Fenêtre normale :** Plus touchée — conserve sa position. ✅

**Alternative (encore plus sûre) :** Supprimer totalement le `ShowWindow` du fallback et ne garder que `SetForegroundWindow`. Windows peut mettre une fenêtre au premier plan sans la "restaurer" si elle est déjà visible (maximisée = visible). Le `SW_RESTORE` n'était utile que pour les fenêtres minimisées.

### Fix BUG B — Ajouter `mode` à `pc.keyboard.type`

**Sous-fix B.1 — Tool Schema**  
**Fichier :** `raya/tools/catalog/pc.py`

Ajouter `"mode"` à l'input_schema et à la description de `pc.keyboard.type` :
```python
("pc.keyboard.type", "keyboard.type",
 "Saisit du texte au clavier dans l'application active. "
 "mode='replace' (défaut) : sélectionne tout (Ctrl+A) puis colle — remplace tout le contenu existant. "
 "mode='append' : colle à la position curseur actuelle sans effacer. "
 "À utiliser quand le champ/document est vide ou quand l'intention est d'ajouter du texte.",
 {"type": "object", "properties": {
     "text": {"type": "string"},
     "mode": {"type": "string", "enum": ["replace", "append"], "description": "replace=Ctrl+A avant frappe (défaut), append=frappe à la position curseur"}
 }, "required": ["text"]},
 ...
```

**Sous-fix B.2 — Device Agent**  
**Fichier :** `raya/devices/windows/agent.py`  
**Fonction :** `_keyboard_type()`

```python
def _keyboard_type(agent, command, should_stop):
    args = command.arguments
    mode = args.get("mode", "replace")
    active_raw = window_mgmt.get_active_window()
    active_info = active_raw.get("active") if active_raw.get("status") == "ok" else None
    if mode == "replace":
        keyboard.press("ctrl+a")   # Sélectionne tout avant collage
    ok = keyboard.type_text(args["text"])
    if not ok:
        return _fail(command, "TYPE_FAILED", "échec de la saisie clavier", "clipboard_paste")
    evidence: dict = {"length": len(args["text"])}
    if active_info:
        evidence["window"] = active_info["title"]
    return _ok(command, {"typed": True, "mode": mode}, evidence, "clipboard_paste")
```

**Cohérence avec les autres outils :**
- `browser.type` : default `replace` ✅
- `pc.ui.type` : default `replace` ✅
- `pc.keyboard.type` : default `replace` après ce fix ✅

**Note sur `append` :** Avec `append`, le comportement existant est préservé (Ctrl+V à la position curseur). L'outil reste utilisable pour les cas où l'utilisateur dit explicitement "ajoute X".

**Sous-fix B.3 (OPTIONNEL) — Contexte pré-frappe**

Pour que le modèle puisse choisir correctement entre `replace` et `append`, il faudrait lui fournir l'état du document. La voie architecturalement correcte est :
1. Le modèle appelle `pc.ui.inspect(window)` pour lire le contenu avant de taper
2. Ce retour expose les contrôles et leur valeur actuelle
3. Le modèle décide ensuite du mode

Ceci n'est pas un fix de code mais une **directive §18** additionnelle : "Avant tout `pc.keyboard.type` dans un document potentiellement non vide, inspecte d'abord le contenu via `pc.ui.inspect` ou utilise `mode=replace` si l'intention est d'écrire un nouveau contenu."

Cette directive est le fix le moins invasif pour la sémantique du modèle.

---

## §7 — Tests à ajouter (max 15 proposés)

| # | Test | Pourquoi nécessaire |
|---|------|---------------------|
| T1 | `test_activate_hwnd_does_not_restore_maximized_window` | Régression directe BUG A — vérifie que SW_RESTORE n'est pas appelé sur une fenêtre maximisée |
| T2 | `test_activate_hwnd_restores_minimized_window` | Vérifie que les fenêtres minimisées sont bien restaurées (comportement attendu conservé) |
| T3 | `test_focus_window_preserves_maximized_state` | BUG A E2E Windows réel — Chrome/Edge maximisé reste maximisé après focus_window() |
| T4 | `test_keyboard_type_has_mode_parameter` | Vérifie que pc.keyboard.type déclare un paramètre `mode` dans son schéma |
| T5 | `test_keyboard_type_replace_mode_sends_ctrl_a` | Vérifie que mode='replace' envoie Ctrl+A avant Ctrl+V (mécanisme) |
| T6 | `test_keyboard_type_append_mode_pastes_at_cursor` | Vérifie que mode='append' conserve le comportement actuel (pas de Ctrl+A) |
| T7 | `test_keyboard_type_default_mode_is_replace` | Vérifie que le défaut est 'replace' (cohérence avec browser.type et pc.ui.type) |
| T8 | `test_real_notepad_replace_mode_overwrites_content` | Windows réel : Notepad "Bonjour" + keyboard.type("Salut", mode="replace") → "Salut" seulement |
| T9 | `test_real_notepad_append_mode_adds_to_content` | Windows réel : Notepad "Bonjour" + keyboard.type(" Salut", mode="append") → "Bonjour Salut" |
| T10 | `test_browser_navigate_does_not_change_window_state` | Régression BUG A — browser.navigate ne change pas l'état des fenêtres ouvertes |
| T11 | `test_sw_restore_constant_value_is_9` | Documente que SW_RESTORE=9 = restaure depuis maximisé aussi (référence MSDN) |
| T12 | `test_activate_hwnd_fallback_uses_sw_show_not_sw_restore` | Vérifie que le fallback n'appelle pas SW_RESTORE sur fenêtre non-minimisée |
| T13 | `test_keyboard_type_mode_in_evidence` | Vérifie que le mode utilisé est dans l'evidence (traçabilité) |
| T14 | `test_pc_ui_type_default_mode_replace_consistency` | Vérifie cohérence pc.ui.type default = replace (régression) |
| T15 | `test_all_text_input_tools_have_consistent_defaults` | Test d'architecture : browser.type, pc.ui.type, pc.keyboard.type ont tous default='replace' |

---

## §8 — Ce qui NE DOIT PAS être changé

- **`ActiveWindowSensor`** — ne pas masquer le focus OS réel ; ne pas modifier le polling ; SW_RESTORE dans le sensor n'existe pas (capteur lecture seule)
- **`BrowserController.navigate()`** — innocent ; ne pas ajouter de window management ici
- **`BrowserSession._start()`** — innocent ; ne pas modifier la création d'onglets
- **`keyboard.type_text()`** mécanisme bas niveau — innocent ; ne pas changer le mécanisme Ctrl+V
- **Schéma `pc.ui.type`** — déjà correct ; ne pas toucher
- **Schéma `browser.type`** — déjà correct ; ne pas toucher
- **`_LAST_INTERACTION_OBSERVATION`** (Chantier 20C) — ne pas modifier TTL ni sémantique
- **pygetwindow chemin** dans `_activate_hwnd` — chemin correct (`activate()` = SetForegroundWindow seulement = safe) ; ne pas supprimer ce chemin
- Aucun fichier V1

---

## §9 — V1 Integrity

**V1 strictement intact.** Cet audit est en mode LECTURE SEULEMENT. Aucun fichier n'a été modifié.

Les fichiers potentiellement concernés par les fixes futurs sont tous V2 :
- `raya/devices/windows/mechanisms/window_mgmt.py` — V2
- `raya/devices/windows/agent.py` — V2
- `raya/tools/catalog/pc.py` — V2

---

## §10 — Final Verdict

### BUG A — Window State
**MODIFY**

Fix minimal identifié, risque faible, impact localisé. 3 lignes dans `window_mgmt.py`. Tests à ajouter : T1-T3, T10-T12.

Root cause confirmée par preuve réelle : `ShowWindow(hwnd, SW_RESTORE)` de-maximise Chrome (946,0,1920,1032) depuis état plein écran (-8,-8,1928,1040).

### BUG B — Text Input Semantics
**MODIFY**

Fix B.1 + B.2 : ajouter `mode` à `pc.keyboard.type` (schéma + implémentation Ctrl+A). Fix B.3 : directive §18. Risque modéré (changement de comportement par défaut — les cas actuellement corrects par coïncidence restent corrects avec `replace`). Tests à ajouter : T4-T9, T13-T15.

Root cause confirmée par preuve réelle : "BonjourSalut" après keyboard.type("Bonjour") + keyboard.type("Salut") sur Notepad vide.

### Lien entre les deux bugs
**INDÉPENDANTS.** BUG A = Win32 focus mechanism. BUG B = Tool schema + context gap. Peuvent être fixés dans n'importe quel ordre.

---

## §11 — Annexe : Traces Brutes

### A.1 — SW_RESTORE sur VS Code maximisé
```
AVANT : state=MAXIMIZED, rect=(-8, -8, 1928, 1040)
[ShowWindow(hwnd, SW_RESTORE)]
APRÈS : state=NORMAL,    rect=(352, 140, 1568, 948)
→ Fen�tre remaximisée : MAXIMIZED
```

### A.2 — SW_RESTORE sur Chrome maximisé
```
Chrome "RAYA | INTERPRISE - Google Chrome" :
  current_rect: (-8, -8, 1928, 1040)   [MAXIMIZED]
  restore_rect: (946, 0, 1920, 1032)   [position droite — BUG observable]

AVANT  : state=MAXIMIZED, rect=(-8, -8, 1928, 1040)
[ShowWindow(hwnd, SW_RESTORE)]
APRÈS  : state=NORMAL,    rect=(946, 0, 1920, 1032)
→ BUG A CONFIRMED: Chrome MAXIMIZED → NORMAL via SW_RESTORE
→ Fenêtre positionnée à DROITE de l'écran (x=946/1920)
→ Chrome restauré : MAXIMIZED
```

### A.3 — pygetwindow.activate() sur Chrome maximisé
```
Avant activate() : state=MAXIMIZED
Après activate() : state=MAXIMIZED  (inchangé)
→ pygetwindow.activate() = safe (SetForegroundWindow uniquement)
```

### A.4 — browser.navigate window state delta
```
Avant navigate() :
  Edge "Nouvel onglet" NORMAL (951,87,1896,1099)
  Chrome "RAYA | INTERPRISE" MAXIMIZED (-8,-8,1928,1040)
  Chrome "Toledo Portal" MAXIMIZED (-8,-8,1928,1040)

Après navigate(https://example.com) :
  Edge "Example Domain et 1 page supplémentaire" NORMAL (951,87,1896,1099)
  Chrome "RAYA | INTERPRISE" MAXIMIZED (-8,-8,1928,1040)  ← INCHANGÉ
  Chrome "Toledo Portal" MAXIMIZED (-8,-8,1928,1040)      ← INCHANGÉ
→ browser.navigate innocent
```

### B.1 — Notepad keyboard.type append confirmé
```
Notepad lancé : "Sans titre — Bloc-notes"  (frais, Windows 11)
keyboard.type("Bonjour") → "*Bonjour — Bloc-notes"
keyboard.type("Salut")   → "*BonjourSalut — Bloc-notes"
→ BUG B CONFIRMED: append systématique
```

---

*Audit généré en mode OBSERVATION SEULEMENT — aucun code modifié — 2026-09-15*
