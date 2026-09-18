# RAYA V2 — Chantier Memory + Shared Data Layer + Experience Learning + Obsidian
## Implementation Report — 2026-09-08

---

## Résumé exécutif

Chantier Memory complet. Le pipeline Experience Learning est opérationnel end-to-end : task COMPLETED avec plan multi-étapes + retry → distillation → MemoryStore → retrieval sémantique → rendu dans le system prompt. Le Data Layer SQLite est corrigé (portabilité + sécurité OneDrive). Le logging fichier est actif. Obsidian est intégré en lecture seule. V1 archivée sans suppression.

**Tests : 34 nouveaux passent. 1246/1248 dans la suite complète (2 pré-existants, zéro régression).**

---

## Fichiers modifiés ou créés

### Code de production

| Fichier | Nature | Impact |
|---|---|---|
| `raya/memory/experience.py` | **NOUVEAU** | Pipeline de distillation d'expériences |
| `raya/memory/__init__.py` | Modifié | Export `maybe_distill_experience` |
| `raya/tools/catalog/obsidian.py` | **NOUVEAU** | Outils `obsidian.list_notes` + `obsidian.read_note` |
| `raya/tools/catalog/__init__.py` | Modifié | Export `register_obsidian_tools` |
| `raya/safety/risk.py` | Modifié | `"obsidian.read": SAFE` ajouté |
| `raya/memory/experience.py` | Modifié | Regex fragile corrigée (`#id`, `.class`, `password=`) |
| `raya/context_engine/assembler.py` | Modifié | `_experience_sections()` + exclusion EXPERIENCE de `_memory_sections` |
| `raya/context_engine/render.py` | Modifié | Rendu experience/rule/preference |
| `raya/harness/loop.py` | Modifié | Appel `maybe_distill_experience` dans `_finalize_long_horizon_task` |
| `raya/observability/logger.py` | Modifié | `setup_file_logging()` + rotation quotidienne |
| `raya/observability/__init__.py` | Modifié | Export `setup_file_logging` |
| `raya/runtime/bootstrap.py` | Modifié | `setup_file_logging(config.log_dir)` + `register_obsidian_tools` |
| `raya/runtime/config.py` | Modifié | Champs `log_dir`, `obsidian_vault_dir`, `_resolve_obsidian_vault()` |
| `raya/persistence/sqlite_backend.py` | Modifié | `journal_mode=DELETE` (OneDrive safety) |
| `.env` | Modifié | Suppression hardcode `RAYA_DB_PATH`, ajout `RAYA_LOG_DIR` |

### Tests (nouveaux)

| Fichier | Tests | Résultat |
|---|---|---|
| `tests/memory/test_experience_memory.py` | 10 | 10/10 ✅ |
| `tests/tools/test_obsidian_catalog.py` | 12 | 12/12 ✅ |
| `tests/context_engine/test_experience_context.py` | 6 | 6/6 ✅ |
| `tests/observability/test_file_logging.py` | 6 | 6/6 ✅ |

---

## Changements architecturaux

### 1. Experience Memory Pipeline

```
Task COMPLETED + plan multi-étapes (≥2 steps) + au moins 1 retry
    ↓
maybe_distill_experience() [harness/loop.py:_finalize_long_horizon_task]
    ↓
_is_experience_worthy() → critères : objective non-trivial, ≥2 steps, retry
    ↓
_extract_experience() → strategy, proof, interface, steps_count, attempts
    _sanitize_text() → supprime x=N, y=N, #id, .class, https://, password=, token=
    ↓
MemoryEntry(type=EXPERIENCE, layer=EXPERIENCE, channel_scope=inferred)
    ↓
MemoryStore.write() → SQLite (même base, pas de DB parallèle)
    ↓
[prochain tour pertinent]
_experience_sections() → memory.search(type_filter=EXPERIENCE)
    Guard: query_text < 5 chars → []  (pas d'expérience pour requête triviale)
    Guard: layer != EXPERIENCE → skip (via _memory_sections exclusion)
    ↓
render_system_prompt() → "Past successful strategy for: 'X' — approach: ..."
```

