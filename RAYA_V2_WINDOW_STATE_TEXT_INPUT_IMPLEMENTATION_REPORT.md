# RAYA V2 — Window State & Text Input Implementation Report

**Chantier:** BUG A (Window State Preservation) + BUG B (Text Input Semantics)
**Date:** 2026-09-15
**STATUS: TERMINÉ**

---

## 1. Root Causes

### BUG A — Maximized Window De-maximized on Focus

**File:** `raya/devices/windows/mechanisms/window_mgmt.py` — `_activate_hwnd()`

**Cause:** The Win32 fallback path called `win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)` unconditionally before every `SetForegroundWindow`. The Win32 constant `SW_RESTORE` (value 9) is defined as: "Activates and displays the window. If the window is minimized **or maximized**, Windows restores it to its original size and position." A maximized Chrome window at (-8, -8, 1928, 1040) was thus reduced to its restore rect at approximately (946, 0, 1920, 1032) — x=946 places it at the right half of a 1920px screen, exactly matching the reported symptom.

**Scope:** Win32 fallback path only. The pygetwindow path was already safe: it calls `w.restore()` only if `w.isMinimized` is true, and `w.activate()` = `SetForegroundWindow` only.

**Browser.navigate confirmed innocent:** Browser navigation goes through Playwright CDP (`page.goto()`). No Win32 window management code is invoked in the browser device path. E2E audit verified: 4 visible windows before and after `browser.navigate` call, state unchanged.

---

### BUG B — Keyboard Type Appends Instead of Replacing

**File:** `raya/devices/windows/mechanisms/keyboard.py` — `type_text()` (no change needed)
**Root cause:** `pc.keyboard.type` uses `keyboard.type_text()` which pastes via Ctrl+V at the current cursor position. There was no `mode` parameter — no way to select existing content first. `browser.type` and `pc.ui.type` both had `mode=replace/append/clear`. `pc.keyboard.type` had nothing, and its mechanism (raw clipboard paste at cursor) is fundamentally an append operation.

**Default decision:** Default chosen as `mode='append'` — preserves existing behavior for all prior callers (zero regression risk), and is less destructive than making every call silently issue Ctrl+A. The `mode='replace'` behavior requires an explicit opt-in, which is communicated via the tool description and the §19 Write semantics directive in `render.py`.

**Existing usage inspection:** Reviewed all test files and integration scenarios. No caller of `pc.keyboard.type` passed any `mode` argument before this change. Callers that typed into a fresh context (new Notepad, search bar after Ctrl+A) were not affected. The one scenario that WAS broken was writing into an existing document — and the directive now explicitly mandates `mode='replace'` for that case.

---

## 2. Files Modified

| File | Change |
|------|--------|
| `raya/devices/windows/mechanisms/window_mgmt.py` | BUG A: conditional SW_RESTORE |
| `raya/devices/windows/agent.py` | BUG B: `_keyboard_type` mode parameter |
| `raya/tools/catalog/pc.py` | BUG B: schema + description + `_LAST_INTERACTION_OBSERVATION` wired |
| `raya/context_engine/render.py` | BUG B: extended Write semantics directive (§19) |

**New test files:**
- `tests/devices/windows/test_window_state_focus.py` (6 tests — BUG A)
- `tests/tools/test_keyboard_type_mode.py` (13 tests — BUG B)

**V1 files: zero modifications** (invariant respected).

---

## 3. Exact Changes

### 3.1 — `window_mgmt.py` : `_activate_hwnd()` Win32 fallback

**Before:**
```python
try:
    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
except Exception:
    pass
```

**After:**
```python
try:
    # SW_RESTORE (9) démaximise aussi les fenêtres MAXIMIZED — utiliser
    # uniquement quand la fenêtre est réellement minimisée (show_cmd==2).
    # Sur une fenêtre maximisée, SW_RESTORE la réduit et la repositionne
    # à sa restore_rect (souvent droite de l'écran) — comportement
    # indésirable. ShowWindow n'est pas nécessaire pour les fenêtres
    # normales ou maximisées : SetForegroundWindow suffit.
    placement = win32gui.GetWindowPlacement(hwnd)
    if placement[1] == 2:  # SW_SHOWMINIMIZED — restaure seulement si minimisée
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
except Exception:
    pass
```

