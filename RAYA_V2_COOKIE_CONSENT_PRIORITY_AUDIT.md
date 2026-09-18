# RAYA V2 — Cookie Consent Priority Audit

**Date :** 2026-09-15  
**Mode :** AUDIT UNIQUEMENT — aucun code modifié  
**Fichiers analysés :** `raya/devices/browser/controller.py`, `raya/context_engine/render.py`  
**Scripts d'audit :** `scripts/audit_standaard_dom.py`, `scripts/validate_cookie_consent_e2e.py`

---

## A. Root Cause Exacte

### Le mécanisme actuel — texte → correspondance → clic

`dismiss_overlays()` itère séquentiellement sur `_OVERLAY_ACCEPT_TEXTS` et pour chaque texte appelle :

```python
loc = await self._find_clickable_in(overlay_root, t)
if loc:
    await loc.click()
    dismissed.append(t)
    break  # premier texte trouvé → stop
```

`_find_clickable_in` essaie six stratégies en cascade :

```python
1. frame.get_by_role("button", name=target, exact=False)
2. frame.get_by_role("link",   name=target, exact=False)  ← POINT D'ENTRÉE DU BUG
3. frame.get_by_label(target,  exact=False)
4. frame.get_by_title(target,  exact=False)
5. frame.locator(f"[aria-label*='{esc}' i]")
6. frame.get_by_text(target,   exact=False)
```

Avec `exact=False`, Playwright effectue une correspondance **sous-chaîne insensible à la casse** sur le **nom accessible** de l'élément.

### La collision "OK" ↔ "cookiebeleid"

Sur standaard.be, le CMP Didomi contient un lien `<a>cookiebeleid</a>` (politique cookies).

```
target = "OK"
"ok" (lowercase) is substring of "cookiebeleid"?

c-o-o-k-i-e-b-e-l-e-i-d
        ^
        "ok" at position 2-3 → TRUE
```

Résultat vérifié par l'audit direct :

```
[FIRST MATCH] 'OK' -> method=get_by_role(link) found_text='cookiebeleid' tag=a
[ALSO FOUND]  'Akkoord' -> method=get_by_role(button) found_text='Akkoord' tag=button
```

Le lien `cookiebeleid` matche **avant** le bouton `Akkoord` parce que :
1. "OK" (position 10/19 dans `_OVERLAY_ACCEPT_TEXTS`) est testé avant "Akkoord" (position 12)
2. `get_by_role("button", name="OK")` : aucun bouton ne matche (Akkoord ne contient pas "ok" comme sous-chaîne)
3. `get_by_role("link", name="OK")` : "cookiebeleid" contient "ok" → MATCH → retour immédiat
4. Le clic sur ce lien navigue potentiellement vers la politique cookies ou ne fait rien d'utile
5. La bannière reste intacte
6. 3 rounds s'enchaînent en cliquant le même lien sans effet

### Vérification post-clic : inexistante dans l'outil

```python
# Extrait actuel de dismiss_overlays
if not clicked:
    break
dismissed.append(clicked)  # ← on assume que le clic a marché
await page.wait_for_timeout(300)
# prochain round : re-marquer les overlays
```

Après le clic, le code **ne vérifie pas si la bannière a disparu**. Il re-marque les overlays au round suivant et constate qu'il y en a encore — mais au lieu d'essayer un candidat DIFFÉRENT, il recommence avec le même premier texte trouvé.

### Résumé de la chaîne causale

```
"OK" dans _OVERLAY_ACCEPT_TEXTS (pos 10)
    ↓
_find_clickable_in(overlay_root, "OK", exact=False)
    ↓
get_by_role("button", name="OK") → rien
    ↓
get_by_role("link", name="OK") → "cookiebeleid" via substring "ok"
    ↓
click("cookiebeleid") → lien navigational, pas une action consent
    ↓
bannière intacte
    ↓
pas de vérification → dismissed.append("OK") comptabilisé
    ↓
round 2 : re-marque → même overlay → même "OK" en premier → même clic
    ↓
3 rounds × clic inutile
    ↓
rounds=3 dans le résultat → modèle pense que dismiss a réussi
    ↓
bannière toujours présente
```

