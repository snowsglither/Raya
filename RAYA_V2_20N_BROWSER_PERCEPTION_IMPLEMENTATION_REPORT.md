# RAYA V2 — Chantier 20N-B : Browser Perception Implementation Report

**Date :** 2026-09-18  
**Scope :** 4 surgical interventions on the Browser perception layer (POST-20K/20L/20M/20N)  
**Status :** ✅ DONE — 12/12 automated tests pass, 3/3 E2E pass, zero regressions

---

## Executive Summary

Chantier 20N-B transforms RAYA's Browser perception from raw data transport to structured Perception: DOM elements now carry semantic metadata, navigation identity is tracked in WorldState, Vision grounding coordinates carry staleness context, old DOM results are automatically compacted, and `check_confirmation` is exposed as a tool. No new subsystems. No second brain. All changes through existing ObservationSpec, WorldState, and messages[] mechanisms.

---

## Modifications Implemented

### Modification A — `_STRUCT_JS` enrichment + Amazon root removal

**File :** `raya/devices/browser/controller.py`

**Before :**
- Amazon-specific priority roots (`#desktop_buybox`, `addToCart_feature_div`, etc.) hardcoded in `_STRUCT_JS` — violates no-site-specific rule
- Input elements returned as `{kind, text, tag}` only — no semantic metadata

**After :**
- Amazon priority roots completely removed. Buttons collected generically: `grab(BTN_SEL, 'button', 120)`
- New `grab_inputs()` JS function adds per-element enrichment:
  - `input_type` : `el.getAttribute('type')` — text, search, email, password, etc. (only on `<input>`)
  - `disabled` : `el.disabled` — boolean, only emitted when true
  - `aria_role` : `el.getAttribute('role') || el.getAttribute('aria-role')` — combobox, textbox, etc.
- Also adds URL + title to `screenshot()` return dict (feeds the Vision context chain)

**E2E validation :** E2E-N2 on duckduckgo.com confirmed `aria_role: "combobox"` on search textarea.

---

### Modification B — DOM → WorldState via ObservationSpec

**Files :** `raya/devices/browser/agent.py`, `raya/tools/catalog/browser.py`

**Before :**
- `browser.read_page` had `observation=()` — title, URL, fingerprint never promoted to WorldState
- `_read_page` evidence: only `url` + `cookie_banner`

**After :**

**`agent.py::_read_page`** now computes and includes in evidence:
- `title` : `r.get("title", "")`
- `page_fingerprint` : `hashlib.md5(f"{url}|{title}".encode()).hexdigest()[:12]` — deterministic 12-char hex

**`catalog/browser.py`** new `_PAGE_STATE_OBSERVATION` (3-tuple):
```python
ObservationSpec(domain="browser", key="current_url",        evidence_field="url",              freshness_ttl_s=60)
ObservationSpec(domain="browser", key="page_title",         evidence_field="title",            freshness_ttl_s=60)
ObservationSpec(domain="browser", key="page_fingerprint",   evidence_field="page_fingerprint", freshness_ttl_s=30)
```

`browser.read_page` observation updated from `()` to `_PAGE_STATE_OBSERVATION`.

**Semantic precision:** `page_fingerprint` = hash(url|title) = navigation identity signal ONLY. It is NOT proof that the DOM is unchanged — AJAX/React/Vue mutations that don't change URL+title are invisible to this mechanism. Cognition must never use `fingerprint_unchanged == DOM_unchanged`.

**E2E validation :** E2E-N1 on fr.wikipedia.org confirmed `evidence["title"]` non-empty, `evidence["page_fingerprint"]` has 12 chars.

---

### Modification C — Vision → WorldState with coordinate context

**Files :** `raya/tools/catalog/visual.py`, `raya/runtime/bootstrap.py`

**Before :**
- `vision.find_in_browser` promoted only `visual.browser_visual_state` (description + entities)
- Vision grounding coords (screen_x, screen_y) were ephemeral: in `_obs_to_output` output only, never in WorldState
- `capture_browser_fn` returned only `{path, width, height}`

**After :**

**`bootstrap.py::_capture_browser_fn`** now passes through `url` and `title` from `browser.screenshot` output, making them available to visual handlers:
```python
return {"path": ..., "width": ..., "height": ..., "url": ..., "title": ...}
```

**`catalog/visual.py`** new `_BROWSER_LAST_TARGET_SPEC`:
```python
ObservationSpec(domain="visual", key="browser_last_target", evidence_field="browser_last_target",
                confidence=Confidence.INFERRED, freshness_ttl_s=30)
```

