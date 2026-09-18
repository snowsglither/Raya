# RAYA V2 — Chantier 18 : Agentic Interaction Report

**Date :** 2026-09-07
**Statut :** COMPLET — 27/27 tests passants

---

## Résumé exécutif

Chantier 18 améliore le comportement agentique de RAYA V2 à l'exécution en ciblant quatre défauts comportementaux observés (questions parallèles, narration excessive, confirmation inutile, ignorance des redirections mid-task) et un problème de priorité de contexte. Aucune architecture n'a été créée. Seules des primitives existantes ont été améliorées.

---

## Problèmes ciblés

| ID | Symptôme observé | Catégorie |
|----|-----------------|-----------|
| P1 | Question conversationnelle pendant une tâche → abandon silencieux ou refus de répondre | Comportemental |
| P2 | Narration de chaque micro-action ("je vais ouvrir...", "je cherche...") | Comportemental |
| P3 | Demande de confirmation avant des actions sûres et réversibles clairement demandées | Comportemental |
| P4 | Nouvelle instruction pendant une tâche → restart complet ou ignorance totale | Comportemental + Infrastructure |
| P5 | Tâches actives perdaient leur slot de contexte face aux faits WorldState/Memory | Infrastructure |
| P6 | Historique de conversation tronqué à 5 tours → perte de résolution référentielle | Infrastructure |

---

## Changements effectués

### 1. `raya/context_engine/render.py` — 4 nouvelles directives système

Ajoutées dans `SYSTEM_RULES`, après les directives Chantier 19 :

**§A (Parallel Threads)** — *Résout P1*
> "Parallel threads — task continuity and conversational questions are independent..."
- La question conversationnelle reçoit une réponse directe. La tâche continue depuis son dernier checkpoint. Les deux threads sont indépendants.

**§B (Communication Policy)** — *Résout P2*
> "Communication policy — work silently by default, speak purposefully..."
- Mode par défaut : SILENT_WORK. Parler uniquement pour un résultat final, un blocage, un choix significatif, ou une clarification qui change l'action.

**§C (Human Judgment)** — *Résout P3*
> "Human judgment — ask only when it genuinely matters..."
- Demander uniquement si : (1) ≥2 options raisonnables avec outcomes différents ET (2) préférence inconnue. Toujours inclure une recommandation. Jamais "is it OK to proceed?" avant une action sûre clairement demandée.

**§D (Task Steering)** — *Résout P4 comportemental*
> "Task steering — when the user provides a new instruction or constraint..."
- Nouvelle instruction = adapter les étapes FUTURES, préserver le travail VÉRIFIÉ. Le marqueur "Updated user directive" dans le contexte indique que la directive est déjà intégrée.

**Steering guidance rendering dans TASK_STATE** — *Résout P4 rendu*
- Quand `c.get("steering_guidance")` est présent, la ligne `"Updated user directive for this task: {guidance!r}"` est ajoutée au rendu du contexte de la tâche courante.

### 2. `raya/context_engine/assembler.py` — 3 changements d'infrastructure

| Élément | Avant | Après | Résout |
|---------|-------|-------|--------|
| `_ACTIVE_TASK_RANK_SCORE` | `0.80` | `0.92` | P5 : les tâches actives sont moins facilement évincées du budget de contexte |
| `_conversation_history_section` `limit` | `5` | `10` | P6 : les 10 derniers tours sont disponibles (au lieu de 5) |
| `_task_state_section()` | sans steering | avec `steering_guidance` depuis `task.checkpoint` | P4 : le checkpoint est propagé dans le contexte |

### 3. `raya/harness/steering.py` — Implémentation réelle (remplace le stub NOT_IMPLEMENTED)

```python
def steer(state, instruction, *, tasks, focus) -> ErrorInfo | None
```

- Lit le `task_id` en focus depuis `focus.get_focus(state.session_id)`
- Vérifie que la tâche est `RUNNING` ou `PENDING`
- Persiste `{"steering_guidance": instruction}` dans le checkpoint (merge, préserve le plan existant)
- Retourne `None` sur succès, `ErrorInfo` typé sinon (`NO_FOCUS_TASK`, `TASK_NOT_FOUND`, `TASK_NOT_ACTIVE`)

### 4. `raya/harness/loop.py` — 2 ajouts

**`Harness.steer_active_task(session_id, instruction)`**
- Point d'entrée API pour le steering, exposé pour les interfaces (même principe que `confirm_pending`, `create_long_horizon_task`, etc.)
- Vérifie la session, délègue à `steer()`

**Injection dans `_run_long_horizon_step()`** — *Résout P4 infrastructure*
```python
steering_guidance = (task.checkpoint or {}).get("steering_guidance")
if steering_guidance:
    user_lines.append(f"Updated user directive (apply from this step forward): {steering_guidance}")
    user_lines.append("Preserve already completed and verified work — do NOT undo or repeat it.")
```
- Lire le checkpoint AVANT l'appel modèle → la directive est déjà persistée dès que `steer_active_task` est appelé.
- Le step suivant voit la directive sans qu'aucun restart du plan ne soit nécessaire.

---

## Tests — 27 nouveaux tests

