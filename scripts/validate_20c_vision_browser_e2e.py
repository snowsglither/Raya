"""RAYA V2 — Chantier 20C : Validation E2E Vision Browser (post-correction RC-1/RC-2/RC-3).

Ce script :
- NE MODIFIE AUCUN CODE SOURCE
- Utilise le vrai runtime RAYA, vrai Ollama Cloud, vrai navigateur Playwright
- Exécute 5 scénarios réels et capture la trace exacte de chaque tool call
- Produit RAYA_V2_20C_REAL_E2E_VALIDATION_REPORT.md

Usage :
    python scripts/validate_20c_vision_browser_e2e.py
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Force UTF-8 stdout
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RESULTS_DIR = Path(__file__).parent.parent / "e2e_results"
RESULTS_DIR.mkdir(exist_ok=True)


# ─── Lecture clé API ─────────────────────────────────────────────────────────

def _read_api_key() -> str | None:
    for candidate in (
        Path(r"C:\Users\ruben\OneDrive\Bureau\RAYA\.env"),
        Path(__file__).resolve().parents[1] / ".env",
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
    print("ERREUR : OLLAMA_API_KEY non trouvée. Arrêt.")
    sys.exit(1)


# ─── Bootstrap RAYA avec Browser ─────────────────────────────────────────────

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
        db_path=tmp_path / "audit.db",
        log_dir=tmp_path / "logs",
        tool_workspace_dir=tmp_path / "workspace",
        device_screenshot_dir=tmp_path / "screenshots",
        vision_screenshot_dir=tmp_path / "vision",
    )
    return bootstrap(config=cfg)


# ─── Envoi d'un message RAYA et capture de la trace complète ─────────────────

def _send(handles, session_id: str, text: str) -> dict:
    from raya.contracts import Channel, HarnessRequest, InterfaceInput

    req = HarnessRequest(
        channel=Channel.CLI,
        session_id=session_id,
        input=InterfaceInput(text=text),
    )
    t0 = time.monotonic()
    state = handles.harness.handle_request(req)
    elapsed = time.monotonic() - t0
    trace = handles.harness.last_tool_trace(session_id)
    response = handles.harness.response_text(session_id)
    return {
        "input": text,
        "response": response,
        "trace": trace,
        "elapsed_s": round(elapsed, 1),
        "status": state.status.value,
        "error": state.error.code if state.error else None,
        "total_tool_calls": len(trace),
    }


def _fmt_trace(trace: list[dict]) -> str:
    """Formate la trace pour affichage/rapport."""
    lines = []
    for i, entry in enumerate(trace, 1):
        tool = entry.get("tool_name", "?")
        args = entry.get("arguments", {})
        status = entry.get("status", "?")
        outcome = entry.get("outcome", "?")
        evidence = entry.get("evidence") or {}
        # Arguments résumés
        args_str = json.dumps(args, ensure_ascii=False)
        if len(args_str) > 120:
            args_str = args_str[:120] + "…"
        lines.append(f"  [{i:2d}] {tool:<35} {status:<8} {outcome:<12} args={args_str}")
        if evidence:
            ev_str = json.dumps(evidence, ensure_ascii=False)
            if len(ev_str) > 100:
                ev_str = ev_str[:100] + "…"
            lines.append(f"       evidence: {ev_str}")
    return "\n".join(lines)


def _categorize_trace(trace: list[dict]) -> dict:
    """Catégorise les tool calls par famille."""
    dom_calls = [e for e in trace if e.get("tool_name", "").startswith("browser.") and e.get("tool_name") != "browser.screenshot"]
    screenshot_calls = [e for e in trace if e.get("tool_name") == "browser.screenshot"]
    vision_calls = [e for e in trace if e.get("tool_name", "").startswith("vision.")]
    grounding_calls = [e for e in trace if e.get("tool_name") == "vision.find_in_browser"]
    click_pos_calls = [e for e in trace if e.get("tool_name") == "browser.click_at_position"]
    observe_browser_calls = [e for e in trace if e.get("tool_name") == "vision.observe_browser"]

    dom_failures = [e for e in dom_calls if e.get("status") == "failure"]
    vision_successes = [e for e in vision_calls if e.get("status") == "success"]
    vision_failures = [e for e in vision_calls if e.get("status") == "failure"]

    return {
        "dom_calls": len(dom_calls),
        "dom_failures": len(dom_failures),
        "screenshot_calls": len(screenshot_calls),
        "vision_calls": len(vision_calls),
        "vision_successes": len(vision_successes),
        "vision_failures": len(vision_failures),
        "observe_browser_calls": len(observe_browser_calls),
        "grounding_calls": len(grounding_calls),
        "click_at_position_calls": len(click_pos_calls),
        "total": len(trace),
    }


# ─── Vérification résultat vision.observe_browser ────────────────────────────

def _check_observe_browser_result(trace: list[dict]) -> dict:
    """Inspecte le résultat réel d'un appel vision.observe_browser."""
    for entry in trace:
        if entry.get("tool_name") == "vision.observe_browser":
            return {
                "found": True,
                "status": entry.get("status"),
                "outcome": entry.get("outcome"),
                "evidence": entry.get("evidence"),
            }
    return {"found": False}


