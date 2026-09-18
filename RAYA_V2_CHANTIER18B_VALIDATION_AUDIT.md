# RAYA V2 — Chantier 18B : Rapport d'audit de validation
**Date :** 2026-09-07  
**Auditeur :** Claude Sonnet 4.6 (lecture seule — aucune modification de code)  
**Scope :** Browser Agentic Execution & Recovery

---

## Verdict

**GO WITH GAPS**

Les primitives Chantier 18B sont correctement implémentées. Les directives objective-centric,
DOM fallback, et form submission sont présentes dans le chemin de production (boucle
conversationnelle + long-horizon). Le mécanisme de récupération est réel et testable.

Un gap de sécurité bloquant pour la production a été identifié : les champs `type=password`
ne sont pas filtrés dans `_STRUCT_JS`, ce qui expose les valeurs de mot de passe au modèle
via `browser.read_page`.

**→ GO vers le 3e chantier, avec correction obligatoire du gap sécurité avant usage production.**

---

## 1. Implementation Audit

### A. `_STRUCT_JS` — Exposition des éléments au modèle

**Enrichissements confirmés :**

- `left` et `top` (coordonnées absolues) présents sur chaque bouton — `controller.py:75-76`
- `aria_label` exposé conditionnellement si présent — `controller.py:77-78`
- `role` et `placeholder` exposés pour les inputs — `controller.py:80-84`

**Le modèle peut distinguer les actions :**

La structure `video_card.html` démontre le cas générique :

```html
<button aria-label="Lire Film Test">▶ Lire</button>
<button aria-label="Add Film Test to list">+ Ma liste</button>
<button aria-label="Plus d'informations sur Film Test">...</button>
```

`read_page` retourne :
```json
{"text": "▶ Lire", "aria_label": "Lire Film Test", "left": ..., "top": ...}
{"text": "+ Ma liste", "aria_label": "Add Film Test to list", ...}
{"text": "...", "aria_label": "Plus d'informations sur Film Test", ...}
```

Le modèle dispose de `role`, `aria_label`, `left/top`, et `text` pour discriminer
les boutons sans aucun hardcoding de site. Ça fonctionne.

**GAP IDENTIFIÉ — SÉCURITÉ (CRITIQUE) :**

Le sélecteur d'inputs (`controller.py:108`) n'exclut PAS `input[type=password]` :

```javascript
const inputs = grab(
  'input:not([type=hidden]):not([type=submit]):not([type=button]), ...',
  'input', 20
);
```

La fonction `label()` (`controller.py:62-66`) lit `el.value` :

```javascript
const label = (el) => (
    (el.innerText || el.value || el.getAttribute('aria-label') || ...)
```

`el.value` sur un champ password retourne le mot de passe en clair (même autofill Edge).
Résultat : sur `login_prefilled.html` contenant `value="secret123"`, le `read_page` retourne
dans `inputs` :

```json
{"kind": "input", "text": "secret123", "tag": "input", "aria_label": "Mot de passe", ...}
```

