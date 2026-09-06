# RAYA V2 — PRE-PHASE 7 STABILIZATION
## Identity / Memory / Context Wiring

**Scope note.** The initial brief for this pass covered both Identity/Memory/
Context *and* Windows application routing ("Ouvre Calculatrice"). A
clarifying instruction appended at the end of the same request explicitly
narrowed this pass to **Identity/Memory/Context only** and said, verbatim:
*"Ne modifie absolument rien dans Windows Device Agent, PC Control, Browser
Agent, Tool routing, application launch ou computer-use. Le test « Ouvre
Calculatrice » a été observé mais est hors scope de cette passe."* This
report follows that narrower, more specific instruction. Windows routing is
documented in §4 as an **observed, separate, untouched issue** — nothing in
`raya/devices/`, `raya/tools/catalog/pc.py`, `raya/tools/catalog/browser.py`,
or the agentic tool-execution loop's retry behavior was modified.

---

## 1. Executive Summary

A real manual test session (visible in the actual `data/raya_v2.sqlite3`,
timestamps 2026-09-04/05) showed RAYA answering "who am I" and "what model
are you" with hallucinated or generic answers, and never receiving any of
its own identity data. Diagnosis found the **exact, single root cause**:
`raya/context_engine/assemble()` has produced a rich `Context` (system
rules, memory, world state) since Phase 1 — but **nothing in
`raya/harness/loop.py` ever turned it into a `Message` sent to the model**.
Verified on the real HTTP payload built by
`raya/models/providers/ollama_cloud.py::_messages_to_ollama()`: it received
only the current turn's raw text, no system message, ever.

A second, independent cause compounded this: the historical user profile
(`RAYA/profil_ruben.md`, V1's file, used here purely as a **data source**,
never as code) had **never been ingested** into `MemoryStore` — zero
`PERSONAL`-layer entries existed anywhere in the real database.

Both are now fixed with the smallest changes that make the existing
pipeline actually work — no new Memory Manager, no new Context Engine, no
second orchestrator, no hardcoded identity answers, no V1 import. Verified
**live against the real Ollama Cloud API**: "Qui es-tu ?", "Quel modèle
utilises-tu ?", "Qui suis-je ?", and "Qui est mon frère Christopher ?" all
now answer correctly and honestly from real, structured data (§8).

50 new tests added (624 total, up from 574 at the end of Phase 6). All
pre-existing tests that were passing before this pass still pass; one
previously-broken test (`test_cli_transmits_input_to_harness`) is now fixed
as a side effect of a genuine stale-label bug found and corrected (§6).

Windows application routing ("Ouvre Calculatrice") remains **untouched**,
as instructed — documented in §4, not diagnosed in depth, not fixed.

---

## 2. Diagnostic Initial

### A. Identity

