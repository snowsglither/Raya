# RAYA V2 — CHANTIER 15 IMPLEMENTATION REPORT

Agentic Reliability / Real-World Execution Reliability

Statut : **GO**

## 1. Executive Summary

Chantier 14 found and fixed two real agentic-reliability bugs (planner
blind to available tools; step evidence not shown to the model after a
successful non-idempotent tool call) after they caused a real, repeated
Telegram send incident. Chantier 15's job was to verify, systematically,
that this class of failure is structurally closed — not just patched at
the one site where it was observed.

The inspection found the two Chantier 14 fixes were sound but incomplete:
the "don't repeat a succeeded action" guarantee still depended entirely on
the model reading and obeying a prompt instruction — nothing prevented a
model from ignoring that instruction and calling the tool again for real.
It also found a distinct, separate semantic gap: a task blocked on a
Safety confirmation it can never resolve in the background was
indistinguishable, at the `TaskState` level, from a task that is
genuinely broken. Both are fixed here, in place, with the existing
architecture — no new orchestrator, no phone work, no interface changes.

## 2. Initial Architecture Inspection

Inspected (via a read-only research pass, no code touched until findings
were confirmed): `raya/contracts/task.py` (TaskState, ErrorInfo, Plan/
PlanStep), `raya/cognition/recovery.py` (LoopDetector), `raya/cognition/
verification.py` (VerificationOutcome), `raya/harness/loop.py`
(`_run_long_horizon_step`, `_handle_step_setback`, `_is_step_tool_
already_satisfied`, `cancel_task`, `recover`), `raya/harness/execution_
records.py` (ExecutionRecord, `decide_recovery`), `raya/harness/
scheduler.py` (STOP check ordering), `raya/attention/evaluator.py`
(`_decide_task_event`), `raya/tools/execution.py`, `raya/safety/
permissions.py`/`risk.py`. Findings are grounded in exact code read, not
assumption — see §4/§5.

## 3. Existing Mechanisms Reused

No new subsystem. Every fix in this chantier extends a mechanism that
already existed at its correct architectural layer:

- `TaskState` enum + `_ALLOWED_TRANSITIONS` (contracts) — extended with
  one new value, not replaced.
- `TaskRegistry.fail()`'s exact pattern — `block()` mirrors it precisely.
- `_is_step_tool_already_satisfied()` (Harness) — extended with one more
  guard clause, same function, same call site.
- `Tool.idempotent` (contracts, Phase 0) — was set on every Tool at
  registration but never read anywhere; now actually consulted.
- The `user_lines` prompt-assembly block in `_run_long_horizon_step`
  (Chantier 14) — extended with one more line, same mechanism.
- `self._tasks.checkpoint()` (already called at the end of the function)
  — the same call, invoked one more time, earlier.
- `AttentionEvaluator._decide_task_event` — one more `if event.type ==`
  branch, same dispatcher, same pattern as `task.recovered`/`task.failed`.

## 4. Problems Found

### Bug #1 — Exactly-once guarantee was prompt-only, not structural

**Symptom (found at inspection, not yet observed live post-Chantier-14):**
Chantier 14's fix makes the model's non-idempotent-tool-success evidence
*visible* in the next tick's prompt, with an instruction not to repeat
the action. But nothing stops the model from ignoring that instruction —
`_is_step_tool_already_satisfied()`, the one deterministic guard in the
pipeline, only recognizes success via a `Tool`'s `ObservationSpec`
(World-State-based). `telegram.send_message` (and any purely
side-effecting tool) has no `ObservationSpec` — so the guard was, and
remained, a no-op for exactly the class of tool that caused the Chantier
14 incident. The protection was 100% cooperative (prompt text), 0%
structural.

**Root Cause:** `_is_step_tool_already_satisfied()` had exactly one path
to "satisfied": comparing an `ObservationSpec`'s expected argument against
World State. No path existed for "this tool is explicitly non-idempotent
and it already reported success" — even though `Tool.idempotent` has
existed as a field since Phase 0.

**Fix:** `raya/harness/loop.py::_is_step_tool_already_satisfied()` — if
`step_evidence` shows a prior SUCCESS for this exact tool name AND
`tool_def.idempotent is False`, return `True` unconditionally, before
ever consulting `ObservationSpec`/World State. A step represents one
atomic objective (by construction, `build_plan()`), so a non-idempotent
tool that already succeeded once for this step structurally never needs
to run again for it.