### `tests/context_engine/test_chantier18_directives.py` (10 tests)
Présence structurelle des directives §A, §B, §C, §D dans `render_system_prompt()`, et rendu du `steering_guidance` dans TASK_STATE.

| Test | Vérifie |
|------|---------|
| `test_side_questions_directive_present` | §A : "Parallel threads" présent |
| `test_side_questions_directive_mentions_independent_threads` | §A : "task continues from its last checkpoint" |
| `test_communication_policy_directive_present` | §B : "Communication policy" présent |
| `test_communication_policy_directive_mentions_silent_execution` | §B : "silent execution", "do NOT narrate" |
| `test_human_judgment_directive_present` | §C : "Human judgment" présent |
| `test_human_judgment_directive_requires_recommendation` | §C : "always include", "recommendation" |
| `test_task_steering_directive_present` | §D : "Task steering" présent |
| `test_task_steering_directive_mentions_updated_directive_marker` | §D : "Updated user directive", "Preserve all work already verified" |
| `test_task_state_renders_without_steering_guidance_normally` | Sans guidance : pas de "Updated user directive for this task" |
| `test_task_state_renders_steering_guidance_when_present` | Avec guidance : ligne présente dans le rendu |

### `tests/harness/test_chantier18_steering.py` (10 tests)
Logique de `steer()` (pure, avec stubs) + intégration `Harness.steer_active_task()`.

| Test | Vérifie |
|------|---------|
| `test_steer_no_focus_returns_error` | `NO_FOCUS_TASK` quand aucune tâche en focus |
| `test_steer_task_not_found_returns_error` | `TASK_NOT_FOUND` si tâche disparue |
| `test_steer_completed_task_returns_error` | `TASK_NOT_ACTIVE` sur COMPLETED |
| `test_steer_failed_task_returns_error` | `TASK_NOT_ACTIVE` sur FAILED |
| `test_steer_running_task_updates_checkpoint` | RUNNING → checkpoint mis à jour, `None` retourné |
| `test_steer_pending_task_updates_checkpoint` | PENDING → checkpoint mis à jour |
| `test_steer_preserves_existing_checkpoint_plan` | Plan et autres clés du checkpoint préservés |
| `test_steer_overwrites_previous_guidance` | Nouvelle directive remplace l'ancienne |
| `test_harness_steer_active_task_no_session_returns_error` | `NO_SESSION` si session inconnue |
| `test_harness_steer_active_task_success_returns_none` | Succès avec harness réel |

### `tests/harness/test_chantier18_agentic.py` (7 tests)
Infrastructure : rank score, conversation limit, _task_state_section, bout-en-bout.

| Test | Vérifie |
|------|---------|
| `test_active_task_rank_score_is_0_92` | `_ACTIVE_TASK_RANK_SCORE == 0.92` |
| `test_conversation_history_default_limit_is_10` | Signature : `limit=10` |
| `test_task_state_section_includes_steering_guidance` | Section inclut `steering_guidance` depuis checkpoint |
| `test_task_state_section_no_guidance_when_absent` | Absent du content si pas dans checkpoint |
| `test_task_state_section_no_guidance_when_checkpoint_none` | Absent si `checkpoint=None` |
| `test_steer_active_task_persists_guidance_in_checkpoint` | Bout-en-bout : `steer_active_task` → `get_task().checkpoint` |
| `test_long_horizon_step_no_steering_line_when_not_set` | Sans guidance dans checkpoint → pas de "Updated user directive" dans le prompt |

---

## Contraintes respectées

- **V1 intacte** — aucun fichier V1 modifié, aucun import V2 → V1
- **Pas de nouvelle architecture** — aucun AstraManager, ReferenceResolverManager, deuxième orchestrateur, deuxième Context Engine
- **Safety inchangée** — les directives §B/§C n'exemptent pas des actions dangereuses (safety.should_stop() et les vérifications de Permission continuent à s'appliquer normalement)
- **Stubs honnêtes → implémentation réelle** — `steering.py` retournait `NOT_IMPLEMENTED` ; il retourne maintenant `None` sur succès, `ErrorInfo` typé sur échec
- **Jamais "succès déclaré sans vérification"** — le checkpoint est lu directement depuis la base de données dans `_run_long_horizon_step`, la directive n'est pas inventée

---

## Fichiers modifiés

```
raya/context_engine/render.py     — 4 directives §A–§D + steering_guidance dans TASK_STATE
raya/context_engine/assembler.py  — _ACTIVE_TASK_RANK_SCORE 0.80→0.92, limit 5→10, steering_guidance dans _task_state_section
raya/harness/steering.py          — implémentation réelle (remplace le stub NOT_IMPLEMENTED)
raya/harness/loop.py              — steer_active_task() + injection steering dans _run_long_horizon_step
```

## Fichiers créés

```
tests/context_engine/test_chantier18_directives.py   — 10 tests (directives + TASK_STATE rendering)
tests/harness/test_chantier18_steering.py            — 10 tests (steer() + Harness.steer_active_task())
tests/harness/test_chantier18_agentic.py             — 7 tests (infrastructure + bout-en-bout)
```

**Total : 27 tests — 27/27 passants**
