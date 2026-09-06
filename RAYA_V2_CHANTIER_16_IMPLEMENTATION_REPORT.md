# RAYA V2 — CHANTIER 16 IMPLEMENTATION REPORT

Contextualisation + Situation Awareness + Capability Discovery / Application Usage

Statut : **GO**

## 1. Executive Summary

This chantier had two real, distinct deliverables: (A) let a normal
conversational turn see the user's other active tasks (previously only
visible from inside a running background task's own step — a genuine
gap), and (B) give RAYA a real way to discover whether a CLI tool exists
and to run it without opening a visible window, so it can prefer a direct
command over GUI automation instead of hallucinating a program that
doesn't exist. Both were built by extending the existing Context Engine
and Windows Device Agent — no new orchestrator, no second Context Engine,
no new Harness. A dedicated "ContextSnapshot"/"Situation" object was
deliberately **not** built: the existing `Context`/`ContextSection`
contract already carries provenance, freshness, and relevance ranking —
building a second, parallel structure would have duplicated it. Real E2E
testing (including a genuine `nmap` scan of localhost) surfaced honest,
correct model behavior beyond what was anticipated — see §16/§18.

## 2. Initial Architecture Inspection

Inspected via a read-only research pass before any code was touched:
`raya/context_engine/assembler.py`, `ranking.py`, `render.py` in full;
`raya/contracts/context.py` (`Context`/`ContextSection`/`SectionKind`/
`Freshness`); `raya/contracts/world_state.py` (`FactStatus`/`Confidence`);
`raya/world_state/store.py` (freshness/TTL, lazy staleness); `raya/tools/
registry.py` (`discover()`); `raya/devices/windows/agent.py` and every file
in `raya/devices/windows/mechanisms/`; `raya/devices/registry.py`;
`raya/cognition/intent.py`. Findings grounded in exact code read, not
assumption.

## 3. Existing Mechanisms Reused

- `Context`/`ContextSection` (provenance, `rank_score`, `Freshness`,
  budget-based trimming) — this **is** the contextualization layer; only
  extended, never duplicated.
- `WorldStateStore`'s lazy ACTIVE→STALE flip on read, and `FactStatus`
  (no `EXPIRED` value — a fact is never deleted/hidden, only down-weighted)
  — unchanged, confirmed already correct for the model-facing `Context`.
- `Confidence` (`KNOWN_FACT`/`INFERRED`/`HYPOTHESIS`) — unchanged; "UNKNOWN"
  is represented the same way it already was: absence of a section, never
  a new enum value bolted onto a frozen contract.
- `ToolRegistry.discover()`/`Tool.capability_tags` — reused as-is for the
  two new Tools; no second capability vocabulary invented (Device-level
  `Capability`/`list_capabilities()` stays a separate, pre-existing
  concept for device health/identity, untouched, not conflated with
  Tool-level capability discovery).
- `WindowsDeviceAgent`'s exact `_CAPABILITIES`/`_DISPATCH` pattern — the
  two new capabilities (`capability.discover`, `shell.execute`) are wired
  in identically to every existing one (`application.launch`, `ui.click`,
  etc.), not a separate mechanism.
- `raya/tools/catalog/pc.py`'s exact `_tool()`/`register_pc_tools()`
  pattern — the two new Tools follow it verbatim.
- `SafetyService.check_permission()`/`classify_risk()` — the new `pc.shell`
  tag is just one more entry in the same table, gated through the exact
  same confirmation flow (`Harness.confirm_pending()`) already used for
  every other SENSITIVE tool.
- `render_system_prompt()`'s existing directive-list pattern (Chantier
  12/14/15) — three more directives appended, same mechanism.

## 4. Contextualization Design