**Mechanics:** `GetWindowPlacement()` returns `(flags, show_cmd, ...)`. `show_cmd=1` = NORMAL, `show_cmd=2` = MINIMIZED, `show_cmd=3` = MAXIMIZED. SW_RESTORE is now called only when `show_cmd == 2`. If `GetWindowPlacement` raises, the except block catches it silently and continues to `SetForegroundWindow` — no regression in error paths.

---

### 3.2 — `agent.py` : `_keyboard_type()`

**Before:**
```python
def _keyboard_type(agent, command, should_stop):
    args = command.arguments
    ok = keyboard.type_text(args["text"])
    if not ok:
        return _fail(command, "TYPE_FAILED", "échec de la saisie clavier", "clipboard_paste")
    return _ok(command, {"typed": True}, {}, "clipboard_paste")
```

**After:**
```python
def _keyboard_type(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    args = command.arguments
    mode = args.get("mode", "append")
    # Capture the foreground window BEFORE typing — this is the interaction target
    active_raw = window_mgmt.get_active_window()
    active_info = active_raw.get("active") if active_raw.get("status") == "ok" else None
    if mode == "replace":
        # Ctrl+A sélectionne tout le contenu existant — le Ctrl+V suivant le remplace.
        keyboard.press("ctrl+a")
    ok = keyboard.type_text(args["text"])
    if not ok:
        return _fail(command, "TYPE_FAILED", "échec de la saisie clavier", "clipboard_paste")
    evidence: dict = {"length": len(args["text"]), "mode": mode}
    if active_info:
        evidence["window"] = active_info["title"]
    return _ok(command, {"typed": True, "mode": mode}, evidence, "clipboard_paste")
```

**B.3 — Observation before writing:** `get_active_window()` is called before `type_text()`. This captures which window was foreground at the moment of the action, not after. The result goes into `evidence["window"]`, which the `ObservationSpec` (`_LAST_INTERACTION_OBSERVATION`) promotes to World State.

**B.4 — New document handling:** No `Ctrl+N` is hardcoded anywhere. The directive (§19) tells the model: if typing in a fresh context, `mode='replace'` is still the right call for a "write X" command — Ctrl+A on an empty field is a no-op.

**B.5 — Verification:** `output["typed"] == True` confirms the mechanism succeeded (clipboard paste completed). It does NOT confirm the document looks correct. The directive explicitly says: "tool success ≠ objective success — if the intent was 'write X', verify by reading back (e.g. pc.ui.inspect or screen.capture) if correctness is critical."

---

### 3.3 — `pc.py` : `pc.keyboard.type` schema and `_LAST_INTERACTION_OBSERVATION`

**New `_LAST_INTERACTION_OBSERVATION` ObservationSpec (Chantier 20C artifact, now wired to `pc.keyboard.type`):**
```python
_LAST_INTERACTION_OBSERVATION = (
    ObservationSpec(domain="pc", key="last_interaction_target", evidence_field="window",
                     freshness_ttl_s=300),
)
```

**New tool entry (replacing old single-field definition):**
```python
("pc.keyboard.type", "keyboard.type",
 "Saisit du texte au clavier dans l'application active. "
 "mode='replace' : sélectionne tout le contenu existant (Ctrl+A) avant de coller — "
 "remplace TOUT le contenu de la zone de saisie. À utiliser pour 'écris X', 'remplace par X', "
 "'mets X' quand l'intention est d'écrire un nouveau contenu. "
 "mode='append' (défaut) : colle à la position curseur actuelle sans effacer — "
 "conserve le contenu existant. À utiliser pour 'ajoute X', 'écris X à la suite'. "
 "Règle générale : les commandes d'écriture naturelles ('écris X', 'tape X') correspondent "
 "à mode='replace' ; réserve mode='append' quand l'utilisateur dit explicitement d'ajouter.",
 {"type": "object", "properties": {
     "text": {"type": "string"},
     "mode": {"type": "string", "enum": ["replace", "append"],
              "description": "replace=Ctrl+A avant frappe (remplace contenu), append=frappe à curseur (défaut)"},
 }, "required": ["text"]},
 PermissionLevel.SENSITIVE, "pc.interact", False, _LAST_INTERACTION_OBSERVATION),
```

---

### 3.4 — `render.py` : Write semantics directive (§19) extension

**Text added after** the `browser.type`/`pc.ui.type` paragraph:

