"""Chantier 20F — Audit: Post-action verification + latency.

AUDIT ONLY — NO PRODUCTION CODE CHANGES.

Reproduces the AirPods Pro 3 scenario where Amazon confirmed the cart add
but RAYA reported failure. Instruments Harness via monkey-patches to capture:
  - Every _summarize_tool_result JSON (what the model sees after each tool call)
  - Every _explain_blocked_turn invocation (when/why the loop stopped)
  - Per-tool execution timing
  - Per-model-call inference timing
"""
from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ─── Audit log (populated by monkey-patches) ─────────────────────────────────

_audit = {
    "tool_summaries": [],   # list of {seq, t_abs, tool_name, json_summary}
    "blocked_turns": [],    # list of {t_abs, reason_hint, trace_snapshot}
    "tool_timings": [],     # list of {seq, tool_name, elapsed_s, t_start, t_end, status}
    "model_calls": [],      # list of {seq, capability, elapsed_s, t_start, t_end}
}
_t_session_start: float = 0.0
_seq_counter = 0


def _next_seq() -> int:
    global _seq_counter
    _seq_counter += 1
    return _seq_counter


# ─── Pre-bootstrap monkey-patches ────────────────────────────────────────────
# These are applied BEFORE bootstrap() so every Harness instance is instrumented.
# NO production files are modified — only in-process class/function bindings.

import raya.harness.loop as _loop_mod  # noqa: E402

_orig_summarize = _loop_mod.Harness._summarize_tool_result
_orig_explain   = _loop_mod.Harness._explain_blocked_turn
_orig_invoke    = _loop_mod.Harness._invoke_model
_orig_execute   = _loop_mod.execute_tool


def _patched_summarize(tool_name, tool_result, outcome):
    result = _orig_summarize(tool_name, tool_result, outcome)
    _audit["tool_summaries"].append({
        "seq": _next_seq(),
        "t_abs": round(time.monotonic() - _t_session_start, 3),
        "tool_name": tool_name,
        "json_summary": result,
    })
    return result


def _patched_explain_blocked(self, objective_text, trace, reason_hint, correlation_id):
    _audit["blocked_turns"].append({
        "t_abs": round(time.monotonic() - _t_session_start, 3),
        "reason_hint": reason_hint,
        "trace_len": len(trace),
        "trace_snapshot": [
            {"tool": t.get("tool_name"), "status": t.get("status"), "outcome": t.get("outcome")}
            for t in trace
        ],
    })
    return _orig_explain(self, objective_text, trace, reason_hint, correlation_id)


def _patched_invoke_model(self, req):
    t0 = time.monotonic()
    result = _orig_invoke(self, req)
    t1 = time.monotonic()
    cap = req.capability.value if req.capability else "?"
    _audit["model_calls"].append({
        "seq": _next_seq(),
        "capability": cap,
        "elapsed_s": round(t1 - t0, 3),
        "t_start": round(t0 - _t_session_start, 3),
        "t_end": round(t1 - _t_session_start, 3),
    })
    return result


def _patched_execute(registry, safety, tool_call, bus=None):
    t0 = time.monotonic()
    result = _orig_execute(registry, safety, tool_call, bus=bus)
    t1 = time.monotonic()
    _audit["tool_timings"].append({
        "seq": _next_seq(),
        "tool_name": tool_call.tool_name,
        "elapsed_s": round(t1 - t0, 3),
        "t_start": round(t0 - _t_session_start, 3),
        "t_end": round(t1 - _t_session_start, 3),
        "status": result.status.value if result else "?",
    })
    return result


_loop_mod.Harness._summarize_tool_result = staticmethod(_patched_summarize)
_loop_mod.Harness._explain_blocked_turn  = _patched_explain_blocked
_loop_mod.Harness._invoke_model          = _patched_invoke_model
_loop_mod.execute_tool                       = _patched_execute


# ─── API key ─────────────────────────────────────────────────────────────────

