# RAYA V2 — Context Continuity & Agentic Relevance Repair (Chantier 18A)

## Résumé exécutif

Chantier 18A résout deux lacunes concrètes dans la continuité contextuelle de RAYA :

1. **Les sections ACTIVE_TASKS ne transmettaient ni la progression ni la directive de steering** — pendant une side question, le modèle ne pouvait pas rapporter l'état réel d'une tâche de fond ("slide 4 sur 10, 40%") ni appliquer une directive mid-turn déjà émise.

2. **Aucun test ne couvrait le chemin world_state_domains=() (défaut de handle_request)** — le chemin réel de production n'était validé par aucune suite, uniquement les variantes à domaines explicites.

Aucune nouvelle architecture. Aucun nouvel orchestrateur. Deux fonctions modifiées, 27 nouveaux tests.

---

## Phase 0 — Audit préalable (résultats)

### Q1 — World State déjà injecté dans assemble() ?
**OUI.** `assembler.py:288` appelle `_world_state_sections(world_state, world_state_domains)` systématiquement. Pas de bug ici.

### Q2 — retrieve_relevant(()) retourne tous les faits ou aucun ?
**Tous.** `store.py` : `if domains:` → filtre par domaine ; `else:` → retourne tous les faits non-supersedés. `()` est falsy → branche `else` → tous les faits.

### Q3 — Domaines du World State en production ?
`"browser"` (current_url TTL 60s, last_typed_text/last_typed_target/last_clicked_target TTL 300s), `"pc"` (active_window TTL 60s), `"filesystem"` (chemins nommés).

### Q4 — ACTIVE_TASKS transmettait-il progress et steering_guidance ?
**NON.** `_active_tasks_sections()` ne lisait que `task_id`, `objective`, `state`, `not_before` — zéro progress, zéro steering_guidance.

### Q5 — render_system_prompt rendait-il ces champs ?
**NON.** La branche `elif section.kind == SectionKind.ACTIVE_TASKS:` ne produisait aucun texte pour progress ni steering.

### Q6 — Tests existants couvrent-ils le chemin world_state_domains=() ?
**NON.** `test_context.py` passe toujours `world_state_domains=("pc",)` explicitement — jamais le défaut `()`.

### Bilan : 2 gaps confirmés, 0 overfix autorisé

---

## Changements appliqués

### `raya/context_engine/assembler.py` — `_active_tasks_sections()`

**Problème :** Le modèle ne voyait pas la progression d'une tâche de fond pendant une side question.  
**Preuve :** La fonction ne lisait pas `task.progress` ni `task.checkpoint["steering_guidance"]`.  
**Cause :** Lacune d'implémentation (champ jamais ajouté lors du Chantier 16).  
**Fix minimal :** Ajouter progress (si non-vide) et steering_guidance (si présent) dans le content de la section ACTIVE_TASKS.

```python
if t.progress.current_step or t.progress.percent is not None:
    content["progress"] = {
        "current_step": t.progress.current_step,
        "percent": t.progress.percent,
    }
sg = (t.checkpoint or {}).get("steering_guidance")
if sg:
    content["steering_guidance"] = sg
```

### `raya/context_engine/render.py` — branche `SectionKind.ACTIVE_TASKS`

**Problème :** Même si le content avait progress/steering, render_system_prompt ne les sérialisait pas.  
**Fix minimal :** Ajouter progress_str et la ligne "Active task directive:" dans la branche ACTIVE_TASKS.

```python
elif section.kind == SectionKind.ACTIVE_TASKS:
    c = section.content or {}
    progress = c.get("progress") or {}
    progress_str = ""
    if progress.get("current_step"):
        progress_str = f", step={progress['current_step']!r}"
        if progress.get("percent") is not None:
            progress_str += f" ({progress['percent']:.0f}%)"
    lines.append(
        f"Other active task ({c.get('task_id')}): {c.get('objective')!r}, state={c.get('state')}"
        + progress_str
        + (f", scheduled for {c.get('not_before')}" if c.get("not_before") else "")
    )
    if c.get("steering_guidance"):
        lines.append(f"Active task directive: {c.get('steering_guidance')!r}")
```

---

## Tests ajoutés (27 au total)

### `tests/context_engine/test_chantier18a_world_state_context.py` — 9 tests

Couvre le chemin production (`world_state_domains=()`) que les tests existants ne validaient pas :

