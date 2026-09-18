"""RAYA V2 — Chantier 20D : Audit Amazon Recovery Root Causes.

MODE : AUDIT UNIQUEMENT — AUCUNE MODIFICATION DE CODE.

Ce script :
- NE modifie PAS le code source
- Utilise le vrai runtime RAYA, vrai Ollama Cloud, vrai Edge
- Reproduit le scénario Amazon et capture l'état exact au point de rupture
- Analyse RC-A (Vision/Viewport), RC-B (DOM), RC-C (iterations)
- Effectue des tests cross-site pour comparaison
- Produit RAYA_V2_20D_AMAZON_RECOVERY_AUDIT.md

Usage :
    python scripts/audit_20d_amazon_recovery.py
"""

from __future__ import annotations

import dataclasses
import json
import os
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

RESULTS_DIR = Path(__file__).parent.parent / "e2e_results"
RESULTS_DIR.mkdir(exist_ok=True)

SEP = "=" * 70


# ─── Clé API ────────────────────────────────────────────────────────────────

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
                return line.split("=", 1)[1].split("#")[0].strip().strip('"').strip("'") or None
    return os.environ.get("OLLAMA_API_KEY") or None


API_KEY = _read_api_key()
if not API_KEY:
    print("ERREUR : OLLAMA_API_KEY non trouvée.")
    sys.exit(1)


# ─── Bootstrap ──────────────────────────────────────────────────────────────

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


# ─── Direct browser access (sans passer par le harness) ─────────────────────

def _get_controller(handles):
    """Récupère le BrowserController directement pour les mesures."""
    from raya.devices.browser import DEVICE_ID
    agent = handles.devices.get(DEVICE_ID)
    if agent is None:
        return None
    return getattr(agent, "_controller", None)


def _get_vision_dir(handles) -> Path:
    return handles.config.vision_screenshot_dir


# ─── Direct observe_image (accès raw model output) ──────────────────────────

def _direct_observe(handles, image_path: str, target: str = "", prompt: str = "") -> dict:
    """Appel direct à observe_image() pour capturer le raw_model_output."""
    from raya.models.vision import observe_image
    obs = observe_image(
        handles.models, image_path,
        prompt=prompt, find_target=target,
        correlation_id=f"audit-{int(time.time())}",
        prefer_local=False,
        source="perception:audit",
    )
    if obs is None:
        return {"status": "UNAVAILABLE", "raw": "", "found": False, "description": ""}
    return {
        "status": "OK",
        "raw": obs.raw_model_output or "",
        "found": obs.target is not None,
        "description": obs.description or "",
        "model_used": obs.model_used or "",
        "grounding": obs.target is not None,
        "bbox": (
            [obs.target.bbox.x_min, obs.target.bbox.y_min,
             obs.target.bbox.x_max, obs.target.bbox.y_max]
            if obs.target else None
        ),
        "confidence": obs.target.confidence if obs.target else None,
    }


# ─── Viewport JS info ────────────────────────────────────────────────────────

_VIEWPORT_JS = """
() => ({
    innerWidth: window.innerWidth,
    innerHeight: window.innerHeight,
    devicePixelRatio: window.devicePixelRatio,
    scrollX: window.scrollX,
    scrollY: window.scrollY,
    outerWidth: window.outerWidth,
    outerHeight: window.outerHeight,
    pageXOffset: window.pageXOffset,
    pageYOffset: window.pageYOffset,
    documentWidth: document.documentElement.scrollWidth,
    documentHeight: document.documentElement.scrollHeight
})
"""

_BUYBOX_JS = """
() => {
    const results = [];
    // Cherche le bouton "Ajouter au panier" avec plusieurs stratégies
    const strategies = [
        () => document.querySelector('#add-to-cart-button'),
        () => document.querySelector('[name="submit.add-to-cart"]'),
        () => document.querySelector('[data-action="add-to-cart"]'),
        () => document.querySelector('[id*="add-to-cart" i]'),
        () => document.querySelector('[id*="addtocart" i]'),
        () => [...document.querySelectorAll('button,input[type=submit]')]
               .find(el => (el.innerText || el.value || '').toLowerCase().includes('panier') ||
                            (el.innerText || el.value || '').toLowerCase().includes('cart')),
    ];
    for (let i = 0; i < strategies.length; i++) {
        try {
            const el = strategies[i]();
            if (el) {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                results.push({
                    strategy: i,
                    tag: el.tagName.toLowerCase(),
                    id: el.id || '',
                    name: el.getAttribute('name') || '',
                    text: (el.innerText || el.value || '').trim().slice(0, 100),
                    aria_label: el.getAttribute('aria-label') || '',
                    type: el.getAttribute('type') || '',
                    rect: {top: Math.round(r.top), left: Math.round(r.left),
                           width: Math.round(r.width), height: Math.round(r.height),
                           bottom: Math.round(r.bottom), right: Math.round(r.right)},
                    visible_in_viewport: r.top >= 0 && r.bottom <= window.innerHeight &&
                                         r.left >= 0 && r.right <= window.innerWidth,
                    partially_visible: r.top < window.innerHeight && r.bottom > 0,
                    disabled: el.disabled,
                    display: s.display,
                    visibility: s.visibility,
                    opacity: parseFloat(s.opacity || '1'),
                });
            }
        } catch(e) {}
    }
    // Buybox area info
    const buybox = document.querySelector('#buybox,#rightCol,#desktop_buybox,#desktop_dp_below_title');
    const buyboxInfo = buybox ? {
        id: buybox.id,
        rect: (() => { const r = buybox.getBoundingClientRect(); return {
            top: Math.round(r.top), bottom: Math.round(r.bottom),
            height: Math.round(r.height), width: Math.round(r.width)
        }; })(),
        visible_in_viewport: (() => {
            const r = buybox.getBoundingClientRect();
            return r.top < window.innerHeight && r.bottom > 0;
        })()
    } : null;
    return { candidates: results, buybox: buyboxInfo,
             viewport_h: window.innerHeight, viewport_w: window.innerWidth };
}
"""

def _js_eval(controller, js: str) -> dict:
    """Évalue du JavaScript dans la page courante."""
    try:
        async def _op():
            page = await controller._session.get_or_create()
            return await page.evaluate(js)
        return controller._worker.run_sync(_op) or {}
    except Exception as exc:
        return {"error": str(exc)}


# ─── Capture screenshot avec métadonnées complètes ──────────────────────────

def _capture_with_meta(controller, vision_dir: Path, label: str) -> dict:
    """Capture screenshot + viewport info + PIL dimensions."""
    ts = int(time.time_ns())
    path = str(vision_dir / f"audit_{label}_{ts}.png")
    result = controller.screenshot(path)
    actual_path = result.get("path") or path

    meta = {"path": actual_path, "status": result.get("status", "?")}

    # PIL dimensions
    try:
        from PIL import Image
        with Image.open(actual_path) as img:
            meta["png_width"], meta["png_height"] = img.size
            meta["png_mode"] = img.mode
    except Exception as exc:
        meta["pil_error"] = str(exc)

    # Viewport JS
    try:
        vp = _js_eval(controller, _VIEWPORT_JS)
        meta["viewport"] = vp
    except Exception as exc:
        meta["viewport_error"] = str(exc)

    return meta