def _read_api_key() -> str | None:
    for candidate in (
        Path(r"C:\Users\ruben\OneDrive\Bureau\RAYA\.env"),
        ROOT / ".env",
    ):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OLLAMA_API_KEY="):
                val = line.split("=", 1)[1].split("#")[0].strip().strip('"').strip("'")
                return val or None
    return os.environ.get("OLLAMA_API_KEY") or None


API_KEY = _read_api_key()
if not API_KEY:
    print("ERREUR : OLLAMA_API_KEY non trouvee. Arret.")
    sys.exit(1)


# ─── Bootstrap ───────────────────────────────────────────────────────────────

def _bootstrap(tmp_path: Path):
    from raya.runtime.bootstrap import bootstrap
    from raya.runtime.config import load_config

    base = load_config()
    cfg = dataclasses.replace(
        base,
        ollama_api_key=API_KEY,
        enable_windows_device=False,
        enable_browser_device=True,
        enable_phone_device=False,
        enable_perception=False,
        enable_screen_sensor=False,
        enable_camera_sensor=False,
        enable_telegram=False,
        db_path=tmp_path / "audit20f.db",
        log_dir=tmp_path / "logs",
        tool_workspace_dir=tmp_path / "workspace",
        device_screenshot_dir=tmp_path / "screenshots",
        vision_screenshot_dir=tmp_path / "vision",
    )
    return bootstrap(config=cfg)


# ─── Send ────────────────────────────────────────────────────────────────────

def _send(handles, session_id: str, text: str) -> dict:
    global _t_session_start
    from raya.contracts import Channel, HarnessRequest, InterfaceInput

    req = HarnessRequest(
        channel=Channel.CLI,
        session_id=session_id,
        input=InterfaceInput(text=text),
    )
    _t_session_start = time.monotonic()
    t0 = _t_session_start
    state = handles.harness.handle_request(req)
    elapsed = time.monotonic() - t0
    trace = handles.harness.last_tool_trace(session_id)
    response = handles.harness.response_text(session_id)
    return {
        "response": response,
        "trace": trace,
        "elapsed_s": round(elapsed, 1),
        "total_tool_calls": len(trace),
    }


# ─── Trace formatting ────────────────────────────────────────────────────────

def _fmt_trace(trace: list[dict], timings: list[dict]) -> str:
    timing_by_name: dict[str, list[float]] = {}
    for t in timings:
        name = t["tool_name"]
        timing_by_name.setdefault(name, []).append(t["elapsed_s"])

    # Build per-call index
    timing_index: list[tuple[str, float]] = [(t["tool_name"], t["elapsed_s"]) for t in timings]

    lines = []
    tool_cursor = 0
    for i, entry in enumerate(trace, 1):
        tool = entry.get("tool_name", "?")
        args = entry.get("arguments", {})
        status = entry.get("status", "?")
        outcome = entry.get("outcome", "?")
        evidence = entry.get("evidence") or {}

        # Find timing for this call
        elapsed_str = "?.???s"
        for j in range(tool_cursor, len(timing_index)):
            if timing_index[j][0] == tool:
                elapsed_str = f"{timing_index[j][1]:.3f}s"
                tool_cursor = j + 1
                break

        args_str = json.dumps(args, ensure_ascii=False)
        if len(args_str) > 90:
            args_str = args_str[:90] + "..."

        lines.append(f"  [{i:2d}] {elapsed_str:8} {tool:<35} {status:<9} {outcome:<12} {args_str}")
        if evidence:
            ev_str = json.dumps(evidence, ensure_ascii=False)
            if len(ev_str) > 150:
                ev_str = ev_str[:150] + "..."
            lines.append(f"              evidence: {ev_str}")
    return "\n".join(lines)


# ─── Analysis helpers ─────────────────────────────────────────────────────────