**Regression Test:** `tests/harness/test_long_horizon.py::
test_non_idempotent_tool_never_called_twice_even_when_model_insists` —
scripts the fake model to **deliberately** request the same tool twice in
a row (simulating a model that ignores the prompt instruction); asserts
the real handler is invoked exactly once. Without the fix, this test
fails with `calls['n'] == 2` — reproducing the exact Chantier 14 incident
shape deterministically. Also: `test_non_idempotent_tool_already_
succeeded_is_never_replayed_structurally` (direct unit test of the new
guard clause, using a tool with no `ObservationSpec` at all, exactly like
`telegram.send_message`).

**Real-world validation:** Chantier 14's own final live retest (a real
Telegram send, `RAYA_V2_CHANTIER_14_IMPLEMENTATION_REPORT.md` §13) already
demonstrated exactly-once behavior in production conditions before this
fix existed — that success depended on the model cooperating. This
chantier does not re-send a real Telegram message (§16/§17: already
sufficiently covered by Chantier 14's real send plus this chantier's
deterministic proof that the guarantee no longer depends on model
cooperation) — see §17 for the explicit BLOCKED status of a repeat real
send and why it was skipped.

### Bug #2 — "Blocked pending confirmation" was indistinguishable from "genuinely broken"

**Symptom:** A background task whose only path forward requires a Safety
confirmation it has no interactive channel to obtain
(`CONFIRMATION_REQUIRED_IN_BACKGROUND`, a documented Chantier 14
limitation) ended up in `TaskState.FAILED` — the exact same terminal
state as `LONG_HORIZON_NO_PLAN` (corrupted checkpoint) or
`LONG_HORIZON_STUCK` (dependency deadlock). Both `Attention` and any
future caller could only tell them apart by string-matching
`task.error.code`.

**Root Cause:** `TaskState` never had a value representing "cannot
proceed without external action" as distinct from "execution technically
failed." The `CONFIRMATION_REQUIRED_IN_BACKGROUND` path also routed
through `_handle_step_setback(..., RecoveryAction.ESCALATE)`, which calls
`replan_step()` — asking the planner to "propose an alternative" makes no
sense for a confirmation gate (no alternative *tool call* can bypass a
Safety confirmation), so this was also doing unnecessary, meaningless
replanning work.

**Fix:**
- `raya/contracts/task.py` — new `TaskState.BLOCKED`, added to
  `_ALLOWED_TRANSITIONS` (`RUNNING → BLOCKED`, `BLOCKED → {RUNNING,
  CANCELLED}` — resumable exactly like `PAUSED`, never terminal), and to
  the closed `TaskEvent` catalogue (`task.blocked`).
- `raya/tasks/registry.py` — new `TaskRegistry.block(task_id, error)`,
  mirroring `fail()` exactly.
- `raya/harness/loop.py` — the `CONFIRMATION_REQUIRED_IN_BACKGROUND` path
  now calls `self._tasks.block(...)` directly and returns, **skipping**
  `_handle_step_setback`/`replan_step()` entirely (no replanning attempt
  for a confirmation gate).
- `raya/harness/loop.py::cancel_task()` — `BLOCKED` treated like
  `PENDING`/`PAUSED` (immediate cancel, nothing active in the scheduler).
- `raya/attention/evaluator.py` — new `task.blocked` branch, `PROCESS_NOW`
  (the user must act, same urgency class as `task.recovered`).