---

## B. Correction Minimale Recommandée

### La question architecturale posée

> "Comment faire pour que RAYA comprenne quel bouton réalise réellement l'intention
> de l'utilisateur dans le contexte de la page, sans dépendre d'une liste de
> mots-clés ou de règles spécifiques aux sites ?"

Il ne s'agit **pas** de réordonner `_OVERLAY_ACCEPT_TEXTS`. Le bug n'est pas "OK avant Akkoord" — c'est que le système traite une correspondance de texte comme une décision sémantique.

Trois niveaux de correction orthogonaux :

---

### Fix 1 — Vérification post-clic dans `dismiss_overlays` (PRIORITAIRE)

**Ce que le code fait :** clic → assume succès → round suivant → même candidat.  
**Ce qu'il devrait faire :** clic → vérifie si l'overlay a changé → si non, essaie le candidat suivant dans le même round.

```python
# Concept (pas du code final)
for t in _OVERLAY_ACCEPT_TEXTS + _OVERLAY_REJECT_TEXTS + _OVERLAY_CLOSE_TEXTS:
    loc = await _find_clickable_in(overlay_root, t)
    if loc is None:
        continue
    await loc.click()
    await page.wait_for_timeout(300)
    # Vérification : l'overlay est-il encore marqué ?
    still_present = int(await page.evaluate(
        "() => document.querySelectorAll('[data-raya-overlay]').length"
    ) or 0)
    if still_present == 0:
        dismissed.append(t)  # seulement si la bannière a réellement disparu
        break
    # Sinon : continuer vers le prochain candidat dans la même itération
```

**Propriété générique :** ce fix ne connaît aucun site. Il observe uniquement si l'état de l'overlay a changé après le clic — c'est un signal réel et objectif.

---

### Fix 2 — Filtre structurel : boutons consent ≠ liens navigationnels

**Observation clé de l'audit :**

| Élément | Tag | Rôle | Texte | Action réelle |
|---------|-----|------|-------|---------------|
| Bouton consent | `<button>` | (none) | "Akkoord" | Accepte les cookies ✓ |
| Faux positif | `<a>` | (none) | "cookiebeleid" | Navigue vers politique cookies ✗ |

Un consentement cookies est **toujours** déclenché par un élément de type action (`<button>`, `role=button`, `input[type=submit]`) — jamais par un lien de navigation pur (`<a>` sans `role=button`).

Modification ciblée de `_find_clickable_in` lorsqu'utilisé dans `dismiss_overlays` :

```python
# Option A : exclure les liens purs (exact=True pour les correspondances courtes)
# Option B : ajouter un paramètre `buttons_only=True` qui limite les stratégies
#            de recherche à get_by_role("button") uniquement
```

**Propriété générique :** la règle "consent = button, not navigational link" est valide pour tous les CMP sur tous les sites — c'est une propriété de l'interface, pas du site.

---

### Fix 3 — Sémantique du résultat `rounds` dans la directive (render.py)

**Problème actuel :** la directive dit que `rounds=0` = escalade nécessaire. Mais `rounds=3` peut aussi signifier "3 clics inefficaces".

Le modèle devrait interpréter :

```
rounds > 0 AND banner still present  →  dismiss_overlay a cliqué mais n'a PAS résolu
```

La directive STEP 5 (vérification) couvre déjà ce cas partiellement — mais elle n'indique pas explicitement que `rounds > 0` n'est pas une preuve de succès. Ce point mérite d'être renforcé.

---

### Fix 4 — Escalade vers le modèle pour l'analyse sémantique contextuelle