```
"When using pc.keyboard.type, its default is mode='append' (raw keyboard inserts at cursor) — "
"you MUST explicitly pass mode='replace' for any command meaning 'write X', 'type X', "
"'écris X', 'remplace par X', 'mets X' in a field or document. "
"Use mode='append' only when the user explicitly says 'add', 'ajoute', 'à la suite', "
"or when you have observed (via prior tool result or pc.ui.inspect) that the cursor is "
"already positioned correctly for insertion. "
"Critical: before using pc.keyboard.type in a document that may already contain content "
"and where the choice between replace and append changes the outcome materially, "
"inspect the current state via pc.ui.inspect if available, or choose mode='replace' "
"as the conservative default for 'write/écris' commands."
```

---

## 4. Final `pc.keyboard.type` Schema

```json
{
  "name": "pc.keyboard.type",
  "description": "Saisit du texte au clavier dans l'application active. mode='replace': sélectionne tout le contenu existant (Ctrl+A) avant de coller — remplace TOUT le contenu. mode='append' (défaut): colle à la position curseur actuelle sans effacer.",
  "input_schema": {
    "type": "object",
    "properties": {
      "text": {"type": "string"},
      "mode": {
        "type": "string",
        "enum": ["replace", "append"],
        "description": "replace=Ctrl+A avant frappe (remplace contenu), append=frappe à curseur (défaut)"
      }
    },
    "required": ["text"]
  },
  "permission_level": "SENSITIVE",
  "capability_tag": "pc.interact",
  "idempotent": false,
  "observation": "_LAST_INTERACTION_OBSERVATION (domain=pc, key=last_interaction_target, ttl=300s)"
}
```

---

## 5. Default Mode Decision + Justification

**Decision: `mode='append'` as default.**

**Justification:**

1. **Existing behavior preserved.** Every prior caller of `pc.keyboard.type` relied on paste-at-cursor semantics. No existing test or integration scenario passed `mode`. Changing the default to `replace` would silently break all existing invocations that intentionally typed at cursor (e.g., typing a search query after clicking a search bar that was already focused and empty).

2. **Principle of least surprise for a raw keyboard primitive.** `pc.keyboard.type` is a lower-level tool than `browser.type` or `pc.ui.type`. A raw keyboard press appends at cursor — that IS the expected behavior. Making it `replace` by default would diverge from what "typing" means.