**Regression Tests:** `tests/contracts/test_contracts.py::
test_running_can_transition_to_blocked_and_resume`,
`test_blocked_can_be_cancelled_but_never_directly_completed`;
`tests/tasks/test_tasks.py::test_block_stores_error_and_is_resumable`,
`test_block_emits_task_blocked_event`; `tests/harness/
test_long_horizon.py::test_sensitive_tool_in_background_blocks_honestly_
never_bypasses_safety` (updated from the pre-existing "...fails_
honestly..." test — same scenario, corrected expectation),
`test_confirmation_required_in_background_never_attempts_replanning`,
`test_cancel_task_on_blocked_task_cancels_immediately`,
`test_resume_task_from_blocked_resubmits_and_completes`; `tests/
attention/test_attention.py::test_task_blocked_is_process_now_not_
background`.

**Real-world validation:** Not re-validated live — triggering a real
confirmation-gated background action purely to observe `BLOCKED` would
require staging a real sensitive action, which §3/Rule 4 of this chantier
explicitly forbids doing "just to test." The state-machine-level behavior
is deterministic and fully covered by the 7 tests above, exercising the
real `TaskRegistry`/`Harness`/`Safety`/`Scheduler` (only the model is
scripted).

### Bug #3 (minor, additive) — multi-tick prompt showed only the current step's evidence

**Symptom:** A step 2 that logically depends on what step 1 produced had
no structured access to step 1's result — only its own (empty, at first)
evidence, per Chantier 14's fix. The model would have to re-discover
step 1's outcome via tools, or risk narrating from assumption.

**Root Cause:** `user_lines` in `_run_long_horizon_step` only ever
included `step.evidence` for the *current* step — `plan.steps` (with
their `result`/`status`) were available in-process but never surfaced to
the prompt.

**Fix:** `raya/harness/loop.py` — one additional line: if any other step
in the plan is `COMPLETED`, a short one-line summary of each (`objective
-> result text`) is added to the prompt.

**Regression Test:** `tests/harness/test_long_horizon.py::
test_multi_tick_prompt_includes_prior_completed_step_result` — asserts
the exact text of a completed step 1's model-provided summary appears in
step 2's prompt.

**Real-world validation:** NOT_TESTED live in isolation — the real
calculator E2E (§17) is single-turn, not a multi-step Task; a dedicated
live multi-step Task scenario proving this specific improvement was not
run separately, since the deterministic test directly inspects the exact
prompt text sent to the model layer (the same code path a real model
would receive).

### Bug #4 (minor, latent, closed alongside Bug #1) — evidence could be lost in a narrow crash window

**Symptom (found at inspection, not observed live):** `self._tasks.
checkpoint(...)` (the durable write) was only called once, at the very
end of `_run_long_horizon_step`. If two tool calls happen in the same
model turn and the second one never returns (crash, hang), the first
one's real, successful evidence was held only in memory and never reached
the database.

**Root Cause:** Incremental progress within a single step was tracked in
memory (`step_evidence` dict, `step.evidence =`) but only persisted at
the very end of the enclosing function.

**Fix:** `raya/harness/loop.py` — `self._tasks.checkpoint(task_id,
{"plan": to_dict(plan)})` is now also called immediately after each
individual tool result is recorded into `step_evidence`, not only once at
the end.

**Regression Test:** `tests/harness/test_long_horizon.py::
test_step_evidence_is_checkpointed_immediately_not_only_at_end_of_step` —
a step with two tool calls where the second one hangs forever; asserts
the first tool's evidence is present in the **persisted** (re-read from
the registry) checkpoint while the process is still "frozen" mid-step.

**Real-world validation:** `tests/harness/test_long_horizon.py::
test_restart_never_replays_a_non_idempotent_tool_that_already_succeeded`
— a full two-process `bootstrap()` restart scenario (same technique as
the existing Phase 10 restart test): process 1 succeeds at a non-
idempotent tool call, then "crashes" (abandoned mid next-tick, no clean
shutdown); process 2 recovers, resumes, and even though its scripted
model tries the same tool again, the real tool is invoked exactly once
across the entire restart. This is the closest deterministic proxy to a
real Telegram-across-a-crash scenario without staging one (§16).

## 5. Root Causes (summary)