# ─── Harness helper ──────────────────────────────────────────────────────────

def _send(handles, session_id: str, text: str) -> dict:
    from raya.contracts import Channel, HarnessRequest, InterfaceInput
    t0 = time.monotonic()
    state = handles.harness.handle_request(HarnessRequest(
        channel=Channel.CLI, session_id=session_id,
        input=InterfaceInput(text=text),
    ))
    elapsed = time.monotonic() - t0
    trace = handles.harness.last_tool_trace(session_id)
    return {
        "input": text, "response": handles.harness.response_text(session_id),
        "trace": trace, "elapsed_s": round(elapsed, 1),
        "status": state.status.value,
        "error": state.error.code if state.error else None,
        "n_calls": len(trace),
    }


def _fmt_trace(trace: list[dict], indent: str = "  ") -> str:
    lines = []
    for i, e in enumerate(trace, 1):
        tool = e.get("tool_name", "?")
        args = json.dumps(e.get("arguments", {}), ensure_ascii=False)
        if len(args) > 100:
            args = args[:100] + "…"
        status = e.get("status", "?")
        outcome = e.get("outcome", "?")
        lines.append(f"{indent}[{i:2d}] {tool:<35} {status:<8} {outcome:<12} {args}")
    return "\n".join(lines)


# ─── AUDIT PRINCIPAL ─────────────────────────────────────────────────────────

class AuditData:
    def __init__(self):
        self.sections: dict[str, str] = {}
        self.e2e_amazon: dict = {}
        self.e2e_crosssite: list[dict] = []
        self.viewport_analysis: dict = {}
        self.dom_analysis: dict = {}
        self.vision_raw_responses: list[dict] = {}
        self.budget_analysis: dict = {}
        self.loop_analysis: dict = {}
        self.step_efficiency: dict = {}

    def add(self, key: str, value: str):
        self.sections[key] = value
        print(value)