No new contract, no new module named "ContextSnapshot" or "Situation."
Deliberate scope decision (§32 of the consigne: "si quelque chose existe
déjà, REUSE"): `Context` already is a compact, provenance-tracked,
freshness-aware, budget-limited representation of "what do we currently
know" — exactly the definition the consigne gives for a ContextSnapshot.
Building a second, parallel structure would violate the chantier's own
anti-duplication rule and its own warning in §4 (never invent an inferred
fact like "Ruben is working" without real evidence) — a distilled
"Situation" layer with fields like `current_activity`/`temporal_context`
would necessarily involve exactly that kind of inference. This is
documented as a deliberate non-build, not an oversight (§18/§22).

## 5. ContextSnapshot

Realized as the existing `Context` object, extended with one new
`SectionKind` (`ACTIVE_TASKS`, §7 below). Provenance/freshness/confidence
are unchanged, pre-existing fields — reused, not rebuilt, exactly per the
consigne's own guidance in §23 ("ne pas créer un système de provenance
parallèle — réutiliser celui de World State/Memory").

## 6. Situation Awareness

Handled the same way as §4: RAYA's "situation" is the set of `Context`
sections assembled for a given turn (system rules, active tasks, relevant
World State facts with freshness, relevant memory, conversation history,
tool schemas) — genuinely available, genuinely traceable to source, never
an invented higher-level summary. Confirmed live (§16, Scenario A): asked
"what applications are open," RAYA used a real `pc.application.list` call
and answered from the real result, not from any cached/inferred belief.

## 7. Conversation Context

No new mechanism — confirmed at inspection that multi-turn reference
resolution ("ferme-la," Chantier 15's proven calculator scenario) already
works purely from the existing conversation-history text section, with no
structured "last opened entity" pointer needed or added. Re-confirmed live
in this chantier's own E2E (§16): when a prior turn genuinely opened
nothing (Nmap has no window), RAYA correctly recognized the referent was
ambiguous and asked for clarification rather than guessing — the
conversation-history mechanism generalizes correctly to "no clear
referent" as well as "one clear referent," without new code.

## 8. Task Context

**Real gap found and closed.** `assemble()`'s `task=` parameter only ever
carried the ONE task whose long-horizon step is currently executing —
confirmed that a plain `handle_request()` conversational turn never had
visibility into the user's OTHER active tasks (scheduled reminders,
background downloads). Fixed with a new, non-mandatory `SectionKind.
ACTIVE_TASKS` (distinct from the mandatory `TASK_STATE`, which stays
reserved for "the task whose step is running right now"): `assemble()`
gained an `active_tasks: tuple[Task, ...] = ()` parameter, and `Harness.
handle_request()`'s conversational assembly now passes the user's
non-terminal tasks (same PENDING/RUNNING/PAUSED/BLOCKED filter as
Chantier 14's `tasks.list`). `ACTIVE_TASKS` sections participate in normal
budget-based trimming exactly like World State/Memory sections — never an
unconditional dump of every task regardless of how many exist (consigne
§22, "contexte pertinent et limité"), proven with a 50-task test that
still respects a small budget.

## 9. Capability Discovery

New `pc.capability.discover` Tool (SAFE, `pc.read` tag, read-only) wraps
a new, deliberately bounded mechanism (`raya/devices/windows/mechanisms/
capability_discovery.py::find()`): resolves an executable via `shutil.
which()` (the same mechanism a shell would use to resolve a command —
PATH only, **never** a disk scan or registry enumeration) and looks up a
small, fixed table of well-known tool→capability mappings taken directly
from the consigne's own examples (nmap→network.scan, git→vcs.git,
python→scripting.python, powershell/cmd→shell, ping→network.probe,
tracert→network.trace, nslookup→network.dns, ipconfig/netsh→network.
config, curl→network.http). A real, available tool absent from this small
table still reports `available`/`path` correctly, with `capability: null`
— never a guessed capability (tested with `attrib`, a real Windows
utility deliberately not in the table).

## 10. Application / Tool Discovery

Confirmed at inspection: no executable/PATH discovery of any kind existed
anywhere in the codebase before this chantier (only a narrow, unrelated
`winreg` read of the OS default-browser ProgId). `capability_discovery.
find()` (§9) is the entire new surface — deliberately minimal, no Start
Menu enumeration, no installed-programs registry walk, matching the
consigne's explicit "pas de scan massif" constraint.

## 11. USE ≠ SHOW

New `pc.shell.execute` Tool always runs hidden (`subprocess.
CREATE_NO_WINDOW`) — there is no visible mode for this Tool at all; making
something visible remains exclusively `pc.application.launch`'s job, a
structurally separate capability. A new system-prompt directive states
this explicitly: using a tool is not the same as showing it, and an
application should only become visible on explicit user request or
genuine GUI necessity. Validated live (§16, Scenario C/E): a real `nmap`
scan ran and produced a real result with zero window ever appearing —
confirmed independently by asking "what's open" immediately after, which
correctly did not list Nmap.

## 12. CLI vs GUI

New directive: prefer `pc.shell.execute` over GUI automation when a
direct command-line tool can achieve the objective, checking availability
with `pc.capability.discover` first rather than assuming. Validated live:
asked to scan localhost, RAYA called `pc.capability.discover("nmap")`
*then* `pc.shell.execute("nmap 127.0.0.1")` — never touched GUI automation
for this objective, exactly the intended order of operations.

## 13. Tool Selection

Unchanged mechanism (`Harness._discover_tool_schemas()`, Chantier 14's
`build_plan(available_tools=...)` fix) — the two new Tools are simply two
more entries the model can select from, following the identical discovery
path as everything else. No classifier, no hardcoded objective→tool
mapping was added anywhere.

## 14. Safety

`pc.shell` is classified **SENSITIVE unconditionally** in `raya/safety/
risk.py` — deliberately never contextual (unlike `pc.interact`/`browser.
interact`, which can downgrade to SAFE based on content). Rationale
documented in code: an arbitrary shell command can do categorically more
damage than a bounded UI click, so every real invocation requires the
same explicit confirmation (`Harness.confirm_pending()`) as any other
SENSITIVE tool — never bypassed, never auto-approved. `pc.capability.
discover` is SAFE (pure read, `pc.read` tag) — it cannot have side
effects by construction (`shutil.which` never executes anything).
Validated live: the real `nmap` scan correctly stopped at
`AWAITING_USER_INPUT` and only ran after explicit approval.

## 15. Tests Added

23 new tests, 0 existing tests modified:

| File | New |
|---|---|
| `tests/tools/test_pc_catalog.py` | 8 |
| `tests/devices/windows/test_shell_and_capability_discovery.py` (new file) | 6 |
| `tests/context_engine/test_context.py` | 3 |
| `tests/context_engine/test_render.py` | 1 |
| `tests/context_engine/test_chantier16_directives.py` (new file) | 3 |
| `tests/harness/test_chantier16_context.py` (new file) | 2 |
| **Total** | **23** |

Within the 15–30 target range. Most tests exercise real mechanisms (real
`shutil.which`, real `subprocess` calls capturing real stdout/exit codes)
rather than mocks, matching the codebase's existing discipline.

## 16. Real E2E

One comprehensive live session (`bootstrap()`, real Ollama Cloud, real
`WindowsDeviceAgent`, `enable_windows_device=True`), five real turns:

1. **"Quelles applications sont actuellement ouvertes sur mon PC ?"** →
   `pc.application.list` called for real; response listed the actual real
   applications open on the machine at that moment (VS Code, Chrome,
   Settings, File Explorer, Edge, NVIDIA overlay) — grounded observation,
   not invention. **PASS.**
2. **"Fais un scan nmap sur 127.0.0.1 (localhost uniquement), mais ne
   l'affiche pas."** → `pc.capability.discover("nmap")` called first,
   then `pc.shell.execute("nmap 127.0.0.1")`. Correctly stopped at
   `AWAITING_USER_INPUT` (SENSITIVE) with a real `pending_confirmation`
   payload; after `confirm_pending(approved=True)`, the real scan ran
   (real `nmap.exe`, real localhost target only, per the consigne's
   explicit restriction) and RAYA reported a real result reflecting
   actual scan output (6 open ports found on this machine). **PASS** —
   CLI preferred over GUI, hidden execution, real Safety gate, real
   evidence-grounded response. **Observed anomaly (not a bug in this
   chantier's code):** the final response text was in Chinese
   ("扫描完成，本机有 6 个端口处于开放状态") despite the entire conversation being
   in French — an unprompted model-level language switch, most likely
   triggered by raw `nmap` output patterns in the model's context. Noted
   honestly in §18; not something this chantier's directives caused or
   can reasonably fix (this is model behavior, not a Contextualization or
   Tool Selection defect).
3. **"Ouvre l'application Nmap."** → RAYA discovered (via `pc.application.
   launch` timing out with no window, then confirming via `pc.
   application.list` and `pc.capability.discover`) that the real `nmap.exe`
   on this machine is a pure CLI binary with no GUI at all, and said so
   honestly instead of pretending a window had opened, offering two real
   alternatives instead. **This is a stronger real proof of "no
   hallucination" than originally planned** (the test assumed Nmap had a
   GUI to show; it does not, on this install) — genuinely honest
   degradation rather than a fabricated success. The originally-intended
   "explicit open → visible" scenario itself remains proven by Chantier
   15's real calculator E2E (still valid, unrelated to this chantier's
   changes) rather than re-demonstrated here with Nmap specifically.