All four issues share one root cause pattern: a correctness guarantee
existed only as long as the **model cooperated** with a textual
instruction (Chantier 14's fix, and the pre-14 `ObservationSpec`
mechanism which simply didn't cover non-observable tools) or only as long
as no crash occurred at an inconvenient instant (checkpoint timing). None
involved a missing *architectural* layer — every fix closes a gap inside
a function that already existed at the correct place.

## 6. Changes Made

- `raya/contracts/task.py` : `TaskState.BLOCKED`, transition table,
  `task.blocked` event.
- `raya/tasks/registry.py` : `TaskRegistry.block()`.
- `raya/harness/loop.py` : confirmation-required path → `block()` (no
  replanning); `cancel_task()` treats BLOCKED like PENDING/PAUSED;
  `_is_step_tool_already_satisfied()` gains the idempotent=False
  short-circuit; prompt includes prior completed steps' results;
  incremental `checkpoint()` after each tool result.
- `raya/attention/evaluator.py` : `task.blocked` → `PROCESS_NOW`.
- No changes to `raya/harness/scheduler.py`, `raya/cognition/planning.py`
  (already fixed in Chantier 14), `raya/cognition/recovery.py`,
  `raya/cognition/verification.py`, `raya/harness/execution_records.py`.

## 7. Tool Selection Reliability

Verified sound (already fixed in Chantier 14: `build_plan()` receives
`available_tools`). Confirmed live again in this chantier's real
calculator E2E (§17): the model chose `pc.application.launch` +
`pc.ui.inspect`/`pc.ui.click` directly, never an invented alternative.
No further code change needed for this axis.

## 8. Duplicate Action Protection

Was prompt-only (Chantier 14) → now structural (Bug #1 fix, §4). A
non-idempotent tool's confirmed success can never be replayed for the
same step regardless of what the model does next, proven with a model
that actively tries to repeat it (`test_non_idempotent_tool_never_
called_twice_even_when_model_insists`) and across a real process restart
(`test_restart_never_replays_a_non_idempotent_tool_that_already_
succeeded`).

## 9. Evidence / Verification

`verify_tool_result()`/`VerificationOutcome`/`_promote_observations_and_
verify()` were already sound (confirmed at inspection: a tool with no
`ObservationSpec` still gets a correct `SUCCESS` outcome from raw
`ToolResultStatus` alone — verification itself was never the bug).
Unchanged. The gap was purely in *whether the model saw* that evidence
(Chantier 14) and *whether a second real action could still happen
regardless* (Bug #1, this chantier).

## 10. Task State Semantics

`PENDING/RUNNING/PAUSED/COMPLETED/FAILED/CANCELLED` + new `BLOCKED`.
`BLOCKED` is resumable (like `PAUSED`), never terminal, and reached only
from `RUNNING`. `_ALLOWED_TRANSITIONS` enforced via `Task.transition_to()`
raising `ValueError` on any illegal transition (unchanged mechanism,
verified still strict — `BLOCKED → COMPLETED` directly is rejected,
tested).

## 11. Failure / Replanning

`LoopDetector`/`replan_step()` confirmed generic (hash of tool name +
sorted arguments, no site/tool-name branching) and unchanged. The one
behavioral change: a confirmation-required block no longer triggers a
(meaningless) replanning attempt — verified by a test whose planning
script has exactly one entry, so any `replan_step()` call would crash the
test with "script exhausted."

## 12. Multi-Tick Continuity

Current step's own evidence (Chantier 14) + now also prior completed
steps' results (Bug #3 fix, §4) are both visible to the model at every
tick. Verified by direct inspection of the exact prompt text sent to the
model layer.

## 13. STOP / Safety

Inspected in full: `TaskScheduler._process_one_step` checks `should_stop()`
before calling `step_fn` at all; `_run_long_horizon_step` checks it before
each individual tool call. A narrow, harmless ordering gap exists (an
in-flight model call started just before STOP completes anyway, but no
tool ever fires afterward) — already documented as an accepted invariant,
confirmed by inspection to carry no real duplicate-action risk, not
modified. `CONFIRMATION_REQUIRED_IN_BACKGROUND` handling (Bug #2) never
bypasses Safety — it now correctly reports `BLOCKED` instead of `FAILED`,
but the underlying refusal (no silent confirmation bypass) is unchanged
and re-verified.

## 14. Recovery

`Harness.recover()`/`TaskRegistry.recover_after_restart()` unchanged.
The narrow evidence-loss window they could otherwise inherit is closed by
the incremental-checkpoint fix (Bug #4), proven directly by a real
two-process restart test (§4, Bug #4).

**Deferred, documented (§19):** `ExecutionRecord.decide_recovery()` is
correct (6 existing unit tests) but was found, at inspection, to be
**never called** anywhere in `resume_task()`/`recover()` — a full,
ExecutionRecord-driven resume policy remains unwired. The narrower,
targeted incremental-checkpoint fix closes the actual duplication risk
observed without requiring this larger wiring. Left as Future Work,
per this chantier's own scope rule (§24 of the consigne): fixing
`decide_recovery`'s wiring would mean changing how `resume_task()`
decides what to do with a resumed step, a bigger behavioral change than
"a small, targeted, in-place fix."

## 15. Tests Added

13 new tests, 3 existing tests corrected (not duplicated):

| File | New | Corrected |
|---|---|---|
| `tests/contracts/test_contracts.py` | 2 | 0 |
| `tests/tasks/test_tasks.py` | 2 | 0 |
| `tests/attention/test_attention.py` | 1 | 0 |
| `tests/harness/test_long_horizon.py` | 8 | 3 (1 renamed BLOCKED-not-FAILED, 2 given explicit `idempotent=True` they implicitly relied on before `idempotent` was ever read) |
| **Total** | **13** | **3** |

Within the 15–30 target range on the low end, deliberately: most axes
(A, E, F, H) were already sufficiently covered by Chantier 12/14 tests
(§16 below) — new tests were added only for genuinely new behavior.

## 16. Existing Tests Reused (not duplicated)

- Tool selection (Axis A): `tests/cognition/test_planning.py` (Chantier 14,
  `available_tools` forwarding).
- LoopDetector (Axis F): `tests/cognition/test_recovery.py` (8 tests,
  confirmed generic at inspection, untouched).
- Replanning (Axis F): `tests/harness/test_long_horizon.py::
  test_step_failure_triggers_replanning_and_task_still_completes`,
  `test_step_failure_without_alternative_fails_the_task_honestly`.
- STOP during tasks (Axis H): `tests/harness/test_long_horizon.py::
  test_stop_prevents_further_tool_calls_mid_task`, `tests/stop/
  test_scheduler_stop.py`.
- Restart recovery baseline (Axis I): `tests/harness/test_long_horizon.py::
  test_restart_recovers_a_running_long_horizon_task_and_continues_from_
  checkpoint`, `tests/harness/test_recovery_phase2.py`.
- `decide_recovery()` correctness (Axis I, orphaned wiring aside):
  `tests/harness/test_execution_records.py` (10 tests).

## 17. Real E2E

**Scenario 1 — Windows multi-step (real, live, priority #1 per consigne):**
"Ouvre la calculatrice et fais 7 + 3." → tools called:
`pc.application.launch, pc.screen.capture, pc.ui.inspect, pc.ui.click ×4,
pc.screen.capture` → response: *"J'ai ouvert la calculatrice et effectué
le calcul. L'affichage confirme le résultat : 7 + 3 = 10."* — grounded in
a real post-action screen read, not asserted from intent. **PASS.**
"Ferme-la." (second turn, same session) → `pc.application.close` called,
correctly resolving "la" from conversational context → *"Fait : j'ai
fermé la calculatrice."* **PASS** (multi-turn context continuity).

**Scenario 2 — Real Telegram send:** **NOT_RE-TESTED, deliberately.**
Chantier 14's own report already contains a real, live, single-message
Telegram send validating exactly-once behavior end-to-end
(`RAYA_V2_CHANTIER_14_IMPLEMENTATION_REPORT.md` §13, final retest).
Re-sending a real message here would violate this chantier's own Rule 4
("maximum UN message contrôlé si absolument nécessaire" — and only if
not already covered) for a fix (Bug #1) that is now — unlike Chantier
14's fix — proven *not* to depend on model cooperation, and is covered by
two deterministic tests that directly exercise the real `Harness`/
`TaskRegistry`/`Safety`/`ToolRegistry` pipeline. Judged unnecessary and
correctly declined rather than spamming Telegram again.

**Scenario 3 — Browser real task:** **NOT_TESTED.** Not exercised by any
Chantier 15 code change (no browser-specific code touched), and Chantier
11/12 already validated browser tool-selection/verification live. Adding
a new live browser scenario here would not exercise anything this
chantier actually changed — declined per Rule 3 (no test/validation
effort without a real gap to close).

## 18. PASS / FAIL / BLOCKED / NOT_TESTED

| Item | Result |
|---|---|
| Direct tool selection (planner aware of tools) | PASS (Chantier 14 fix, reconfirmed live §17) |
| Non-idempotent action never replayed (model cooperates) | PASS (Chantier 14) |
| Non-idempotent action never replayed (model does NOT cooperate) | PASS (this chantier, Bug #1) |
| Evidence chain (ToolResult → evidence → verification) | PASS (confirmed sound at inspection, unchanged) |
| BLOCKED distinct from FAILED | PASS (Bug #2) |
| No replanning attempted for a confirmation block | PASS (Bug #2) |
| Multi-tick continuity (current step evidence) | PASS (Chantier 14) |
| Multi-tick continuity (prior steps' results) | PASS (Bug #3, this chantier) |
| Evidence survives a crash mid-step (incremental checkpoint) | PASS (Bug #4, deterministic + real restart) |
| STOP checked before every step and every tool call | PASS (confirmed at inspection, unchanged) |
| Recovery does not duplicate a completed action | PASS (Bug #4 real restart test) |
| `ExecutionRecord.decide_recovery()` actually consulted on resume | **NOT_TESTED / NOT_WIRED** — orphaned, documented §14/§19, deferred |
| Real Windows multi-step E2E | PASS |
| Real Telegram re-send | **NOT_RE-TESTED** (deliberately, §17) |
| Real browser E2E | **NOT_TESTED** (deliberately, §17) |
| V1 integrity | PASS |
| Architecture lint | PASS |

## 19. Known Limitations

- **`ExecutionRecord.decide_recovery()` is orphaned.** It is correct
  (10 passing unit tests) but never called by `resume_task()`/`recover()`.
  A step resumed after a genuine crash relies entirely on
  `step.evidence`/the incremental-checkpoint fix (Bug #4) — which closes
  the specific duplication risk this chantier set out to close — but does
  not give the resume path a general, ExecutionRecord-driven policy for
  "was this specific operation actually executed, unknown, or not."
  Future Work: wire `decide_recovery()` into the resume path once a
  concrete need justifies the larger behavioral change.
- **Model-as-source-of-truth is stronger for single-turn tool calls than
  for long-horizon step summaries.** A direct conversational tool call's
  final response is built from a structured `_summarize_tool_result()` of
  the real `ToolResult` (the model only phrases it). A long-horizon step's
  final `result["text"]`, once the model is *allowed* to say "done"
  (gated on real evidence, Chantier 14 fix), is still the model's own free
  text, not independently re-derived from evidence. The risk window this
  leaves is narrow (the model can only reach this point after being shown
  real evidence) but not zero. Judged out of scope for a "small, targeted"
  fix — building a structured summarizer for arbitrary long-horizon steps
  would be new machinery, not a gap-closing fix. Documented, not built.
- **STOP/tool-call ordering** has a narrow, accepted, pre-existing gap
  (an in-flight model call can finish after STOP fires, before the next
  tool-call check) — confirmed at inspection to carry no duplicate-action
  risk, left unchanged.

## 20. Architecture Compliance

`Interface → Attention → Harness → Cognition/Tasks/Context → World
State/Memory → Tools → Environment` respected throughout. No new
orchestrator/manager/scheduler. `Attention` gained one more declarative
branch (`task.blocked`), never became a planner. `Context Engine`/
`Memory`/`World State` untouched, never execute. `Cognition` (`planning.py`,
untouched this chantier) still never calls a Tool directly. The Harness
remains the sole execution runtime; every fix lives inside it or in the
contract/registry layers directly beneath it.

## 21. V1 Integrity

`RAYA/` (V1): `git status --short` shows only pre-existing untracked
scratch files (`_shopping_full.txt`, `prompt_claude_code_*.txt`) — no
tracked file modified. V1 unchanged.

## 22. Regression Results

Targeted suites directly affected (`tests/contracts/`, `tests/tasks/`,
`tests/harness/`, `tests/attention/`, `tests/tools/`, `tests/cognition/`,
`tests/stop/`, `tests/context_engine/`): **525 passed, 1 failed** — the
failure is the same pre-existing, already-documented `.env`
`OLLAMA_API_KEY`-vs-`NullProvider` flake from every prior chantier this
session (`test_handle_request_fails_honestly_with_null_provider_stub`),
unrelated to any change here. Architecture lint (`scripts/arch_lint.py`):
**PASS**. Full, unrelated suites (voice, spatial, mobile, distributed
agents, etc.) — **NOT_RERUN — unchanged dependency** (consigne §22/Rule 3:
no structural change to a central shared infrastructure justifies a full
run; nothing outside `contracts/tasks/attention/harness` was touched).

## 23. Final Verdict

**GO.**

Two real, previously-latent reliability gaps were found and closed with
small, targeted, in-place fixes reusing existing mechanisms exactly where
they already lived: `_is_step_tool_already_satisfied()` (exactly-once,
now structural instead of cooperative) and a new `TaskState.BLOCKED`
(distinct from FAILED, correctly resumable, correctly surfaced to
Attention). Two smaller, additive gaps (multi-tick prior-step visibility,
incremental checkpoint timing) were closed alongside them. One deeper gap
(`decide_recovery()` never wired into resume) and one narrower one
(long-horizon step summaries not structurally re-derived from evidence)
are documented honestly as deferred rather than built here, per this
chantier's own scope discipline. No new orchestrator, scheduler, manager,
or interface was created. V1 is untouched. Stopping here.
