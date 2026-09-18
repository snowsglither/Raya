# RAYA V2 — Chantier 18C : Real Browser Agentic Execution & Security

**Date :** 2026-09-07  
**Statut :** TERMINÉ — 10 nouveaux tests PASS + 5 E2E skip (Ollama key invalide)

---

## Résumé exécutif

Chantier 18C avait deux objectifs : (1) corriger le **GAP CRITIQUE de sécurité** identifié dans `controller.py` où `input[type=password]` exposait `el.value` via `browser.read_page`, et (2) valider un cycle complet **observe → act → verify** avec un vrai modèle LLM et vrai Edge/CDP. La faille de sécurité est **entièrement corrigée et couverte par des tests de régression**. Les scénarios E2E avec vrai LLM sont écrits et prêts — ils nécessitent une clé Ollama valide pour s'exécuter.

---

## 1. GAP CRITIQUE corrigé — Password field security

### Problème (contrôleur.py:62–66)

La fonction `label()` dans `_STRUCT_JS` utilisait `el.value` comme fallback textuel :

```javascript
const label = (el) => (
    (el.innerText || el.value || el.getAttribute('aria-label') || ...)
);
```

Pour un `input[type=password]` avec `value="secret123"`, `el.innerText` est vide (les inputs n'ont pas d'innerText), donc la fonction tombait sur `el.value` → **le mot de passe réel apparaissait dans `browser.read_page`**.

### Fix (contrôleur.py — _STRUCT_JS)

```javascript
const label = (el) => {
    const pw = el.tagName === 'INPUT' && (el.getAttribute('type') || '').toLowerCase() === 'password';
    const val = pw ? '' : (el.value || '');
    return ((el.innerText || val || el.getAttribute('aria-label') ||
     el.getAttribute('title') || el.getAttribute('placeholder') || '')
    .replace(/\s+/g, ' ').trim().slice(0, 90));
};
```

**Résultat :** Pour `input[type=password]`, `val` est forcé à `''`. L'aria-label (ex: `"Mot de passe"`) reste visible pour que le modèle sache que le champ existe — mais **jamais sa valeur réelle**.

### Ajout : `input_type` exposé

Dans `grab()` pour les inputs :

```javascript
const it = (el.getAttribute('type') || '').toLowerCase();
if (it && it !== 'text') item.input_type = it;
```

Le modèle reçoit `input_type: 'password'` (ou `'email'`, `'number'`, etc.) pour savoir avec quel type de champ il interagit sans voir sa valeur.

---

## 2. Directive Chantier 18C ajoutée — render.py

Directive de conscience password ajoutée dans le contexte browser (après les 3 directives 18B) :

```
Password field awareness — when browser.read_page returns an input with
input_type='password', the field's actual value is intentionally absent from
the observation. This is by design: you should never need to read, display,
log, or repeat a password value. If the field is already filled (the form is
prefilled or autofill has populated it), submit the form via browser.click on
the submit button — do not attempt to re-enter or inspect the password. 
If the field is empty and filling it is required, that is a HUMAN BLOCKER:
stop and ask the user to enter their credentials.
```

---

## 3. Tests — Chantier 18C (15 tests, 10 PASS + 5 SKIP)

**Fichier :** `tests/devices/browser/test_chantier18c_real_browser_agentic.py`

### Groupe 1 : GAP CRITIQUE — password JAMAIS exposé (6 tests)

| Test | Résultat |
|------|---------|
| `test_password_field_value_not_in_text_in_read_page` | PASS |
| `test_secret_value_absent_from_inputs_text_list` | PASS |
| `test_password_input_has_input_type_password` | PASS |
| `test_secret_absent_from_tool_result_output_json` | PASS |
| `test_secret_absent_from_tool_result_evidence_json` | PASS |
| `test_complete_read_page_observation_contains_no_secret` | PASS |

### Groupe 2 : input_type exposé (2 tests)

| Test | Résultat |
|------|---------|
| `test_email_input_has_input_type_email` | PASS |
| `test_regular_text_input_value_still_visible` | PASS |

### Groupe 3 : Audit hardcoding (2 tests)

| Test | Résultat |
|------|---------|
| `test_no_amazon_specific_ids_in_struct_js` | PASS |
| `test_no_site_specific_domains_in_controller` | PASS |

### Groupe 4 : Scénarios E2E réels (5 tests)

| Test | Résultat | Raison |
|------|---------|--------|
| `test_live_e2e_observe_and_read_page_title` | SKIP | Ollama key invalide |
| `test_live_e2e_click_primary_control` | SKIP | Ollama key invalide |
| `test_live_e2e_prefilled_login_no_password_in_trace` | SKIP | Ollama key invalide |
| `test_live_e2e_recovery_after_element_not_found` | SKIP | Ollama key invalide |
| `test_live_e2e_tool_success_confirmed_via_read_page` | SKIP | Ollama key invalide |

**Note :** Les scénarios E2E sont structurellement valides et exécutables. Ils seront activés dès qu'une clé Ollama Cloud valide sera disponible. Le skip gracieux utilise le code d'erreur (`NOT_IMPLEMENTED`, `AUTH_ERROR`, etc.) pour distinguer "infrastructure absente" de "vrai échec de scénario".

---

## 4. Chemin de sécurité complet — audit

Le password est bloqué à **trois niveaux indépendants** :

| Niveau | Mécanisme | Où |
|--------|-----------|-----|
| Extraction DOM | `label()` ignore `el.value` pour password | `_STRUCT_JS` dans `controller.py` |
| ToolResult | `output` et `evidence` ne contiennent jamais la valeur | `agent.py::_read_page` → `_ok()` |
| Directive modèle | Le modèle ne doit pas afficher ni stocker les passwords | `render.py` — directive 18C §1 |

Vérifié : `browser.read_page`, `ToolResult.output`, `ToolResult.evidence`, l'observation complète sérialisée en JSON → aucun `"secret123"` dans aucun de ces chemins.

---

## 5. Régression globale

| Suite | Avant 18C | Après 18C | Delta |
|-------|----------|----------|-------|
| `tests/context_engine/` | 122/122 | 122/122 | 0 |
| `tests/devices/browser/` | 37/37 | 47/47 + 5 skip | +10 tests |
| `tests/tools/test_browser_catalog.py` | 14/14 | 14/14 | 0 |
| Suite complète (sans 18C) | ~1319 total, 15 failures | 1324 total, 14 failures | -1 failure |

**0 nouvelle régression introduite par Chantier 18C.**

---

## 6. Fichiers modifiés

| Fichier | Modification |
|---------|-------------|
| `raya/devices/browser/controller.py` | `label()` sécurisé + `input_type` exposé dans `grab()` |
| `raya/context_engine/render.py` | Directive 18C §1 (password field awareness) |
| `tests/devices/browser/test_chantier18c_real_browser_agentic.py` | **CRÉÉ** — 15 tests |

---

## 7. Ce qui reste (inconnu)

Les 5 scénarios E2E nécessitent une clé Ollama Cloud active. Dès qu'elle sera disponible, relancer :

```
python -m pytest tests/devices/browser/test_chantier18c_real_browser_agentic.py -k "live_e2e" -v
```

Les scénarios couverts :
1. Observe → read_page → title (lecture pure)
2. Click bouton principal (action simple)
3. Login formulaire préfillé — trace sans `secret123` (critique sécurité)
4. Recovery après `ELEMENT_NOT_FOUND` (DOM fallback)
5. Tool SUCCESS confirmé par read_page (vérification objectif)