def _classify_steps(trace: list[dict]) -> list[dict]:
    """Classify each step as USEFUL / RECOVERY / REDUNDANT / FAILED / BLOCKED."""
    classified = []
    seen_tools = {}
    for i, entry in enumerate(trace):
        tool = entry.get("tool_name", "?")
        status = entry.get("status", "?")
        outcome = entry.get("outcome", "?")
        args = entry.get("arguments", {})
        args_key = json.dumps(args, sort_keys=True)

        if status == "failure" or outcome == "failure":
            kind = "FAILED"
        elif tool in seen_tools and seen_tools[tool] == args_key and status == "success":
            kind = "REDUNDANT"
        elif status == "success" or outcome == "success":
            kind = "USEFUL"
        else:
            kind = "RECOVERY"

        seen_tools[tool] = args_key
        classified.append({
            "step": i + 1,
            "tool": tool,
            "status": status,
            "outcome": outcome,
            "kind": kind,
        })
    return classified


def _find_cart_signal_in_read_page(tool_summaries: list[dict]) -> list[dict]:
    """Find browser.read_page calls and check if cart confirmation patterns appear."""
    cart_keywords = [
        "ajouté au panier", "ajoutée au panier", "1 article",
        "dans le panier", "added to cart", "in your cart",
        "panier", "cart", "article"
    ]
    results = []
    for s in tool_summaries:
        if s["tool_name"] != "browser.read_page":
            continue
        summary_lower = s["json_summary"].lower()
        found_kw = [kw for kw in cart_keywords if kw in summary_lower]
        results.append({
            "seq": s["seq"],
            "t_abs": s["t_abs"],
            "cart_keywords_found": found_kw,
            "has_cart_signal": len(found_kw) > 0,
            "json_preview": s["json_summary"][:500],
        })
    return results


def _find_click_at_position_summaries(tool_summaries: list[dict]) -> list[dict]:
    """Extract what model received after browser.click_at_position."""
    return [s for s in tool_summaries if s["tool_name"] == "browser.click_at_position"]


def _find_vision_summaries(tool_summaries: list[dict]) -> list[dict]:
    """Extract vision tool summaries."""
    return [s for s in tool_summaries if s["tool_name"].startswith("vision.")]


# ─── E2E run ─────────────────────────────────────────────────────────────────

