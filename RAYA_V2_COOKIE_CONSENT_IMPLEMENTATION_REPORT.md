# RAYA V2 — Cookie Consent : Rapport d'Implémentation

**Date :** 2026-09-15  
**Chantier :** Cookie Consent — Smart Candidate Selection + Post-Click Verification  
**Précédents :** Audit DOM+Vision, Chantier Fixes A-I, Validation E2E, Priority Audit

---

## 1. Root Causes Corrigées

### Ce chantier (Smart Selection)

| ID | Cause | Gravité | Statut |
|----|-------|---------|--------|
| SC-1 | `_find_clickable_in` cherchait des liens de navigation en plus des boutons — `get_by_role("link", name="OK", exact=False)` matchait "cookiebeleid" via sous-chaîne "ok" dans "c-**oo-k**-iebeleid" | CRITIQUE | **CORRIGÉ** |
| SC-2 | Aucune vérification post-clic — un clic sur un élément non-consent était comptabilisé comme "dismissed" | CRITIQUE | **CORRIGÉ** |
| SC-3 | Boucle aveugle — le même candidat inefficace recliqué 3× sans jamais essayer le suivant | HAUTE | **CORRIGÉ** |
| SC-4 | `rounds > 0` interprété comme succès par le modèle alors qu'il signifie "clics effectués", pas "banner disparue" | HAUTE | **CORRIGÉ** |

### Chantier précédent (Fixes A-I) — conservés intacts

| Fix | Description | Statut |
|-----|-------------|--------|
| A | `_STRUCT_JS` détection cookie_banner étendue + garde contextuelle | ✓ conservé |
| B | `.gdpr` → `[class*=gdpr i]` | ✓ conservé |
| C | Textes accept NL/DE/ES/IT | ✓ conservé |
| D-I | Directive render.py : multi-signaux, escalade, vision, vérification, continuation | ✓ conservé + étendu |

---

## 2. Principe Architectural Appliqué

> **keyword match ≠ semantic decision**  
> **keyword match ≠ action success**

Les textes dans `_OVERLAY_ACCEPT_TEXTS` sont des **indices linguistiques** qui génèrent des candidats. La décision finale repose sur :

1. **Filtre structurel** : le candidat est-il un élément d'action (button/role=button) ou un lien de navigation ?
2. **Vérification réelle** : après le clic, l'overlay a-t-il réellement disparu ?
3. **Escalade sémantique** : si le mécanisme automatique échoue, le modèle analyse la structure DOM complète (position, rôle, texte environnant, aria-label) pour identifier le CTA primaire.

Aucune règle site-spécifique. Aucun nouveau manager.

---

## 3. Fichiers Modifiés

| Fichier | Nature |
|---------|--------|
| `raya/devices/browser/controller.py` | Nouveau `_find_consent_action_in` + refonte `dismiss_overlays` |
| `raya/context_engine/render.py` | STEP 2/3 : `effective` + clarification `rounds > 0` ≠ succès |
| `tests/browser/test_cookie_smart_selection.py` | 11 nouveaux tests (créé) |

**Non modifiés :** V1, `raya/safety/`, `raya/harness/`, `raya/tools/`, `raya/devices/browser/agent.py`.

---

## 4. Changements Exacts

### Fix 1 — `_find_consent_action_in` (controller.py)

Nouvelle méthode, distincte de `_find_clickable_in` (générique).

```python
async def _find_consent_action_in(self, frame, target: str):
    """Restreint aux éléments role=button — jamais liens de navigation purs.
    
    get_by_role('button') couvre : <button>, <a role='button'>,
    <input type='submit|button|reset|image'>, tout element role='button'.
    Exclut naturellement <a> sans role.
    
    exact=False : 'Accepter' matche 'Accepter tous les cookies' ✓
    Filtre role=button : 'ok' dans 'cookiebeleid' (lien) non trouvé ✓"""
    try:
        loc = frame.get_by_role("button", name=target, exact=False).first
        if await loc.count() > 0 and await loc.is_visible():
            return loc
    except Exception:
        pass
    return None
```

**Chemin générique conservé :** `_find_clickable_in` (pour `browser.click`) inclut toujours les liens — aucune régression sur la navigation générale.