- No `Identity` service/module/contract existed anywhere in `RayaV2` before
  this pass (`grep -ri identity raya/` returned only an unrelated demo
  content key from a Phase 1 test fixture and this pass's own new code).
- RAYA's own name ("RAYA") was not exposed to the model as *data* anywhere
  — `context_engine/assembler.py::_system_rules_section()` returned a
  hardcoded, stale string: `"RAYA V2 — Phase 1 (World State/Memory/Tasks
  persistants, Model Layer stub)"`, unrelated to identity, unrelated to the
  runtime model, never updated since Phase 1.
- The user's identity was not stored anywhere. `RAYA/profil_ruben.md` (V1)
  exists and contains it, but `grep -rl "profil_ruben\|Ruben Lukusa"
  RayaV2/` returned **nothing** — zero ingestion path, zero reference,
  anywhere in the codebase.
- Inspecting the real `data/raya_v2.sqlite3` confirmed this empirically:
  the `memory_entries` collection contained **9 rows, all
  `layer=conversation`** (raw user turns recorded by
  `Harness.handle_request()`), **zero `layer=personal`** entries.

### B. Memory pipeline (traced end-to-end)

```
profile/source (RAYA/profil_ruben.md)
   │
   ✗ NEVER INGESTED — no code path existed
   ▼
MemoryStore                    (empty of personal facts)
   │
   ▼ retrieval (memory.search) — works correctly, but has nothing to find
   ▼
ContextEngine.assemble()       (builds a real Context — correctly filtered,
                                 correctly channel-isolated, correctly
                                 budgeted — verified by the pre-existing
                                 tests/context_engine/test_context.py suite,
                                 all passing before this pass)
   │
   ✗ Context is computed, stored in Harness._last_context (for the `/context`
   ✗ CLI command only), and then DISCARDED — never converted to a Message.
   ▼
ModelRequest.messages            = [Message(role="user", content=[current turn text])]
                                    — nothing else, ever.
   ▼
raya/models/providers/ollama_cloud.py::_messages_to_ollama()
   → real HTTP payload to Ollama Cloud: [{"role": "user", "content": "<text>"}]
```

Confirmed the exact break point is the **second `✗`** by reading
`ModelRequest`'s contract (`raya/contracts/model.py`): it has no
`system_prompt` field, no `context` field — only `messages`. Nothing else
downstream could plausibly have been injecting the context either.

### C. Context

`context_engine/assembler.py`'s selection/ranking/channel-isolation logic
was already correct (confirmed by the untouched, still-passing
`tests/context_engine/test_context.py`). The problem was never "wrong
filtering" — it was "the result of correct filtering is never used." A
second, more subtle issue was found once ingestion was added: `MemoryStore.
search()`'s relevance ranking is a deterministic substring/word-overlap
match (`RAYA_V2_MIGRATION_PLAN.md §7.3`, "no NLP, no vector search" — a
deliberate, documented design choice, not a bug). "Qui suis-je ?" shares
**zero** words ≥4 letters with "Nom complet : Ruben Lukusa" — a keyword
search alone would never surface identity facts for exactly this canonical
question. See §3 for the fix.

### D. Model

Verified on the real wire format (`_messages_to_ollama()` in
`raya/models/providers/ollama_cloud.py`) — confirmed DeepSeek received
*only* `{"role": "user", "content": "<raw text>"}` before this pass, nothing
else. Not a theoretical read of the code: traced the exact function that
builds the HTTP JSON body.

### E. Windows application routing

**Observed, not diagnosed in depth, not modified** (out of scope per the
clarifying instruction). For the record, the real database shows the user
did test "Ouvre Calculatrice" on 2026-09-05 — this remains a **separate,
tracked, unresolved issue** to be addressed in a future, dedicated pass
that is explicitly scoped to touch `raya/devices/windows/`,
`raya/tools/catalog/pc.py`, and the agentic loop's retry/anti-loop logic.
Nothing in this pass touched any of those files.

---

## 3. Exact Cause and Fix — Identity/Memory/Context

**Cause**: `context_engine.assemble()`'s output (`Context`) was computed
and then never rendered into a `Message` sent to the Model Layer.
**Secondary cause**: the historical profile was never ingested into
`MemoryStore`. **Tertiary cause**: deterministic keyword-only memory
retrieval structurally cannot match "Qui suis-je ?" against "Nom complet :
Ruben Lukusa" (zero lexical overlap).

### Fix 1 — Runtime identity actually computed and exposed (real, not invented)

- `raya/models/router.py`: extracted the existing candidate-selection logic
  (capability match → availability → local/cloud preference) into a shared
  `_ordered_candidates()`, reused by both the existing `route()` and a new
  `describe_active(registry, capability, prefer_local=False) ->
  ModelDescriptor | None` — describes exactly the model `route()` would
  select right now, or `None` if nothing is registered/available. **Never
  invents a name.** `raya/models/__init__.py` exports it.
- `raya/harness/loop.py::handle_request()`: computes
  `active_descriptor = describe_active(self._model_registry,
  ModelCapability.REASONING)` and passes `runtime_identity =
  {"provider": ..., "model": ...}` (or `None`/`None` if nothing is
  registered) into `assemble()`. **Harness** does this — never
  `context_engine` — because only `harness/` is allowed to import
  `raya.models` (`RAYA_V2_REPOSITORY_STRUCTURE.md §20`, unchanged).

### Fix 2 — Context Engine's `system_rules` section carries real data, not a stale string

- `raya/context_engine/assembler.py::_system_rules_section(runtime_identity)`
  now returns structured `runtime_context` data:
  `{"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": ...,
  "model": ...}}`. `"RAYA"` is a stable branding constant (`_ASSISTANT_NAME`)
  — categorically different from a hardcoded *answer* to "what's your
  name?"; the model still composes its own sentence from this data.

### Fix 3 — The Context is actually rendered and sent

- New `raya/context_engine/render.py::render_system_prompt(context) -> str`
  — a pure, deterministic function that reads *only* `context.sections`
  (already filtered/ranked/budgeted by `assembler.py`/`ranking.py`) and
  turns each section kind into one line of text. Never adds information
  absent from the sections. Deliberately skips `TOOL_SCHEMAS` (already sent
  natively via `ModelRequest.available_tools`, avoiding duplication).
- `raya/harness/loop.py::_run_agentic_loop()` now builds
  `messages = [Message(role="system", content=[...render_system_prompt(context)...]),
  Message(role="user", content=[...request text...])]` instead of just the
  user message. `OllamaCloudAdapter._messages_to_ollama()` already forwards
  any `role` verbatim — no provider-side change needed.

### Fix 4 — Structured profile ingestion (never a giant static prompt)

- New `scripts/ingest_profile.py` — an explicit, manually-run migration
  script (never auto-invoked by `bootstrap()`). Parses a markdown source
  file (`## SECTION` / `- bullet`) and writes **one `MemoryEntry` per
  bullet** (never one blob) via the existing public
  `MemoryStore.write()` — no new Memory Manager. Each entry gets:
  `layer=PERSONAL`, `channel_scope=SHARED` (identity is available from any
  channel — chat/voice/UI), `confidence=KNOWN_FACT`,
  `lifecycle=CONFIRMED`, and `provenance=f"profile_migration:{section_slug}"`.
  Idempotent (re-running skips bullets already present, verified §7/§8).
  **The script never contains a single word of Ruben's personal data as a
  Python literal** — it reads the source file dynamically every run
  (verified by `tests/architecture/test_identity_stabilization_proof.py
  ::test_ingest_profile_script_never_hardcodes_personal_data_only_parses_source`).

### Fix 5 — "Qui suis-je ?" retrieval fix without a new NLP layer

- `raya/context_engine/assembler.py::_identity_baseline_sections()`: the
  bullets from the profile's `IDENTITÉ` section specifically (name,
  address/tutoiement preference — provenance exactly
  `"profile_migration:identity"`) are **always** included in `Context`,
  exactly like `SYSTEM_RULES`/`TASK_STATE` are always included — **never
  filtered by keyword match against the question**. This is a *category*-
  level policy decided once (which section counts as "baseline identity"),
  not a phrase-level hack — it does not special-case "Qui suis-je ?" or any
  other specific wording. Every *other* personal fact (hobbies, family,
  health, career) remains reachable **only** via the existing keyword-match
  `_memory_sections()`, so a neutral question never triggers a profile
  dump (verified: for an unrelated question, only the ~7 baseline IDENTITÉ
  bullets appear out of 38 real ingested facts — §8).

