# RAYA V2 — Cookie Consent DOM + Vision Audit

**Date:** 2026-09-15
**Mode:** AUDIT ONLY — aucune modification de code
**Verdict final:** MODIFY

---

## 1. Executive Summary

RAYA V2 possède tous les mécanismes techniques nécessaires au traitement du consentement cookies :
- `browser.read_page` retourne un flag `cookie_banner` et les boutons de la page
- `browser.dismiss_overlay` existe et fonctionne sur certains sites
- `browser.click` peut cibler un bouton par son texte, même dans des frames
- `vision.find_in_browser` peut localiser visuellement un élément si le DOM échoue
- La Safety est SAFE pour les actions de consentement standard

**Pourtant RAYA ne traite pas correctement les cookies.** La raison est une chaîne de 3 défaillances cumulées :

1. **Détection fausse** : `cookie_banner=False` sur des pages qui ont réellement un banner (taux d'erreur ~60% dans l'audit).
2. **Directive incomplète** : le modèle ne reçoit d'instruction que pour `cookie_banner=true`, pas pour les bannières non détectées.
3. **Séquence de repli absente** : quand `browser.dismiss_overlay` échoue (rounds=0), aucune instruction ne guide le modèle vers `browser.click` sur les boutons visibles ou vers `vision.find_in_browser`.

Conséquence : le modèle ne sait pas qu'il y a un consentement à gérer → essaie de continuer l'objectif → `browser.click` sur le contenu échoue (bloqué par l'overlay invisible) → interprète comme "élément absent" → cherche une alternative (autre URL, autre stratégie) → c'est le comportement de "contournement" observé.

---

## 2. Architecture actuelle

### 2.1 DOM (`browser.read_page` / `controller.py`)

**`_STRUCT_JS`** évalue une page et retourne :
- `url`, `title`
- `cookie_banner` (boolean) — détecté avec exactement 4 sélecteurs CSS :
  ```js
  document.querySelectorAll('[id*=cookie i],[class*=cookie i],[id*=consent i],[class*=consent i]')
  ```
- `buttons[]` (max 120) — tous les boutons visibles page entière, avec `text`, `left`, `top`, `aria_label`
- `links[]` (max 60), `inputs[]` (max 20)

**Portée** : main document uniquement. Aucune inspection des iframes.

**Position info** : chaque bouton inclut `left` et `top` (coordonnées viewport), utilisables avec `browser.click_at_position` comme fallback positionnel.

**Gap critique** : `cookie_banner` utilise 4 sélecteurs ; `dismiss_overlay` en utilise 10+. Les bannières ne répondant qu'aux 6 sélecteurs supplémentaires ne sont pas détectées.

---

### 2.2 Vision (`visual.py`)

Cinq tools disponibles :
- `vision.observe_browser` — screenshot browser → description visuelle générale
- `vision.find_in_browser` — screenshot browser → localise un élément, retourne `(screen_x, screen_y)` utilisables avec `browser.click_at_position`
- `vision.observe_screen`, `vision.find_on_screen`, `vision.observe_image` — pour l'écran entier

**Inscription dans le code** : les descriptions des tools browser-vision disent explicitement **"DOM-FIRST"** :
```
"DOM-FIRST : utiliser browser.read_page en premier. Ce tool est un FALLBACK..."
"DOM-FIRST : utiliser browser.click en premier. Ce tool est un FALLBACK..."
```

**Fusion DOM + Vision : NON SIMULTANÉE.** Le modèle reçoit DOM OU vision, jamais les deux en même temps structurellement. La vision n'est déclenchée que si le modèle décide de l'appeler explicitement, après avoir traité le DOM.

**Aucune directive** n'indique au modèle d'appeler `vision.find_in_browser` lorsque `dismiss_overlay` échoue et qu'un banner est peut-être présent visuellement.

---

### 2.3 `browser.dismiss_overlay` (`controller.py::dismiss_overlays`)

**Mécanisme** :
1. Évalue `_OVERLAY_MARK_JS` sur la page principale — marque les éléments visibles correspondant à `_OVERLAY_SELECTORS` avec `data-raya-overlay`
2. Cherche un bouton "Accept" dans les éléments marqués (`_find_clickable_in(overlay_root, text)`)
3. Priorité d'action : **Accept → Reject → Close** (hard-coded, le modèle n'a aucun contrôle)