New `_BROWSER_FIND_OBS_SPEC` (2-tuple) for `vision.find_in_browser`:
```python
(_BROWSER_OBS_SPEC[0],  # visual.browser_visual_state — description
 _BROWSER_LAST_TARGET_SPEC)  # visual.browser_last_target — coords + context
```

**`_handle_find_in_browser`** now adds `browser_last_target` to evidence:
```json
{
  "label": "Add to cart",
  "screen_x": 640, "screen_y": 400,
  "bbox": {"x_min": 0.45, "y_min": 0.55, "x_max": 0.65, "y_max": 0.60},
  "confidence": 0.92,
  "observation_id": "obs-abc123",
  "observed_url": "https://example.com/product",
  "observed_page_fingerprint": "a3f2b1c4d5e6",
  "observed_at": 1758199200.4
}
```

`observed_url` + `observed_page_fingerprint` + `observed_at` allow Cognition to reason: "this target was found on URL X with fingerprint Y, 8 seconds ago — still valid before calling click_at_position."

---

### Modification D — Context compaction of old DOM results

**File :** `raya/harness/loop.py`

**Before :**
- `_compact_read_page_output` capped buttons/links for the CURRENT result only
- Each `browser.read_page` call added 500-3000 tokens to messages[] permanently
- Multiple read_page calls on a multi-step browser task saturated context

**After :**

New static method `_compact_old_dom_messages(messages)`:
- Called in `_run_agentic_loop` immediately after appending a new `browser.read_page` result
- Iterates messages in reverse, preserves the most recent DOM result intact
- Compacts all older DOM results to: `{url, title, buttons_count, links_count, _compacted: True}`
- Idempotent: already-compacted messages are skipped
- Never removes messages, never touches non-DOM results, never touches Vision results

```python
if requested.tool_name == "browser.read_page":
    self._compact_old_dom_messages(messages)
```

Context savings: a 5-step browser task with read_page at each step goes from ~5×2500 = 12,500 tokens in DOM results to ~4×50 + 2500 ≈ 2,700 tokens.

---

### Modification E — `browser.check_confirmation` exposed as Tool

**Files :** `raya/devices/browser/agent.py`, `raya/tools/catalog/browser.py`

**Before :** `check_confirmation()` existed in `BrowserController` but was NOT a Tool — Cognition had no way to call it.

**After :**

**`agent.py`** new `_check_confirmation` handler:
```python
def _check_confirmation(agent, command):
    result = agent._controller.check_confirmation()
    if result is True:
        return _ok(command, {"confirmed": True}, {"confirmation_detected": True}, ...)
    return _ok(command, {"confirmed": False, "inconclusive": True}, {}, ...)
```

Added to `_DISPATCH` and `_CAPABILITIES`.

**`catalog/browser.py`** new `_CONFIRMATION_OBSERVATION`:
```python
ObservationSpec(domain="browser", key="last_confirmation", evidence_field="confirmation_detected", freshness_ttl_s=30)
```

New Tool `browser.check_confirmation` (SAFE, `browser.read`, idempotent):
> "Vérifie si une action a été confirmée par la page (URL de panier ou texte de confirmation visible). Retourne confirmed=true uniquement sur un signal réel — jamais un succès inventé."

**False positive audit:** `check_confirmation()` uses URL check (`/cart`, `/panier`) + past-tense text patterns ("ajouté au panier", "added to cart", etc.). The text patterns are specifically past-tense confirmation text, NOT action button labels ("Ajouter au panier"). Risk is low but not zero if the page body contains cart summary text while viewing a product. Documented: use URL signal as primary, text as secondary.

**E2E validation :** E2E-N3 on github.com confirmed `confirmed=False, inconclusive=True` — no false positive.

---

## Test Summary

| File | Tests | Status | Notes |
|------|-------|--------|-------|
| `tests/test_20n_browser_perception.py` | 12 | ✅ All pass | Modifications A/B/C/D/E |
| `tests/integration/test_20n_e2e_browser_perception.py` | 3 | ✅ All pass | Real Edge/Playwright + real sites |
| `tests/tools/test_safe_autonomy.py` | 59 | ✅ All pass | No regression (was 59, still 59) |
| `tests/test_20k_foundational_wiring.py` | 9 | ✅ All pass | No regression |
| `tests/models/test_ollama_image_transport.py` | 7 | ✅ All pass | No regression |