| Test | Ce qu'il prouve |
|------|----------------|
| `test_world_state_included_with_default_empty_domains` | `domains=()` → tous les faits inclus |
| `test_browser_url_visible_in_context_after_navigate_observation` | browser.current_url dans le contexte |
| `test_pc_active_window_visible_in_context_after_launch_observation` | pc.active_window dans le contexte |
| `test_last_typed_text_in_context_after_type_observation` | browser.last_typed_text dans le contexte |
| `test_last_clicked_target_in_context_after_click_observation` | browser.last_clicked_target dans le contexte |
| `test_stale_world_state_still_appears_in_context` | Fait stale visible mais marqué STALE |
| `test_superseded_world_state_never_appears_in_context` | Seule la valeur courante visible |
| `test_world_state_rendered_with_freshness_and_domain` | Rendu inclut domain.key + freshness |
| `test_multiple_domains_all_included_by_default` | browser + pc coexistent dans le contexte |

### `tests/context_engine/test_chantier18a_active_task_enrichment.py` — 10 tests

Valide le fix ACTIVE_TASKS à deux niveaux (assembler + render) :

| Test | Ce qu'il prouve |
|------|----------------|
| `test_active_task_includes_progress_when_step_set` | progress inclus si current_step non-vide |
| `test_active_task_includes_progress_when_only_percent_set` | progress inclus si percent seul |
| `test_active_task_no_progress_key_when_empty` | pas de clé "progress" si TaskProgress par défaut |
| `test_active_task_includes_steering_guidance_when_set` | steering_guidance inclus depuis checkpoint |
| `test_active_task_no_steering_key_when_checkpoint_none` | pas de clé si checkpoint=None |
| `test_active_task_no_steering_key_when_checkpoint_has_no_guidance` | pas de clé si clé absente du checkpoint |
| `test_active_task_progress_rendered_in_system_prompt` | step + percent dans le rendu texte |
| `test_active_task_steering_rendered_in_system_prompt` | "Active task directive:" dans le rendu |
| `test_active_task_no_step_in_render_when_not_set` | pas de "step=" si progress vide |
| `test_active_task_no_steering_in_render_when_not_set` | pas de directive si absent |

### `tests/harness/test_chantier18a_referential_continuity.py` — 8 tests

Prouve l'intégration bout en bout via `assemble()` avec les deux types de données simultanément :

| Test | Ce qu'il prouve |
|------|----------------|
| `test_active_task_visible_in_assembled_context` | assemble() avec active_tasks produit ACTIVE_TASKS |
| `test_active_task_with_progress_in_assembled_context` | progress dans le context assemblé |
| `test_active_task_with_steering_in_assembled_context` | steering dans le context assemblé |
| `test_world_state_and_active_task_coexist_in_context` | WORLD_STATE + ACTIVE_TASKS coexistent |
| `test_side_question_render_shows_task_progress` | rendu complet montre step + % |
| `test_side_question_render_shows_task_steering` | rendu complet montre "Active task directive" |
| `test_side_question_render_shows_world_state_and_task_progress` | URL + progress dans le même rendu |
| `test_current_task_excluded_from_active_tasks_section` | exclude_task_id fonctionne |

---

## Résultats

| Suite | Tests | Résultat |
|-------|-------|----------|
| `test_chantier18a_world_state_context.py` | 9 | ✅ 9/9 |
| `test_chantier18a_active_task_enrichment.py` | 10 | ✅ 10/10 |
| `test_chantier18a_referential_continuity.py` | 8 | ✅ 8/8 |
| `tests/context_engine/` (régression complète) | 122 | ✅ 122/122 |

---

## Intégrité V1

**V1 non touchée.** Vérifiée via `git status` : aucun fichier V1 modifié.

Les seuls fichiers modifiés par ce chantier :
- `raya/context_engine/assembler.py` — `_active_tasks_sections()` uniquement
- `raya/context_engine/render.py` — branche `SectionKind.ACTIVE_TASKS` uniquement

Ces deux fichiers étaient déjà modifiés par Chantier 18 (pas V1).

---

## Invariants respectés

- **Contexte ≠ permission** : World State stale reste visible (marqué STALE) mais n'a jamais constitué une autorisation implicite — directive inchangée.
- **No overfix** : Seuls les gaps prouvés par audit ont été corrigés. Aucune réorganisation, aucun renommage, aucune abstraction supplémentaire.
- **Dosage** : 27 nouveaux tests (dans la cible 15–30).
- **Déterminisme** : Zéro appel modèle dans les tests de ce chantier.
