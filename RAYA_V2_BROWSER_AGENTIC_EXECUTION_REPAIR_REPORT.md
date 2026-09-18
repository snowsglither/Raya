# RAYA V2 — Browser Agentic Execution & Recovery (Chantier 18B)

## Status

**GO** — 18/18 nouveaux tests PASS. 19/19 tests Browser existants PASS. 0 régression.

---

## Audit

### Q1 — Comment browser.click résout-il une cible ?
`_find_clickable_in` tente 6 stratégies dans l'ordre : `get_by_role(button)`, `get_by_role(link)`, `get_by_label`, `get_by_title`, `[aria-label*=... i]`, `get_by_text`. Fallback sur les iframes. Retourne `not_found` si aucun locator ne trouve l'élément.

### Q2 — Quelles informations DOM sont disponibles ?
`_STRUCT_JS` retourne : buttons `{kind, text, tag}`, links `{kind, text, tag, href}`, inputs `{kind, text, tag, left, top, role, aria_label, placeholder}`. **Gap confirmé :** boutons sans `left`, `top`, `aria_label`.

### Q3 — Éléments avec role/aria-label/bounding box ?
**Inputs : OUI.** `left`, `top`, `aria_label`, `role`, `placeholder` présents. **Boutons : NON.** Aucun de ces champs. **Gap confirmé.**

### Q4 — Comment un click failure est remonté ?
`_click` retourne `ELEMENT_NOT_FOUND` avec `retryable=False`. Pas de re-read automatique côté device — c'est le modèle (via directive) qui doit décider de relire.

### Q5 — Le Browser Agent relit-il la page après un échec ?
**Non.** Le controller retourne directement `not_found`. La stratégie de fallback appartient au modèle.

### Q6 — Comment les overlays sont-ils détectés ?
`_OVERLAY_SELECTORS` + marquage `data-raya-overlay`. Clic scoped à l'intérieur de l'overlay uniquement. Fonctionne correctement.

### Q7 — Vision fallback ?
`browser.screenshot` existe mais le modèle n'avait aucune primitive de clic par coordonnées pour exploiter les positions observées. **Gap confirmé.**

### Q8 — Coordonnées réelles disponibles ?
`inputs` ont `left`/`top` depuis `getBoundingClientRect()`. Boutons : **non, avant ce chantier.**

### Q9 — Comment les observations sont-elles enregistrées ?
`ObservationSpec` : `browser.last_clicked_target`, `browser.last_typed_text/target`, `browser.current_url`. Fonctionne via `_promote_observations_and_verify()` dans Harness.

### Q10 — Comment l'objectif est-il représenté ?
Via la directive modèle (render_system_prompt). L'objectif n'est pas un objet structuré côté device — il vit dans le message utilisateur et les directives du système.

### Q11 — Comment l'action est-elle vérifiée ?
`check_confirmation()` dans `BrowserController` (URL ou texte de confirmation). `verify_tool_result()` dans cognition. Le modèle doit appeler `browser.read_page` pour confirmer l'effet réel.

### Q12 — Comment Cognition décide-t-il de réessayer ?
`LoopDetector.record()` → `REPLAN` (1er échec) ou `ESCALATE` (2e identique). `detect_repeating_cycle` / `detect_no_progress` pour les boucles état.

### Q13 — LoopDetector ?
Implémenté, déterministe, par clé de tâche/tour. Fonctionne correctement.

### Q14 — Autre capability de clic par position ?
**Aucune.** Gap confirmé.

### Q15 — Safety traite-t-il les clics Browser ?
Classification contextuelle (Phase 11) : `browser.interact` SAFE par défaut, SENSITIVE si verbe dangereux dans les arguments. Correct. Pas modifié.

### Q16 — Vérification des conséquences ?
`verify_tool_result` + `verify_observation_against_intent` + `combine_outcomes`. Infrastructure complète mais le modèle manquait de directives pour les utiliser correctement côté browser.

---

## Confirmed Root Causes