Lorsque `dismiss_overlays` échoue (rounds > 0 mais banner présente), le modèle doit prendre le relais avec une **analyse sémantique des candidats**.

Les données disponibles via `browser.read_page` pour chaque bouton :

```json
{
  "kind": "button",
  "text": "Akkoord",
  "tag": "button",
  "left": 428,
  "top": 617,
  "aria_label": "Akkoord"
}
```

Avec ces données, le modèle peut raisonner :

- "Ce bouton est `<button>` (rôle action) — pas un lien"
- "Son texte est 'Akkoord' — terme de consentement NL"  
- "Il est positionné en bas du dialog (top=617) — zone CTA primaire"
- "Le texte environnant du container mentionne 'cookies' et 'privacybeleid'"
- "Les autres boutons sont 'Stel voorkeuren in' — secondaire, et des liens navigationnels"
- → **Candidat primaire : 'Akkoord'**

C'est ici que les **mots-clés deviennent des indices, pas des décisions** : "Akkoord" est reconnu comme terme de consentement NL, ce qui renforce la confiance, mais la décision finale vient de l'analyse contextuelle (role, position, structure).

---

## C. Pourquoi Ces Corrections Sont Génériques

Aucune des corrections ci-dessus ne code de logique spécifique à un site :

| Fix | Signal utilisé | Généricité |
|-----|---------------|-----------|
| Post-click vérification | État observable (overlay présent/absent) | Universel |
| Filtre bouton/lien | Sémantique HTML/ARIA (button = action, a = navigation) | Universel |
| Sémantique `rounds` | Signal mesurable (rounds > 0 ≠ succès) | Universel |
| Analyse modèle | Raisonnement sur DOM structuré | Universel |

Un site inconnu avec un CMP personnalisé sera traité de la même façon :
- Le post-click check vérifie si l'overlay a changé
- Le filtre bouton/lien s'applique à n'importe quel CMP
- Le modèle analyse la structure pour identifier le CTA primaire

---

## D. Fichiers et Fonctions à Modifier

| Fichier | Fonction | Modification |
|---------|----------|-------------|
| `raya/devices/browser/controller.py` | `dismiss_overlays()` | Fix 1 (vérification post-clic) + Fix 2 (filtre structurel) |
| `raya/devices/browser/controller.py` | `_find_clickable_in()` | Paramètre optionnel `buttons_only=False` ou variante dédiée |
| `raya/context_engine/render.py` | Directive cookie (STEP 2 → 5) | Fix 3 : préciser que `rounds > 0` n'implique pas succès |

**Aucun nouveau fichier.** Modifications localisées dans les deux fichiers existants.

---

## E. Risques / Régressions Possibles

### Risque 1 — Fix 1 : performance (faible)