def run_amazon_airpods(handles, session_id: str) -> dict:
    print("\n" + "=" * 72)
    print("CHANTIER 20F — Amazon AirPods Pro 3 add-to-cart audit")
    print("Exact original prompt: 'Raya va sur Amazon et mets moi des AirPods Pro 3 dans mon panier stp'")
    print("=" * 72)

    # Reset audit log for this run
    for key in _audit:
        _audit[key].clear()
    global _seq_counter
    _seq_counter = 0

    result = _send(
        handles,
        session_id,
        "Raya va sur Amazon et mets moi des AirPods Pro 3 dans mon panier stp",
    )

    trace = result["trace"]
    timings = list(_audit["tool_timings"])
    model_calls = list(_audit["model_calls"])
    tool_summaries = list(_audit["tool_summaries"])
    blocked_turns = list(_audit["blocked_turns"])

    # Print trace
    trace_str = _fmt_trace(trace, timings)
    print(trace_str)

    # Analysis
    step_classes = _classify_steps(trace)
    click_pos_summaries = _find_click_at_position_summaries(tool_summaries)
    read_page_after_click = _find_cart_signal_in_read_page(tool_summaries)
    vision_summaries = _find_vision_summaries(tool_summaries)

    resp = result["response"] or ""
    reports_success = any(k in resp.lower() for k in [
        "ajout", "panier", "cart", "added", "1 article", "dans le panier"
    ])
    reports_failure = any(k in resp.lower() for k in [
        "pas réussi", "n'ai pas", "pas pu", "échoué", "impossible",
        "finaliser", "malheureusement", "désolé"
    ])

    # Was _explain_blocked_turn triggered?
    explain_triggered = len(blocked_turns) > 0
    explain_reason = blocked_turns[0]["reason_hint"] if explain_triggered else None

    # Was cart action performed?
    click_calls = [e for e in trace if e.get("tool_name") in ("browser.click", "browser.click_at_position")]
    click_success = any(e.get("status") == "success" for e in click_calls)

    # Was browser.read_page called AFTER the last click?
    last_click_idx = -1
    for i, e in enumerate(trace):
        if e.get("tool_name") in ("browser.click", "browser.click_at_position"):
            last_click_idx = i
    read_after_click = any(
        i > last_click_idx for i, e in enumerate(trace)
        if e.get("tool_name") == "browser.read_page"
    ) if last_click_idx >= 0 else False

    # Print key findings
    print(f"\n  Total elapsed    : {result['elapsed_s']}s")
    print(f"  Total tool calls : {result['total_tool_calls']}/12")
    print(f"  _explain_blocked : {explain_triggered} — {explain_reason!r}")
    print(f"  Reports success  : {reports_success}")
    print(f"  Reports failure  : {reports_failure}")
    print(f"  Click performed  : {len(click_calls)} call(s), success={click_success}")
    print(f"  Read after click : {read_after_click}")
    print(f"\n  RAYA Response (700 chars):\n  {resp[:700]}")

    print(f"\n  click_at_position JSON summary (model received):")
    for s in click_pos_summaries:
        print(f"    [{s['t_abs']:.1f}s] {s['json_summary'][:300]}")

    print(f"\n  browser.read_page with cart keywords:")
    for r in read_page_after_click:
        flag = "CART SIGNAL" if r["has_cart_signal"] else "no cart"
        print(f"    [{r['t_abs']:.1f}s] {flag} — keywords={r['cart_keywords_found']}")
        if r["has_cart_signal"]:
            print(f"    preview: {r['json_preview'][:300]}")

    print(f"\n  Step classification:")
    for sc in step_classes:
        print(f"    [{sc['step']:2d}] {sc['kind']:<10} {sc['tool']:<35} {sc['status']}/{sc['outcome']}")

    print(f"\n  Timing breakdown:")
    total_tool_s = sum(t["elapsed_s"] for t in timings)
    total_model_s = sum(m["elapsed_s"] for m in model_calls)
    print(f"    Tool execution  : {total_tool_s:.1f}s")
    print(f"    Model inference : {total_model_s:.1f}s")
    print(f"    Total elapsed   : {result['elapsed_s']}s")
    print(f"    Model calls     : {len(model_calls)}")

    return {
        "response": resp,
        "elapsed_s": result["elapsed_s"],
        "total_tool_calls": result["total_tool_calls"],
        "trace": trace,
        "trace_str": trace_str,
        "step_classes": step_classes,
        "timings": timings,
        "model_calls": model_calls,
        "tool_summaries": tool_summaries,
        "blocked_turns": blocked_turns,
        "click_pos_summaries": click_pos_summaries,
        "read_page_after_click": read_page_after_click,
        "vision_summaries": vision_summaries,
        "explain_triggered": explain_triggered,
        "explain_reason": explain_reason,
        "reports_success": reports_success,
        "reports_failure": reports_failure,
        "click_calls": len(click_calls),
        "click_success": click_success,
        "read_after_click": read_after_click,
        "total_tool_s": total_tool_s,
        "total_model_s": total_model_s,
    }


# ─── Report generation ───────────────────────────────────────────────────────