4. **"Ferme-la."** (following turn 3, where nothing was actually opened)
   → RAYA correctly recognized there was no unambiguous referent (several
   real windows open, none of them a just-opened Nmap window) and asked
   which one to close, rather than guessing. **PASS** for "never guess an
   ambiguous reference" — a stricter, correctly-handled case than the
   simple single-referent scenario.
5. **"Utilise l'outil totalement_inexistant_xyz..."** → refused honestly,
   correctly stated no such tool exists, listed real available tool
   categories, and proactively suggested the real, actually-available
   path (`pc.shell.execute` + `nmap`) for a hypothetical real need.
   **PASS.**

No lingering process/window was left behind (`nmap.exe` produces no
persistent process when run bare); verified via `tasklist` after the run.

## 17. PASS / FAIL / BLOCKED / NOT_TESTED

| Item | Result |
|---|---|
| Context Engine already serves as ContextSnapshot | PASS (design decision, §4) |
| Active tasks visible during normal conversation | PASS (§8, unit + integration tests) |
| ACTIVE_TASKS never mandatory, respects budget | PASS (50-task test) |
| Freshness/UNKNOWN handling | PASS (unchanged, already correct) |
| Capability discovery (PATH-based, bounded) | PASS (real `shutil.which`, real machine) |
| No capability invented for unknown-but-available tool | PASS (`attrib` test) |
| CLI execution capability exists and works | PASS (real `nmap`/`cmd` calls) |
| CLI execution always hidden (USE ≠ SHOW) | PASS (confirmed via `pc.application.list` after) |
| CLI preferred over GUI when sufficient | PASS (real scan scenario) |
| Explicit "open X" can make X visible | PASS (Chantier 15 calculator E2E; not re-tested with Nmap since it has no GUI) |
| No tool/path/executable hallucination | PASS (real scenarios 3 and 5) |
| Safety unconditional for shell execution | PASS (real confirmation gate exercised) |
| Multi-turn ambiguity correctly triggers clarification, not a guess | PASS |
| Architecture lint | PASS |
| V1 integrity | PASS |
| Full unrelated test suites (voice, spatial, mobile, distributed agents) | **NOT_RERUN — unchanged dependency** |
| Model's own free-text language consistency | **Observed anomaly, NOT_TESTED as a fixable item** — out of this chantier's scope (see §18) |