La vérification post-clic ajoute une évaluation JS par candidat testé. Sur une page sans bannière : aucun impact (la boucle ne s'exécute pas). Sur une page avec bannière qui répond au premier clic : +1 évaluation JS (négligeable). Sur un cas pathologique (N candidats non effectifs) : N évaluations JS supplémentaires, mais le cas se terminait déjà en N rounds — pas de dégradation réelle.

### Risque 2 — Fix 2 : vrais boutons consent avec tag `<a role=button>` (faible)

Certains CMP utilisent des liens stylés en boutons avec `role="button"`. `get_by_role("button")` capture déjà ces éléments car Playwright résout le rôle ARIA, pas le tag. Un `<a role="button">Accepter</a>` sera trouvé par `get_by_role("button")`. Le risque est quasi nul.

### Risque 3 — Fix 1 : CMP avec animation lente (modéré)

Si la bannière disparaît en 600ms après le clic (animation CSS), la vérification à 300ms pourrait conclure "toujours présent" à tort. Mitigation : augmenter le délai de vérification à 500ms, ou vérifier deux fois (300ms + 500ms).

### Risque 4 — Fix 3 : directive plus longue = plus de tokens (négligeable)

Déjà géré par le budget tokens du contexte.

### Régressions connues : zéro sur lesoir.be et rtbf.be

Ces deux sites ont des boutons `<button>Accepter</button>` — le Fix 2 (filtre bouton/lien) n'affecte pas leur comportement. Le Fix 1 (vérification post-clic) ne change rien car le clic est déjà efficace (overlay disparaît en une round).

---

## F. Tests Nécessaires Après Correction

### Tests automatisés proposés (≤ 10)

| # | Test | Ce qui est vérifié |
|---|------|-------------------|
| 1 | `test_dismiss_skips_ineffective_click_and_tries_next` | Mock : overlay reste après clic "OK" → code continue vers "Akkoord" |
| 2 | `test_dismiss_only_counts_effective_clicks` | `rounds` = nombre de clics EFFECTIFS, pas total de clics |
| 3 | `test_find_clickable_in_link_not_returned_for_short_ambiguous_text` | `get_by_role(link, "OK")` ne retourne pas un lien dont le texte est "cookiebeleid" |
| 4 | `test_overlay_selectors_cover_same_structures_as_struct_js` | Synchro structurelle (déjà existant — maintenir) |
| 5 | `test_accept_texts_do_not_contain_single_letter_substrings` | Aucun texte dans `_OVERLAY_ACCEPT_TEXTS` n'est une sous-chaîne commune de mots de consentement |
| 6 | `test_directive_rounds_positive_not_sufficient_for_success` | render.py : directive mentionne que rounds>0 ≠ résolu |
| 7 | `test_dismiss_falls_through_to_button_when_link_matches_first` | Avec un overlay contenant un lien "cookiebeleid" et un bouton "Akkoord", le bouton est cliqué |
| 8 | `test_post_click_verification_re_marks_and_checks` | JS de vérification appelé après chaque clic candidat |
| 9 | `test_model_escalation_on_rounds_positive_banner_still_present` | Directive active l'escalade si rounds>0 ET banner visible |
| 10 | `test_dismiss_effective_single_round_on_simple_banner` | Régression : sites simples (lesoir, rtbf) toujours resolus en 1 round |

### E2E réels proposés (≤ 4)

| # | Site | Scenario | Attendu |
|---|------|----------|---------|
| E2E-A | standaard.be (profil vierge) | Dismiss → vérifie → "Akkoord" cliqué (pas "cookiebeleid") → banner gone | PASS |
| E2E-B | lesoir.be (profil vierge) | Régression : dismiss toujours efficace en 1 round | PASS |
| E2E-C | rtbf.be (profil vierge) | Régression : dismiss toujours efficace en 1 round | PASS |
| E2E-D | Site avec CMP inconnu (trouver au moment du test) | Généricité : fix fonctionne sans connaissance préalable du site | PASS ou diagnostic honnête |

---

## G. Analyse DOM + Vision

### DOM seul — ce qu'il fournit

`browser.read_page` retourne pour chaque bouton :
- `text` : texte visible
- `tag` : élément HTML
- `aria_label` : étiquette d'accessibilité
- `left`, `top` : coordonnées dans la fenêtre
- `role` : rôle ARIA

Avec ces données, le modèle peut distinguer :

```
[button] text="Akkoord" aria="Akkoord" top=617 left=428  ← bouton <button>, positionné en bas du dialog
[link]   text="cookiebeleid" top=521                      ← lien <a>, positionné dans le corps du texte
```

Position + role + type d'élément = suffisant pour identifier le CTA primaire **sans vision**.

### Vision — quand elle est utile

La vision ajoute de la valeur dans des cas que le DOM ne résout pas seul :

| Cas | DOM | Vision |
|-----|-----|--------|
| Bouton sans texte (icône seule) | Invisible pour le DOM | Voit "X", "✓", image |
| CMP dans canvas/SVG | Pas de DOM cliquable | Peut voir le bouton |
| Overlay avec z-index complexe | Peut rapporter mauvaises coords | Voit la position réelle |
| Bannière dans iframe cross-origin | Inaccessible au DOM | Voit le contenu visuel |

**Principe DOM+Vision correct (conforme à la spec utilisateur) :**
- DOM fournit la **structure sémantique** (role, aria, text)
- Vision fournit la **représentation spatiale** (position réelle, visibilité réelle)
- Ensemble : résolution d'ambiguïté quand l'un est insuffisant
- Vision n'est pas appelée si le DOM résout seul

**Pour standaard.be spécifiquement :** le DOM seul suffit. "Akkoord" est un `<button>` avec aria-label clair. La vision n'est pas nécessaire. Le bug n'est pas un manque d'information — c'est un manque de vérification post-clic.

---

## H. Full Loop — Vrai RAYA + Vrai Modèle

**Test souhaité :**
```
Utilisateur : "Va sur standaard.be et lis-moi le titre"
→ RAYA runtime
→ modèle Ollama local
→ navigateur
→ cookie_banner détecté
→ modèle choisit dismiss_overlay
→ dismiss_overlay échoue (rounds=3, banner présente)
→ modèle escalade vers read_page + analyse
→ modèle identifie "Akkoord" comme candidat consent
→ browser.click("Akkoord")
→ vérification
→ titre lu
```

**Résultat :**

```
BLOCKED_NO_MODEL
```

Ollama refusé sur `http://localhost:11434` :  
`[WinError 10061] Aucune connexion n'a pu être établie car l'ordinateur cible l'a expressément refusée`

Ollama n'est pas en cours d'exécution sur cette machine au moment du test.

**Ce test ne peut pas être remplacé par un mock.** Le comportement à valider est précisément la capacité du modèle à raisonner sur des données DOM structurées et à sélectionner le bon candidat — c'est une propriété du modèle, pas de l'infrastructure.

---

## I. Verdict

**FIX_REQUIRED**

### Pourquoi FIX_REQUIRED et non NEEDS_FURTHER_AUDIT

La root cause est précisément identifiée et reproductible :

1. `get_by_role("link", name="OK", exact=False)` matche "cookiebeleid" via substring "ok"  
   → **vérifié par l'audit DOM direct** (audit_standaard_dom.py, résultats JSON)

2. Aucune vérification post-clic n'existe dans `dismiss_overlays`  
   → **lu dans le code source** (controller.py lignes 331-358)

3. `rounds > 0` dans le résultat est interprété par le modèle comme succès  
   → **vérifié par l'E2E** : rounds=3 retourné, banner toujours présente

4. Le fix nécessaire est architecturalement clair et générique  
   → **aucun site-specific logic requis**

### Ce que le fix n'est PAS

- Pas un réordonnancement de `_OVERLAY_ACCEPT_TEXTS`
- Pas une suppression de "OK" de la liste
- Pas une règle standaard.be
- Pas un nouveau manager

### Ce que le fix EST

Un **mécanisme d'auto-vérification** dans `dismiss_overlays` qui traite un clic comme une hypothèse, et vérifie l'hypothèse avant de la valider. Si l'hypothèse est fausse (overlay toujours présent), le mécanisme continue vers le prochain candidat dans le même round — sans nécessiter d'informations spécifiques au site.

Combiné à une **restriction structurelle** : les éléments de navigation (`<a>` sans role=button) ne sont pas des candidats valides pour une action de consentement.

### Portée minimale de la correction

**2 fichiers**, modifications localisées :

1. `raya/devices/browser/controller.py` — `dismiss_overlays` : post-click verification loop + filtre structurel
2. `raya/context_engine/render.py` — directive STEP 2/3 : clarifier que `rounds > 0` sans disparition de banner = escalade

**V1 : intacte**  
**Aucun nouveau composant**  
**Full loop test : BLOCKED_NO_MODEL** (Ollama non disponible au moment du test)