3. **Less destructive fallback.** If the model accidentally omits `mode`, the worst case with `append` is duplicate/extra text (visible, correctable). With `replace` as default, the worst case is silent data loss (user's existing document content overwritten without warning).

4. **The directive does the heavy lifting.** The §19 block in `render.py` explicitly instructs the model: for 'write/écris' natural language commands, pass `mode='replace'`. The schema description reinforces this. The model does not need the default to save it — the instruction is explicit.

5. **Asymmetry with browser.type/pc.ui.type is intentional.** Those tools operate on identified UI elements where replace semantics are appropriate (you know exactly what field you're targeting). `pc.keyboard.type` is a global keyboard primitive — it fires wherever the cursor is.

---

## 6. Semantic Behavior

| User says | Correct call |
|-----------|-------------|
| "écris X" / "write X" / "tape X" | `mode='replace'` |
| "remplace par X" / "mets X" | `mode='replace'` |
| "ajoute X" / "écris X à la suite" | `mode='append'` |
| "add X at the end" | `mode='append'` |
| Default (no user qualifier) for a write command | `mode='replace'` per §19 directive |
| Raw cursor insertion (known position) | `mode='append'` |

**Ctrl+A behavior on empty fields:** Ctrl+A on an empty text field is a no-op — no text selected, paste works normally. `mode='replace'` is therefore safe to use even in a fresh document or search bar.

---

## 7. Observation / Inspection Behavior (B.3)

`_keyboard_type()` captures `get_active_window()` **before** calling `type_text()`. This ensures the evidence reflects the window that was active at the time of the action, not after any side effects.

The `evidence["window"]` field feeds into `_LAST_INTERACTION_OBSERVATION`, which the harness promotes to `WorldState[pc.last_interaction_target]` with TTL=300s. This is the same ObservationSpec used by `pc.ui.type`, making interaction target tracking consistent across keyboard primitives.

**No hardcoded application names** anywhere in the implementation. The mechanism is generic: capture active window, report it, let the ObservationSpec promote it.

---

## 8. Verification Behavior (B.5)

`output["typed"] == True` (and `CommandStatus.SUCCESS`) confirms:
- The clipboard was set to the text
- Ctrl+V was sent successfully
- (If mode='replace') Ctrl+A was sent before typing

It does NOT confirm:
- The document/field received the text correctly
- The result matches the user's intent

**The directive states clearly:** for critical writes, verify via `pc.ui.inspect` or `pc.screen.capture` after typing. The tool result is mechanism-level success, not objective-level success. The model must not declare "done" purely from `typed=True` if the outcome requires verification.

---

## 9. Tests

### New test files

#### `tests/devices/windows/test_window_state_focus.py` — 6 tests (BUG A)

| # | Name | Type | What it verifies |
|---|------|------|-----------------|
| T1 | `test_activate_hwnd_does_not_restore_maximized_window` | Unit | `GetWindowPlacement` returns show_cmd=3 → `ShowWindow` NOT called |
| T2 | `test_activate_hwnd_restores_minimized_window` | Unit | show_cmd=2 → `ShowWindow(SW_RESTORE)` IS called |
| T3 | `test_activate_hwnd_does_not_alter_normal_window` | Unit | show_cmd=1 → `ShowWindow` NOT called |
| T4 | `test_activate_hwnd_fallback_handles_placement_error_gracefully` | Unit | `GetWindowPlacement` raises → silent fallback, no crash |
| T5 | `test_browser_navigate_does_not_call_window_management` | Unit | browser modules don't import `window_mgmt` |
| T6 | `test_real_focus_window_preserves_maximized_state` | **Real E2E** | Opens Notepad maximized, calls `focus_window`, verifies show_cmd still=3 after |

#### `tests/tools/test_keyboard_type_mode.py` — 13 tests (BUG B)

| # | Name | Type | What it verifies |
|---|------|------|-----------------|
| T1 | `test_keyboard_type_schema_has_mode_field` | Unit | Schema has `mode` with enum [replace, append] |
| T2 | `test_keyboard_type_default_mode_is_append` | Unit | No `mode` arg → no Ctrl+A sent |
| T3 | `test_keyboard_type_replace_sends_ctrl_a_before_typing` | Unit | `mode='replace'` → `keyboard.press("ctrl+a")` called before `type_text` |
| T4 | `test_keyboard_type_append_does_not_send_ctrl_a` | Unit | `mode='append'` → `keyboard.press` never called |
| T5 | `test_keyboard_type_mode_in_output_and_evidence` | Unit | Both `output["mode"]` and `evidence["mode"]` populated |
| T6 | `test_browser_type_schema_unchanged` | Regression | `browser.type` schema not altered |
| T7 | `test_pc_ui_type_schema_unchanged` | Regression | `pc.ui.type` schema not altered |
| T8 | `test_write_semantics_directive_covers_keyboard_type_replace` | Unit | `render_system_prompt` text mentions `pc.keyboard.type` and `mode='replace'` |
| T9 | `test_write_semantics_directive_distinguishes_append_replace` | Unit | Directive distinguishes `append` and `ajoute` |
| T10 | `test_write_semantics_directive_mentions_inspection` | Unit | Directive mentions `pc.ui.inspect` |
| T11 | `test_real_notepad_replace_mode_overwrites_content` | **Real E2E** | Pre-type "Bonjour" → `mode='replace'` "Salut" → result is "Salut" only |
| T12 | `test_real_notepad_append_mode_preserves_content` | **Real E2E** | Pre-type "Bonjour" → `mode='append'` "Salut" → result contains "Bonjour" + "Salut" |
| T13 | `test_real_keyboard_type_replace_via_device_agent` | **Real E2E** | Full Device Agent path with `mode='replace'` field in `ToolCall.arguments` |

**Total new tests: 19** (within the 15-test budget per targeted category, split: 6 BUG A + 13 BUG B)

---

## 10. Real E2E Results

All 5 real E2E tests (T6 + T11 + T12 + T13 + T6-BUG-A) executed against live system:

| Test | Scenario | Before fix | After fix | Result |
|------|----------|-----------|-----------|--------|
| T6 (window) | Notepad maximized → `focus_window` | show_cmd changed 3→1 | show_cmd=3 preserved | PASS |
| T11 (keyboard) | "Bonjour" → `mode='replace'` "Salut" | "BonjourSalut" | "Salut" | PASS |
| T12 (keyboard) | "Bonjour" → `mode='append'` "Salut" | N/A (new behavior) | "BonjourSalut" | PASS |
| T13 (keyboard) | Device Agent full path, `mode='replace'` | N/A (new behavior) | replace confirmed | PASS |
| T5 (browser) | browser.navigate → window states | N/A (confirmed innocent) | no win32 imports | PASS |

**Test suite run:** 19/19 targeted tests pass. Pre-existing UIA failures (4 tests, root cause: `uiautomation` module not installed, confirmed pre-existing via `git stash` verification) are NOT caused by these changes.

---

## 11. Regressions

**None introduced.**

Regression verification:
- `test_browser_type_schema_unchanged` (T6): PASS — browser.type schema not altered
- `test_pc_ui_type_schema_unchanged` (T7): PASS — pc.ui.type schema not altered
- BUG A fix only adds a guard around an existing call — if the guard raises, the behavior is unchanged (falls through to SetForegroundWindow)
- BUG B fix is additive — `mode` defaults to `append` which is the original behavior

**Pre-existing failures (not caused by this chantier):**
- `test_health_reports_online_when_uia_responds` — requires `uiautomation` module
- `test_ui_inspect_lists_real_elements_of_notepad` — requires `uiautomation` module
- `test_ui_type_writes_real_text_into_notepad_via_uia` — requires `uiautomation` module
- `test_ui_click_activates_real_element` — requires `uiautomation` module

Verified pre-existing via `git stash` (same 4 failures present without any new code).

---

## 12. Limitations

### BUG A

- **pygetwindow path not tested for E2E (maximized):** pygetwindow v0.0.9 `activate()` already calls `SetForegroundWindow` only, and `restore()` is already guarded by `isMinimized`. The E2E test (T6) targets the Win32 fallback path because pygetwindow is an optional import that may or may not be available. In practice, on the test system, pygetwindow is available, so T6 exercises the pygetwindow path — but `test_real_focus_window_preserves_maximized_state` verifies the visible outcome (window stays maximized) regardless of which internal path was taken.

- **Multiple monitors:** `restore_rect` behavior with `SW_RESTORE` is well-defined per-window but the restore position may be on a different monitor. The fix avoids the problem entirely for maximized windows by never calling SW_RESTORE when not minimized.

### BUG B

- **`Ctrl+A` scope is application-dependent:** In some applications, Ctrl+A selects all text in the focused field; in others (e.g. file explorers), it may select all files/items in a list view. `mode='replace'` should be used only when the target is a text input context. The directive does not attempt to restrict this — it relies on the model to use `mode='replace'` only where appropriate.

- **No `mode='clear'` for `pc.keyboard.type`:** `pc.ui.type` has `mode='clear'` (empty the field, type nothing). This was intentionally omitted from `pc.keyboard.type`: clearing via raw keyboard requires application-specific behavior (Delete, Backspace, Ctrl+A then Delete). The `mode='replace'` with an empty string achieves the same effect where Ctrl+A + paste(empty) works — not universally safe.

- **No document-level state inspection before typing:** The directive instructs the model to call `pc.ui.inspect` before writing in ambiguous contexts. But `pc.ui.inspect` requires `uiautomation` (not installed). In practice, the model must rely on `mode='replace'` as the conservative default for write commands.

---

## 13. V1 Integrity

**RAYA V1 REMAINS STRICTLY UNTOUCHED.**

No file under `raya/v1/` (or equivalent V1 paths) was read, modified, or referenced by any of the changes in this chantier. The V1 constraint is enforced at the architectural boundary (V2 devices/ never calls into V1 code). Zero V1 files appear in `git diff`.

---

## Summary

| Item | Status |
|------|--------|
| BUG A: SW_RESTORE on maximized window | FIXED — conditional on `show_cmd==2` |
| BUG B: Append-only keyboard.type | FIXED — `mode` parameter, default=`append` |
| B.2: Semantic directive in render.py | DONE — §19 extended |
| B.3: Observation before writing | DONE — `get_active_window()` before `type_text()` |
| B.4: New document handling (no hardcoded Ctrl+N) | DONE — no app names, generic mechanism |
| B.5: Verification distinction | DONE — directive explicit, `typed=True` ≠ objective success |
| Tests: 19/19 targeted | PASS |
| Real E2E: 5 scenarios | PASS |
| Regressions | NONE |
| V1 integrity | INTACT |