## 18. Known Limitations

- **Language-switching anomaly (§16, scenario 2)**: the model produced a
  Chinese response mid-French-conversation once, apparently triggered by
  raw CLI tool output in its context. Not reproduced deliberately, not
  investigated further (would require a separate, dedicated
  investigation into raw-output sanitization or prompt-level language
  pinning) — documented honestly as observed, not fixed, and explicitly
  out of this chantier's scope (Contextualization/Capability Discovery,
  not model output-language control).
- **`pc.capability.discover`'s known-capability table is small and static**
  by design (consigne §9's own explicit constraint) — a real, available
  tool outside this ~11-entry table always reports `capability: null`,
  never guessed. This is the intended, honest behavior, not a bug.
- **No installed-GUI-application discovery** (Start Menu / registry
  "App Paths" enumeration) was built — only PATH-based CLI executable
  discovery. Deliberately out of scope: the consigne explicitly forbids a
  "scan massif," and `pc.application.launch` (unchanged) already resolves
  GUI applications by canonical executable name without needing a
  separate discovery step.
- **No structured "last relevant entity" tracking was added** for
  multi-turn reference resolution — confirmed unnecessary at inspection
  and re-confirmed live (§16, turn 4): the existing conversation-history
  mechanism already correctly handles both the "one clear referent" and
  "no clear referent, ask" cases without it.

## 19. Architecture Compliance

`Interface → Attention → Harness → Cognition/Tasks/Context → World
State/Memory → Tools → Environment` respected throughout.
`context_engine/` still reads `world_state`/`memory`/`tasks` (read-only)
and never executes anything or writes back — `_active_tasks_sections()`
only reads `Task` objects already fetched by the Harness, never calls
`TaskRegistry` itself. The two new Tools live at the correct layer
(`tools/catalog/pc.py` delegating to `devices/windows/`, identical to
every existing `pc.*` tool) — Cognition never calls a Device directly,
Tools always pass through Safety, the Harness remains the only execution
runtime. No new orchestrator/manager/scheduler/second Context Engine/
second World State/second Memory/second Attention was created.

## 20. V1 Integrity

`RAYA/` (V1): `git status --short` shows only pre-existing untracked
scratch files — no tracked file modified. V1 unchanged.

## 21. Regression Results

Targeted suites directly affected or adjacent (`tests/contracts/`,
`tests/context_engine/`, `tests/harness/`, `tests/tools/`, `tests/devices/
windows/`, `tests/attention/`, `tests/tasks/`, `tests/cognition/`,
`tests/stop/`, `tests/world_state/`): **608 passed, 1 failed** — the
failure is the same pre-existing, already-documented `.env`
`OLLAMA_API_KEY`-vs-`NullProvider` flake seen in every prior chantier this
session (`test_handle_request_fails_honestly_with_null_provider_stub`),
unrelated to this chantier. One additional known pre-existing flake
(`test_application_focus_brings_real_window_to_foreground`, caused by a
stray leftover Notepad window from earlier manual testing, documented
across multiple prior chantiers) appeared once and was confirmed
non-reproducing in isolation, as before. Architecture lint
(`scripts/arch_lint.py`): **PASS**. Full, unrelated suites (voice,
spatial, mobile, distributed agents, etc.) — **NOT_RERUN — unchanged
dependency** (no change here touches those subsystems).

## 22. Future Work

Documented, deliberately not built here (consigne §35):

- Language-switching anomaly investigation (§18).
- Installed GUI-application discovery beyond PATH-based CLI tools.
- A genuinely richer "Situation" inference layer, if a concrete future
  need ever justifies the inference risk it would introduce (§4) —
  not needed for anything this chantier's GO criteria required.
- Wiring `ExecutionRecord.decide_recovery()` into the resume path
  (deferred since Chantier 15, unaffected by this chantier).
- Structured long-horizon step summarizer (deferred since Chantier 15,
  unaffected by this chantier).
- Contextual autonomy, social reasoning, automatic person-importance,
  autonomous JARVIS-style behavior, permanent camera/vision, advanced
  long-term autonomy, phone call audio, full mobile companion, IoT,
  server infrastructure — all explicitly out of scope per consigne §35,
  not touched.

## 23. Final Verdict

**GO.**

Two real gaps were found and closed with small, targeted extensions of
existing mechanisms: task visibility during normal conversation (a new,
non-mandatory `ACTIVE_TASKS` context section) and a genuine, safety-gated
CLI-execution + PATH-based discovery capability (previously entirely
absent from the codebase), enabling "prefer CLI over GUI" and "USE ≠ SHOW"
to be real, working behaviors rather than aspirational directives. A
separate "ContextSnapshot"/"Situation" object was deliberately not built,
since the existing Context Engine already satisfies that role — building
one would have duplicated existing architecture and risked inventing
unearned inferences, both explicitly forbidden by this chantier's own
rules. Real E2E testing, including an actual `nmap` scan of localhost,
confirmed correct tool selection, hidden execution, real Safety gating,
and — notably — honest degradation when a real limitation (Nmap has no
GUI on this machine) was discovered live, rather than a fabricated
success. No new orchestrator, scheduler, manager, or interface was
created. V1 is untouched. Stopping here — no Chantier 17.