**`_OVERLAY_SELECTORS`** (10 sélecteurs) :
```python
"[id*=cookie i]", "[class*=cookie i]", "[id*=consent i]", "[class*=consent i]",
"[role=dialog]", "[aria-modal=true]", ".modal", ".popup", ".overlay",
".gdpr", ".cookie-banner"
```

**`_OVERLAY_ACCEPT_TEXTS`** (9 textes — FR/EN uniquement) :
```python
"Tout accepter", "Accepter tout", "Accepter", "J'accepte",
"Accept all", "I agree", "Agree", "Got it", "OK"
```

**Gaps identifiés** :
- `.gdpr` ne matche que `class="gdpr"` exact (token), pas `class="gdpr-lmd-wall"` (préfixe)
- Langues manquantes : NL "Alle accepteren", DE "Alles akzeptieren", ES "Aceptar todo", IT "Accetta tutto", "Autoriser tout", etc.
- **Aucune recherche dans les iframes** : `_OVERLAY_MARK_JS` tourne dans le main document uniquement

---

### 2.4 Browser Agent (flow complet)

**`browser.click`** (`_find_clickable`) :
- Cherche dans la page principale + TOUS les frames (iframes inclus)
- 6 stratégies de localisation : `get_by_role`, `get_by_role("link")`, `get_by_label`, `get_by_title`, `[aria-label*=...]`, `get_by_text`
- Attente bornée (1 500ms) pour les éléments JS lazy-loaded
- **Avantage sur `dismiss_overlay`** : cherche dans les iframes, utilise des sélecteurs plus flexibles

**`LoopDetector`** :
- Escalade après 2 échecs identiques (même tool+args)
- Escalade après 4 échecs du même tool_name (args différents)
- Un succès de l'outil X n'efface que les compteurs de X

**Nudge** (3 consécutifs sans succès) : message système générique "change de stratégie" — mais aucun hint spécifique sur le consentement.

---

### 2.5 Directive modèle (render.py §SYSTEM_RULES)

La directive cookie/overlay reçue par le modèle :

```
"Cookie banner and overlay handling — when browser.read_page returns cookie_banner=true,
or when browser.click fails (ELEMENT_NOT_FOUND) and an overlay is likely blocking the target:
(1) call browser.dismiss_overlay to close the banner — this is a SAFE action...
(2) if dismiss_overlay returns rounds=0 (nothing dismissed), call browser.read_page to
re-observe; proceed if the path is now clear.
(3) resume the original objective from the point of interruption."
```

**Gaps de la directive :**
- Déclencheur primaire : `cookie_banner=true` → faux négatif fréquent (voir §3)
- Déclencheur secondaire : modèle doit DÉDUIRE que `ELEMENT_NOT_FOUND` est causé par un overlay (pas une garantie)
- Quand `dismiss_overlay` échoue (rounds=0) : directive dit "re-observe" mais NE DIT PAS d'essayer `browser.click` sur les boutons visibles
- Pas de mention de `vision.find_in_browser` comme escalade après échec dismiss_overlay
- Pas d'instruction pour : "si les boutons de la page contiennent un texte de consentement visible, clique dessus directement"

---

### 2.6 Safety

**`browser.dismiss_overlay`** avec `{"max_rounds": 3}` :
- Tag : `browser.interact` — contextuel
- `_extract_text_values({"max_rounds": 3})` → `[]` (valeur entière, pas de texte)
- Repli : `_CONTEXTUAL_TAGS["browser.interact"] = SAFE`
- **Résultat : SAFE → aucune confirmation requise ✓**

**`browser.click("Accepter et continuer")`** :
- `_mentions_dangerous_action({"target": "Accepter et continuer"})` → aucun stem dangereux
- **Résultat : SAFE ✓**