| # | Problème | Preuve | Cause |
|---|---------|--------|-------|
| 1 | Boutons sans `left`/`top`/`aria_label` dans `read_page` | `_STRUCT_JS` grab() : métadonnées position+aria uniquement dans `if (kind === 'input')` | Omission lors de l'ajout des métadonnées pour les inputs |
| 2 | IDs Amazon-spécifiques en dur dans `priorityRoots` | `#desktop_buybox, #addToCart_feature_div` dans `_STRUCT_JS` | Copy depuis V1, viole §21 (pas de hardcoding par site) |
| 3 | `browser.type` ne peut pas soumettre sans modifier le contenu | `"required": ["target", "text"]` + `fill()` toujours appelé même si `text=""` | Spec originale supposait toujours une saisie de texte |
| 4 | Aucun clic par coordonnées observées | Pas de `browser.click_at_position` | Non implémenté |
| 5 | Aucune directive "TOOL SUCCESS ≠ OBJECTIVE SUCCESS" | `render.py` sans directive browser objective-centric | Non ajouté lors des chantiers précédents |

---

## Existing Capabilities Reused

- `_find_clickable` (6 stratégies) — inchangé
- `check_confirmation()` — inchangé
- `dismiss_overlay` — inchangé
- `LoopDetector` — inchangé, validé dans les tests
- `verify_tool_result` / `combine_outcomes` — inchangés
- Safety / risk.py — inchangé
- ObservationSpec WorldState — inchangé

---

## Changes Made

### `raya/devices/browser/controller.py` — `_STRUCT_JS`

**Problème :** Boutons sans position ni aria_label — modèle ne peut pas distinguer les contrôles primaires/secondaires ni utiliser des coordonnées pour click_at_position.
**Preuve :** `grab()` appliquait `getBoundingClientRect()` et `aria-label` uniquement pour `kind === 'input'`.
**Solution :** Déplacer la collecte `left`, `top`, `aria_label` hors du `if (kind === 'input')` — s'applique maintenant à tous les éléments (boutons, liens, inputs).
**Retrait IDs Amazon-spécifiques :** `#desktop_buybox, #addToCart_feature_div` retirés de `priorityRoots`. Gardé : `[id*=buybox i], [id*=addtocart i], [id*=add-to-cart i], [class*=buybox i]` — patterns génériques.

### `raya/devices/browser/controller.py` — `type_text()`

**Problème :** `text=""` + `mode="replace"` appelait `fill("")` — effaçait le contenu existant. Aucun moyen de soumettre un formulaire préfillé sans modifier son contenu.
**Solution :** `if text: await loc.fill(...)` — skip fill si text vide pour mode=replace. `text` devient optionnel (`text: str = ""`). Comportement inchangé si `text` est fourni.

### `raya/devices/browser/controller.py` — `click_at_position()`

**Problème :** Pas de primitive pour cliquer par coordonnées observées.
**Solution :** `page.mouse.click(x, y)` — coordonnées attendues depuis `read_page` (button.left, button.top ou input.left, input.top). Jamais des coordonnées inventées (directive dans render.py).

### `raya/devices/browser/agent.py`

- `browser.type` : `"text"` retiré de `required`, `_type()` utilise `command.arguments.get("text", "")`
- Nouvelle capability `browser.click_at_position` dans `_CAPABILITIES`
- Nouveau handler `_click_at_position()` et entrée dans `_DISPATCH`

### `raya/tools/catalog/browser.py`

- `browser.type` : schéma mis à jour (`"required": ["target"]`), description enrichie
- Nouveau tool `browser.click_at_position` enregistré avec `PermissionLevel.SENSITIVE`

### `raya/context_engine/render.py` — 3 directives

**§3 Browser Objective Verification :** "tool returning success ≠ objective met — use browser.read_page to observe actual effect."

**§5/§14 DOM Fallback & Failure Recovery :** "ELEMENT_NOT_FOUND → read_page → re-examine → alternative target. Never repeat same failed target. check objective already satisfied. click_at_position as last resort with real coordinates."

**§8 Form Submit without prior typing :** "browser.click on submit button OR browser.type text='' submit=true to press Enter without modifying content. Never fill/read/display credentials."

---

## Browser Strategy