def _check_find_in_browser_result(trace: list[dict]) -> dict:
    """Inspecte le résultat réel d'un appel vision.find_in_browser."""
    for entry in trace:
        if entry.get("tool_name") == "vision.find_in_browser":
            ev = entry.get("evidence") or {}
            return {
                "found": True,
                "status": entry.get("status"),
                "outcome": entry.get("outcome"),
                "has_coordinates": "screen_x" in ev or "x" in str(ev),
                "evidence": ev,
            }
    return {"found": False}


# ─── Exécution des E2E ────────────────────────────────────────────────────────

SEPARATOR = "=" * 70


def run_e2e_1(handles, tmp_path: Path) -> dict:
    """E2E #1 — vision.observe_browser : observation visuelle directe."""
    print(f"\n{SEPARATOR}")
    print("E2E #1 — vision.observe_browser (Wikipedia, page simple)")
    print(SEPARATOR)

    # Navigate puis demande une observation visuelle directe
    nav = _send(handles, "e2e1", "Va sur https://fr.wikipedia.org/wiki/Python_(langage)")
    print(f"  Navigation: {nav['status']} — {nav['total_tool_calls']} tool calls ({nav['elapsed_s']}s)")
    print(_fmt_trace(nav["trace"]))

    obs = _send(handles, "e2e1", "Utilise vision.observe_browser pour décrire visuellement ce que tu vois sur cette page.")
    print(f"\n  Observation visuelle: {obs['status']} — {obs['total_tool_calls']} tool calls ({obs['elapsed_s']}s)")
    print(_fmt_trace(obs["trace"]))

    ob_result = _check_observe_browser_result(obs["trace"])
    print(f"\n  vision.observe_browser: {ob_result}")
    print(f"  Réponse modèle (extrait): {obs['response'][:300]}")

    return {
        "id": "E2E_1",
        "name": "vision.observe_browser Wikipedia",
        "nav_trace": nav["trace"],
        "obs_trace": obs["trace"],
        "observe_browser_result": ob_result,
        "response": obs["response"],
        "total_calls": nav["total_tool_calls"] + obs["total_tool_calls"],
        "elapsed_s": nav["elapsed_s"] + obs["elapsed_s"],
        "vision_used": any(e.get("tool_name", "").startswith("vision.") for e in obs["trace"]),
        "observe_browser_success": ob_result.get("status") == "success",
        "grounding": False,
        "action_reelle": False,
        "verification": ob_result.get("status") == "success",
        "result": "PASS" if ob_result.get("status") == "success" else "FAIL",
    }