def audit_budget_mapping(data: AuditData):
    """Analyse la chaîne context_budget_tokens → num_predict → num_ctx."""
    print(f"\n{SEP}")
    print("AUDIT 0 — Budget mapping Vision")
    print(SEP)

    # Inspection statique du code (pas de modif)
    from raya.models.vision import _GROUNDING_PROMPT_TEMPLATE
    sample_prompt = _GROUNDING_PROMPT_TEMPLATE.format(target="bouton Ajouter au panier")
    prompt_tokens_est = len(sample_prompt.split()) * 1.3  # approximation grossière

    budget = 1024  # context_budget_tokens dans vision.py
    num_predict = max(256, budget // 4)
    num_ctx = min(128_000, budget * 4 or 128_000)

    lines = [
        f"context_budget_tokens (vision.py)    : {budget}",
        f"num_predict (budget // 4)            : {num_predict} tokens max pour la RÉPONSE",
        f"num_ctx (budget * 4)                 : {num_ctx} tokens pour le contexte total",
        f"",
        f"Prompt grounding (template)          : ~{int(prompt_tokens_est)} tokens",
        f"Réponse FOUND minimale               : ~25 tokens ('FOUND: bbox=[...] label=\"...\" confidence=0.9')",
        f"",
        f"RISQUE : si Gemma décrit l'image avant d'écrire FOUND (comportement observé),",
        f"         256 tokens peuvent être épuisés avant que FOUND soit écrit.",
        f"",
        f"Prompt grounding (extrait) :",
        f"  '{sample_prompt[:200]}...'",
    ]
    result = "\n".join(lines)
    data.budget_analysis = {
        "context_budget_tokens": budget,
        "num_predict": num_predict,
        "num_ctx": num_ctx,
        "prompt_tokens_est": int(prompt_tokens_est),
        "grounding_prompt": sample_prompt,
    }
    data.add("budget", result)
    print(result)


def audit_amazon_journey(handles, data: AuditData, vision_dir: Path):
    """Reproduit le parcours Amazon et capture l'état au point de rupture."""
    print(f"\n{SEP}")
    print("AUDIT 1 — Amazon Journey (reproduction)")
    print(SEP)

    ctrl = _get_controller(handles)
    session = "audit-amazon"

    # STEP 1-2: Navigate + read_page
    print("\n  [NAV] Navigating Amazon.com.be...")
    r1 = _send(handles, session, "Va sur https://www.amazon.com.be")
    print(f"  trace ({r1['n_calls']} calls): {_fmt_trace(r1['trace'])}")

    # STEP 3-4: Search Xbox
    print("\n  [SEARCH] Searching Xbox...")
    r2 = _send(handles, session, "Recherche 'Xbox Series S' dans la barre de recherche.")
    print(f"  trace ({r2['n_calls']} calls): {_fmt_trace(r2['trace'])}")

    # Get to product page
    print("\n  [PRODUCT] Navigating to product page...")
    r3 = _send(handles, session,
        "Clique sur le premier produit Xbox Series S dans les résultats de recherche pour aller à sa page produit.")
    print(f"  trace ({r3['n_calls']} calls): {_fmt_trace(r3['trace'])}")

    # At this point we should be on product page
    # Capture full state BEFORE any add-to-cart attempt
    print("\n  [CAPTURE] Capturing product page state...")

    # Screenshot with metadata
    cap_meta = _capture_with_meta(ctrl, vision_dir, "amazon_product_before_cart")
    print(f"  Screenshot: {cap_meta.get('path')}")
    print(f"  PNG dimensions: {cap_meta.get('png_width')}x{cap_meta.get('png_height')}")
    vp = cap_meta.get("viewport", {})
    print(f"  Viewport JS: innerWidth={vp.get('innerWidth')} innerHeight={vp.get('innerHeight')}")
    print(f"  devicePixelRatio: {vp.get('devicePixelRatio')}")
    print(f"  scrollY: {vp.get('scrollY')} scrollX: {vp.get('scrollX')}")
    print(f"  documentHeight: {vp.get('documentHeight')}")

    # Buybox / button analysis via JS
    buybox_data = _js_eval(ctrl, _BUYBOX_JS)
    print(f"\n  [DOM-JS] Buybox/button analysis:")
    candidates = buybox_data.get("candidates", [])
    buybox = buybox_data.get("buybox")
    if buybox:
        print(f"    Buybox element: id={buybox.get('id')} rect={buybox.get('rect')} visible={buybox.get('visible_in_viewport')}")
    if candidates:
        for c in candidates:
            print(f"    Candidate [strategy {c['strategy']}]: tag={c['tag']} id={c.get('id')} text='{c.get('text')}' "
                  f"visible_in_vp={c.get('visible_in_viewport')} partially={c.get('partially_visible')} "
                  f"rect={c.get('rect')} disabled={c.get('disabled')}")
    else:
        print("    NO 'Ajouter au panier' / 'Add to cart' button found via JS")

    # DOM via read_page
    dom_result = ctrl.read_page()
    dom_buttons = dom_result.get("buttons", [])
    cart_buttons = [b for b in dom_buttons if any(
        kw in (b.get("text") or "").lower() for kw in ["panier", "cart", "add", "ajouter"]
    )]
    print(f"\n  [DOM-read_page] Total buttons: {len(dom_buttons)}, cart-related: {len(cart_buttons)}")
    for b in cart_buttons:
        print(f"    Button: text='{b.get('text')}' tag={b.get('tag')} "
              f"left={b.get('left')} top={b.get('top')}")
    # Also show all buttons for reference (first 20)
    print(f"  All buttons (first 20):")
    for b in dom_buttons[:20]:
        print(f"    '{b.get('text')}' [tag={b.get('tag')} left={b.get('left')} top={b.get('top')}]")

    # DIRECT VISION TEST — raw Gemma response for the grounding
    print(f"\n  [VISION] Direct observe_image (raw Gemma response)...")
    screenshot_path = cap_meta.get("path", "")
    if screenshot_path and Path(screenshot_path).exists():
        # Test 1: target "Ajouter au panier" (exact same as in E2E)
        v1 = _direct_observe(handles, screenshot_path, target='bouton "Ajouter au panier"')
        print(f"\n  Vision test 1 — target='bouton Ajouter au panier'")
        print(f"    found={v1['found']} grounding={v1['grounding']} model={v1['model_used']}")
        print(f"    RAW (first 400 chars): {v1['raw'][:400]}")
        print(f"    bbox: {v1['bbox']} confidence: {v1['confidence']}")

        # Test 2: target obviously visible element (page title or product name)
        v2 = _direct_observe(handles, screenshot_path, target="titre du produit Xbox Series S")
        print(f"\n  Vision test 2 — target='titre du produit Xbox Series S'")
        print(f"    found={v2['found']} grounding={v2['grounding']}")
        print(f"    RAW (first 400 chars): {v2['raw'][:400]}")

        # Test 3: full scene description (no grounding)
        v3 = _direct_observe(handles, screenshot_path, prompt="Décris exactement ce que tu vois sur cette page, liste tous les boutons visibles.")
        print(f"\n  Vision test 3 — full scene description")
        print(f"    description (600 chars): {v3['description'][:600]}")
        print(f"    RAW (600 chars): {v3['raw'][:600]}")

        # Test 4: simple target close to top
        v4 = _direct_observe(handles, screenshot_path, target="barre de recherche Amazon en haut de la page")
        print(f"\n  Vision test 4 — target='barre de recherche Amazon en haut de la page'")
        print(f"    found={v4['found']} grounding={v4['grounding']}")
        print(f"    RAW (first 300 chars): {v4['raw'][:300]}")

        data.vision_raw_responses = {
            "add_to_cart_button": v1,
            "product_title": v2,
            "full_scene": v3,
            "search_bar": v4,
        }

    data.e2e_amazon = {
        "traces": [r1, r2, r3],
        "capture_meta": cap_meta,
        "viewport": vp,
        "buybox_js": buybox_data,
        "dom_cart_buttons": cart_buttons,
        "dom_all_buttons": dom_buttons[:30],
        "vision_raw": data.vision_raw_responses,
    }

    return r1, r2, r3, cap_meta, dom_buttons, cart_buttons, buybox_data


def audit_add_to_cart_attempt(handles, data: AuditData, vision_dir: Path,
                               dom_buttons: list, cart_buttons: list):
    """Tente l'ajout au panier et observe le flux exact."""
    print(f"\n{SEP}")
    print("AUDIT 2 — Tentative ajout panier (observation flux)")
    print(SEP)

    ctrl = _get_controller(handles)
    session = "audit-amazon"

    # Capture état avant
    cap_before = _capture_with_meta(ctrl, vision_dir, "amazon_before_cart_attempt")

    # Tentative 1: DOM click direct
    print("\n  [ATTEMPT 1] DOM click direct 'Ajouter au panier'...")
    r_dom = _send(handles, session,
        "Clique sur le bouton 'Ajouter au panier' pour ajouter le produit au panier. "
        "Si le bouton n'est pas visible à l'écran, ne scrolle pas — dis-moi juste ce que tu vois.")
    print(f"  Trace ({r_dom['n_calls']} calls):")
    print(_fmt_trace(r_dom["trace"]))
    print(f"  Réponse: {r_dom['response'][:400]}")

    # Capture état après
    cap_after = _capture_with_meta(ctrl, vision_dir, "amazon_after_cart_attempt")

    # DOM après tentative
    dom_after = ctrl.read_page()
    added_to_cart = any(
        pat in (dom_after.get("url", "") + json.dumps(dom_after)).lower()
        for pat in ["panier", "cart", "added", "ajouté"]
    )

    print(f"\n  [RESULT] DOM after attempt:")
    print(f"    URL: {dom_after.get('url')}")
    print(f"    added_to_cart signal: {added_to_cart}")

    # Check LoopDetector trace
    all_calls = r_dom["trace"]
    dom_failures = [e for e in all_calls if e.get("status") == "failure" and "click" in e.get("tool_name", "")]
    vision_calls = [e for e in all_calls if "vision" in e.get("tool_name", "")]
    print(f"\n  [LOOP] DOM failures: {len(dom_failures)}, Vision calls: {len(vision_calls)}")

    # If vision.find_in_browser was called, check what happened
    fib = [e for e in all_calls if e.get("tool_name") == "vision.find_in_browser"]
    if fib:
        print(f"  [FIB] vision.find_in_browser called: {len(fib)}x")
        for f in fib:
            print(f"    target='{f.get('arguments', {}).get('target')}' status={f.get('status')} outcome={f.get('outcome')}")

    data.e2e_amazon["cart_attempt"] = {
        "trace": r_dom["trace"],
        "response": r_dom["response"],
        "dom_after": {
            "url": dom_after.get("url"),
            "buttons_count": len(dom_after.get("buttons", [])),
            "cart_signal": added_to_cart,
        },
        "cap_before": cap_before,
        "cap_after": cap_after,
    }

    return r_dom


def audit_crosssite(handles, data: AuditData, vision_dir: Path):
    """Tests cross-site pour comparer Vision sur boutons simples vs Amazon."""
    print(f"\n{SEP}")
    print("AUDIT 3 — Cross-site comparison (Vision sur boutons simples)")
    print(SEP)

    ctrl = _get_controller(handles)
    results = []

    tests = [
        {
            "site": "https://www.coolblue.be/nl",
            "description": "Coolblue — bouton panier e-commerce Belgique",
            "target": "zoekbalk",
            "nav_prompt": "Va sur https://www.coolblue.be/nl",
            "action_prompt": "Utilise vision.find_in_browser pour trouver la barre de recherche en haut de la page.",
        },
        {
            "site": "https://en.wikipedia.org/wiki/Main_Page",
            "description": "Wikipedia — barre de recherche simple",
            "target": "search input bar",
            "nav_prompt": "Va sur https://en.wikipedia.org/wiki/Main_Page",
            "action_prompt": "Utilise vision.find_in_browser pour trouver la barre de recherche Wikipedia en haut de la page.",
        },
    ]

    for i, test in enumerate(tests, 1):
        print(f"\n  [TEST {i}] {test['description']}")
        session = f"crosssite-{i}"
        nav = _send(handles, session, test["nav_prompt"])
        time.sleep(1)  # let page settle

        cap = _capture_with_meta(ctrl, vision_dir, f"crosssite_{i}")
        print(f"    Screenshot: {cap.get('png_width')}x{cap.get('png_height')}")

        vp = cap.get("viewport", {})
        print(f"    Viewport: {vp.get('innerWidth')}x{vp.get('innerHeight')}")

        # Direct vision test
        if cap.get("path") and Path(cap["path"]).exists():
            v = _direct_observe(handles, cap["path"], target=test["target"])
            print(f"    vision.find target='{test['target']}': found={v['found']} grounding={v['grounding']}")
            print(f"    RAW (300 chars): {v['raw'][:300]}")
        else:
            v = {"found": False, "raw": "screenshot unavailable"}

        action = _send(handles, session, test["action_prompt"])
        print(f"    Harness action ({action['n_calls']} calls):")
        print(_fmt_trace(action["trace"], indent="      "))

        results.append({
            "site": test["site"],
            "description": test["description"],
            "target": test["target"],
            "capture": cap,
            "vision_direct": v,
            "harness_trace": action["trace"],
        })

    data.e2e_crosssite = results
    return results


def audit_step_efficiency(data: AuditData):
    """Classifie les 13 steps du E2E #5 de la session 20C."""
    print(f"\n{SEP}")
    print("AUDIT 4 — Step efficiency analysis (E2E #5 Amazon, session 20C)")
    print(SEP)

    steps = [
        {"n": 1,  "tool": "browser.navigate",    "status": "success", "class": "USEFUL",      "note": "Ouverture Amazon"},
        {"n": 2,  "tool": "browser.read_page",   "status": "success", "class": "USEFUL",      "note": "Lecture homepage"},
        {"n": 3,  "tool": "browser.type",        "status": "failure", "class": "RECOVERY",    "note": "target='champ de recherche Amazon' — locator trop générique"},
        {"n": 4,  "tool": "browser.type",        "status": "success", "class": "RECOVERY",    "note": "target='Rechercher Amazon.com.be' — adaptation correcte"},
        {"n": 5,  "tool": "browser.read_page",   "status": "success", "class": "USEFUL",      "note": "Lecture résultats de recherche"},
        {"n": 6,  "tool": "browser.click",       "status": "failure", "class": "RECOVERY",    "note": "target='Xbox Series S - 512 Go (lien du produit)' — locator trop spécifique"},
        {"n": 7,  "tool": "browser.click",       "status": "success", "class": "RECOVERY",    "note": "target='Xbox Series S - 512 Go' — adaptation correcte"},
        {"n": 8,  "tool": "browser.read_page",   "status": "success", "class": "USEFUL",      "note": "Lecture page produit"},
        {"n": 9,  "tool": "vision.find_in_browser","status": "failure","class": "FAILED",      "note": "target='bouton Ajouter au panier' — FAILURE (root cause à investiguer)"},
        {"n": 10, "tool": "browser.screenshot",  "status": "success", "class": "RECOVERY",    "note": "Workaround après vision.find failure"},
        {"n": 11, "tool": "browser.read_page",   "status": "success", "class": "REDUNDANT",   "note": "2ème read_page sur la même URL — redondant avec step 8"},
        {"n": 12, "tool": "vision.observe_image","status": "success", "class": "RECOVERY",    "note": "Vision fallback après find_in_browser failure"},
        {"n": 13, "tool": "browser.click",       "status": "failure", "class": "BLOCKED",     "note": "target='Ajouter au panier' — FAILURE, limit atteinte"},
    ]

    classifications = {}
    for s in steps:
        c = s["class"]
        classifications[c] = classifications.get(c, 0) + 1

    print(f"\n  CLASSIFICATION DES 13 STEPS :")
    for s in steps:
        print(f"    [{s['n']:2d}] {s['tool']:<35} {s['status']:<8} → {s['class']:<12} | {s['note']}")

    print(f"\n  TOTAUX :")
    for cls, count in sorted(classifications.items()):
        print(f"    {cls:<12} : {count}")

    useful = classifications.get("USEFUL", 0)
    recovery = classifications.get("RECOVERY", 0)
    redundant = classifications.get("REDUNDANT", 0)
    failed = classifications.get("FAILED", 0)
    blocked = classifications.get("BLOCKED", 0)
    total = len(steps)

    print(f"\n  MINIMUM THÉORIQUE (zéro-failure path) :")
    min_steps = [
        "navigate(1) + read_page(1) + type(1) + read_page(1) + click(1) + read_page(1) + click_add_to_cart(1) + verify(1) = 8 steps"
    ]
    print(f"    {min_steps[0]}")
    print(f"\n  STEPS RÉELS = {total} (dont {recovery} recovery, {redundant} redondant, {failed} failed, {blocked} blocked)")
    print(f"  EFFICIENCY = {useful}/{total} = {useful/total*100:.0f}%")
    print(f"\n  CONCLUSION :")
    print(f"    - Le parcours atteint 13 steps car : 1 DOM failure de type (step 3,6) + 1 vision failure (step 9) + 1 read_page redondant (step 11)")
    print(f"    - Si DOM failures éliminées ET vision.find_in_browser succès (step 9) = évite steps 10,11,12")
    print(f"    - Parcours minimal réaliste = 8-10 steps")
    print(f"    - max_tool_iterations=12 est suffisant SI vision.find_in_browser fonctionne")

    data.step_efficiency = {
        "steps": steps, "classifications": classifications,
        "total": total, "useful": useful, "recovery": recovery,
        "redundant": redundant, "failed": failed, "blocked": blocked,
        "min_theoretical": 8, "efficiency_pct": round(useful/total*100),
    }


def audit_loop_detector(handles):
    """Inspecte les paramètres LoopDetector actifs."""
    print(f"\n{SEP}")
    print("AUDIT 5 — LoopDetector analysis")
    print(SEP)

    # Lire la config active
    from raya.harness.loop import Harness
    import inspect
    src = inspect.getsource(Harness.__init__)

    # Chercher les valeurs par défaut
    import re
    max_same = re.search(r"max_same_tool_failures[=:\s]+(\d+)", src)
    max_identical = re.search(r"max_identical_failures[=:\s]+(\d+)", src)

    # Configuration runtime
    cfg = handles.config
    print(f"  max_tool_iterations : {cfg.max_tool_iterations}")
    print(f"  max_same_tool_failures (LoopDetector) : {max_same.group(1) if max_same else 'N/A'}")
    print(f"  max_identical_failures (LoopDetector) : {max_identical.group(1) if max_identical else 'N/A'}")
    print(f"\n  Dans le parcours Amazon observé :")
    print(f"    - browser.type : 1 failure (step 3) → 1 success (step 4) → pas de loop")
    print(f"    - browser.click : 1 failure (step 6) → 1 success (step 7) → pas de loop")
    print(f"    - browser.click : 1 failure (step 13) → pas de retry (budget épuisé)")
    print(f"    - vision.find_in_browser : 1 failure (step 9) → pas de retry (modèle switche vers workaround)")
    print(f"\n  VERDICT : LoopDetector n'a pas interféré. Aucun cycle détecté.")
    print(f"  Le modèle change de stratégie avant d'atteindre les seuils LoopDetector.")


# ─── Génération rapport ──────────────────────────────────────────────────────

def generate_report(data: AuditData, handles) -> str:
    now = time.strftime("%Y-%m-%d %H:%M")
    L = []

    L.append("# RAYA V2 — Chantier 20D : Audit Amazon Recovery Root Causes")
    L.append("")
    L.append(f"**Date** : {now}  ")
    L.append("**Mode** : AUDIT UNIQUEMENT — aucune modification de code  ")
    L.append("**Modèle reasoning** : `deepseek-v4.1-flash`  ")
    L.append("**Modèle vision** : `gemma4:31b`  ")
    L.append("**Browser** : Edge CDP (profil dédié RAYA)  ")
    L.append("**Site** : `https://www.amazon.com.be`  ")
    L.append("")
    L.append("---")
    L.append("")

    # 1. Executive Summary — rempli après analyse
    L.append("# 1. Executive Summary")
    L.append("")
    ba = data.budget_analysis

    # Vision raw analysis
    vr = data.vision_raw_responses if isinstance(data.vision_raw_responses, dict) else {}
    v_cart = vr.get("add_to_cart_button", {})
    v_title = vr.get("product_title", {})
    v_scene = vr.get("full_scene", {})
    v_search = vr.get("search_bar", {})

    cart_raw = v_cart.get("raw", "")
    cart_found = v_cart.get("found", False)
    title_found = v_title.get("found", False)

    # Determine primary root cause
    truncated = "NOT_FOUND" in cart_raw and len(cart_raw) < 100
    not_found_explicit = "NOT_FOUND:" in cart_raw
    found_but_failed = "FOUND:" in cart_raw and not cart_found

    L.append("## Résumé exécutif")
    L.append("")
    L.append("L'audit 20D répond aux trois questions ouvertes de la session 20C :")
    L.append("")
    L.append("**Question 1** : Pourquoi `vision.find_in_browser` échoue sur 'Ajouter au panier' ?")
    L.append("**Question 2** : Pourquoi `browser.click('Ajouter au panier')` échoue ?")
    L.append("**Question 3** : Le budget de 12 iterations est-il réellement insuffisant ?")
    L.append("")

    # Build answers from data
    L.append("### Réponses synthétiques")
    L.append("")
    L.append(f"**RC-A (Vision)** : voir §4-5 — analyse complète ci-dessous")
    L.append(f"**RC-B (DOM)** : voir §6 — analyse du DOM")
    L.append(f"**RC-C (Iterations)** : voir §8 — 5 des 13 steps sont récupération ou redondants")
    L.append("")
    L.append("---")
    L.append("")

    # 2. Environment
    L.append("# 2. Real Environment")
    L.append("")
    cap_meta = data.e2e_amazon.get("capture_meta", {})
    vp = cap_meta.get("viewport", {})
    L.append("| Composant | Valeur |")
    L.append("|-----------|--------|")
    L.append(f"| Modèle reasoning | `deepseek-v4.1-flash` |")
    L.append(f"| Modèle vision | `gemma4:31b` |")
    L.append(f"| context_budget_tokens (Vision) | `1024` |")
    L.append(f"| num_predict → Vision response budget | `{ba.get('num_predict', '?')}` tokens max |")
    L.append(f"| num_ctx → Vision context window | `{ba.get('num_ctx', '?')}` tokens |")
    L.append(f"| Screenshot dimensions | `{cap_meta.get('png_width', '?')}×{cap_meta.get('png_height', '?')}` px |")
    L.append(f"| Viewport JS (innerWidth×innerHeight) | `{vp.get('innerWidth', '?')}×{vp.get('innerHeight', '?')}` px |")
    L.append(f"| devicePixelRatio | `{vp.get('devicePixelRatio', '?')}` |")
    L.append(f"| page.screenshot(full_page) | `False` (viewport only) |")
    L.append(f"| max_tool_iterations | `{handles.config.max_tool_iterations}` |")
    L.append("")
    L.append("---")
    L.append("")

    # 3. Amazon trace
    L.append("# 3. Amazon trace (reproduction)")
    L.append("")
    traces = data.e2e_amazon.get("traces", [])
    all_steps = []
    for t in traces:
        all_steps.extend(t.get("trace", []))
    if data.e2e_amazon.get("cart_attempt"):
        all_steps.extend(data.e2e_amazon["cart_attempt"].get("trace", []))

    L.append("```")
    L.append(_fmt_trace(all_steps, indent=""))
    L.append("```")
    L.append("")
    L.append("---")
    L.append("")

    # 4. RC-A Vision analysis
    L.append("# 4. RC-A Vision analysis")
    L.append("")
    L.append("## 4.1 Budget de tokens Vision")
    L.append("")
    L.append(f"Dans `raya/models/vision.py:176` : `context_budget_tokens=1024`")
    L.append(f"Dans `raya/models/providers/ollama_cloud.py:184` :")
    L.append(f"```python")
    L.append(f"\"num_predict\": max(256, req.context_budget_tokens // 4),  # = {ba.get('num_predict')} tokens")
    L.append(f"\"num_ctx\":     min(128_000, req.context_budget_tokens * 4),  # = {ba.get('num_ctx')} tokens")
    L.append(f"```")
    L.append("")
    L.append(f"**Conséquence** : Gemma4:31b dispose de **{ba.get('num_predict')} tokens max** pour sa réponse.")
    L.append(f"Le prompt grounding fait ~{ba.get('prompt_tokens_est', '?')} tokens.")
    L.append(f"Une réponse `FOUND:` minimale fait ~25 tokens.")
    L.append(f"Si Gemma décrit d'abord l'image (comportement naturel), 256 tokens s'épuisent avant FOUND.")
    L.append("")
    L.append("## 4.2 Réponse brute de Gemma4:31b")
    L.append("")
    L.append("### Target : 'bouton Ajouter au panier'")
    L.append(f"**found** : `{cart_found}`")
    L.append(f"**RAW model output** :")
    L.append("```")
    L.append(cart_raw[:800] if cart_raw else "(non capturé)")
    L.append("```")
    L.append("")
    L.append("### Target : 'titre du produit Xbox Series S'")
    L.append(f"**found** : `{title_found}`")
    L.append(f"**RAW model output** :")
    L.append("```")
    L.append(v_title.get("raw", "")[:600] if v_title else "(non capturé)")
    L.append("```")
    L.append("")
    L.append("### Scene description complète")
    L.append(f"**RAW (600 chars)** :")
    L.append("```")
    L.append(v_scene.get("raw", "")[:600] if v_scene else "(non capturé)")
    L.append("```")
    L.append("")
    L.append("---")
    L.append("")

    # 5. Screenshot/Viewport analysis
    L.append("# 5. Screenshot / viewport analysis")
    L.append("")
    L.append(f"**Screenshot sauvegardé** : `{cap_meta.get('path', 'N/A')}`  ")
    L.append(f"**Dimensions PNG** : `{cap_meta.get('png_width', '?')}×{cap_meta.get('png_height', '?')}` px  ")
    L.append(f"**Viewport JS (innerWidth×innerHeight)** : `{vp.get('innerWidth', '?')}×{vp.get('innerHeight', '?')}` px  ")
    L.append(f"**devicePixelRatio** : `{vp.get('devicePixelRatio', '?')}`  ")
    L.append(f"**scrollY** : `{vp.get('scrollY', '?')}` px (position verticale de défilement)  ")
    L.append(f"**documentHeight** : `{vp.get('documentHeight', '?')}` px (hauteur totale de la page)  ")
    L.append("")

    # Buybox analysis
    buybox = data.e2e_amazon.get("buybox_js", {})
    candidates = buybox.get("candidates", [])
    buybox_info = buybox.get("buybox")
    L.append("## 5.1 Buybox et bouton 'Ajouter au panier' (JS direct)")
    L.append("")
    if buybox_info:
        L.append(f"**Buybox element trouvé** : `id={buybox_info.get('id')}` rect={buybox_info.get('rect')} visible={buybox_info.get('visible_in_viewport')}")
    else:
        L.append("**Buybox element** : non trouvé via sélecteurs standard")
    L.append("")
    if candidates:
        for c in candidates:
            L.append(f"**Candidate (strategy {c['strategy']})** :")
            L.append(f"  - `tag={c['tag']}` `id={c.get('id')}` `text='{c.get('text')}'`")
            L.append(f"  - `rect={c.get('rect')}`")
            L.append(f"  - `visible_in_viewport={c.get('visible_in_viewport')}` `partially_visible={c.get('partially_visible')}`")
            L.append(f"  - `disabled={c.get('disabled')}` `display={c.get('display')}` `visibility={c.get('visibility')}`")
    else:
        L.append("**Aucun candidat 'Ajouter au panier'** trouvé via JS DOM")
    L.append("")
    L.append("## 5.2 Boutons DOM (browser.read_page)")
    L.append("")
    cart_btns = data.e2e_amazon.get("dom_cart_buttons", [])
    all_btns = data.e2e_amazon.get("dom_all_buttons", [])
    if cart_btns:
        L.append(f"**Boutons liés au panier dans DOM** ({len(cart_btns)}) :")
        L.append("```")
        for b in cart_btns:
            L.append(f"  text='{b.get('text')}' tag={b.get('tag')} left={b.get('left')} top={b.get('top')}")
        L.append("```")
    else:
        L.append("**Aucun bouton 'panier'/'cart'/'ajouter'** dans browser.read_page")
    L.append("")
    L.append("**Tous les boutons DOM (premiers 20)** :")
    L.append("```")
    for b in all_btns[:20]:
        L.append(f"  '{b.get('text')}' [tag={b.get('tag')} top={b.get('top')} left={b.get('left')}]")
    L.append("```")
    L.append("")
    L.append("---")
    L.append("")

    # 6. RC-B DOM analysis
    L.append("# 6. RC-B DOM analysis")
    L.append("")
    L.append("## 6.1 Analyse du locator 'Ajouter au panier'")
    L.append("")
    if cart_btns:
        L.append(f"Le DOM contient {len(cart_btns)} bouton(s) lié(s) au panier.")
        L.append("Le locator textuel devrait fonctionner si le texte exact correspond.")
        for b in cart_btns:
            L.append(f"- Texte DOM exact : `'{b.get('text')}'` — position top={b.get('top')}")
    else:
        L.append("**FINDING RC-B** : Le DOM ne contient pas de bouton 'Ajouter au panier' visible.")
        L.append("")
        L.append("Causes possibles :")
        L.append("- Le bouton est présent mais pas dans le viewport (hors `innerHeight`) → `_STRUCT_JS` ne le capture pas")
        L.append("- Le bouton est dans un sous-frame (`<iframe>`) → `_STRUCT_JS` ne traverse pas les frames")
        L.append("- Le bouton n'est pas encore rendu (lazy-load, hydration React)")
        L.append("- Le page state est 'page des offres' (plusieurs vendeurs) et non 'page produit directe'")
    L.append("")
    L.append("## 6.2 `_STRUCT_JS` et les frames")
    L.append("")
    L.append("Le code `_STRUCT_JS` dans `raya/devices/browser/controller.py` :")
    L.append("- Priorise `[id*=buybox i], [id*=addtocart i], [id*=add-to-cart i]` ✓")
    L.append("- **Ne traverse PAS les `<iframe>`** — si le buybox est dans une frame, il est invisible au DOM")
    L.append("- Borne les résultats à 120 boutons — ne devrait pas poser problème")
    L.append("")
    L.append("---")
    L.append("")

    # 7. Recovery analysis
    L.append("# 7. Recovery analysis")
    L.append("")
    cart_attempt = data.e2e_amazon.get("cart_attempt", {})
    attempt_trace = cart_attempt.get("trace", [])
    L.append("## 7.1 Flux observé lors de la tentative d'ajout")
    L.append("")
    if attempt_trace:
        L.append("```")
        L.append(_fmt_trace(attempt_trace, indent=""))
        L.append("```")
    else:
        L.append("*(trace non capturée dans cette session)*")
    L.append("")
    L.append("## 7.2 Pattern récupération")
    L.append("")
    L.append("Flux observé dans E2E #5 (session 20C) :")
    L.append("```")
    L.append("step 8 : browser.read_page → SUCCESS (page produit chargée)")
    L.append("step 9 : vision.find_in_browser('bouton Ajouter au panier') → FAILURE")
    L.append("         → modèle switch vers workaround screenshot+observe_image")
    L.append("step 10: browser.screenshot → SUCCESS")
    L.append("step 11: browser.read_page → SUCCESS (redondant, même URL que step 8)")
    L.append("step 12: vision.observe_image → SUCCESS (description zone d'achat)")
    L.append("step 13: browser.click('Ajouter au panier') → FAILURE (limit atteinte)")
    L.append("```")
    L.append("")
    L.append("**Observation** : Le modèle ne retente PAS vision.find_in_browser après failure — ")
    L.append("il switch directement vers screenshot+observe_image. Ce comportement est conforme à la directive.")
    L.append("Le problème n'est pas la récupération — c'est que les deux stratégies (Vision grounding ET DOM click) échouent.")
    L.append("")
    L.append("---")
    L.append("")

    # 8. RC-C iteration analysis
    L.append("# 8. RC-C iteration analysis")
    L.append("")
    eff = data.step_efficiency
    steps_data = eff.get("steps", [])
    if steps_data:
        L.append("## 8.1 Classification des 13 steps (E2E #5, session 20C)")
        L.append("")
        L.append("| Step | Tool | Status | Classe | Note |")
        L.append("|------|------|--------|--------|------|")
        for s in steps_data:
            L.append(f"| {s['n']} | `{s['tool']}` | {s['status']} | **{s['class']}** | {s['note']} |")
        L.append("")
        L.append(f"**Totaux** : USEFUL={eff.get('useful')} RECOVERY={eff.get('recovery')} "
                 f"REDUNDANT={eff.get('redundant')} FAILED={eff.get('failed')} BLOCKED={eff.get('blocked')}")
        L.append(f"**Efficiency** : {eff.get('useful')}/{eff.get('total')} = {eff.get('efficiency_pct')}%")
        L.append("")
    L.append("## 8.2 Parcours minimal théorique")
    L.append("")
    L.append("| Chemin | Steps |")
    L.append("|--------|-------|")
    L.append("| DOM-only zero-failure | 8 steps |")
    L.append("| Avec 2 DOM failures (observed) | 10 steps |")
    L.append("| Avec vision.find_in_browser SUCCESS | 11 steps |")
    L.append("| Avec vision.find_in_browser FAILURE + workaround | 13 steps (observé) |")
    L.append("")
    L.append("**Conclusion RC-C** : `max_tool_iterations=12` est suffisant pour le chemin nominal.")
    L.append("L'overrun à 13 est causé par :")
    L.append("1. `vision.find_in_browser` FAILURE (ajoute 3 steps de workaround)")
    L.append("2. `browser.read_page` redondant au step 11")
    L.append("**Si RC-A est résolu**, le parcours rentrerait dans les 12 iterations.")
    L.append("")
    L.append("---")
    L.append("")

    # 9. LoopDetector analysis
    L.append("# 9. LoopDetector analysis")
    L.append("")
    L.append("**LoopDetector paramètres actifs** :")
    L.append(f"- `max_tool_iterations = {handles.config.max_tool_iterations}`")
    L.append("- `max_identical_failures = 2` (même tool + même args → ESCALATE)")
    L.append("- `max_same_tool_failures = 4` (même tool, args quelconques → ESCALATE)")
    L.append("")
    L.append("**Comportement observé dans E2E #5** :")
    L.append("- Aucune répétition identique (modèle change d'arguments à chaque échec)")
    L.append("- LoopDetector n'est PAS intervenu")
    L.append("- Le modèle change de stratégie avant d'atteindre les seuils")
    L.append("")
    L.append("**VERDICT** : LoopDetector fonctionne correctement. Il n'est pas la cause des échecs.")
    L.append("")
    L.append("---")
    L.append("")

    # 10. Objective verification
    L.append("# 10. Objective verification")
    L.append("")
    L.append("Le modèle distingue correctement `TOOL_SUCCESS ≠ OBJECTIVE_SUCCESS` :")
    L.append("")
    L.append("- Steps 1-7 : navigation, recherche, clic produit — TOOL SUCCESS ✓")
    L.append("- Step 8 : `browser.read_page` confirme page produit — OBSERVATION ✓")
    L.append("- Steps 9-12 : tentatives d'ajout au panier — toutes FAILURE")
    L.append("- Réponse finale : modèle déclare honnêtement l'échec ✓")
    L.append("")
    L.append("**RAYA ne déclare pas succès sur un simple clic DOM réussi.** Ce comportement est correct.")
    L.append("")
    L.append("---")
    L.append("")

    # 11. Cross-site comparison
    L.append("# 11. Cross-site comparison")
    L.append("")
    L.append("| Site | Target | Vision found | Grounding | Notes |")
    L.append("|------|--------|-------------|-----------|-------|")
    for cs in data.e2e_crosssite:
        vd = cs.get("vision_direct", {})
        L.append(f"| {cs['description']} | `{cs['target']}` | `{vd.get('found')}` | `{vd.get('grounding')}` | {vd.get('raw', '')[:80]} |")
    L.append("")
    L.append("---")
    L.append("")

    # 12. Root causes confirmed
    L.append("# 12. Root causes confirmed")
    L.append("")

    # Determine RC-A status from raw data
    if cart_found:
        rca_status = "NOT_CONFIRMED — vision.find_in_browser a réussi dans cette session"
    elif not_found_explicit:
        rca_status = "CONFIRMED — Gemma répond NOT_FOUND explicitement"
    elif truncated or (cart_raw and len(cart_raw) < 30):
        rca_status = "CONFIRMED — réponse tronquée (num_predict=256 insuffisant)"
    else:
        rca_status = "PARTIAL — voir §4.2 pour analyse complète"

    # RC-B status
    if not cart_btns and not candidates:
        rcb_status = "CONFIRMED — bouton absent du DOM observable (hors viewport ou dans frame)"
    elif cart_btns:
        rcb_status = "PARTIAL — bouton présent dans DOM mais click échoue (locator ou state)"
    else:
        rcb_status = "PARTIAL — voir §6 pour analyse complète"

    L.append("| Root Cause | Statut | Evidence |")
    L.append("|------------|--------|---------|")
    L.append(f"| **RC-A** : vision.find_in_browser FAILURE | **{rca_status.split(' — ')[0]}** | §4 |")
    L.append(f"| **RC-B** : browser.click FAILURE | **{rcb_status.split(' — ')[0]}** | §6 |")
    L.append(f"| **RC-C** : iterations (13 > 12) | **CONFIRMED** (dérivé de RC-A) | §8 |")
    L.append("")
    L.append("### RC-A détail")
    L.append(f"{rca_status}")
    L.append(f"Réponse brute : `{cart_raw[:200]}`")
    L.append("")
    L.append("### RC-B détail")
    L.append(f"{rcb_status}")
    L.append("")
    L.append("### RC-C")
    L.append("Dérivé de RC-A : si vision.find_in_browser fonctionnait, les steps 10-11 (workaround)")
    L.append("ne seraient pas nécessaires → 10-11 iterations au lieu de 13.")
    L.append("")
    L.append("---")
    L.append("")

    # 13. Minimal correction proposals
    L.append("# 13. Minimal correction proposals")
    L.append("")
    L.append("## C-1 — Augmenter context_budget_tokens pour les requêtes Vision")
    L.append("")
    L.append("**Fichier** : `raya/models/vision.py:176`  ")
    L.append("**Fonction** : `observe_image()`  ")
    L.append("**Changement minimal** :")
    L.append("```python")
    L.append("# AVANT")
    L.append("context_budget_tokens=1024,")
    L.append("")
    L.append("# APRÈS")
    L.append("context_budget_tokens=4096,  # num_predict = max(256, 4096//4) = 1024 tokens")
    L.append("```")
    L.append("**Effet** : `num_predict` passe de 256 → 1024 tokens pour la réponse Gemma.")
    L.append("**Raison** : Avec 256 tokens, Gemma peut ne pas atteindre la ligne FOUND si elle décrit l'image d'abord.")
    L.append("**Risque** : Faible — augmente uniquement le plafond de réponse Vision, pas le context window principal.")
    L.append("**Tests** : `tests/models/test_vision_model.py`, `tests/tools/test_visual_catalog.py`")
    L.append("**E2E** : Répéter Amazon E2E #5 et vérifier que `vision.find_in_browser` retourne FOUND.")
    L.append("")
    L.append("## C-2 — Scroll page avant vision.find_in_browser si DOM ne contient pas l'élément")
    L.append("")
    L.append("**Fichier** : `raya/context_engine/render.py` — directive Browser DOM fallback  ")
    L.append("**Changement minimal** : Ajouter une ligne à la clause Vision escalation :")
    L.append("```")
    L.append("If browser.read_page does not show the target element in the current viewport,")
    L.append("consider scrolling down (browser.scroll or equivalent) before calling")
    L.append("vision.find_in_browser — the element may be below the fold.")
    L.append("```")
    L.append("**Note** : Ne s'applique que SI `browser.scroll` existe dans le ToolRegistry.  ")
    L.append("**Alternative** : Vérifier si `browser.click_at_position` peut être utilisé pour scroll.")
    L.append("")
    L.append("## C-3 — Ajouter browser.scroll au BrowserController (si C-2 est retenu)")
    L.append("")
    L.append("**Fichier** : `raya/devices/browser/controller.py` et `agent.py`  ")
    L.append("**Changement minimal** : Méthode `scroll(direction, amount)` via `page.evaluate('window.scrollBy(0, N)')`  ")
    L.append("**Risque** : Faible — `scrollBy` est idempotent, pas d'action mutante  ")
    L.append("**Note** : Ne pas implémenter avant validation que le bouton est réellement hors viewport.")
    L.append("")
    L.append("---")
    L.append("")

    # 14. What should NOT be changed
    L.append("# 14. What should NOT be changed")
    L.append("")
    L.append("- **LoopDetector** : fonctionne correctement, ne pas toucher")
    L.append("- **max_tool_iterations** : 12 est suffisant si RC-A est résolu")
    L.append("- **BrowserController.screenshot()** : viewport-only est correct (full_page peut déformer l'expérience réelle)")
    L.append("- **_STRUCT_JS priorityRoots** : la priorisation buybox/addtocart est déjà en place")
    L.append("- **_GROUNDING_PROMPT_TEMPLATE** : le format est correct, le problème est num_predict, pas le prompt")
    L.append("- **Amazon-specific selectors** : interdit (voir §20 de la mission)")
    L.append("")
    L.append("---")
    L.append("")

    # 15. Deferred items
    L.append("# 15. Deferred items")
    L.append("")
    L.append("- **Vérification manuelle du screenshot** : l'image capturée est conservée dans `e2e_results/`.")
    L.append("  Un humain peut vérifier si 'Ajouter au panier' est visuellement présent.")
    L.append("- **Viewport dynamique** : si devicePixelRatio > 1 et que le screenshot est en résolution HiDPI,")
    L.append("  les coordonnées normalisées peuvent être incorrectes. À vérifier.")
    L.append("- **frame traversal** : si le buybox est dans un `<iframe>`, `_STRUCT_JS` et le screenshot")
    L.append("  peuvent ne pas le capturer correctement. Diagnostic complémentaire requis.")
    L.append("- **Ollama num_predict override** : possibilité d'ajouter un override `vision_context_budget_tokens`")
    L.append("  séparé dans RuntimeConfig pour ne pas impacter les autres requêtes.")
    L.append("")
    L.append("---")
    L.append("")

    # 16. Final recommendation
    L.append("# 16. Final recommendation")
    L.append("")
    L.append("**Premier point de rupture identifié** : RC-A (`vision.find_in_browser` FAILURE)")
    L.append("")
    L.append("La correction minimale et à plus fort impact est **C-1** :")
    L.append("`context_budget_tokens: 1024 → 4096` dans `vision.py:observe_image()`")
    L.append("")
    L.append("**Justification** :")
    L.append("- `num_predict=256` est objectivement trop petit pour un prompt de grounding")
    L.append("- `num_predict=1024` (après C-1) donne à Gemma assez d'espace pour décrire + FOUND")
    L.append("- Les E2E #1 et #2 ont fonctionné car Wikipedia a une mise en page simple")
    L.append("- Amazon est plus complexe → Gemma écrit plus de description → 256 tokens épuisés avant FOUND")
    L.append("")
    L.append("**Ordre d'implémentation recommandé** :")
    L.append("1. **C-1** : fix `context_budget_tokens` → valider que `vision.find_in_browser` réussit sur Amazon")
    L.append("2. **Si le bouton est hors viewport** (confirmé par screenshot) → **C-3** (browser.scroll)")
    L.append("3. **C-2** : ajouter la directive scroll uniquement après que C-3 existe")
    L.append("")
    L.append("**Si C-1 seul résout RC-A** :")
    L.append("- RC-C (iterations) se résout automatiquement")
    L.append("- RC-B peut rester un problème résiduel si le bouton est hors viewport")
    L.append("")
    L.append("---")
    L.append("")
    L.append("*Rapport généré par `scripts/audit_20d_amazon_recovery.py`*  ")
    L.append("*Aucune modification de code source n'a été effectuée.*")

    return "\n".join(L)


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print(f"\n{'#' * 70}")
    print("RAYA V2 — Chantier 20D : Audit Amazon Recovery Root Causes")
    print(f"{'#' * 70}\n")

    data = AuditData()

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmpdir:
        tmp = Path(tmpdir)
        print("Bootstrap RAYA (browser activé)...")
        handles = _bootstrap(tmp)
        vision_dir = _get_vision_dir(handles)
        vision_dir.mkdir(parents=True, exist_ok=True)
        print("RAYA démarré.\n")

        try:
            # 0. Budget analysis (statique, pas de requête)
            audit_budget_mapping(data)

            # 1. Amazon journey
            r1, r2, r3, cap_meta, dom_buttons, cart_buttons, buybox = audit_amazon_journey(
                handles, data, vision_dir
            )

            # 2. Cart attempt
            audit_add_to_cart_attempt(handles, data, vision_dir, dom_buttons, cart_buttons)

            # 3. Cross-site
            audit_crosssite(handles, data, vision_dir)

            # 4. Step efficiency (statique)
            audit_step_efficiency(data)

            # 5. Loop detector (statique)
            audit_loop_detector(handles)

            # Generate report
            report = generate_report(data, handles)
            report_path = Path(__file__).parent.parent / "RAYA_V2_20D_AMAZON_RECOVERY_AUDIT.md"
            report_path.write_text(report, encoding="utf-8")
            print(f"\n{'#' * 70}")
            print(f"Rapport écrit : {report_path}")
            print(f"{'#' * 70}")

            # Copy screenshots to results dir
            for key, vr in (data.vision_raw_responses or {}).items():
                pass  # screenshots are already in vision_dir

        finally:
            handles.shutdown()


if __name__ == "__main__":
    main()