```
Observe (browser.read_page)
  ↓
Identify target (by role, aria-label, text, position)
  ↓
Act (browser.click / browser.type / browser.navigate)
  ↓
Observe (browser.read_page)
  ↓
Verify objective state (URL, text, counter)
  ↓ if FAIL
Re-read DOM
  → alternative target (different text/aria-label)
  → click_at_position (if coordinates from read_page available)
  ↓ if objective already satisfied
DECLARE SUCCESS
  ↓ if CAPTCHA / 2FA / PIN
HUMAN BLOCKER
```

---

## Objective Verification

| Tool result | Objective state | Conclusion |
|-------------|----------------|------------|
| browser.click SUCCESS | Page unchanged | Objective NOT met — re-verify |
| browser.click SUCCESS | URL contains /cart | Objective MET |
| browser.click FAILURE | Cart already has item | Objective MET (check_confirmation) |
| browser.navigate SUCCESS | Wrong page displayed | Objective NOT met |
| browser.type SUCCESS | Form not submitted | Objective NOT met |

---

## Form Submission

Deux chemins valides sans modifier le contenu des champs :

1. `browser.click("Se connecter")` — trouve le bouton submit par role/text/aria-label
2. `browser.type(target="Email", text="", submit=True)` — presse Enter sur le champ sans modifier son contenu (fix type_text: skip fill si text vide)

Jamais : lecture, affichage, ou remplissage de champ mot de passe.

---

## Primary / Secondary Controls

`read_page` expose maintenant `aria_label` pour tous les boutons. Un bouton `aria-label="Lire Film Test"` avec texte visible `"▶ Lire"` est trouvable par `browser.click("Lire Film Test")` via `get_by_role("button", name="Lire Film Test")`. La distinction primaire/secondaire repose sur le contexte sémantique transmis au modèle, jamais une règle codée par site.

---

## Recovery

Classification implicite par LoopDetector :

| Cause | Détection | Stratégie |
|-------|-----------|-----------|
| ELEMENT_NOT_FOUND | LoopDetector REPLAN | read_page → alternative target |
| ELEMENT_NOT_FOUND répété | LoopDetector ESCALATE | click_at_position ou blocker |
| Boucle A→B→A→B | detect_repeating_cycle | Changer de stratégie |
| Stagnation même URL | detect_no_progress | HUMAN BLOCKER |
| CAPTCHA/2FA | Détection visuelle ou page | HUMAN BLOCKER |
| Objectif déjà atteint | check_confirmation | SUCCESS sans retry |

---

## Loop Prevention

`LoopDetector` (2 échecs identiques → ESCALATE) appliqué. Validé par `test_loop_detector_escalates_after_two_identical_browser_failures`. Réinitialisation sur succès validée par `test_loop_detector_resets_after_browser_success`.

---

## Human Blockers

Inchangés (directives existantes, Chantier 18 §C) :
- CAPTCHA → HUMAN BLOCKER
- 2FA → HUMAN BLOCKER
- PIN → HUMAN BLOCKER
- Credentials non disponibles → HUMAN BLOCKER (directive §8 ci-dessus)

---

## Safety

**Aucune modification de Safety.** `browser.interact` reste SAFE par défaut / SENSITIVE si verbe dangereux (Phase 11). `browser.click_at_position` enregistré avec `PermissionLevel.SENSITIVE` — un clic à coordonnées pures sans description sémantique, classification prudente.

---

## Tests

### Nouveaux tests — 18 au total (dans la cible 15–30)

**`tests/devices/browser/test_chantier18b_browser_agentic.py`**