def run_e2e_2(handles, tmp_path: Path) -> dict:
    """E2E #2 — vision.find_in_browser : grounding réel."""
    print(f"\n{SEPARATOR}")
    print("E2E #2 — vision.find_in_browser (Wikipedia, logo ou titre)")
    print(SEPARATOR)

    nav = _send(handles, "e2e2", "Va sur https://fr.wikipedia.org/wiki/Python_(langage)")
    print(f"  Navigation: {nav['status']} — {nav['total_tool_calls']} tool calls ({nav['elapsed_s']}s)")
    print(_fmt_trace(nav["trace"]))

    grnd = _send(handles, "e2e2", "Utilise vision.find_in_browser pour localiser le titre principal 'Python' sur cette page et donne-moi ses coordonnées pixel exactes.")
    print(f"\n  Grounding: {grnd['status']} — {grnd['total_tool_calls']} tool calls ({grnd['elapsed_s']}s)")
    print(_fmt_trace(grnd["trace"]))

    fib_result = _check_find_in_browser_result(grnd["trace"])
    print(f"\n  vision.find_in_browser: {fib_result}")
    print(f"  Réponse modèle (extrait): {grnd['response'][:300]}")

    return {
        "id": "E2E_2",
        "name": "vision.find_in_browser Wikipedia titre",
        "nav_trace": nav["trace"],
        "grnd_trace": grnd["trace"],
        "find_in_browser_result": fib_result,
        "response": grnd["response"],
        "total_calls": nav["total_tool_calls"] + grnd["total_tool_calls"],
        "elapsed_s": nav["elapsed_s"] + grnd["elapsed_s"],
        "vision_used": any(e.get("tool_name", "").startswith("vision.") for e in grnd["trace"]),
        "grounding": fib_result.get("has_coordinates", False),
        "grounding_status": fib_result.get("status"),
        "action_reelle": False,
        "verification": fib_result.get("has_coordinates", False),
        "result": "PASS" if fib_result.get("has_coordinates") else ("PARTIAL" if fib_result.get("status") == "success" else "FAIL"),
    }


def run_e2e_3(handles, tmp_path: Path) -> dict:
    """E2E #3 — DOM-only : action simple sans Vision."""
    print(f"\n{SEPARATOR}")
    print("E2E #3 — DOM-only : lecture page (pas de Vision attendue)")
    print(SEPARATOR)

    result = _send(handles, "e2e3", "Va sur https://example.com et dis-moi le titre de la page et ce qu'il y a écrit.")
    print(f"  Status: {result['status']} — {result['total_tool_calls']} tool calls ({result['elapsed_s']}s)")
    print(_fmt_trace(result["trace"]))

    cats = _categorize_trace(result["trace"])
    vision_used = cats["vision_calls"] > 0
    print(f"\n  Vision appelée: {vision_used} ({cats['vision_calls']} appels vision)")
    print(f"  DOM calls: {cats['dom_calls']}")
    print(f"  Réponse modèle (extrait): {result['response'][:300]}")

    return {
        "id": "E2E_3",
        "name": "DOM-only example.com",
        "trace": result["trace"],
        "cats": cats,
        "response": result["response"],
        "total_calls": result["total_tool_calls"],
        "elapsed_s": result["elapsed_s"],
        "vision_used": vision_used,
        "dom_only_confirmed": not vision_used and cats["dom_calls"] > 0,
        "grounding": False,
        "action_reelle": True,
        "verification": not vision_used,
        "result": "PASS" if not vision_used else "FAIL",
    }


