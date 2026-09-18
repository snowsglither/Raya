# RAYA V2 — Chantier 20K : Foundational Wiring Implementation Report

**Date :** 2026-09-16  
**Scope :** Repair of 5 foundational wiring gaps identified by the 20J Capability Baseline Audit  
**Status :** ✅ DONE — All 5 gaps closed, 12/12 tests pass, 3/3 E2E pass, zero regressions introduced

---

## Executive Summary

Chantier 20K repaired the 5 production gaps that blocked the future DOM+Vision refonte. No new architecture was introduced. Every change follows existing V2 patterns (DeviceAgent → catalog → bootstrap). V1 (`C:\Users\ruben\Desktop\MonAssistant`) was not touched.

---

## Gaps Closed

### C1 — image_ref silently dropped in `_messages_to_ollama()`

**File :** `raya/models/providers/ollama_cloud.py`

**Before :** `_messages_to_ollama()` filtered `if p.type == "text"` only. Every `ContentPart(type="image_ref")` was silently discarded — the model never received image data.

**After :**
- Extracted named helper `_encode_image_ref(path) -> str | None` (base64-encodes local file; returns None gracefully if missing/unreadable — never raises)
- `_messages_to_ollama()` now builds `entry["images"] = [b64, ...]` for all valid `image_ref` parts
- Missing files are silently skipped (graceful degradation, matches test expectations)
- `OllamaLocalAdapter` inherits automatically (no change needed there)

**Tests :** `tests/models/test_ollama_image_transport.py` — 7 tests (pre-written, now passing)

---

### C2 — `register_visual_tools` not exported, not called in bootstrap

**Files :**
- `raya/tools/catalog/__init__.py` — added `from .visual import register_visual_tools` + `__all__` entry
- `raya/runtime/bootstrap.py` — added `_register_vision_tools()` helper + call between `_register_devices()` and `_start_perception()`

**Before :** `register_visual_tools` existed in `visual.py` but was not exported from the catalog package. Bootstrap never called it. The 5 vision tools were inaccessible to the model.

**After :**
- `register_visual_tools` importable from `raya.tools.catalog`
- `_register_vision_tools()` constructs the 3 required callables:
  - `_observe_fn` wraps `raya.models.vision.observe_image(models, ...)`
  - `_capture_screen_fn` uses `pyautogui.screenshot()` → PIL dimensions → `{path, width, height}`
  - `_capture_browser_fn` calls `browser.screenshot` via `Command` → `{path, width, height}`
- Best-effort: if pyautogui/vision unavailable, logs warning and continues (never crashes bootstrap)
- Confirmed working: E2E-1/2/3 logs show "vision tools registered" on startup

**Tests :** `tests/test_20k_foundational_wiring.py::TestVisualToolsExport` — 2 tests

---

### C3 — `browser.click_at_position` absent from `_DISPATCH` and catalog

**Files :**
- `raya/devices/browser/controller.py` — added `click_at_position(x, y)` method using `page.mouse.click(x, y)` via `_worker.run_sync`
- `raya/devices/browser/agent.py` — added `_click_at_position` handler, added to `_DISPATCH` and `_CAPABILITIES`
- `raya/tools/catalog/browser.py` — added `_CLICK_AT_POSITION_OBSERVATION` + `browser.click_at_position` entry in `_defs`

**Before :** `browser.click_at_position` appeared in architecture blueprints but was absent from `_DISPATCH`, `_CAPABILITIES`, and the tool catalog. The grounding pipeline (`vision.find_in_browser` → `screen_x/screen_y` → action) had no mechanism to execute.

**After :**
- `BrowserController.click_at_position(x, y)` → `page.mouse.click(x, y)` → `{status, x, y}`
- `_click_at_position` handler in `agent.py`: parses x/y, calls controller, returns `evidence={"clicked_at": "x,y"}`
- `_CLICK_AT_POSITION_OBSERVATION` promotes `browser.last_clicked_at` to WorldState (domain=browser, key=last_clicked_at, evidence_field=clicked_at, ttl=30s)
- Capability in `_CAPABILITIES` for DeviceRegistry health/discovery

**Tests :** `tests/test_20k_foundational_wiring.py::TestClickAtPositionWiring` — 5 tests

---

### C4 — `browser.screenshot` returns no `width`/`height`

**Files :**
- `raya/devices/browser/controller.py` — `screenshot()` now returns `{status, path, width, height}`
- `raya/devices/browser/agent.py` — `_screenshot()` passes dimensions through to `output`

**Before :** `screenshot()` returned `{"status": "ok", "path": path}` with no dimensions. `capture_browser_fn` in bootstrap couldn't build a valid `ViewportInfo`, making coordinate conversion impossible.

**After :**
- PIL Image.open used to read real dimensions from the saved PNG
- Fallback: `page.viewport_size` if PIL unavailable or file missing
- Width/height appear in both controller result and agent output
- `_screenshot` in agent passes them through only when present (safe for older code paths)

**Tests :** `tests/test_20k_foundational_wiring.py::TestScreenshotDimensions` — 2 tests

---

### C5 — `browser.last_clicked_target` WorldState key never written