### Fix 2+3 — `dismiss_overlays` refonte (controller.py)

```
AVANT:
  for round in max_rounds:
    mark_overlays()
    for texts_group in (ACCEPT, REJECT, CLOSE):
      for t in texts_group:
        loc = _find_clickable_in(overlay_root, t)  ← cherche aussi les liens
        if loc: click(); break
    unmark()
    dismissed.append(t)  ← assume succès sans vérifier

APRÈS:
  tried = set()  ← Fix 4 : pas de boucle aveugle
  for round in max_rounds:
    mark_overlays()
    for t in ACCEPT+REJECT+CLOSE:
      if t in tried: continue
      loc = _find_consent_action_in(overlay_root, t)  ← Fix 1 : buttons only
      if loc is None: continue
      click()
      wait(500ms)
      unmark(); re_mark()  ← Fix 3 : vérification post-clic
      if n_after < n_before:
        effective_in_round = t; break  ← clic réellement efficace
      else:
        tried.add(t)  ← Fix 4 : candidat inefficace → jamais recliqué
        continue  ← essayer le suivant dans le même round
    if not effective_in_round: break
    dismissed.append(effective_in_round)

  return {"dismissed": [...], "rounds": N, "effective": N > 0}  ← Fix 7
```

### Fix 5 — Directive render.py (STEP 2/3)

```
AVANT:
  STEP 3 — if dismiss_overlay returns rounds=0 (nothing found/dismissed):
  examine the buttons[] list...

APRÈS:
  STEP 2 — [...] The result includes 'effective': true/false —
  'rounds' alone is NOT proof of success (rounds>0 means candidates
  were clicked, NOT that the banner is gone).
  STEP 3 — if dismiss_overlay returns effective=false OR rounds=0
  (nothing clicked OR clicked but banner persisted): examine
  the buttons[] list...
```

---

## 5. Comportement Avant / Après — standaard.be

| Étape | AVANT (bug) | APRÈS (fix) |
|-------|-------------|-------------|
| read_page | `cookie_banner=true`, "Akkoord" button visible | idem |
| dismiss_overlays — candidat testé | "OK" → `get_by_role("link", name="OK")` → "cookiebeleid" lien | "OK" → `get_by_role("button", name="OK")` → aucun bouton ne match |
| candidat suivant | aucun (break sur premier match) | "Akkoord" → `get_by_role("button", name="Akkoord")` → bouton trouvé |
| post-click | pas de vérification → `dismissed=["OK"]` × 3 | overlay disparu → `dismissed=["Akkoord"]`, `effective=true` |
| read_page après | `cookie_banner=true`, mêmes boutons | `cookie_banner=false`, boutons consent absents |
| titre page | accessible mais banner toujours visible | accessible, page propre |

---

## 6. Tests Automatisés

### Nouveaux tests (`tests/browser/test_cookie_smart_selection.py`)

| # | Test | Fix couvert |
|---|------|------------|
| 1 | `test_find_consent_action_in_exists_as_distinct_method` | Séparation des chemins |
| 2 | `test_find_consent_action_in_uses_only_button_role` | Fix 1 : pas de lien |
| 3 | `test_find_clickable_in_still_searches_links_for_generic_click` | Non-régression browser.click |
| 4 | `test_cookiebeleid_does_not_match_ok_via_role_button` | Fix 2 : substring impossible |
| 5 | `test_ok_remains_in_accept_texts_but_cannot_match_navigational_link` | Textes = indices, pas décisions |
| 6 | `test_dismiss_overlays_source_contains_post_click_verification` | Fix 3 : vérification post-clic |
| 7 | `test_dismiss_overlays_result_includes_effective_field` | Fix 7 : résultat `effective` |
| 8 | `test_dismiss_overlays_maintains_tried_set` | Fix 4 : pas de boucle aveugle |
| 9 | `test_dismiss_overlays_candidate_loop_covers_all_texts_in_priority_order` | Un seul passage complet |
| 10 | `test_directive_escalates_on_effective_false_not_only_rounds_zero` | Fix 5 : escalade directive |
| 11 | `test_directive_clarifies_rounds_positive_not_proof_of_success` | Fix 5 : sémantique `rounds` |