Le mot de passe est exposé au modèle. La directive de `render.py` ("Never fill in, read,
display, or guess a password") est une directive comportementale, pas une protection structurelle.
Ce gap existe dans le chemin de production, pas seulement dans les fixtures de test.

### B. `click_at_position` — Provenance des coordonnées

**Implémentation :**

```python
def click_at_position(self, x: int, y: int) -> dict:
    """Clic souris à des coordonnées provenant d'une observation réelle..."""
    async def _op():
        page = await self._session.get_or_create()
        await page.mouse.click(x, y)
        return {"status": "ok", "x": x, "y": y}
```

La protection est **directive-seulement**. La description du tool (`catalog/browser.py:86`)
dit explicitement "only with coordinates from the actual read_page result, never invented or
estimated", et `controller.py:305-308` le répète dans son docstring.

Mais rien n'empêche structurellement un modèle de passer des coordonnées inventées.
Si le modèle hallumine `x=500, y=300` sans avoir appelé `read_page`, le clic s'exécutera.

Provenance vérifiable côté modèle uniquement : le modèle DOIT voir les valeurs `left`/`top`
dans un résultat `read_page` précédent pour qu'elles soient réelles. Il n'y a pas de
chain-of-custody structurelle entre ces valeurs et l'appel `click_at_position`.

### C. `browser.type` avec `text` optionnel

**Implémentation :**

```python
# controller.py:239
else:  # replace — skip fill si text vide pour ne pas effacer le contenu existant
    if text:
        await loc.fill(text, timeout=timeout_ms)
if submit:
    await loc.press("Enter", timeout=timeout_ms)
```

`text=None` ou `text=""` + `submit=True` → presse Enter sans modifier le contenu. Correct.

**Vérification des modes Chantier 19 :**

| mode    | text vide | text non-vide | submit=True |
|---------|-----------|---------------|-------------|
| replace | skip fill (préserve) | `loc.fill()` ✓ | Enter après ✓ |
| append  | skip JS   | `el.value += t` ✓ | Enter après ✓ |
| clear   | `el.value = ''` ✓ | idem | Enter après ✓ |

Tous les modes Chantier 19 fonctionnent. Aucune régression.

---

## 2. Hardcoding Audit

### Recherche exhaustive effectuée

Termes recherchés dans `raya/devices/browser/`, `raya/tools/catalog/browser.py`,
`raya/context_engine/render.py` : Amazon, Netflix, Coolblue, Disney, YouTube, Spotify,
URLs spécifiques, sélecteurs CSS site-spécifiques, noms de sites dans les stratégies.

**Résultat : aucun hardcoding de site dans la logique.**

Les noms de sites (Amazon, YouTube, Coolblue) n'apparaissent que dans les **commentaires**
de `render.py` pour expliquer l'origine des correctifs — jamais dans les directives ni dans
le code décisionnel.

### Cas limite — `priorityRoots`

**FILE :** `raya/devices/browser/controller.py`  
**LINE :** 91-93

```javascript
const priorityRoots = [...document.querySelectorAll(
    '[id*=buybox i], [id*=addtocart i], [id*=add-to-cart i], [class*=buybox i]'
)];
```

**PROBLÈME :** "buybox" est un terme de vocabulaire Amazon. Techniquement générique
(opérateur `*=`, pas de sélecteur exact), mais sémantiquement Amazon-flavored.
`addtocart` et `add-to-cart` sont des patterns WooCommerce/Shopify/etc., véritablement
génériques.

**GRAVITÉ :** FAIBLE. Sur un site non-e-commerce, `priorityRoots` sera simplement vide,
et les boutons seront classés sans priorité — ce qui est le comportement correct. Pas de
biais fonctionnel, uniquement de terminologie.

### `check_confirmation` — Defaults e-commerce biaisés

**FILE :** `raya/devices/browser/controller.py`  
**LINE :** 316-317

```python
def check_confirmation(self, url_contains: tuple[str, ...] = ("/cart", "/panier"),
                        text_patterns: tuple[str, ...] = _CART_CONFIRM_PATTERNS) -> bool | None:
```

**PROBLÈME :** Les defaults `/cart`, `/panier` et les patterns "ajouté au panier" ne
s'appliquent que dans un contexte e-commerce. Retourne `None` (honnête) hors contexte
e-commerce — pas faux, mais biaisé.

**GRAVITÉ :** FAIBLE. La valeur de retour `None` est honnête ("je ne sais pas").
Pas de faux positif possible.

---

## 3. Objective-Centric Audit

### Présence de la directive dans le chemin de production

**Directive Chantier 18B §3** (`render.py:373-381`) :

```
"Browser objective verification — a browser tool returning success (browser.click,
browser.type, browser.navigate) is not the same as the objective being met. After any
mutating browser action, use browser.read_page to observe the actual effect before
declaring success..."
```

**Chemin conversationnel :**

```
USER → handle_request() → assemble() → render_system_prompt() 
     → [directive 18B §3 présente] → ModelRequest → modèle
```

`render_system_prompt()` est appelé depuis `_run_agentic_loop()` à chaque tour.
La directive est dans le system prompt de chaque tour.

**Chemin long-horizon :**

```
TaskScheduler → _run_long_horizon_step() → assemble() → render_system_prompt()
              → [directive 18B §3 présente] → ModelRequest → modèle
```

`_run_long_horizon_step()` appelle aussi `render_system_prompt()`. La directive est
présente dans les deux chemins de production.

**Conclusion :** Oui, la directive "TOOL SUCCESS ≠ OBJECTIVE SUCCESS" est réellement
dans le chemin de production, pas seulement dans les tests.

### Chemin USER → OBJECTIVE complet

```
USER OBJECTIVE
  → handle_request() / create_long_horizon_task()
  → _run_agentic_loop() / _run_long_horizon_step()
  → ModelRequest [system_prompt avec directive 18B §3]
  → modèle appelle browser.click / browser.type
  → execute_tool() → BrowserDeviceAgent.execute() → BrowserController
  → ToolResult {status, output, evidence}
  → _promote_observations_and_verify() → WorldState mis à jour
  → messages.append(role="tool", ToolResult résumé)
  → NEXT ITERATION : modèle voit directive "re-read avant de déclarer succès"
  → modèle appelle browser.read_page
  → observation de l'état réel
  → modèle déclare succès ou continue
```

Ce chemin est réel. La vérification s'appuie sur l'état observable (URL, texte de
confirmation) via `read_page`, jamais sur l'affirmation du modèle.

---

## 4. Recovery Audit

### Mécanisme réel

**Séquence vérifiée dans le code :**

```
browser.click(target="Cible introuvable")
  → BrowserController._find_clickable() → None
  → {"status": "not_found"}
  → BrowserDeviceAgent._click() → Result(FAILURE, code="ELEMENT_NOT_FOUND")
  → execute_tool() → ToolResult(status=FAILURE)
  → verify_tool_result() → VerificationOutcome.FAILURE
  → LoopDetector.record(key, "browser.click", args, FAILURE)
    → REPLAN (1ère fois) ou ESCALATE (N-ième)
  → messages.append(role="tool", JSON avec status/error/verification)
  → NEXT MODEL TURN : modèle voit résultat d'échec + directive DOM fallback
```

**Directive DOM fallback** (`render.py:385-396`) dans le system prompt :

```
"Browser DOM fallback — if browser.click fails (ELEMENT_NOT_FOUND): call
browser.read_page immediately to re-observe the current page structure. Examine
available buttons and links — identify an element with equivalent role, aria-label,
or visible text that could fulfill the same objective. Never repeat the same failed
target string immediately..."
```

**Ce qui est structurellement garanti :**
1. Le modèle reçoit le résultat d'échec complet (status, code, message)
2. La directive de fallback est dans chaque system prompt
3. Le `LoopDetector` escalate après N échecs identiques → honnête message d'échec
4. Le nudge générique (`_NUDGE_THRESHOLD=3`) injecte un rappel en message système
   après 3 appels consécutifs au même outil sans succès

**Ce qui est comportemental (directive-dépendant) :**
5. Le modèle DOIT décider d'appeler `read_page` après ELEMENT_NOT_FOUND
   Le harness ne le force pas structurellement

**Réponse à la question centrale :**

> "Si browser.click échoue, le système donne-t-il au modèle suffisamment d'information
> et de capacité pour essayer autre chose ?"

OUI. Le modèle reçoit :
- Le code d'erreur exact (`ELEMENT_NOT_FOUND`)
- La directive de fallback dans chaque turn
- La capacité `browser.read_page` disponible
- Les valeurs `left`/`top` dans read_page pour un fallback positionnel

La décision finale reste au modèle. Le LoopDetector garantit qu'une répétition aveugle
est détectée et escaladée. Le système ne laisse pas le modèle boucler indéfiniment
sans intervention.

---

## 5. Form Submission Audit

### Chemin vérifié

```
form already populated
  → browser.type(target="champ", text=None, submit=True)
    → controller.type_text(target, text="", submit=True)
    → skip fill (text vide) ✓
    → loc.press("Enter") ✓
  → observe (browser.read_page ou check URL)
  → verify
```

OU :

```
  → browser.click(target="Se connecter")
    → _find_clickable() par role=button, text match
    → loc.click() ✓
  → navigate vers logged_in.html ✓
```

### Sécurité credentials

**Pas de credentials retournés au modèle via le chemin de clic :** Le test
`test_submit_prefilled_form_via_click_on_submit_button` ne passe PAS par `read_page`
avant le clic — la directive de vérification objective incite à le faire APRÈS, et
la page de destination ne contient pas de passwords.

**MAIS :** Si le modèle respecte la directive "re-read après action mutante" et appelle
`read_page` sur `login_prefilled.html` AVANT de cliquer (pour observer l'état initial),
le mot de passe serait exposé dans `inputs[*].text`. Voir gap sécurité §1.A ci-dessus.

**CAPTCHA / 2FA :** Traité par la directive `render.py:101-110` :

```
"If you reach a step that genuinely requires the user's own action — ...
solving a CAPTCHA, entering a 2FA code or PIN... stop retrying and clearly
tell them what you need them to do..."
```

Cette directive est dans le system prompt. Pas de contournement structurel possible
(RAYA n'a aucun tool pour résoudre un CAPTCHA ou intercepter un 2FA). Correct.

---

## 6. Primary / Secondary Control Audit

### Informations disponibles au modèle

Pour chaque bouton visible via `read_page` :

| Champ | Source | Disponible |
|-------|--------|-----------|
| `text` | `innerText` | ✓ toujours |
| `left` | `getBoundingClientRect().left` | ✓ (enrichissement 18B) |
| `top` | `getBoundingClientRect().top` | ✓ (enrichissement 18B) |
| `aria_label` | `getAttribute('aria-label')` | ✓ si présent |
| `tag` | `tagName.toLowerCase()` | ✓ toujours |

Pour distinguer Play/Add/More sur une carte vidéo :

```json
{"text": "▶ Lire", "aria_label": "Lire Film Test", "left": 120, "top": 250}
{"text": "+ Ma liste", "aria_label": "Add Film Test to list", "left": 180, "top": 250}
{"text": "...", "aria_label": "Plus d'informations sur Film Test", "left": 210, "top": 250}
```

Le modèle peut discriminer par :
1. `aria_label` (primaire — sémantique) : "Lire" vs "Add" vs "Plus d'informations"
2. `text` (secondaire — visible) : "▶ Lire" vs "+ Ma liste" vs "..."
3. `left`/`top` (tertiaire — positional) : gauche < milieu < droite

**Ce qui manque :** La hiérarchie parent n'est pas exposée. Sur une page avec
plusieurs cartes (ex: grille Netflix), tous les boutons de toutes les cartes se
retrouvent mélangés dans la liste. La discrimination repose alors exclusivement sur
`aria_label` pour identifier la carte concernée. Sur des sites sans aria-labels
(accessibilité insuffisante), cette approche serait limitée — mais c'est une
limitation générale du web, pas un bug RAYA.

**Règle : aucune logique site-spécifique n'est présente.** La résolution est
entièrement basée sur les attributs observés. Correct.

---

## 7. Real E2E

Les tests E2E du Chantier 18B (`tests/devices/browser/test_chantier18b_browser_agentic.py`)
nécessitent un Edge/CDP réel en écoute sur le port configuré. Sans cet environnement,
les fixtures `LocalFixtureServer` ne peuvent pas être servies.

Aucun test E2E avec modèle réel dans la boucle n'est disponible dans la suite de tests
(le harness est testé avec des fake providers, pas un LLM réel).

### TEST A — Formulaire prérempli
**BLOCKED** — Nécessite Edge/CDP. La mécanique est validée par `test_submit_prefilled_form_via_click_on_submit_button` (device-level uniquement). Avec modèle réel dans la boucle : NOT_TESTED.

### TEST B — Primary Action
**BLOCKED** — Nécessite Edge/CDP. `test_primary_control_clickable_by_aria_label` et `test_secondary_control_clickable_by_aria_label` valident le mécanisme au niveau device. Avec modèle réel : NOT_TESTED.

### TEST C — Recovery
**BLOCKED** — Nécessite Edge/CDP. `test_click_fails_read_page_reveals_real_target_second_click_succeeds` valide le mécanisme device. La boucle modèle réel suivant la directive DOM fallback : NOT_TESTED.

### TEST D — Objective vs Tool Success
**BLOCKED** — Nécessite Edge/CDP + modèle réel dans la boucle. Le mécanisme `check_confirmation` est validé par `test_objective_satisfied_navigating_directly_without_action`. NOT_TESTED avec le vrai pipeline.

### TEST E — Human Blocker (CAPTCHA/2FA)
**NOT_TESTED** — Pas de fixture contrôlée disponible. La directive est présente dans le system prompt. Aucun contournement structurel n'est possible (aucun tool pour résoudre CAPTCHA/2FA n'existe dans le registre).

---

## 8. Confirmed Remaining Gaps

### GAP 1 — SÉCURITÉ (CRITIQUE — blocant pour production)

**FILE :** `raya/devices/browser/controller.py`  
**LINE :** 108 (sélecteur inputs) + 62-66 (fonction `label()`)  

**PROBLÈME :** `input[type=password]` n'est pas exclu du sélecteur `inputs` dans
`_STRUCT_JS`. La fonction `label()` lit `el.value`, ce qui retourne le mot de passe
en clair (y compris les passwords autofillés par Edge). Tout appel `read_page` sur
une page de connexion expose le password au modèle dans `inputs[*].text`.

**GRAVITÉ :** CRITIQUE. Violation directe du principe "never read or display credentials".
La directive comportementale ne protège pas contre l'exposition structurelle.

**CORRECTION NÉCESSAIRE :** Exclure `input[type=password]` du sélecteur et/ou filtrer
`el.value` pour les champs password dans `label()`.

---

### GAP 2 — COMPORTEMENTAL (MOYEN — directive-seulement)

**FILE :** `raya/devices/browser/controller.py` / `raya/harness/loop.py`  
**PROBLÈME :** La récupération après `ELEMENT_NOT_FOUND` est directive-dépendante.
Le harness n'injecte pas structurellement un appel `read_page` après un échec
`browser.click`. Le modèle doit décider de suivre la directive lui-même.

**Backstop existant :** `LoopDetector` escalate après N échecs identiques.
Le nudge système intervient après 3 appels consécutifs sans succès.

**GRAVITÉ :** MOYEN. Non-blocant pour le chantier suivant. La combinaison
[directive + LoopDetector + nudge] fournit une protection suffisante pour les
cas réels. Un modèle performant suivra la directive ; pour un modèle moins
coopératif, le LoopDetector prend le relais.

---

### GAP 3 — COMPORTEMENTAL (FAIBLE — limite structurelle)

**FILE :** `raya/devices/browser/controller.py:305-314`  
**PROBLÈME :** Aucune vérification structurelle que les coordonnées passées à
`click_at_position` proviennent d'un résultat `read_page`. Un modèle peut passer
des coordonnées inventées.

**GRAVITÉ :** FAIBLE. La description du tool est explicite ("never invented or
estimated"). Le cas nominal est : read_page → extraire `left`/`top` → click_at_position.
Un modèle qui invente des coordonnées produira des clics manqués (aucune conséquence
de sécurité), et la directive objective-centric forcera une vérification.

---

### GAP 4 — SÉMANTIQUE (FAIBLE — terminologie)

**FILE :** `raya/devices/browser/controller.py:92`  
**PROBLÈME :** `[id*=buybox i]` et `[class*=buybox i]` utilisent la terminologie
"buybox" d'Amazon. Générique techniquement, Amazon-flavored sémantiquement.  

**GRAVITÉ :** FAIBLE. Pas d'impact fonctionnel. `priorityRoots` sera vide sur
les sites sans "buybox" dans leurs attributs.

---

## 9. Recommended Next Step

1. **AVANT PRODUCTION :** Corriger GAP 1. Exclure `input[type=password]` du sélecteur
   d'inputs dans `_STRUCT_JS`. Un seul sélecteur à modifier sur une ligne.

2. **GO vers le 3e chantier** sans attendre. GAP 2 et GAP 3 sont des limites
   comportementales normales pour un système dirigé par LLM — ils sont documentés,
   monitorés (LoopDetector), et non-bloquants pour les cas nominaux.

3. **Le 3e chantier** devrait cibler le chemin E2E complet avec un modèle réel
   dans la boucle, pour valider que les directives 18B sont effectivement suivies
   par le modèle, pas seulement par les tests unitaires.

---

## 10. V1 Integrity

**V1 non impactée.** Le `git status` ne montre aucune modification de fichier
appartenant à une architecture V1. Tous les fichiers modifiés sont sous `raya/` (V2)
et `tests/` (V2). La structure V1 (`modules/`, `core/`) n'est pas visible dans ce
workspace — soit elle est dans un répertoire séparé, soit la migration est complète.

Aucune régression V2 antérieure au Chantier 18B n'a été introduite. Les modes
Chantier 19 (`replace`/`append`/`clear`/`submit`) ont été explicitement vérifiés
dans les tests `test_type_replace_mode_regression` et `test_type_submit_true_regression`.

---

## Synthèse décisionnelle

| Question | Réponse |
|----------|---------|
| Les primitives 18B sont-elles implémentées ? | OUI — toutes les 5 |
| Les directives objective-centric sont-elles dans le prod path ? | OUI — conversationnel + long-horizon |
| Le recovery est-il possible structurellement ? | OUI (mécanisme) — mais directive-dépendant (comportement) |
| Y a-t-il du hardcoding de site ? | NON dans la logique (commentaires uniquement) |
| Y a-t-il un gap de sécurité ? | OUI — passwords exposés via read_page (CRITIQUE) |
| Chantier 19 est-il intact ? | OUI — aucune régression |

**Réponse à la question centrale :** RAYA dispose des primitives et des directives
nécessaires pour récupérer lorsqu'une méthode Browser échoue. Le comportement agentique
complet est **présent au niveau des mécanismes**. Sa fiabilité en production dépend du
suivi des directives par le modèle sous-jacent — ce qui est le cas nominal pour tout
système LLM dirigé par directives. Le LoopDetector garantit qu'aucune boucle ne peut
se poursuivre indéfiniment.

**Verdict final : GO WITH GAPS.** Corriger le gap password (1 ligne de sélecteur CSS)
avant le premier déploiement réel sur des pages de connexion.