**New tests created :** 15 (12 automated + 3 E2E) — within limits

---

## E2E Results

| Test | Site | Capability | Result | Evidence |
|------|------|------------|--------|----------|
| E2E-N1 | fr.wikipedia.org | read_page evidence | ✅ PASS | `title="Wikipédia"`, `page_fingerprint="a2..."` (12 chars) |
| E2E-N2 | duckduckgo.com | _STRUCT_JS enrichment | ✅ PASS | Input has `aria_role="combobox"` |
| E2E-N3 | github.com | check_confirmation honesty | ✅ PASS | `confirmed=false, inconclusive=true` |

---

## Architecture Invariants Respected

| Rule | Status |
|------|--------|
| V1 (`MonAssistant`) not touched | ✅ |
| No new managers/orchestrators/parallel systems | ✅ |
| No second brain (no model call in devices, no observe→decide→act outside Harness) | ✅ |
| No DOM+Vision fusion (no component decides which signal wins) | ✅ |
| No site-specific code (Amazon selectors removed, no new hardcoded selectors) | ✅ |
| No modifications to Safety/STOP/V1/Memory/Tasks/Telegram/Voice/Spatial | ✅ |
| `page_fingerprint` never marketed as DOM-stability proof | ✅ |
| `check_confirmation` returns inconclusive honestly (never fabricates success) | ✅ |
| All promotions via existing ObservationSpec mechanism | ✅ |
| Compaction in-place (never removes messages, never removes Vision results) | ✅ |
| Max 12 new automated tests (exactly 12) | ✅ |
| Max 3 real E2E (exactly 3) | ✅ |
| No modifications to `_finalize_turn`, `_explain_blocked_turn`, LoopDetector, Context Engine, max_tool_iterations | ✅ |

---

## Files Changed

| File | Modification | Type |
|------|-------------|------|
| `raya/devices/browser/controller.py` | A: _STRUCT_JS enrichment, Amazon removal, screenshot URL/title | Code |
| `raya/devices/browser/agent.py` | B: _read_page evidence, _screenshot passthrough, E: _check_confirmation | Code |
| `raya/tools/catalog/browser.py` | B: _PAGE_STATE_OBSERVATION, E: _CONFIRMATION_OBSERVATION + check_confirmation tool | Code |
| `raya/tools/catalog/visual.py` | C: _BROWSER_LAST_TARGET_SPEC, _BROWSER_FIND_OBS_SPEC, _handle_find_in_browser | Code |
| `raya/runtime/bootstrap.py` | C: _capture_browser_fn passes url+title | Code |
| `raya/harness/loop.py` | D: _compact_old_dom_messages + call in agentic loop | Code |
| `tests/test_20n_browser_perception.py` | A/B/C/D/E unit tests | New tests |
| `tests/integration/test_20n_e2e_browser_perception.py` | A/B/E E2E tests | New E2E tests |

---

## Success Criteria Check

- [x] `_STRUCT_JS` inputs enriched with `input_type`, `disabled`, `aria_role`
- [x] Amazon priority roots removed from `_STRUCT_JS`
- [x] `browser.read_page` promotes `browser.page_title` to WorldState (TTL 60s)
- [x] `browser.read_page` promotes `browser.page_fingerprint` to WorldState (TTL 30s, hash url|title)
- [x] `page_fingerprint` semantic precision documented: navigation identity ONLY, not DOM stability
- [x] `vision.find_in_browser` evidence includes `browser_last_target` with `observed_url`, `observed_page_fingerprint`, `observed_at`
- [x] `visual.browser_last_target` promoted to WorldState (TTL 30s)
- [x] No DOM+Vision fusion — Cognition remains sole decision-maker on which signal to use
- [x] Context compaction: old DOM results compacted when new one arrives, most recent preserved
- [x] Compaction idempotent: already-compacted messages not re-processed
- [x] `browser.check_confirmation` exposed as SAFE Tool with `_CONFIRMATION_OBSERVATION`
- [x] `check_confirmation` never fabricates success — inconclusive is honest
- [x] False positive risk documented (URL signal reliable; text pattern has minor risk)
- [x] Max 12 new automated tests (12 exactly)
- [x] Max 3 real E2E (3 exactly, real Edge/Playwright, real sites)
- [x] Zero regressions (68 pre-existing tests still pass)
- [x] V1 strictly untouched
- [x] No new subsystems, no PerceptionManager, no second orchestrator