**Résultat :** 11/11 PASSED

### Tests existants conservés

| Suite | Résultat |
|-------|----------|
| `tests/browser/test_cookie_consent_intelligent.py` (12 tests) | 12/12 PASSED |
| `tests/context_engine/test_scope_correction.py` (10 tests) | 10/10 PASSED |
| Ensemble `tests/context_engine/ tests/tools/ tests/browser/` | **470/470 PASSED** |

---

## 7. E2E Réels — Profil Vierge

Script : `scripts/validate_cookie_consent_e2e.py`  
Constantes importées directement de `raya.devices.browser.controller`

| Site | Banner | Candidat sélectionné | effective | cookie_banner après | Verdict |
|------|--------|----------------------|-----------|---------------------|---------|
| standaard.be (NL) | ✓ `true` | **"Akkoord"** (was "OK") | `true` | `false` | **PASS** |
| lesoir.be (FR) | ✓ `true` | "Accepter" | `true` | `false` | **PASS** |
| rtbf.be (FR) | ✓ `true` | "Accepter" | `true` | `false` | **PASS** |
| libre.be | SSL error | — | — | — | **BLOCKED** |

**standaard.be avant :** bannière "Cookie- en advertentievoorkeuren" avec bouton "Akkoord"  
**standaard.be après :** page d'accueil entièrement accessible, aucun overlay

Les 3 sites accessibles : **3/3 PASS**

---

## 8. Full Loop (Ollama)

**BLOCKED_NO_MODEL**

`http://localhost:11434` — connexion refusée : Ollama non démarré.

Le test full loop (vrai RAYA + vrai modèle + vrai navigateur) ne peut pas être substitué par un mock — le comportement à valider est la capacité du modèle à raisonner sur les données DOM structurées de `read_page` pour identifier le CTA primaire de consentement en cas d'escalade.

---

## 9. Régressions

| Test | Avant chantier | Après chantier |
|------|---------------|----------------|
| `tests/devices/windows/test_windows_agent.py` (4 UIA tests) | FAIL pré-existant | FAIL inchangé |
| `test_real_keyboard_type_writes_last_interaction_target` | FAIL pré-existant | FAIL inchangé |
| Tous les autres (470) | PASS | PASS |

**Régressions introduites : 0**

---

## 10. Limites

1. **Boutons consent sans role sémantique** : si un CMP utilise `<div class="btn">Accepter</div>` sans `role="button"`, `_find_consent_action_in` ne le trouvera pas. Mitigation : la directive STEP 3 demande au modèle d'utiliser `browser.click` directement après escalade — `browser.click` utilise `_find_clickable_in` qui inclut `get_by_text`.

2. **Full loop non validé** (Ollama absent) : la chaîne "modèle voit `effective=false` → escalade vers `read_page` → `browser.click('Akkoord')`" est couverte par la directive mais pas testée en conditions réelles.

3. **CMP dans iframe cross-origin** : `dismiss_overlays` opère dans le main frame + iframes same-origin. Une bannière dans un iframe tiers reste hors de portée du DOM — vision serait le seul recours.

4. **libre.be** : inaccessible (SSL) — non testé.

---

## 11. Intégrité V1

Aucun fichier V1 modifié.

---

## 12. Architecture Compliance

| Contrainte | Statut |
|-----------|--------|
| Pas de CookieManager / ConsentManager / SiteHandler | ✓ |
| Pas de règles site-spécifiques | ✓ |
| Pas de coordonnées hardcodées | ✓ |
| Pas de nouveau orchestrateur | ✓ |
| Réutilise BrowserController existant | ✓ |
| Textes = indices, pas décisions automatiques | ✓ |
| Vérification réelle de l'état (pas tool success = objective success) | ✓ |
| DOM-FIRST, Vision = fallback | ✓ |
| V1 intacte | ✓ |

---

## Verdict Final

**PASS**

Les 4 fixes (filtre structurel + post-click verification + tried set + sémantique `effective`) résolvent le bug "OK → cookiebeleid" de manière générique, sans aucune connaissance de site. 3/3 sites réels testés PASS sur profil vierge. 0 régression. Architecture conforme.