def _write_report(r: dict, report_path: Path) -> None:
    d = r
    now = time.strftime("%Y-%m-%d %H:%M")

    # --- Step table ---
    step_table_lines = ["| Step | Classification | Tool | Status/Outcome |",
                        "|------|---------------|------|----------------|"]
    for sc in d["step_classes"]:
        step_table_lines.append(f"| {sc['step']:2d} | {sc['kind']} | `{sc['tool']}` | {sc['status']}/{sc['outcome']} |")
    step_table = "\n".join(step_table_lines)

    # --- Timing table ---
    timing_lines = ["| Tool | elapsed_s | t_start | t_end |",
                    "|------|-----------|---------|-------|"]
    for t in d["timings"]:
        timing_lines.append(f"| `{t['tool_name']}` | {t['elapsed_s']}s | {t['t_start']}s | {t['t_end']}s |")
    timing_table = "\n".join(timing_lines)

    # --- Model calls table ---
    model_lines = ["| # | Capability | elapsed_s | t_start | t_end |",
                   "|---|-----------|-----------|---------|-------|"]
    for i, m in enumerate(d["model_calls"], 1):
        model_lines.append(f"| {i} | {m['capability']} | {m['elapsed_s']}s | {m['t_start']}s | {m['t_end']}s |")
    model_table = "\n".join(model_lines)

    # --- click_at_position summaries ---
    click_pos_section = ""
    if d["click_pos_summaries"]:
        for s in d["click_pos_summaries"]:
            click_pos_section += f"\n**t={s['t_abs']:.1f}s** — JSON the model received:\n```json\n{s['json_summary']}\n```\n"
    else:
        click_pos_section = "\n_browser.click_at_position was not called in this run._\n"

    # --- read_page after click ---
    read_section = ""
    if d["read_page_after_click"]:
        for r2 in d["read_page_after_click"]:
            cart_flag = "**CART SIGNAL PRESENT**" if r2["has_cart_signal"] else "no cart keywords"
            read_section += f"\n**t={r2['t_abs']:.1f}s** — {cart_flag}\n"
            read_section += f"Keywords found: `{r2['cart_keywords_found']}`\n"
            read_section += f"JSON preview:\n```json\n{r2['json_preview']}\n```\n"
    else:
        read_section = "\n_browser.read_page returned no data with cart keywords._\n"

    # --- Vision summaries ---
    vision_section = ""
    if d["vision_summaries"]:
        for s in d["vision_summaries"]:
            vision_section += f"\n**{s['tool_name']}** at t={s['t_abs']:.1f}s:\n```json\n{s['json_summary'][:600]}\n```\n"
    else:
        vision_section = "\n_No vision tools called in this run._\n"

    # --- _explain_blocked_turn ---
    if d["explain_triggered"]:
        bt = d["blocked_turns"][0]
        explain_section = f"""
**TRIGGERED** at t={bt['t_abs']:.1f}s after {bt['trace_len']} tool calls.

**Reason hint passed to model:**
> {bt['reason_hint']}

**Stripped trace passed to model (no evidence, no evidence of cart success):**
| # | Tool | Status | Outcome |
|---|------|--------|---------|
""" + "\n".join(
            f"| {i+1} | `{t['tool']}` | {t['status']} | {t['outcome']} |"
            for i, t in enumerate(bt["trace_snapshot"])
        ) + """

**Effect**: The model receives: system prompt "The agent got stuck trying to complete the user's
request" + stripped trace + reason hint. This primes the model to report failure, regardless of
whether the cart action actually succeeded.
"""
    else:
        explain_section = "\n_Not triggered in this run._\n"

    # --- Root cause verdict ---
    rca_lines = []
    if d["explain_triggered"]:
        rca_lines.append("- **RC-A1 CONFIRMED**: `_explain_blocked_turn` was triggered — model was primed to report failure.")
    else:
        rca_lines.append("- **RC-A1 NOT TRIGGERED**: `_explain_blocked_turn` was NOT called.")

    if d["click_success"] and not d["read_after_click"]:
        rca_lines.append("- **RC-A2 CONFIRMED**: `browser.click_at_position` succeeded but `browser.read_page` was NOT called after — model had no cart confirmation signal.")
    elif d["click_success"] and d["read_after_click"]:
        rca_lines.append("- **RC-A2 PARTIAL**: `browser.read_page` WAS called after click — check if cart keywords appeared.")
    elif not d["click_success"]:
        rca_lines.append("- **RC-A2 NOT APPLICABLE**: click did not succeed.")

    has_cart_in_read = any(r2["has_cart_signal"] for r2 in d["read_page_after_click"])
    if has_cart_in_read and d["reports_failure"]:
        rca_lines.append("- **RC-A3 CONFIRMED**: Cart signal WAS in browser.read_page output, yet model reported failure — `_explain_blocked_turn` likely primed failure response.")
    elif not has_cart_in_read and d["read_after_click"]:
        rca_lines.append("- **RC-A3 CONFIRMED**: `browser.read_page` was called but NO cart keywords in output — page state mismatch or wrong page read.")
    elif not d["read_after_click"] and d["click_success"]:
        rca_lines.append("- **RC-A3 N/A**: read_page not called after click — model never saw cart state.")

    total_iters = d["total_tool_calls"]
    if total_iters >= 12:
        rca_lines.append(f"- **RC-B CONFIRMED**: Max iterations reached ({total_iters}/12) — `_explain_blocked_turn` was triggered AT LIMIT.")
    else:
        rca_lines.append(f"- **RC-B NOT TRIGGERED**: {total_iters}/12 iterations used — loop exited before limit.")

    rca_section = "\n".join(rca_lines)

    # --- Final verdict ---
    if d["explain_triggered"] and (d["click_success"] and not d["read_after_click"]):
        final_verdict = "CONFIRMED ROOT CAUSE"
        verdict_detail = "RC-A: `_explain_blocked_turn` triggered after successful cart click with no post-click `browser.read_page` — model received stripped trace + 'agent got stuck' prompt → reported failure despite successful add."
    elif d["explain_triggered"] and has_cart_in_read and d["reports_failure"]:
        final_verdict = "CONFIRMED ROOT CAUSE"
        verdict_detail = "RC-A: Cart was in browser.read_page output, but `_explain_blocked_turn` priming overrode evidence → failure reported."
    elif not d["click_success"] and total_iters >= 12:
        final_verdict = "PARTIAL"
        verdict_detail = "Cart click did not succeed in this run. Max iterations hit. RC-B confirmed but RC-A requires successful click to demonstrate."
    elif not d["click_success"]:
        final_verdict = "BLOCKED"
        verdict_detail = "Cart click did not succeed — CAPTCHA, login wall, or navigation failure. Cannot reproduce post-click verification gap."
    else:
        final_verdict = "PARTIAL"
        verdict_detail = "Partial evidence — some root causes observed but full scenario not reproduced exactly."

    lines = [
        f"# RAYA V2 — Chantier 20F : Post-Action Verification + Latency Audit\n",
        f"\n",
        f"**Date** : {now}  \n",
        f"**Mode** : AUDIT ONLY — Aucun fichier de production modifié  \n",
        f"**Scenario** : Amazon AirPods Pro 3 add-to-cart  \n",
        f"**Verdict** : **{final_verdict}**  \n",
        f"\n---\n",
        f"\n## 1. Executive Summary\n",
        f"\n",
        f"L'utilisateur a dit : *\"Raya va sur Amazon et mets moi des AirPods Pro 3 dans mon panier stp\"*  \n",
        f"Amazon a confirmé visuellement l'ajout. RAYA a répondu qu'elle n'avait pas réussi.\n",
        f"\n",
        f"Deux hypothèses à vérifier :\n",
        f"- **Hypothèse A** : Après `browser.click_at_position` (succès), le modèle n'a pas de signal\n",
        f"  de confirmation dans le JSON qu'il reçoit (`observation=()` → aucun fait WorldState promu).\n",
        f"  Si `_explain_blocked_turn` est déclenché (max iterations ou boucle), le modèle est amorcé\n",
        f"  à rapporter un échec via le system prompt \"The agent got stuck\" — sans voir aucune preuve du panier.\n",
        f"- **Hypothèse B** : Trop d'itérations consommées en navigation → budget épuisé avant vérification.\n",
        f"\n",
        f"**Verdict** : {final_verdict} — {verdict_detail}\n",
        f"\n---\n",
        f"\n## 2. Environment\n",
        f"\n",
        f"| Paramètre | Valeur |\n",
        f"|-----------|--------|\n",
        f"| Site | amazon.com.be |\n",
        f"| Prompt exact | \"Raya va sur Amazon et mets moi des AirPods Pro 3 dans mon panier stp\" |\n",
        f"| max_tool_iterations | 12 |\n",
        f"| Total elapsed | {d['elapsed_s']}s |\n",
        f"| Tool calls | {d['total_tool_calls']}/12 |\n",
        f"| Model calls | {len(d['model_calls'])} |\n",
        f"\n---\n",
        f"\n## 3. Full Trace with Timing\n",
        f"\n```\n",
        f"  [##] elapsed   tool                                status    outcome      arguments\n",
        f"{d['trace_str']}\n",
        f"```\n",
        f"\n---\n",
        f"\n## 4. Step Classification\n",
        f"\n{step_table}\n",
        f"\n**Legend** : USEFUL = moved toward goal, RECOVERY = error handling, REDUNDANT = repeated success, FAILED = tool returned failure, BLOCKED = exceeded loop limit\n",
        f"\n---\n",
        f"\n## 5. Tool Execution Timing\n",
        f"\n{timing_table}\n",
        f"\n**Summary:**\n",
        f"- Total tool execution : **{d['total_tool_s']:.1f}s**\n",
        f"- Total model inference : **{d['total_model_s']:.1f}s**\n",
        f"- Total elapsed        : **{d['elapsed_s']}s**\n",
        f"\n---\n",
        f"\n## 6. Model Inference Timing\n",
        f"\n{model_table}\n",
        f"\n---\n",
        f"\n## 7. What Model Received After browser.click_at_position\n",
        f"\n{click_pos_section}\n",
        f"\n**Architectural explanation** : `browser.click_at_position` has `observation=()` in\n",
        f"`raya/tools/catalog/browser.py:91`. This means:\n",
        f"- No WorldState fact is promoted after a successful click\n",
        f"- `_summarize_tool_result` returns only `{{tool, status, verification, output: {{x, y}}, evidence: null}}`\n",
        f"- The model has NO cart confirmation signal from this tool alone\n",
        f"- The model MUST explicitly call `browser.read_page` to verify the cart state\n",
        f"\n---\n",
        f"\n## 8. browser.read_page After Click (Cart Signal Analysis)\n",
        f"\n{read_section}\n",
        f"\n---\n",
        f"\n## 9. Vision Tools\n",
        f"\n{vision_section}\n",
        f"\n---\n",
        f"\n## 10. _explain_blocked_turn Analysis\n",
        f"\n{explain_section}\n",
        f"\n---\n",
        f"\n## 11. Root Cause Analysis\n",
        f"\n{rca_section}\n",
        f"\n---\n",
        f"\n## 12. Architecture Gap — Post-Action Verification\n",
        f"\n",
        f"### Gap A1 — No observation after click_at_position\n",
        f"\n",
        f"`browser.click_at_position` (`raya/tools/catalog/browser.py:91`) :\n",
        f"```python\n",
        f'("browser.click_at_position", ..., observation=()),\n',
        f"```\n",
        f"\nAfter a successful position-click, the JSON the model sees is:\n",
        f"```json\n",
        f'{{"tool": "browser.click_at_position", "status": "success", "verification": "success",\n',
        f' "output": {{"x": X, "y": Y}}, "evidence": null}}\n',
        f"```\n",
        f"\nThere is NO cart confirmation, no URL change, no count update. The model must infer\n",
        f"that it should call `browser.read_page` next — but this is a reasoning step that\n",
        f"may not happen if the model has other priorities or if iterations are exhausted.\n",
        f"\n### Gap A2 — _explain_blocked_turn strips all evidence\n",
        f"\n",
        f"When `_explain_blocked_turn` is called (`raya/harness/loop.py:419-461`), it passes\n",
        f"to the model:\n",
        f"1. System prompt: **\"The agent got stuck trying to complete the user's request\"** — primes failure\n",
        f"2. Stripped trace: `{{tool, status, outcome}}` only — NO evidence fields, NO cart count\n",
        f"3. Reason hint: e.g. `\"Je n'ai pas terminé cette demande dans les 12 étapes prévues\"`\n",
        f"\nEven if the cart was added (visible in browser), the model has NO signal of this\n",
        f"in the `_explain_blocked_turn` context, and is positively prompted to report failure.\n",
        f"\n### Gap B — Latency budget consumed before verification\n",
        f"\n",
        f"An Amazon add-to-cart task requires approximately:\n",
        f"- Navigate to site (1-2 steps)\n",
        f"- Handle cookie banner (1-2 steps)\n",
        f"- Search product (1-2 steps)\n",
        f"- Navigate to product page (1-2 steps)\n",
        f"- Find add-to-cart button (1-2 steps, possibly vision)\n",
        f"- Click + verify (2 steps)\n",
        f"\nMinimum: ~8 steps. With any navigation uncertainty, 12 is tight.\n",
        f"If verification (step N+1 after click) is step 13, `_explain_blocked_turn` fires.\n",
        f"\n---\n",
        f"\n## 13. RAYA Response\n",
        f"\n```\n{d['response'][:1000]}\n```\n",
        f"\n---\n",
        f"\n## 14. Final Verdict\n",
        f"\n",
        f"| Root Cause | Status |\n",
        f"|------------|--------|\n",
        f"| RC-A1: `_explain_blocked_turn` primes failure | {'CONFIRMED' if d['explain_triggered'] else 'NOT_TRIGGERED'} |\n",
        f"| RC-A2: No observation after click_at_position | ARCHITECTURAL — confirmed by code reading |\n",
        f"| RC-A3: Model had no cart signal | {'CONFIRMED' if d['click_success'] and not d['read_after_click'] else 'SEE ANALYSIS'} |\n",
        f"| RC-B: Iteration budget exhausted | {'CONFIRMED' if d['total_tool_calls'] >= 12 else 'NOT TRIGGERED (' + str(d['total_tool_calls']) + '/12)'} |\n",
        f"\n**Final verdict : {final_verdict}**  \n",
        f"{verdict_detail}\n",
        f"\n---\n",
        f"\n*Rapport généré par `scripts/audit_20f_post_action_verification.py`*  \n",
        f"*Aucun fichier de production modifié.*  \n",
    ]

    report_path.write_text("".join(lines), encoding="utf-8")
    print(f"\nRapport écrit : {report_path}")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("#" * 72)
    print("RAYA V2 — Chantier 20F : Post-Action Verification + Latency Audit")
    print("AUDIT ONLY — NO CODE CHANGES")
    print("#" * 72)

    with tempfile.TemporaryDirectory(prefix="raya_20f_", ignore_cleanup_errors=True) as tmp:
        tmp_path = Path(tmp)
        for d in ("logs", "workspace", "screenshots", "vision"):
            (tmp_path / d).mkdir(exist_ok=True)

        print("\nBootstrap RAYA...")
        handles = _bootstrap(tmp_path)
        print("RAYA démarré.\n")

        try:
            r = run_amazon_airpods(handles, "audit_20f")
        finally:
            handles.shutdown()

        report_path = ROOT / "RAYA_V2_20F_POST_ACTION_VERIFICATION_LATENCY_AUDIT.md"
        _write_report(r, report_path)

    print("#" * 72)
    print("Audit 20F terminé.")
    print("#" * 72)


if __name__ == "__main__":
    main()