**Ce qui n'est PAS stocké :** coordonnées pixel, URLs, sélecteurs DOM CSS/XPath, tokens/passwords en format `key=value`, données triviales (salutations).

**Ce qui n'est PAS déclenché :** aucun appel modèle, aucune conversion log→mémoire, aucune sync SQLite↔Obsidian.

### 2. SQLite + OneDrive

- `PRAGMA journal_mode=DELETE` (était WAL) : supprime les fichiers `-wal`/`-shm` qui corrompent sous sync OneDrive.
- Contrainte documentée : un seul PC actif à la fois. Pas de concurrent multi-machine.
- `RAYA_DB_PATH` retiré du `.env` : path calculé depuis `RAYA_DATA_DIR/raya_v2.sqlite3` (non hardcodé).

### 3. File Logging

- Fichier : `RAYA_LOG_DIR/raya_YYYYMMDD_<hostname>.log`
- Rotation : `TimedRotatingFileHandler` quotidien, 30 jours de rétention.
- Guard : `_file_logging_initialized` — idempotent, pas de double handler.
- `RAYA_LOG_DIR` pointe vers `AppData/Local/RAYA/logs` (local, pas OneDrive) — ne polluera pas la sync.

### 4. Obsidian (lecture seule)

- `obsidian.list_notes` (SAFE) : liste récursive des `.md` dans le vault, exclut dossiers cachés.
- `obsidian.read_note` (SAFE) : lecture d'une note par chemin relatif, protection traversal via `relative_to()`, auto-ajout `.md`.
- Pas d'outil d'écriture — le modèle ne peut pas écrire dans Obsidian sans Safety/permission (non implémenté).
- Vault path : `RAYA_DATA_DIR/vault/` ou `RAYA_OBSIDIAN_VAULT_DIR`. Best-effort : si absent, aucun outil enregistré.

### 5. Corrections de bugs introduits

- **`_FRAGILE_PATTERNS` regex** : `#\w+` et `.class` ne fonctionnaient pas avec `\b` (# et . sont non-word chars). Corrigé via `#[\w-]+` et `(?<!\w)\.[\w-]+` + ajout patterns `password=`, `token=`, `secret=`.
- **`_memory_sections()` leakage** : les entrées `MemoryLayer.EXPERIENCE` remontaient via la recherche générale, court-circuitant le guard de pertinence de `_experience_sections`. Corrigé par exclusion explicite du layer EXPERIENCE dans `_memory_sections`.
- **`obsidian.read` tag absent** de `_RISK_BY_TAG` → classé SENSITIVE par défaut → tools bloqués. Corrigé : `"obsidian.read": PermissionLevel.SAFE`.

---

## État de la suite de tests

```
1246 passed, 5 skipped, 2 failed (pré-existants)
```

**Échecs pré-existants (antérieurs au chantier, confirmés par `git stash`) :**
- `test_loop.py::test_handle_request_fails_honestly_with_null_provider_stub` — cloud models répondent (deepseek-v4-flash) alors que le test attend un FAILED avec `null_provider`. Cause : modèles cloud actifs dans l'environnement.
- `test_recovery_phase2.py::test_recovered_task_can_actually_resume_and_complete` — race condition `COMPLETED → COMPLETED` sous charge de suite complète avec cloud.

**Aucune régression introduite par ce chantier.**

---

## Invariants respectés

- ✅ Aucun fichier V1 modifié
- ✅ Aucune suppression dans le repository V1 (archivage uniquement)
- ✅ LOG ≠ MEMORY (logs fichier, jamais convertis en MemoryEntry)
- ✅ Pas de DB parallèle pour les expériences (même SQLite que le reste)
- ✅ Pas de coordonnées/sélecteurs DOM dans Experience Memory
- ✅ Pas de secrets dans Experience Memory (pattern `key=value` filtré)
- ✅ Safety ne dépend pas de Memory
- ✅ Pas d'écriture automatique Obsidian
- ✅ Pas de sync bidirectionnelle SQLite↔Obsidian
- ✅ `ExecutionRecord` ne devient pas automatiquement Experience
- ✅ Règles de sécurité critiques dans Safety/Policy (non modifiable par le modèle)
- ✅ Chemin DB non hardcodé