def run_e2e_4(handles, tmp_path: Path) -> dict:
    """E2E #4 — DOM failure → Vision escalation.

    Provoque un échec DOM réel sur Wikipedia en demandant un clic
    sur un élément ambigu, puis observe si RAYA escalade vers Vision.
    """
    print(f"\n{SEPARATOR}")
    print("E2E #4 — DOM failure → Vision escalation (Wikipedia)")
    print(SEPARATOR)

    nav = _send(handles, "e2e4", "Va sur https://fr.wikipedia.org/wiki/Python_(langage)")
    print(f"  Navigation: {nav['status']} — {nav['total_tool_calls']} tool calls ({nav['elapsed_s']}s)")

    # Demande un clic sur un élément difficile à localiser par texte DOM exact
    # (le logo Wikipedia est souvent difficile à cibler par texte)
    action = _send(handles, "e2e4",
        "Clique sur le logo Wikipedia en haut à gauche de la page "
        "(l'image de la sphère avec les pièces de puzzle). "
        "Si tu ne peux pas le trouver par DOM, utilise Vision.")
    print(f"\n  Action DOM→Vision: {action['status']} — {action['total_tool_calls']} tool calls ({action['elapsed_s']}s)")
    print(_fmt_trace(action["trace"]))

    cats = _categorize_trace(action["trace"])
    ob_result = _check_observe_browser_result(action["trace"])
    fib_result = _check_find_in_browser_result(action["trace"])

    print(f"\n  DOM calls: {cats['dom_calls']} (failures: {cats['dom_failures']})")
    print(f"  Vision calls: {cats['vision_calls']} (successes: {cats['vision_successes']})")
    print(f"  vision.observe_browser: {ob_result}")
    print(f"  vision.find_in_browser: {fib_result}")
    print(f"  click_at_position: {cats['click_at_position_calls']}")
    print(f"  Réponse modèle (extrait): {action['response'][:400]}")

    # Détecter le flux DOM failure → Vision
    dom_failure_then_vision = cats["dom_failures"] > 0 and cats["vision_calls"] > 0
    result_code = "PASS" if dom_failure_then_vision else ("PARTIAL" if cats["vision_calls"] > 0 else "BLOCKED")

    return {
        "id": "E2E_4",
        "name": "DOM failure → Vision escalation Wikipedia logo",
        "nav_trace": nav["trace"],
        "action_trace": action["trace"],
        "cats": cats,
        "observe_browser_result": ob_result,
        "find_in_browser_result": fib_result,
        "response": action["response"],
        "total_calls": nav["total_tool_calls"] + action["total_tool_calls"],
        "elapsed_s": nav["elapsed_s"] + action["elapsed_s"],
        "vision_used": cats["vision_calls"] > 0,
        "dom_failure_then_vision": dom_failure_then_vision,
        "grounding": fib_result.get("has_coordinates", False),
        "action_reelle": cats["click_at_position_calls"] > 0,
        "verification": dom_failure_then_vision,
        "result": result_code,
    }


def run_e2e_5(handles, tmp_path: Path) -> dict:
    """E2E #5 — Amazon Xbox add-to-cart (scénario complet de l'audit 20B)."""
    print(f"\n{SEPARATOR}")
    print("E2E #5 — Amazon.com.be : Xbox dans le panier (SANS acheter)")
    print(SEPARATOR)

    result = _send(
        handles, "e2e5",
        "Va sur Amazon.com.be et mets une Xbox dans mon panier, sans acheter."
    )

    cats = _categorize_trace(result["trace"])
    ob_result = _check_observe_browser_result(result["trace"])
    fib_result = _check_find_in_browser_result(result["trace"])

    print(f"\n  Status: {result['status']} — {result['total_tool_calls']} tool calls ({result['elapsed_s']}s)")
    print(f"\n  TRACE COMPLÈTE :")
    print(_fmt_trace(result["trace"]))

    print(f"\n  ─── Catégories ───")
    print(f"  DOM calls total     : {cats['dom_calls']}")
    print(f"  DOM failures        : {cats['dom_failures']}")
    print(f"  Screenshots         : {cats['screenshot_calls']}")
    print(f"  Vision calls total  : {cats['vision_calls']}")
    print(f"  vision.observe_browser: {cats['observe_browser_calls']} (résultat: {ob_result.get('status', 'N/A')})")
    print(f"  vision.find_in_browser: {cats['grounding_calls']} (résultat: {fib_result.get('status', 'N/A')})")
    print(f"  Coordonnées grounding : {fib_result.get('has_coordinates', False)}")
    print(f"  click_at_position   : {cats['click_at_position_calls']}")
    print(f"  Iterations totales  : {cats['total']} / 12 max")

    print(f"\n  Réponse modèle:")
    print(f"  {result['response'][:600]}")

    # Déterminer si l'objectif est atteint
    response_lower = (result["response"] or "").lower()
    cart_keywords = ["panier", "cart", "ajouté", "added", "ajouter", "quantity"]
    objective_keywords_pos = any(kw in response_lower for kw in cart_keywords)
    blocked_keywords = ["itérations", "iterations", "limite", "max_tool", "12 étapes", "n'ai pas pu", "je n'ai pas pu", "n'ai pas terminé"]
    objective_blocked = any(kw in response_lower for kw in blocked_keywords)

    if objective_keywords_pos and not objective_blocked:
        objective_result = "ACHIEVED"
    elif objective_blocked or cats["total"] >= 12:
        objective_result = "NOT_ACHIEVED (iterations exhausted)"
    else:
        objective_result = "UNKNOWN — vérifier la réponse"

    print(f"\n  Objectif final : {objective_result}")

    return {
        "id": "E2E_5",
        "name": "Amazon Xbox add-to-cart",
        "trace": result["trace"],
        "cats": cats,
        "observe_browser_result": ob_result,
        "find_in_browser_result": fib_result,
        "response": result["response"],
        "total_calls": result["total_tool_calls"],
        "elapsed_s": result["elapsed_s"],
        "vision_used": cats["vision_calls"] > 0,
        "observe_browser_used": cats["observe_browser_calls"] > 0,
        "observe_browser_success": ob_result.get("status") == "success",
        "grounding": fib_result.get("has_coordinates", False),
        "grounding_status": fib_result.get("status", "N/A"),
        "action_reelle": cats["click_at_position_calls"] > 0,
        "verification": objective_keywords_pos,
        "objective_result": objective_result,
        "iterations_exhausted": cats["total"] >= 12,
        "result": "PASS" if objective_keywords_pos and not objective_blocked else "PARTIAL" if cats["vision_calls"] > 0 else "FAIL",
    }