**Files :**
- `raya/tools/catalog/browser.py` — replaced empty `observation=()` on `browser.click` with `_LAST_CLICKED_OBSERVATION`
- `raya/devices/browser/controller.py` — `click()` now returns `{status, target, url}` (includes current URL after click)
- `raya/devices/browser/agent.py` — `_click()` passes `evidence={"clicked_target": target, "url": url}`

**Before :** `browser.click` had `observation=()` — no evidence field → nothing ever promoted to WorldState. `browser.last_clicked_target` could never be read back.

**After :**
- `_LAST_CLICKED_OBSERVATION` = 2-tuple:
  1. `ObservationSpec(domain="browser", key="last_clicked_target", evidence_field="clicked_target", ttl=30s)` — tracks what was clicked
  2. `ObservationSpec(domain="browser", key="current_url", evidence_field="url", ttl=60s)` — tracks URL after click (no `expected_argument` — different semantics from `browser.navigate`)
- `BrowserController.click()` captures `page.url` after click, returns in result dict
- Agent `_click()` populates both `clicked_target` and `url` in evidence

**Tests :** `tests/tools/test_safe_autonomy.py::TestBrowserClickObservationIncludesUrl` — 5 tests (pre-written, now passing)  
**Fix :** Corrected mock in `test_click_result_contains_url` — `AsyncMock` replaced with `MagicMock` for sync Playwright API, asyncio wrapper removed from `fake_run_sync`.

---

## Test Summary

| File | Tests | Status | Notes |
|------|-------|--------|-------|
| `tests/test_20k_foundational_wiring.py` | 9 | ✅ All pass | C2/C3/C4 wiring |
| `tests/integration/test_20k_e2e_vision_browser.py` | 3 | ✅ All pass | E2E-1/2/3 real sites |
| `tests/models/test_ollama_image_transport.py` | 7 | ✅ All pass | C1 (pre-written) |
| `tests/tools/test_safe_autonomy.py` | 59 | ✅ All pass | C5 (pre-written) |

**New tests created :** 12 (9 unit + 3 E2E) — within the 12-test limit  
**Pre-written tests unlocked :** 66 (previously failing on import, now passing)

---

## E2E Results (3 Real Sites)

| Test | Site | Result | Notes |
|------|------|--------|-------|
| E2E-1 | Desktop screen | ✅ PASS | pyautogui + gemma4:cloud → real description |
| E2E-2 | fr.wikipedia.org | ✅ PASS | vision.find_in_browser + browser navigate |
| E2E-3 | github.com | ✅ PASS | browser.read_page + vision.observe_browser coexist |

Model used: `gemma4:cloud` (VISION capability, declared in `_DEFAULT_MODEL_POOL`)

---

## Architecture Invariants Respected

| Rule | Status |
|------|--------|
| V1 (`MonAssistant`) not touched | ✅ |
| No new managers/orchestrators/parallel systems | ✅ |
| No hardcoding (no `if amazon:`, no fixed coords, no site-specific selectors) | ✅ |
| Safety/STOP respected (all paths check should_stop, SENSITIVE permission on click_at_position) | ✅ |
| No DOM strategy refonte (no changes to `_STRUCT_JS`, no Vision at every screenshot) | ✅ |
| LoopDetector, _finalize_turn, Memory, Tasks, Attention, Context Engine, max_tool_iterations, num_ctx unchanged | ✅ |
| Reuses existing V2 patterns (DeviceAgent → ObservationSpec → WorldState) | ✅ |
| max 12 nouveaux tests | ✅ (12 exactly) |

---

## Files Changed

| File | Gap | Type |
|------|-----|------|
| `raya/models/providers/ollama_cloud.py` | C1 | Code |
| `raya/tools/catalog/__init__.py` | C2 | Code |
| `raya/runtime/bootstrap.py` | C2 | Code |
| `raya/devices/browser/controller.py` | C3, C4, C5 | Code |
| `raya/devices/browser/agent.py` | C3, C4, C5 | Code |
| `raya/tools/catalog/browser.py` | C3, C5 | Code |
| `tests/tools/test_safe_autonomy.py` | C5 | Bug fix (AsyncMock → MagicMock) |
| `tests/test_20k_foundational_wiring.py` | C2/C3/C4 | New tests |
| `tests/integration/test_20k_e2e_vision_browser.py` | C1/C2/C3/C4 | New E2E tests |

---

## Success Criteria Check

- [x] `image_ref` n'est plus silencieusement supprimé
- [x] Vision tools sont accessibles via ToolRegistry production (`_register_vision_tools()` appelé dans bootstrap)
- [x] `browser.click_at_position` est accessible production (catalog + `_DISPATCH` + `_CAPABILITIES`)
- [x] `browser.screenshot` fournit les dimensions réelles (PIL + viewport_size fallback)
- [x] `browser.last_clicked_target` peut réellement être produit (ObservationSpec + evidence wired)
- [x] Safety/STOP restent respectés
- [x] Aucune nouvelle architecture parallèle
- [x] Aucun hardcoding site-specific
- [x] Max 12 nouveaux tests (12 exactement)
- [x] Jusqu'à 3 vrais E2E (3 PASS)
- [x] V1 strictement inchangée
- [x] Aucune régression introduite (failures pre-existantes inchangées)