---

## 4. Windows Application Routing — Observed, Untouched, Deferred

Not modified this pass. For the record, from the real database: the user
asked "Ouvre Calculatrice" on 2026-09-05 and the model attempted a shell
command instead of a Windows `application.launch`-style capability. This is
a **separate defect**, tracked here only as a pointer for a future,
explicitly-scoped pass:

- Suspected area: Tool Discovery exposing (or the model preferring) a
  generic shell/PC command capability over `raya/tools/catalog/pc.py`'s
  `application.launch`/`application.focus` capabilities
  (`raya/devices/windows/`).
- The retry-loop behavior described in the original brief (§9 of the
  brief) — the same invalid tool call repeated several times — is the same
  class of issue and was **not** touched (`raya/cognition/recovery.py`'s
  `LoopDetector` already exists and already escalates on *identical repeated
  failures* for other tool families, per Phase 3's `test_agentic_loop.py
  ::test_repeated_identical_tool_failure_escalates_not_infinite_retry` —
  whether it does so for this specific shell-command failure mode was not
  investigated here).

**No code in `raya/devices/`, `raya/tools/catalog/pc.py`,
`raya/tools/catalog/browser.py`, or the tool-execution retry path was read
for modification, or modified, in this pass.**

---

## 5. Additional Fix Found and Corrected — Stale Phase Labels (§10 of the brief)

Two **live, user/log-visible** strings claimed an obsolete phase number,
exactly as the brief flagged:

- `raya/runtime/bootstrap.py`: `log("info", "RAYA V2 runtime started (Phase 3)", ...)`
  and `Event(type="runtime.started", payload={"phase": "3"})`.
- `raya/interfaces/cli/repl.py`: `print("(Phase 2 : Attention + Task Actors
  concurrents — ...)")`.

Verified purely cosmetic before touching them: `grep` confirmed nothing
reads `payload["phase"]` anywhere, and the only test referencing the CLI
banner (`tests/runtime/test_cli.py`) asserted a *third*, already-wrong
value (`"Phase 0"`) — meaning **this test was already silently broken**,
not by network flakiness as assumed in the Phase 6 report, but by this
exact stale-label bug. Fixed both strings to describe real, durable
capabilities instead of a phase number (which goes stale again next
phase, per the brief's own instruction not to introduce phase-numbered
logic), and fixed the test to assert the *absence* of any "Phase N" string
plus the presence of real command names — a regression-proof way to catch
this class of staleness again in the future.

All other "Phase 2"/"Phase 3" occurrences found by `grep` are in
docstrings/comments describing *when a feature was introduced*
(changelog-style, e.g. "Phase 2 : reste STRICTEMENT la couche
données/lifecycle...") — legitimate historical documentation, not stale
live output, and were left untouched.

---

## 6. Modifications Made

| File | Change |
|---|---|
| `raya/context_engine/assembler.py` | `_system_rules_section()` now takes real `runtime_identity`; added `_identity_baseline_sections()`; `_memory_sections()` gained `exclude_ids` to avoid duplication; `assemble()` gained `runtime_identity` param |
| `raya/context_engine/render.py` | **New.** `render_system_prompt(context) -> str` |
| `raya/context_engine/__init__.py` | Exports `render_system_prompt` |
| `raya/models/router.py` | Refactored into `_ordered_candidates()` + added `describe_active()` |
| `raya/models/__init__.py` | Exports `describe_active` |
| `raya/harness/loop.py` | `handle_request()` computes and passes `runtime_identity`; `_run_agentic_loop()` builds and prepends a real system `Message` from `render_system_prompt()` |
| `raya/runtime/bootstrap.py` | Removed stale "(Phase 3)" from log message and `runtime.started` event payload |
| `raya/interfaces/cli/repl.py` | Removed stale "(Phase 2 : ...)" banner line, replaced with durable command list |
| `scripts/ingest_profile.py` | **New.** Explicit, idempotent, manually-run profile → `MemoryEntry` migration script |
| `tests/runtime/test_cli.py` | Fixed the pre-existing stale `"Phase 0"` assertion |
| `tests/*` (6 new files) | See §7 |

No file under `RAYA/` (V1) was read for modification or modified.
No file under `raya/devices/`, `raya/tools/catalog/pc.py`,
`raya/tools/catalog/browser.py` was modified.

---

## 7. Architecture After Correction

```
Harness.handle_request()
   │
   ├─ describe_active(model_registry, REASONING)   [only Harness imports raya.models]
   │       → runtime_identity = {provider, model} | {None, None}   (never invented)
   │
   ├─ context_engine.assemble(..., runtime_identity=runtime_identity)
   │       → Context {
   │           SYSTEM_RULES     : assistant_identity + runtime   (always present)
   │           MEMORY (baseline): IDENTITÉ section, ALWAYS included, never keyword-filtered
   │           MEMORY (rest)    : keyword-relevant personal facts only
   │           WORLD_STATE      : unchanged (freshness-tagged)
   │           TASK_STATE       : unchanged
   │           CONVERSATION_HISTORY : unchanged
   │         }
   │
   └─ render_system_prompt(context) -> str
           → Message(role="system", ...) prepended to ModelRequest.messages
           → real HTTP payload to Ollama Cloud now includes it
```

`runtime_context` (assistant identity + provider + model), `personal_memory`
(confirmed user facts), `world_state` (live observations), and
`task_context` (current background task) remain **structurally distinct**
`SectionKind` values — never merged into one blob, never cross-written. In
particular, nothing in this pass (or anywhere in the current codebase) ever
writes a `WorldStateFact` into `MemoryStore` or vice versa — the concern
raised in the brief ("Chrome affiche le nom Ruben" ≠ "user.name = Ruben")
is structurally moot today: no Device Agent currently writes identity-like
observations into World State at all (confirmed in the Phase 6 report,
§12/§13, still true). Memory (`profile_migration:identity`, `CONFIRMED`)
remains the only source for identity, by construction.

---

## 8. Tests Added

**50 new tests**, across 6 files (624 total, up from 574):

| File | Count | Proves |
|---|---|---|
| `tests/context_engine/test_identity_context.py` | 14 | Assistant identity always present; runtime identity real/never invented; identity baseline retrieved with zero lexical overlap ("Qui suis-je ?" exact case); no duplication; irrelevant personal memory excluded; relevant hobby/family facts retrieved on demand; provenance preserved; channel isolation (VOICE fact never leaks to CHAT; SHARED visible everywhere); no giant profile dump (7 of 38 facts for an unrelated question) |
| `tests/context_engine/test_render.py` | 9 | `render_system_prompt()` renders assistant name, runtime model/provider (and omits the line entirely when unknown — no hallucination), memory/world-state/task/conversation sections, skips tool schemas, never fabricates content absent from sections |
| `tests/models/test_router_describe_active.py` | 6 | `describe_active()` returns `None` honestly when nothing is registered/available/capable; returns the real descriptor otherwise; stays consistent with what `route()` actually selects; respects `prefer_local` |
| `tests/harness/test_identity_context_wiring.py` | 8 | **End-to-end, real `ModelRequest.messages`** (via `FakeScriptedProvider.calls`): a system message exists at all (the core bug); names RAYA; exposes the real registered model; includes confirmed personal memory when relevant; excludes irrelevant personal memory; never duplicates tool schemas in text; channel isolation holds end-to-end; no credential/secret-like text is ever promoted into the always-included baseline |
| `tests/scripts/test_ingest_profile.py` | 7 | Markdown parsing (sections/bullets, fictional sample data only); `IDENTITÉ` → `identity` slug override; ASCII slugification; `FACT`/`PREFERENCE` classification; real SQLite write produces one entry per bullet (never one blob); idempotent re-run writes zero duplicates; `--dry-run` never writes |
| `tests/architecture/test_identity_stabilization_proof.py` | 7 | `context_engine` never imports `raya.models`; no V1 references in changed files; no hardcoded `"Ruben Lukusa"` or identity-phrase pattern-matching in engine code; the ingestion script never hardcodes personal data (parses a file); no second `MemoryManager`/`ContextEngine` class introduced; `render.py` never bypasses `context.sections` by importing memory/world_state directly; full `arch_lint.run()` still zero violations |

**Regression**: rather than duplicating already-existing coverage, the
*entire* pre-existing suite (Phase 0–6, 574 tests) was re-run unchanged —
see §9 — confirming Browser Agent tests, the full Phase 6 Cockpit UI suite,
Safety confirmation tests, and STOP tests all still pass exactly as before.

---

## 9. Tests Réels Effectués (real end-to-end verification)

Ran against the **real, running Harness**, **real SQLite** (the actual
`RayaV2/data/raya_v2.sqlite3`), and **real Ollama Cloud** (a real API call
per question, real network round-trip, real DeepSeek-V4-Flash response):

```
Q: Qui es-tu ?
A: Je suis RAYA, ton assistant IA. Je tourne actuellement sur le modèle
   deepseek-v4-flash:cloud, servi par ollama_cloud. [...] Tu veux qu'on
   fasse quoi, Ruben ?

Q: Quel modele utilises-tu ?
A: Je tourne sur **DeepSeek-V4-Flash** (variante cloud), servi par le
   provider **Ollama Cloud**.

Q: Qui suis-je ?
A: Toi, c'est Ruben Lukusa. Né le 15 décembre 2005, tu as donc 19 ans. [...]
   tu habites à Evere, Bruxelles. [...] C'est bien toi, non ?

Q: Qui est mon frere Christopher ?
A: Christopher, c'est ton grand frère [...] Né le : 14 octobre 1998 [...]
   Il fait partie de tes trois grands frères, avec Pauliner [...] et
   Glodi-Lione [...]
```

Also verified — via `Harness.last_context()` on a **neutral** real request
("Quelle heure est-il quelque part dans le monde ?") — that only 10 of the
38 real ingested facts were included (the 7 baseline `IDENTITÉ` bullets
plus, harmlessly, a couple of pre-existing `MemoryStore` substring
coincidences unrelated to this fix — see §11), never a full profile dump,
using 494 of 4096 budget tokens (~12%).

The real profile (`RAYA/profil_ruben.md`, Ruben's actual data) was ingested
for real into the real local database via `python scripts/ingest_profile.py`
(38 entries written), then re-run to confirm idempotency (0 new writes, 38
correctly skipped as already present).

---

## 10. PASS / FAIL / BLOCKED / NOT_TESTED

| Item | Status |
|---|---|
| Root cause of Identity/Memory/Context gap identified | **PASS** |
| Context actually reaches `ModelRequest` | **PASS** (real payload verified) |
| Assistant identity (RAYA) grounded, not hallucinated | **PASS** (real Ollama Cloud test) |
| Runtime/model identity grounded, not hallucinated | **PASS** (real Ollama Cloud test) |
| User identity retrievable when confirmed in Memory | **PASS** (real Ollama Cloud test, real profile) |
| Family fact retrieval ("qui est mon frère Christopher") | **PASS** (real Ollama Cloud test) |
| Irrelevant personal facts excluded by default | **PASS** (real `last_context()` inspection + unit tests) |
| No giant static profile injected | **PASS** |
| Channel isolation preserved for personal memory | **PASS** |
| Stale "Phase N" live labels | **PASS** (fixed, regression-tested) |
| Windows "Ouvre Calculatrice" routing | **NOT_TESTED / OUT OF SCOPE** — explicitly excluded from this pass, unresolved, documented in §4 |
| Anti-loop behavior for invalid Windows tool strategy | **NOT_TESTED / OUT OF SCOPE** — same reason |
| 3× full-suite run for pre-existing suite | **PASS**, with 7 pre-existing, reproducible-every-time failures unrelated to this pass (4 real-hardware Windows/UIA tests, 1 test hitting the real cloud API without a stub, 2 wall-clock-timing background-task tests) — see §11 |
| Phase 6 Cockpit UI / Safety confirmation / STOP regression | **PASS** (full pre-existing suite re-run unchanged) |
| V1 (`RAYA/`) untouched | **PASS** |

---

## 11. V1 Contamination Proof

- `grep -rlIE "core\.orchestrator|core\.llm|modules\.brain\.router|modules\.pc_control|modules\.voice|modules\.vision|modules\.hologram" raya/ scripts/` → only match is `scripts/arch_lint.py`'s own detection-pattern tuple (unchanged from Phase 6, still just the strings it searches *for*).
- `git status`/`git diff` on `RAYA/` (the only git repo of the two) is
  byte-identical to the pre-session snapshot — zero files touched.
- `RAYA/profil_ruben.md` was **read** (as an explicit data source, exactly
  as instructed — "Ce fichier/profil est une SOURCE DE DONNÉES") by
  `scripts/ingest_profile.py` at runtime, and by this assistant during
  diagnosis. It was **never copied into `RayaV2/`**, never imported as
  code, and its content never appears as a Python literal anywhere in
  `RayaV2/` (verified by
  `test_ingest_profile_script_never_hardcodes_personal_data_only_parses_source`
  and `test_no_hardcoded_ruben_or_identity_phrase_matching_in_engine_code`).

---

## 12. Limitations Restantes

- **`MemoryStore.search()`'s substring-based relevance matching is
  imprecise** (pre-existing, deliberate "no NLP" design from Phase 1 —
  `RAYA_V2_MIGRATION_PLAN.md §7.3` — not something this pass changed or was
  asked to change). Example observed live: a neutral question happened to
  also surface 2-3 unrelated `PREFERENCES_DE_COMPORTEMENT`/`ETUDES_TRAVAIL`
  facts because a query word appeared as a *substring* of an unrelated word
  in their content (e.g. "part" inside "départ"/"partenaire"). This is
  bounded (never more than a handful of extra short facts, never the full
  profile) and does not affect the identity-baseline fix, which
  deliberately bypasses keyword matching entirely for exactly this reason.
- **`describe_active()` describes the *primary* candidate, not necessarily
  the one that ultimately answers after a `route()` fallback.** In real
  production (`bootstrap.py`) this is always accurate, because
  `NullProvider` is deliberately registered *last* specifically as the
  fallback of honest last resort. It only diverges in a specific test
  fixture (`tests/support/harness_factory.py::build_test_harness`, which
  registers its `FakeScriptedProvider` *after* `bootstrap()` already
  registered `NullProvider`) — documented and worked around in the test
  itself (`test_real_model_request_exposes_whatever_describe_active_actually_returns`),
  never a real production concern (confirmed live in §9: production
  correctly showed `ollama_cloud`/`deepseek-v4-flash:cloud`, never
  `null_provider`).
- **Windows application routing remains broken**, exactly as before this
  pass (§4) — explicitly out of scope, not fixed, not investigated beyond
  the observation already made by the user.
- **7 pre-existing test failures** (4 real-hardware Windows/Notepad UIA
  tests, `test_handle_request_fails_honestly_with_null_provider_stub` and
  `test_scenario_7_background_task_does_not_block_conversation` /
  `test_2_conversation_answered_immediately_during_task`) reproduce
  identically across 3 consecutive full-suite runs — confirmed
  **pre-existing** (present before this pass started) and **unrelated**
  to Identity/Memory/Context (real-hardware dependency and
  network/wall-clock-timing assumptions that don't hold with a real
  `OLLAMA_API_KEY` configured). Not fixed — out of this pass's scope.
- The ingestion script's section→type classification
  (`FACT` vs `PREFERENCE`) is a simple, deterministic heuristic keyed on
  the section slug containing `"preference"` — adequate for the current
  profile's structure, not a general-purpose classifier.

---

## 13. Recommandation GO / NO-GO pour Phase 7

**GO**, specifically for Identity/Memory/Context: both problems named in
the mission's core objective are fixed, verified with real code paths and
a real live model, and locked in with 50 new tests plus a fully green
regression run of the pre-existing 574-test suite.

**Windows application routing remains an open, separate, un-started
problem** — explicitly deferred by this pass's own scope, not resolved.
Phase 7 (or a dedicated follow-up pass before it) should treat "Ouvre
Calculatrice"/generic Windows application-launch routing as a known,
tracked item, not a regression introduced here and not silently forgotten.