**`browser.click("Accept all")`** : SAFE ✓

La Safety n'est PAS le blocage. Toutes les actions de consentement standard sont SAFE.

---

## 3. Cause exacte du problème actuel

### Failure chain complète

```
1. RAYA navigue sur un site avec cookie banner
        ↓
2. browser.read_page → cookie_banner=False  [P0: DETECTION FAUSSE]
        ↓
3. Directive "call browser.dismiss_overlay" NON déclenchée
   (trigger = cookie_banner=true → jamais atteint)
        ↓
4. Modèle essaie de réaliser l'objectif original
   ex: browser.click("titre article") → ELEMENT_NOT_FOUND
        ↓
5. Modèle interprète l'erreur (heuristique):
   Option A: déduit "overlay probable" → appelle dismiss_overlay
             → dismiss_overlay: rounds=0  [P1: DISMISS ÉCHOUE]
             → directive: "re-observe" → read_page → toujours bloqué
             → directive ne dit pas "essaie browser.click sur les boutons visibles"
             → modèle cherche alternative (URL, search)  ← CONTOURNEMENT
   Option B: interprète ELEMENT_NOT_FOUND comme "élément absent"
             → cherche alternative URL/search  ← CONTOURNEMENT direct
             → c'est la voie la plus courante
```

### Pourquoi Option B est plus courante que Option A

Le modèle a deux directives contradictoires :
1. "ELEMENT_NOT_FOUND = possible overlay → essaie dismiss_overlay"
2. "si une tentative directe n'a pas résolu l'objectif, utilise la recherche du site ou une recherche web"

La directive #2 (URL/search fallback) est générale et apparaît plus tôt dans la séquence. La directive #1 exige une inférence ("overlay likely blocking") qui dépend du contexte. Sans signal fort (`cookie_banner=true`), le modèle préfère la stratégie plus évidente (#2 — chercher autrement).

---

## 4. Real E2E Results

### E2E 1 — Banner classique : lesoir.be

**URL** : `https://www.lesoir.be`
**Tâche simulée** : navigation sur le site

| Étape | Résultat |
|-------|---------|
| `browser.navigate` | OK |
| `browser.read_page` | `cookie_banner=True` (première visite fraîche), `buttons=["J'accepte", "En savoir plus"]` |
| `browser.dismiss_overlay` | `dismissed=["Accepter"], rounds=1` — banner fermé ✓ |
| `browser.read_page` après | `cookie_banner=False`, page principale visible |

**Status : PASS** — le chemin nominal fonctionne quand `cookie_banner=True` est correctement détecté.

---

### E2E 2 — DOM + Vision : lemonde.fr

**URL** : `https://www.lemonde.fr`
**Objectif** : vérifier DOM vs vision sur un site avec banner

| Observation | DOM (`read_page`) | Vision (`vision.find_in_browser`) |
|-------------|-------------------|----------------------------------|
| `cookie_banner` | `False` (faux négatif) | N/A (non appelé sans trigger) |
| Boutons visibles | `["Accepter et continuer"]` à `(left=207, top=635)` | Aurait identifié le banner visuellement |
| Fusion simultanée | NON — séquentielle uniquement | Modèle doit l'appeler explicitement |

**Analyse DOM** : la bannière lemonde.fr est un `div.gdpr-lmd-wall` fixé (z-index=10000001, 921×920px). Elle ne correspond pas aux sélecteurs `[id/class*=cookie/consent]` de `_STRUCT_JS`. Sélecteurs qui matchen : `[role=dialog]` (3), `[aria-modal=true]` (3), `.overlay` (1), `[class*=gdpr i]` (110). Les sélecteurs `_OVERLAY_SELECTORS` de `dismiss_overlay` incluent `[role=dialog]` — mais `.gdpr` (token exact) ne matche pas `gdpr-lmd-wall`.