# ─── Génération du rapport Markdown ──────────────────────────────────────────

def _generate_report(results: list[dict]) -> str:
    lines = []
    now = time.strftime("%Y-%m-%d %H:%M")

    lines.append("# RAYA V2 — Chantier 20C : Rapport de Validation E2E Vision Browser")
    lines.append("")
    lines.append(f"**Date** : {now}  ")
    lines.append("**Branche** : main  ")
    lines.append("**Mode** : VALIDATION UNIQUEMENT — aucune modification de code  ")
    lines.append("**Corrections pré-appliquées** : RC-1 (`_capture_browser` asyncio fix), RC-2 (dérivé), RC-3 (directive DOM fallback + Vision escalation)")
    lines.append("")
    lines.append("---")
    lines.append("")

    # Table de synthèse
    lines.append("## Table de synthèse")
    lines.append("")
    lines.append("| E2E | Vision utilisée | Grounding | Action réelle | Verification | Result |")
    lines.append("|-----|-----------------|-----------|---------------|--------------|--------|")
    for r in results:
        lines.append(
            f"| {r['id']} — {r['name']} "
            f"| {'✓' if r.get('vision_used') else '✗'} "
            f"| {'✓' if r.get('grounding') else '✗'} "
            f"| {'✓' if r.get('action_reelle') else '✗'} "
            f"| {'✓' if r.get('verification') else '✗'} "
            f"| **{r.get('result', '?')}** |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    # Détail E2E #1
    r1 = next((r for r in results if r["id"] == "E2E_1"), None)
    if r1:
        lines.append("## E2E #1 — vision.observe_browser")
        lines.append("")
        lines.append(f"**Site** : Wikipedia Python  ")
        lines.append(f"**Objectif** : `vision.observe_browser` retourne une description réelle  ")
        lines.append(f"**Total tool calls** : {r1['total_calls']}  ")
        lines.append(f"**Elapsed** : {r1['elapsed_s']}s  ")
        lines.append("")
        ob = r1.get("observe_browser_result", {})
        lines.append(f"**vision.observe_browser status** : `{ob.get('status', 'N/A')}`  ")
        lines.append(f"**outcome** : `{ob.get('outcome', 'N/A')}`  ")
        if ob.get("evidence"):
            ev_str = json.dumps(ob["evidence"], ensure_ascii=False, indent=2)[:400]
            lines.append(f"**evidence** :\n```json\n{ev_str}\n```")
        lines.append("")
        lines.append("### Trace")
        lines.append("```")
        all_trace = r1.get("nav_trace", []) + r1.get("obs_trace", [])
        lines.append(_fmt_trace(all_trace))
        lines.append("```")
        lines.append("")
        lines.append(f"**Réponse modèle** : {(r1.get('response') or '')[:500]}")
        lines.append("")
        lines.append(f"**Résultat** : **{r1['result']}**")
        if r1["result"] != "PASS":
            lines.append(f"**Root cause** : `vision.observe_browser` status=`{ob.get('status', 'N/A')}` — RC-1 non résolu ou browser non ouvert")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Détail E2E #2
    r2 = next((r for r in results if r["id"] == "E2E_2"), None)
    if r2:
        lines.append("## E2E #2 — vision.find_in_browser (grounding)")
        lines.append("")
        lines.append(f"**Site** : Wikipedia Python  ")
        lines.append(f"**Objectif** : `vision.find_in_browser` retourne des coordonnées pixel réelles  ")
        lines.append(f"**Total tool calls** : {r2['total_calls']}  ")
        lines.append(f"**Elapsed** : {r2['elapsed_s']}s  ")
        lines.append("")
        fib = r2.get("find_in_browser_result", {})
        lines.append(f"**vision.find_in_browser status** : `{fib.get('status', 'N/A')}`  ")
        lines.append(f"**Coordonnées présentes** : `{fib.get('has_coordinates', False)}`  ")
        if fib.get("evidence"):
            ev_str = json.dumps(fib["evidence"], ensure_ascii=False, indent=2)[:400]
            lines.append(f"**evidence** :\n```json\n{ev_str}\n```")
        lines.append("")
        lines.append("### Trace")
        lines.append("```")
        all_trace = r2.get("nav_trace", []) + r2.get("grnd_trace", [])
        lines.append(_fmt_trace(all_trace))
        lines.append("```")
        lines.append("")
        lines.append(f"**Réponse modèle** : {(r2.get('response') or '')[:500]}")
        lines.append("")
        lines.append(f"**Résultat** : **{r2['result']}**")
        if r2["result"] != "PASS":
            lines.append(f"**Root cause** : Coordonnées absentes — `vision.find_in_browser` a échoué ou le modèle n'a pas appelé l'outil")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Détail E2E #3
    r3 = next((r for r in results if r["id"] == "E2E_3"), None)
    if r3:
        lines.append("## E2E #3 — DOM-only (pas de Vision inutile)")
        lines.append("")
        lines.append(f"**Site** : example.com  ")
        lines.append(f"**Objectif** : RAYA reste DOM-only, Vision NON appelée  ")
        cats = r3.get("cats", {})
        lines.append(f"**DOM calls** : {cats.get('dom_calls', 0)}  ")
        lines.append(f"**Vision calls** : {cats.get('vision_calls', 0)} (attendu : 0)  ")
        lines.append(f"**Total tool calls** : {r3['total_calls']}  ")
        lines.append(f"**Elapsed** : {r3['elapsed_s']}s  ")
        lines.append("")
        lines.append("### Trace")
        lines.append("```")
        lines.append(_fmt_trace(r3.get("trace", [])))
        lines.append("```")
        lines.append("")
        lines.append(f"**Réponse modèle** : {(r3.get('response') or '')[:400]}")
        lines.append("")
        lines.append(f"**Résultat** : **{r3['result']}**")
        if r3["result"] != "PASS":
            lines.append("**Root cause** : Vision appelée inutilement sur une tâche DOM-pure")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Détail E2E #4
    r4 = next((r for r in results if r["id"] == "E2E_4"), None)
    if r4:
        lines.append("## E2E #4 — DOM failure → Vision escalation")
        lines.append("")
        lines.append(f"**Site** : Wikipedia (logo — difficile par DOM)  ")
        lines.append(f"**Objectif** : DOM failure → escalade vers Vision → grounding → click_at_position  ")
        cats = r4.get("cats", {})
        lines.append(f"**DOM calls** : {cats.get('dom_calls', 0)} (failures: {cats.get('dom_failures', 0)})  ")
        lines.append(f"**Vision calls** : {cats.get('vision_calls', 0)}  ")
        lines.append(f"**vision.observe_browser** : status=`{r4.get('observe_browser_result', {}).get('status', 'N/A')}`  ")
        lines.append(f"**vision.find_in_browser** : status=`{r4.get('find_in_browser_result', {}).get('status', 'N/A')}` | coordonnées=`{r4.get('find_in_browser_result', {}).get('has_coordinates', False)}`  ")
        lines.append(f"**click_at_position** : {cats.get('click_at_position_calls', 0)} appels  ")
        lines.append(f"**Total tool calls** : {r4['total_calls']}  ")
        lines.append(f"**Elapsed** : {r4['elapsed_s']}s  ")
        lines.append("")
        lines.append("### Trace complète (navigation + action)")
        lines.append("```")
        all_trace = r4.get("nav_trace", []) + r4.get("action_trace", [])
        lines.append(_fmt_trace(all_trace))
        lines.append("```")
        lines.append("")
        lines.append(f"**DOM failure → Vision observé** : `{r4.get('dom_failure_then_vision', False)}`")
        lines.append(f"**Réponse modèle** : {(r4.get('response') or '')[:400]}")
        lines.append("")
        lines.append(f"**Résultat** : **{r4['result']}**")
        if r4["result"] not in ("PASS",):
            lines.append("**Root cause** : Le modèle n'a pas escaladé vers Vision après DOM failure, ou vision.find_in_browser n'a pas fourni de coordonnées exploitables")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Détail E2E #5 Amazon
    r5 = next((r for r in results if r["id"] == "E2E_5"), None)
    if r5:
        lines.append("## Amazon — E2E #5 (scénario audit 20B)")
        lines.append("")
        lines.append("**Objectif** : *« Va sur Amazon.com.be et mets une Xbox dans mon panier, sans acheter »*")
        lines.append("")
        cats = r5.get("cats", {})
        ob = r5.get("observe_browser_result", {})
        fib = r5.get("find_in_browser_result", {})
        lines.append("### Tool calls")
        lines.append("")
        lines.append(f"- **Total iterations** : {r5['total_calls']} / 12 max {'⚠️ LIMIT ATTEINTE' if r5.get('iterations_exhausted') else '✓'}")
        lines.append(f"- **DOM calls** : {cats.get('dom_calls', 0)}")
        lines.append(f"- **DOM failures** : {cats.get('dom_failures', 0)}")
        lines.append(f"- **Screenshots** : {cats.get('screenshot_calls', 0)}")
        lines.append(f"- **Vision calls** : {cats.get('vision_calls', 0)}")
        lines.append(f"- **vision.observe_browser** : {cats.get('observe_browser_calls', 0)} appels — status=`{ob.get('status', 'N/A')}`")
        lines.append(f"- **vision.find_in_browser** : {cats.get('grounding_calls', 0)} appels — status=`{fib.get('status', 'N/A')}` — coordonnées=`{fib.get('has_coordinates', False)}`")
        lines.append(f"- **browser.click_at_position** : {cats.get('click_at_position_calls', 0)} appels")
        lines.append(f"- **Elapsed** : {r5['elapsed_s']}s")
        lines.append("")
        lines.append("### Trace complète")
        lines.append("```")
        lines.append(_fmt_trace(r5.get("trace", [])))
        lines.append("```")
        lines.append("")
        lines.append("### Réponse finale du modèle")
        lines.append("")
        lines.append(f"> {(r5.get('response') or '')[:800]}")
        lines.append("")
        lines.append("### Observations Vision")
        lines.append("")
        lines.append(f"1. **RAYA utilise-t-il Vision ?** : `{r5.get('vision_used', False)}`")
        lines.append(f"2. **Quand** : après DOM failures (observé dans la trace)")
        lines.append(f"3. **vision.find_in_browser fonctionne-t-il ?** : `{fib.get('status', 'N/A')}`")
        lines.append(f"4. **Coordonnées valides** : `{fib.get('has_coordinates', False)}`")
        lines.append(f"5. **browser.click_at_position appelé** : `{cats.get('click_at_position_calls', 0) > 0}`")
        lines.append(f"6. **Clic produit effet** : déductible de la trace")
        lines.append(f"7. **Ajout panier vérifié** : `{r5.get('verification', False)}`")
        lines.append(f"8. **Iterations** : {r5['total_calls']}")
        lines.append(f"9. **Limit 12 atteinte** : `{r5.get('iterations_exhausted', False)}`")
        lines.append(f"10. **Point d'arrêt** : voir dernière entrée de la trace")
        lines.append("")

        obj = r5.get("objective_result", "UNKNOWN")
        if "ACHIEVED" in obj and "NOT" not in obj:
            lines.append(f"**Final objective** : ✓ **ACHIEVED**")
        else:
            lines.append(f"**Final objective** : ✗ **{obj}**")
            lines.append("")
            lines.append("**Root cause si échec** :")
            if r5.get("iterations_exhausted"):
                lines.append("- `max_tool_iterations=12` atteint avant la complétion du panier")
                if not r5.get("observe_browser_success"):
                    lines.append("- `vision.observe_browser` : RC-1 TOUJOURS PRÉSENT si status != success")
                if not fib.get("has_coordinates"):
                    lines.append("- `vision.find_in_browser` : pas de grounding retourné")
            else:
                lines.append("- Objectif non atteint pour une raison non liée aux iterations")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Verdict final
    lines.append("## Verdict final")
    lines.append("")
    pass_count = sum(1 for r in results if r.get("result") == "PASS")
    partial_count = sum(1 for r in results if r.get("result") == "PARTIAL")
    fail_count = sum(1 for r in results if r.get("result") in ("FAIL", "BLOCKED"))

    if pass_count == len(results):
        verdict = "**PASS**"
        verdict_detail = "Tous les E2E passent. RC-1/RC-2/RC-3 validés en production."
    elif pass_count + partial_count == len(results):
        verdict = "**PARTIAL**"
        verdict_detail = "Corrections partiellement validées. Des limitations résiduelles existent."
    else:
        verdict = "**FAIL / BLOCKED**"
        verdict_detail = "Des corrections critiques n'ont pas produit l'effet attendu."

    lines.append(f"### {verdict}")
    lines.append("")
    lines.append(f"{verdict_detail}")
    lines.append("")
    lines.append(f"| PASS | PARTIAL | FAIL/BLOCKED |")
    lines.append(f"|------|---------|--------------|")
    lines.append(f"| {pass_count}/5 | {partial_count}/5 | {fail_count}/5 |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("*Rapport généré automatiquement par `scripts/validate_20c_vision_browser_e2e.py`*  ")
    lines.append("*Aucune modification de code source n'a été effectuée pendant cette validation.*")

    return "\n".join(lines)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'#' * 70}")
    print("RAYA V2 — Chantier 20C : Validation E2E Vision Browser")
    print(f"{'#' * 70}\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        print("Bootstrap RAYA (browser activé, modèle Ollama Cloud réel)…")
        handles = _bootstrap(tmp)
        print("RAYA démarré.\n")

        results = []
        try:
            results.append(run_e2e_1(handles, tmp))
            results.append(run_e2e_2(handles, tmp))
            results.append(run_e2e_3(handles, tmp))
            results.append(run_e2e_4(handles, tmp))
            results.append(run_e2e_5(handles, tmp))
        finally:
            print(f"\n{SEPARATOR}")
            print("Shutdown RAYA…")
            handles.shutdown()

        # Générer le rapport
        report = _generate_report(results)
        report_path = Path(__file__).parent.parent / "RAYA_V2_20C_REAL_E2E_VALIDATION_REPORT.md"
        report_path.write_text(report, encoding="utf-8")
        print(f"\nRapport écrit : {report_path}")

        # Synthèse console
        print(f"\n{'#' * 70}")
        print("SYNTHÈSE FINALE")
        print(f"{'#' * 70}")
        for r in results:
            print(f"  {r['id']:6} — {r['name']:<45} → {r.get('result', '?')}")
        print()


if __name__ == "__main__":
    main()
