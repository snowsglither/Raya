"""Chantier 20G-B — Real E2E Validation + Latency Measurement.

Part J : verify _finalize_turn path works end-to-end with real browser
Part K : measure context compaction ratio (before/after _compact_read_page_output)

Instruments:
  - _compact_read_page_output  → logs bytes before/after per call
  - _finalize_turn              → logs when it fires (vs _explain_blocked_turn)
  - model_route                 → logs per-call latency
  - execute_tool                → logs per-tool latency

Sites used (no real purchases, no real accounts):
  - books.toscrape.com : e-commerce catalogue, read-only scraping
  - the-internet.herokuapp.com/login : standard test login page

Run: python scripts/validate_20gb_finalization_e2e.py
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


# ─── Audit log ───────────────────────────────────────────────────────────────

_audit: dict = {
    "compact_calls": [],   # {seq, tool_name, bytes_before, bytes_after, buttons_before, buttons_after, links_before, links_after}
    "finalize_calls": [],  # {seq, t_abs, trace_len}
    "explain_calls": [],   # {seq, t_abs, reason_hint, trace_len}
    "model_calls": [],     # {seq, capability, elapsed_s, t_start, t_end, available_tools_none}
    "tool_timings": [],    # {seq, tool_name, elapsed_s, status}
}
_t_session_start: float = 0.0
_seq_counter: int = 0


def _next_seq() -> int:
    global _seq_counter
    _seq_counter += 1
    return _seq_counter


# ─── Pre-bootstrap monkey-patches ────────────────────────────────────────────

import raya.harness.loop as _loop_mod  # noqa: E402
from raya.models import route as _orig_model_route_fn  # noqa: E402
from raya.tools import execute as _orig_execute  # noqa: E402

_orig_compact   = _loop_mod.Harness._compact_read_page_output.__func__ if hasattr(_loop_mod.Harness._compact_read_page_output, '__func__') else _loop_mod.Harness._compact_read_page_output
_orig_finalize  = _loop_mod.Harness._finalize_turn
_orig_explain   = _loop_mod.Harness._explain_blocked_turn


def _bytes_of(v) -> int:
    try:
        return len(json.dumps(v, ensure_ascii=False, default=str).encode())
    except Exception:
        return len(str(v).encode())


def _patched_compact(output):
    bytes_before = _bytes_of(output)
    buttons_before = 0
    links_before = 0
    if isinstance(output, dict):
        buttons_before = len(output.get("buttons") or [])
        links_before = len(output.get("links") or [])
    elif isinstance(output, str):
        try:
            d = json.loads(output)
            buttons_before = len(d.get("buttons") or [])
            links_before = len(d.get("links") or [])
        except Exception:
            pass

    result = _orig_compact(output)

    bytes_after = _bytes_of(result)
    buttons_after = 0
    links_after = 0
    if isinstance(result, dict):
        buttons_after = len(result.get("buttons") or [])
        links_after = len(result.get("links") or [])

    _audit["compact_calls"].append({
        "seq": _next_seq(),
        "t_abs": round(time.monotonic() - _t_session_start, 3),
        "bytes_before": bytes_before,
        "bytes_after": bytes_after,
        "reduction_pct": round((1 - bytes_after / max(bytes_before, 1)) * 100, 1),
        "buttons_before": buttons_before,
        "buttons_after": buttons_after,
        "links_before": links_before,
        "links_after": links_after,
    })
    return result


def _patched_finalize(self, objective_text, trace, correlation_id):
    _audit["finalize_calls"].append({
        "seq": _next_seq(),
        "t_abs": round(time.monotonic() - _t_session_start, 3),
        "trace_len": len(trace),
    })
    return _orig_finalize(self, objective_text, trace, correlation_id)


def _patched_explain(self, objective_text, trace, reason_hint, correlation_id):
    _audit["explain_calls"].append({
        "seq": _next_seq(),
        "t_abs": round(time.monotonic() - _t_session_start, 3),
        "reason_hint": reason_hint,
        "trace_len": len(trace),
    })
    return _orig_explain(self, objective_text, trace, reason_hint, correlation_id)


def _make_patched_route(orig_fn):
    def _patched_route(registry, request):
        t0 = time.monotonic()
        result = orig_fn(registry, request)
        t1 = time.monotonic()
        _audit["model_calls"].append({
            "seq": _next_seq(),
            "capability": request.capability.value if request.capability else "?",
            "elapsed_s": round(t1 - t0, 3),
            "t_start": round(t0 - _t_session_start, 3),
            "t_end": round(t1 - _t_session_start, 3),
            "available_tools_none": request.available_tools is None,
        })
        return result
    return _patched_route


def _patched_execute(registry, safety, tool_call, bus=None):
    t0 = time.monotonic()
    result = _orig_execute(registry, safety, tool_call, bus=bus)
    t1 = time.monotonic()
    _audit["tool_timings"].append({
        "seq": _next_seq(),
        "tool_name": tool_call.tool_name,
        "elapsed_s": round(t1 - t0, 3),
        "status": result.status.value if result else "?",
    })
    return result


_loop_mod.Harness._compact_read_page_output = staticmethod(_patched_compact)
_loop_mod.Harness._finalize_turn            = _patched_finalize
_loop_mod.Harness._explain_blocked_turn     = _patched_explain
_loop_mod.execute_tool                       = _patched_execute

import raya.models as _models_mod  # noqa: E402
_models_mod.route = _make_patched_route(_orig_model_route_fn)
_loop_mod.model_route = _models_mod.route


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
        enable_telegram=False,
        db_path=tmp_path / "20gb_e2e.db",
        tool_workspace_dir=tmp_path / "workspace",
        device_screenshot_dir=tmp_path / "screenshots",
        max_tool_iterations=12,
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
        "total_tool_calls": len(trace or []),
    }


# ─── Report ──────────────────────────────────────────────────────────────────

def _print_section(title: str) -> None:
    print(f"\n{'='*70}")
    print(f"  {title}")
    print('='*70)


def _print_report(scenario: str, result: dict) -> None:
    trace = result.get("trace") or []
    model_calls = _audit["model_calls"]
    compact_calls = _audit["compact_calls"]

    _print_section(f"SCENARIO: {scenario}")
    print(f"  Elapsed total  : {result['elapsed_s']}s")
    print(f"  Tool calls     : {result['total_tool_calls']}")
    print(f"  Model calls    : {len(model_calls)}")
    print(f"  Compact calls  : {len(compact_calls)}")
    print(f"  Finalize calls : {len(_audit['finalize_calls'])}")
    print(f"  Explain calls  : {len(_audit['explain_calls'])}")

    if compact_calls:
        print(f"\n  --- Context Compaction (Part K) ---")
        total_saved = sum(c["bytes_before"] - c["bytes_after"] for c in compact_calls)
        for c in compact_calls:
            pct = c["reduction_pct"]
            print(f"    read_page #{c['seq']:2d} | "
                  f"before={c['bytes_before']:6d}B  after={c['bytes_after']:6d}B  "
                  f"saved={c['bytes_before']-c['bytes_after']:5d}B  ({pct:.1f}%)  | "
                  f"buttons {c['buttons_before']}→{c['buttons_after']}  "
                  f"links {c['links_before']}→{c['links_after']}")
        print(f"    TOTAL context saved: {total_saved:,} bytes across {len(compact_calls)} read_page calls")

    if model_calls:
        print(f"\n  --- Model Call Latency (Part K) ---")
        total_model_s = sum(c["elapsed_s"] for c in model_calls)
        inference_calls = [c for c in model_calls if not c["available_tools_none"]]
        finalize_calls_model = [c for c in model_calls if c["available_tools_none"]]
        for c in model_calls:
            marker = "[FINALIZE]" if c["available_tools_none"] else "[TOOL-LOOP]"
            print(f"    {marker} {c['capability']:<20} {c['elapsed_s']:6.3f}s  @{c['t_start']:.1f}s")
        print(f"    Total model time: {total_model_s:.1f}s  "
              f"({len(inference_calls)} loop calls + {len(finalize_calls_model)} finalization)")

    _print_section("FINALIZATION VERDICT (Part J)")
    if _audit["finalize_calls"]:
        fc = _audit["finalize_calls"][0]
        print(f"  PASS : _finalize_turn FIRED at t={fc['t_abs']}s "
              f"after {fc['trace_len']} tool calls")
        if not _audit["explain_calls"]:
            print(f"  PASS : _explain_blocked_turn NOT called (budget exhaustion handled by _finalize_turn)")
        else:
            explains = _audit["explain_calls"]
            print(f"  WARN : _explain_blocked_turn also called {len(explains)} time(s) — "
                  f"check if they are ESCALATE triggers (expected) or budget-end (regression)")
    elif _audit["explain_calls"]:
        ec = _audit["explain_calls"][-1]
        if "prévues" in ec.get("reason_hint", "") or "étapes" in ec.get("reason_hint", ""):
            print(f"  FAIL : _explain_blocked_turn called for budget exhaustion "
                  f"(should be _finalize_turn) — hint: {ec['reason_hint']!r}")
        else:
            print(f"  INFO : _explain_blocked_turn called for ESCALATE (expected) — "
                  f"hint: {ec['reason_hint']!r}")
    else:
        print(f"  INFO : Neither finalize nor explain called — model finished within budget")

    print(f"\n  Response (first 400 chars):")
    response = result.get("response") or ""
    print(f"    {response[:400]!r}")


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("Chantier 20G-B — Real E2E Validation (Parts J + K)")
    print(f"API key present: {'yes' if API_KEY else 'no'}")
    print(f"Site: books.toscrape.com (read-only, no real purchases)")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        handles = None
        try:
            print("\nBootstrapping runtime...")
            handles = _bootstrap(tmp_path)
            print("Bootstrap complete.")

            # Clear audit state between scenarios
            def _reset_audit():
                for k in _audit:
                    _audit[k].clear()

            # ── Scenario 1: Read-only catalogue browsing (many links/buttons)
            _reset_audit()
            print("\nScenario 1: Browse books.toscrape.com catalogue")
            result1 = _send(
                handles,
                session_id="s_books",
                text=(
                    "Dis-moi combien de livres sont sur la page d'accueil de "
                    "https://books.toscrape.com et quel est le titre du premier livre."
                ),
            )
            _print_report("Browse books.toscrape.com (catalogue read-only)", result1)

            # ── Scenario 2: Verify budget exhaustion path (forced via max_tool_iterations=3)
            # Use a session with very short budget by patching the harness
            _reset_audit()
            original_max = handles.harness._max_tool_iterations
            handles.harness._max_tool_iterations = 3
            try:
                print("\nScenario 2: Forced budget exhaustion (max_tool_iterations=3)")
                result2 = _send(
                    handles,
                    session_id="s_budget",
                    text="Va sur https://books.toscrape.com et donne-moi les 20 premiers titres de livres avec leurs prix.",
                )
                _print_report("Budget exhaustion verification (max=3)", result2)
            finally:
                handles.harness._max_tool_iterations = original_max

        finally:
            if handles:
                print("\nShutting down runtime...")
                handles.shutdown()
                print("Done.")


if __name__ == "__main__":
    main()