| Test | Type | Résultat |
|------|------|----------|
| `test_buttons_expose_left_and_top_in_read_page` | Réel E2E | ✅ PASS |
| `test_buttons_expose_aria_label_in_read_page` | Réel E2E | ✅ PASS |
| `test_primary_control_clickable_by_aria_label` | Réel E2E | ✅ PASS |
| `test_secondary_control_clickable_by_aria_label` | Réel E2E | ✅ PASS |
| `test_generic_buybox_prioritized_without_amazon_specific_ids` | Réel E2E | ✅ PASS |
| `test_type_empty_text_replace_does_not_clear_existing_content` | Réel E2E | ✅ PASS |
| `test_type_empty_text_append_submit_presses_enter_without_modifying` | Réel E2E | ✅ PASS |
| `test_submit_prefilled_form_via_click_on_submit_button` | Réel E2E | ✅ PASS |
| `test_click_at_position_capability_registered` | Unitaire | ✅ PASS |
| `test_click_at_position_using_read_page_coordinates` | Réel E2E | ✅ PASS |
| `test_click_success_then_read_page_confirms_objective_url` | Réel E2E | ✅ PASS |
| `test_objective_satisfied_navigating_directly_without_action` | Réel E2E | ✅ PASS |
| `test_click_fails_read_page_reveals_real_target_second_click_succeeds` | Réel E2E | ✅ PASS |
| `test_loop_detector_escalates_after_two_identical_browser_failures` | Unitaire | ✅ PASS |
| `test_loop_detector_resets_after_browser_success` | Unitaire | ✅ PASS |
| `test_type_replace_mode_regression` | Réel E2E | ✅ PASS |
| `test_type_submit_true_regression` | Réel E2E | ✅ PASS |
| `test_type_text_as_optional_field_in_schema` | Unitaire | ✅ PASS |

### Tests existants après modifications — 0 régression

| Suite | Avant | Après |
|-------|-------|-------|
| `test_browser_agent.py` | 19/19 | ✅ 19/19 |
| `tests/context_engine/` | 122/122 | ✅ 122/122 |

---

## Real E2E

Tous les tests browser (18B + existants) s'exécutent contre Edge/CDP réel et des pages HTML locales servies par `LocalFixtureServer`.

| Scénario | Statut | Preuve |
|----------|--------|--------|
| A — Form submit préfillé | ✅ PASS | `test_submit_prefilled_form_via_click_on_submit_button` |
| B — Primary/Secondary control | ✅ PASS | `test_primary_control_clickable_by_aria_label` + `test_secondary_control_clickable_by_aria_label` |
| C — DOM failure → recovery | ✅ PASS | `test_click_fails_read_page_reveals_real_target_second_click_succeeds` |
| D — Objective already satisfied | ✅ PASS | `test_objective_satisfied_navigating_directly_without_action` |
| E — CAPTCHA/2FA human blocker | NOT_TESTED — directive en place, pas de fixture CAPTCHA disponible |

---

## Regression Analysis

| Failure | Classification |
|---------|---------------|
| `tests/voice/test_tts.py` (2) | PRE-EXISTING — hardware Kokoro |
| `tests/devices/windows/test_windows_agent.py` (5) | PRE-EXISTING — Windows UI automation |
| `tests/architecture/test_chantier17_portability.py` (1) | PRE-EXISTING |
| Autres (7) | PRE-EXISTING — timing/scheduler/provider |

0 nouvelle régression introduite par Chantier 18B.

---

## Architecture Lint

Vérifié manuellement :
- Aucun nouveau manager/orchestrateur créé
- Aucun import `raya.models` dans `devices/`
- Aucune règle par site (Netflix/Amazon/YouTube)
- `click_at_position` dans `BrowserController` + handler dans `BrowserDeviceAgent` — même pattern que `click()`
- Directive "coordonnées observées uniquement" dans render.py — jamais dans le code d'exécution

---

## V1 Integrity

V1 non touchée. Vérifié : aucun fichier du répertoire V1 modifié.

Fichiers modifiés par ce chantier :
- `raya/devices/browser/controller.py`
- `raya/devices/browser/agent.py`
- `raya/tools/catalog/browser.py`
- `raya/context_engine/render.py`

Fichiers créés :
- `tests/fixtures/browser/login_prefilled.html`
- `tests/fixtures/browser/logged_in.html`
- `tests/fixtures/browser/video_card.html`
- `tests/devices/browser/test_chantier18b_browser_agentic.py`

---

## Remaining Gaps

- CAPTCHA/2FA human blocker : directive en place, pas de test E2E contre un vrai CAPTCHA (environnement non disponible)
- Visual fallback (screenshot → coordonnées) : screenshot existe, mais le modèle n'a pas de pipeline vision-to-coordinates automatisé — dépend des capacités multimodales du modèle actif
- `check_confirmation()` non exposé comme tool : accessible au modèle via `browser.read_page` (URL + texte page) — pas de nouveau tool nécessaire