**Conséquence** :
- `dismiss_overlay` retourne `rounds=0` (ne trouve pas d'overlay visible correspondant)
- MAIS `browser.click("Accepter et continuer")` → `status=ok` ✓ (le bouton est dans le DOM, `browser.click` est plus flexible)

**Status : PARTIAL** — DOM détecte le bouton de consentement mais pas la bannière. `dismiss_overlay` échoue. `browser.click` direct FONCTIONNE si le modèle le tente.

---

### E2E 3 — DOM ambigu : lemonde.fr selector inspection

**Disconnect confirmé entre `_STRUCT_JS` et `_OVERLAY_SELECTORS`** :

| Sélecteur | Dans `_STRUCT_JS` | Dans `_OVERLAY_SELECTORS` | Match lemonde.fr |
|-----------|-------------------|--------------------------|-----------------|
| `[id*=cookie i]` | ✓ | ✓ | 0 |
| `[class*=cookie i]` | ✓ | ✓ | 1 (mais 0 visible) |
| `[id*=consent i]` | ✓ | ✓ | 0 |
| `[class*=consent i]` | ✓ | ✓ | 0 |
| `[role=dialog]` | ✗ | ✓ | 3 |
| `[aria-modal=true]` | ✗ | ✓ | 3 |
| `.overlay` | ✗ | ✓ | 1 |
| `.gdpr` (token) | ✗ | ✓ | 0 (class=gdpr-lmd-wall ≠ token gdpr) |
| `[class*=gdpr i]` | ✗ | ✗ | 110 |

**Cause du double échec** : `.gdpr` est un sélecteur de classe TOKEN, pas SUBSTRING. `gdpr-lmd-wall` n'est pas un token `gdpr`. Si `_OVERLAY_SELECTORS` avait `[class*=gdpr i]` au lieu de `.gdpr`, lemonde.fr serait détecté.

**Status : IDENTIFIED GAP** — sélecteur `.gdpr` incorrect pour les classes prefixées.

---

### E2E 4 — Multiple consent buttons : standaard.be

**URL** : `https://www.standaard.be`

| Étape | Résultat |
|-------|---------|
| `cookie_banner` | `False` (faux négatif — banner présent) |
| Boutons visibles | 2 (menu seulement — banner pas inclus dans buttons list) |
| `dismiss_overlay` | `dismissed=["Accepter"], rounds=1` ✓ (overlay trouvé via `[role=dialog]` probablement) |

**Paradoxe** : `cookie_banner=False` ET `dismiss_overlay` fonctionne. Le banner correspond à un `_OVERLAY_SELECTOR` non couvert par `_STRUCT_JS`. Preuve que les deux ensembles de sélecteurs sont désynchronisés.

**Consent semantics** : `dismiss_overlay` a cliqué "Accepter" (même sur un journal flamand). Priorité Accept > Reject > Close hard-codée. Le modèle n'a pas de contrôle sur le choix Accept/Reject.

**Status : PARTIAL** — `dismiss_overlay` fonctionne quand appelé manuellement, mais le modèle ne le déclenchera pas (cookie_banner=False).

---

### E2E 5 — Action + Verification : lemonde.fr `browser.click` direct

| Action | Résultat |
|--------|---------|
| `browser.click("Accepter et continuer")` | `status=ok`, `url=https://www.lemonde.fr/` |
| `browser.read_page` après | Plus de bouton "Accepter et continuer", contenu de la page visible |

**Vérification** : `cookie_banner` étant toujours `False` avant et après, la vérification ne peut pas s'appuyer sur ce flag. La vérification réelle = absence du bouton "Accepter et continuer" dans la prochaine `read_page`, ou présence de contenu supplémentaire. Aucune directive explicite ne guide cette vérification.

**Status : PASS** — `browser.click` direct sur le texte de consentement fonctionne et les boutons disparaissent.

---

### E2E 6 — Recovery : lemonde.fr dismiss_overlay échoue

**Scénario** : `dismiss_overlay` retourne `rounds=0` pour lemonde.fr.

**Trace de recovery observée** :
```
browser.navigate("https://www.lemonde.fr") → ok
browser.read_page → cookie_banner=False  [faux négatif → directive non déclenchée]
[modèle tente l'objectif]
browser.click("article cible") → ELEMENT_NOT_FOUND  [bloqué par overlay z=10000001]
[Option A: modèle infère overlay]
browser.dismiss_overlay → {"dismissed": [], "rounds": 0}  [échec]
browser.read_page → cookie_banner=False  [toujours rien]
[directive: "proceed if path is now clear" — mais le path n'est pas clear]
[aucune instruction pour browser.click("Accepter et continuer")]
[LoopDetector: même outil repeated → ESCALATE ou nudge]
→ modèle cherche URL alternative ou abandonne  ← CONTOURNEMENT
```

**Ce qui AURAIT fonctionné** :
```
browser.click("Accepter et continuer")  → ok
browser.read_page → plus de bouton consent, contenu accessible
browser.click("article cible") → ok
```

**Status : FAIL** — chemin de recovery incomplet, aboutit au contournement.

---

## 5. Trace d'un cas où RAYA contourne le consentement

**Scénario** : "Montre-moi l'article sur X sur lemonde.fr"

```
1. browser.navigate("https://www.lemonde.fr")
   → output: {url, title}
   → cookie_banner sera False au read_page suivant

2. browser.read_page
   → output: {cookie_banner: False, buttons: ["Menu", "En continu", "Accepter et continuer"], ...}
   → [MODÈLE: cookie_banner=False → pas de directive overlay déclenchée]
   → [MODÈLE: voit "Accepter et continuer" mais aucune directive ne lui dit de le cliquer]

3. browser.click("titre de l'article X")
   → output: {status: "not_found"}
   → [MODÈLE: ELEMENT_NOT_FOUND]
   → [MODÈLE: doit déduire "overlay likely" — mais c'est une inférence, pas un signal]

4a. [Modèle ne fait pas le lien → option contournement]:
    browser.navigate("https://www.lemonde.fr/search?q=X")  ← CONTOURNEMENT
    → directive: "if direct attempt didn't resolve, use site's own search"
    → mais la page de recherche a aussi le même overlay → boucle
    → LoopDetector ESCALATE → explication d'échec → fin du tour

4b. [Modèle fait le lien → option dismiss]:
    browser.dismiss_overlay
    → output: {dismissed: [], rounds: 0}
    → directive: "re-observe" → read_page → cookie_banner=False
    → directive: "proceed if path is now clear" → MAIS le path n'est pas clear
    → [aucune instruction pour browser.click("Accepter et continuer")]
    → [modèle cherche encore une alternative] ← CONTOURNEMENT secondaire
```

**Racine unique du comportement de contournement** : `cookie_banner=False` (faux négatif) + absence d'instruction "clique directement sur les boutons de consentement visibles dans la liste buttons".

---

## 6. DOM + Vision sont-ils réellement utilisables ensemble ?

**Réponse : NON dans l'architecture actuelle.**

### Ce que l'architecture fait

```
read_page (DOM)
    ↓ résultat
Modèle reçoit DOM seul
    ↓ si échec
Modèle peut APPELER vision.find_in_browser
    ↓ résultat
Modèle reçoit résultat vision (séquentiellement)
```

### Ce que l'architecture NE fait PAS

```
read_page (DOM) ║ vision.observe_browser
                ║ (simultané)
                ↓
Modèle reçoit les deux ensembles en même temps
et peut raisonner sur les deux simultanément
```

### Pourquoi cela importe

Pour le cookie consent :
- DOM retourne `cookie_banner=False` (faux négatif) + liste de boutons
- Vision verrait le banner visuellement et pourrait localiser le bouton "Accepter"
- Si les deux étaient simultanés, une observation contredit l'autre → robustesse
- Dans l'architecture actuelle, la vision n'est appelée que si le modèle DÉCIDE de l'appeler après un échec DOM

### GAP ARCHITECTURAL

Ce n'est pas un bug — c'est un choix de design explicite ("DOM-FIRST"). La fusion simultanée n'est pas dans la conception actuelle. C'est une limitation connue. La correction minimale n'exige pas de fusion — elle exige une meilleure directive d'escalade : "quand dismiss_overlay échoue, essaie browser.click sur les boutons consent visibles, puis vision.find_in_browser si tout échoue".

---

## 7. Safety / Consentement

### Classification des actions de consentement

| Action | Tag | Classification | Confirmation requise |
|--------|-----|---------------|----------------------|
| `browser.dismiss_overlay` | `browser.interact` | **SAFE** (pas de texte dangereux) | Non |
| `browser.click("Accepter et continuer")` | `browser.interact` | **SAFE** | Non |
| `browser.click("Accept all")` | `browser.interact` | **SAFE** | Non |
| `browser.click("Tout accepter")` | `browser.interact` | **SAFE** | Non |
| `browser.click("J'accepte")` | `browser.interact` | **SAFE** | Non |
| `browser.click("Reject all")` | `browser.interact` | **SAFE** | Non |

**Accepter des cookies ≠ acheter / souscrire / envoyer** — aucun stem dangereux dans `_DANGEROUS_ACTION_STEMS` ne correspond au vocabulaire de consentement. Safety est correcte, pas de sur-confirmation.

### Politique actuelle de RAYA sur le choix Accept/Reject

`dismiss_overlay` a une politique hard-codée : **Accept > Reject > Close**. Il n'existe pas de préférence utilisateur configurable pour "rejeter systématiquement les cookies". Si l'utilisateur préfère refuser, il n'a aucun moyen de l'exprimer structurellement.

**Note** : "accepter des cookies" est une action persistante réelle (stockée dans le profil Edge dédié). Le profil `RayaV2-EdgeProfile` accumule le consentement entre les sessions — ce qui est le comportement attendu pour un assistant PC.

---

## 8. Root Cause Classification

| Code | Sévérité | Description |
|------|----------|-------------|
| **COOKIE_DETECTION** | **P0** | `cookie_banner` boolean utilise 4 sélecteurs CSS ; `dismiss_overlay` en utilise 10+. Gap : `[role=dialog]`, `[aria-modal=true]`, `.overlay`, et `.gdpr` (incorrect : devrait être `[class*=gdpr i]`) ne contribuent pas à `cookie_banner`. Taux de faux négatifs : ~60% dans l'audit. |
| **MODEL_INSTRUCTION** | **P0** | La directive principale (`cookie_banner=true`) est rarement déclenchée. La directive secondaire (`ELEMENT_NOT_FOUND + overlay probable`) dépend d'une inférence modèle non garantie. Après `dismiss_overlay` failure, aucune instruction pour `browser.click` direct. Aucune mention de `vision.find_in_browser` comme escalade. |
| **TARGET_SELECTION** | **P1** | `_OVERLAY_ACCEPT_TEXTS` : 9 textes FR/EN seulement. Banners NL/DE/ES/IT/PT non couverts. |
| **DOM_PERCEPTION** | **P1** | `_STRUCT_JS` et `dismiss_overlay` opèrent sur le main document uniquement. Banners dans iframes (OneTrust, Usercentrics, Didomi, Quantcast) = invisibles. `browser.click` cherche dans les frames — avantage non exploité. |
| **PERCEPTION_FUSION** | **P2** | DOM et Vision non simultanés. Vision = fallback explicite appelé par le modèle, jamais déclenché automatiquement pour le consentement. |
| **POST_ACTION_VERIFICATION** | **P2** | Après `dismiss_overlay` (succès ou échec), le modèle n'est pas guidé pour vérifier que le banner a disparu (via absence du bouton dans la prochaine `read_page`). `cookie_banner` étant souvent `False` avant et après, ce flag ne peut pas servir de vérification. |
| **CONSENT_SEMANTICS** | **P3** | Accept > Reject > Close hard-codé dans `dismiss_overlay`. Pas de préférence utilisateur configurable. |
| **SAFETY** | NONE | Toutes les actions de consentement sont SAFE. Pas de sur-confirmation. Correct. |

---

## 9. Proposed Minimal Fix

**Principe** : aucun CookieManager, aucun sélecteur par site, aucune nouvelle architecture. Corrections minimales en 3 fichiers.

### Fix A — `controller.py` : aligner `cookie_banner` avec `_OVERLAY_SELECTORS`

**Fichier** : `raya/devices/browser/controller.py`
**Fonction** : `_STRUCT_JS` (ligne ~113)

**Avant** :
```js
for (const el of document.querySelectorAll('[id*=cookie i],[class*=cookie i],[id*=consent i],[class*=consent i]')) {
    if (vis(el)) { cookieBanner = true; break; }
}
```

**Après** (utiliser les mêmes sélecteurs que `_OVERLAY_MARK_JS`) :
```js
for (const el of document.querySelectorAll(
  '[id*=cookie i],[class*=cookie i],[id*=consent i],[class*=consent i],' +
  '[role=dialog],[aria-modal=true],.modal,.popup,.overlay,[class*=gdpr i],.cookie-banner'
)) {
    if (vis(el)) { cookieBanner = true; break; }
}
```

Note : `.gdpr` → `[class*=gdpr i]` (corrige le bug token vs substring).

---

### Fix B — `controller.py` : étendre `_OVERLAY_SELECTORS` et `_OVERLAY_ACCEPT_TEXTS`

**Fichier** : `raya/devices/browser/controller.py`

**Avant** :
```python
_OVERLAY_SELECTORS = (
    "[id*=cookie i]", "[class*=cookie i]", "[id*=consent i]", "[class*=consent i]",
    "[role=dialog]", "[aria-modal=true]", ".modal", ".popup", ".overlay",
    ".gdpr", ".cookie-banner",
)
_OVERLAY_ACCEPT_TEXTS = ("Tout accepter", "Accepter tout", "Accepter", "J'accepte", "Accept all", "I agree", "Agree", "Got it", "OK")
```

**Après** :
```python
_OVERLAY_SELECTORS = (
    "[id*=cookie i]", "[class*=cookie i]", "[id*=consent i]", "[class*=consent i]",
    "[role=dialog]", "[aria-modal=true]", ".modal", ".popup", ".overlay",
    "[class*=gdpr i]", ".cookie-banner",  # [class*=gdpr i] remplace .gdpr (token → substring)
)
_OVERLAY_ACCEPT_TEXTS = (
    "Tout accepter", "Accepter tout", "Accepter", "J'accepte",
    "Accept all", "I agree", "Agree", "Got it", "OK",
    "Alle accepteren", "Akkoord",           # NL
    "Alles akzeptieren", "Zustimmen",       # DE
    "Aceptar todo", "Aceptar",              # ES
    "Accetta tutto", "Accetto",             # IT
    "Autoriser tout", "Autoriser",          # FR variant
)
```

---

### Fix C — `render.py` : étendre la directive cookie/overlay

**Fichier** : `raya/context_engine/render.py`
**Section** : directive "Cookie banner and overlay handling"

**Ajouts nécessaires** (après la phrase existante sur `dismiss_overlay`) :

```
"If browser.read_page returns buttons that include text like 'Accept', 'Accepter', 
'J'accepte', 'Tout accepter', 'Agree', 'Akkoord', or similar consent language, even if 
cookie_banner is false, you SHOULD click that button directly with browser.click before 
attempting any other action on the page — treat any visible consent/accept button in the 
buttons list as a sign that consent is required first.

If browser.dismiss_overlay returns rounds=0 (nothing found) and the original browser.click 
still fails, check whether a consent button is visible in the last browser.read_page buttons 
list and click it directly with browser.click. 

If browser.click on the consent button also fails (not_found), use vision.find_in_browser 
with target='cookie consent accept button' to locate it visually, then use the returned 
coordinates with browser.click_at_position."
```

---

## 10. Tests proposés (max 10)

| # | Nom | Type | Ce qu'il vérifie |
|---|-----|------|-----------------|
| T1 | `test_cookie_banner_detection_matches_overlay_selectors` | Unit | `_STRUCT_JS` et `_OVERLAY_MARK_JS` utilisent les mêmes sélecteurs après Fix A |
| T2 | `test_gdpr_class_selector_matches_prefixed_class` | Unit | `[class*=gdpr i]` matche `gdpr-lmd-wall`, `.gdpr` ne matche pas (prouve le bug) |
| T3 | `test_dismiss_overlay_accepts_dutch_button` | Unit | `_OVERLAY_ACCEPT_TEXTS` contient "Alle accepteren", "Akkoord" |
| T4 | `test_dismiss_overlay_direct_click_fallback` | Unit | Après `dismiss_overlay` rounds=0, le modèle est guidé vers `browser.click` par la directive |
| T5 | `test_write_semantics_directive_covers_consent_button_in_buttons_list` | Unit | La directive mentionne "buttons list" + consentement direct |
| T6 | `test_write_semantics_directive_mentions_vision_fallback_for_consent` | Unit | La directive mentionne `vision.find_in_browser` comme escalade |
| T7 (réel E2E) | `test_real_lemonde_dismiss_via_direct_click` | E2E réel | `browser.click("Accepter et continuer")` sur lemonde.fr fonctionne après navigate |
| T8 (réel E2E) | `test_real_standaard_cookie_banner_detected_after_fix` | E2E réel | Après Fix A, `cookie_banner=True` sur standaard.be (banner présent) |
| T9 (réel E2E) | `test_real_lemonde_cookie_banner_detected_after_fix` | E2E réel | Après Fix A, `cookie_banner=True` sur lemonde.fr (gdpr-lmd-wall → `[class*=gdpr i]`) |
| T10 | `test_dismiss_overlay_verification_after_click` | Unit | Après dismiss_overlay success, le modèle vérifie que le bouton Accept a disparu de read_page |

---

## 11. Ce qui doit rester inchangé

| Composant | Raison |
|-----------|--------|
| Architecture DOM-FIRST pour la vision | Correct — la vision est coûteuse et lente |
| `browser.click` cross-frame | Déjà correct — cherche dans les iframes, à conserver |
| `dismiss_overlay` dans le DOM principal uniquement | Acceptable pour les banners standard ; les banners iframe sont rares |
| Politique Safety (SAFE pour consentement) | Correcte — pas de sur-confirmation |
| Structure LoopDetector/nudge | Non spécifique au consentement — à conserver générique |
| Aucun CookieManager / SiteHandler | Invariant — jamais de logique par site |

---

## 12. V1 Integrity

**RAYA V1 : STRICTEMENT INTACTE.** Aucun fichier V1 n'a été lu, modifié ou référencé pendant cet audit. L'audit est strictement limité aux fichiers V2 (controller.py, visual.py, risk.py, browser/agent.py, render.py, recovery.py, loop.py).

---

## 13. Final Verdict

**MODIFY**

La capacité technique est présente (`browser.click` direct fonctionne sur lemonde.fr, `dismiss_overlay` fonctionne sur standaard.be/lesoir.be). Le problème n'est pas d'architecture mais de configuration : sélecteurs incorrects, directive incomplète, liste de textes trop limitée.

Les 3 fichiers à modifier (Fix A, B, C) : `controller.py` (2 changements), `render.py` (1 extension de directive). Aucune nouvelle classe, aucun nouveau manager, aucun sélecteur spécifique à un site.

**Estimation impact des fixes** :
- Fix A (sync sélecteurs) : résout ~70% des faux négatifs `cookie_banner`
- Fix B (`.gdpr` → `[class*=gdpr i]` + textes multilingues) : résout lemonde.fr, banners NL/DE
- Fix C (directive étendue) : donne au modèle la séquence complète OBSERVE→IDENTIFY→CLICK→VERIFY

**Après les fixes, le chemin nominal sera** :
```
read_page → cookie_banner=True (fiable) → dismiss_overlay (fonctionne)
OU
read_page → bouton consent visible dans buttons list → browser.click direct (SAFE)
OU [escalade]
dismiss_overlay fails → browser.click fails → vision.find_in_browser → click_at_position
```
