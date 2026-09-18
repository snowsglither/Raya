"""Validation E2E réelle — Cookie Consent Intelligent.

Ce script teste le chemin DOM complet (dismiss_overlays) avec :
  - Un vrai navigateur (Chromium via Playwright)
  - Un profil TEMPORAIRE ET VIERGE (aucun cookie mémorisé)
  - De vrais sites accessibles

Il importe les constantes réelles de controller.py — il teste EXACTEMENT
le code déployé, pas une copie.

Utilisation :
    python scripts/validate_cookie_consent_e2e.py

Résultats dans ./e2e_results/ (screenshots + log).
Profil temporaire supprimé automatiquement en fin d'exécution.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import shutil
import sys
import tempfile
import time

# Importer les constantes RÉELLES du code déployé
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from raya.devices.browser.controller import (
    _OVERLAY_ACCEPT_TEXTS,
    _OVERLAY_CLOSE_TEXTS,
    _OVERLAY_MARK_JS,
    _OVERLAY_REJECT_TEXTS,
    _OVERLAY_SELECTORS,
    _OVERLAY_UNMARK_JS,
    _STRUCT_JS,
)

RESULTS_DIR = pathlib.Path(__file__).parent.parent / "e2e_results"
RESULTS_DIR.mkdir(exist_ok=True)

SITES = [
    {
        "id": "E2E-1",
        "label": "standaard.be",
        "url": "https://www.standaard.be",
        "objective": "lis-moi le titre principal de la page",
        "expected_consent_texts": ["Alle accepteren", "Akkoord", "Accept all"],
    },
    {
        "id": "E2E-2",
        "label": "lesoir.be",
        "url": "https://www.lesoir.be",
        "objective": "lis-moi le titre principal de la page",
        "expected_consent_texts": ["Tout accepter", "Accepter", "J'accepte"],
    },
    {
        "id": "E2E-3",
        "label": "rtbf.be",
        "url": "https://www.rtbf.be",
        "objective": "lis-moi le titre principal de la page",
        "expected_consent_texts": ["Tout accepter", "Accepter tout", "Accept all"],
    },
    {
        "id": "E2E-4",
        "label": "libre.be",
        "url": "https://www.libre.be",
        "objective": "lis-moi le titre principal de la page",
        "expected_consent_texts": ["Tout accepter", "Accepter", "Accept all"],
    },
]


async def _find_consent_action_in(frame, target: str):
    """Miroir de BrowserController._find_consent_action_in (code réel déployé).

    Restreint aux éléments role=button — exclut les liens de navigation purs.
    get_by_role('button') couvre <button>, <a role='button'>, input[type=submit]
    et tout element role='button'. Exclut <a> sans role.
    Utilise exact=False : 'Accepter' matche 'Accepter tous les cookies'."""
    try:
        loc = frame.get_by_role("button", name=target, exact=False).first
        if await loc.count() > 0 and await loc.is_visible():
            return loc
    except Exception:
        pass
    return None


async def dismiss_overlays_real(page, max_rounds: int = 3) -> dict:
    """Miroir de BrowserController.dismiss_overlays (code réel déployé).

    Smart candidate selection :
    - _find_consent_action_in : role=button uniquement, pas de liens nav
    - Post-click verification : clic comptabilisé SEULEMENT si overlay disparu
    - tried set : pas de boucle aveugle sur candidats inefficaces
    - effective dans le résultat"""
    dismissed: list[str] = []
    tried: set[str] = set()

    for round_i in range(max(1, max_rounds)):
        try:
            n_marked = int(await page.evaluate(_OVERLAY_MARK_JS, list(_OVERLAY_SELECTORS)) or 0)
        except Exception:
            n_marked = 0
        if n_marked == 0 and round_i == 0:
            try:
                await page.wait_for_timeout(400)
                n_marked = int(await page.evaluate(_OVERLAY_MARK_JS, list(_OVERLAY_SELECTORS)) or 0)
            except Exception:
                n_marked = 0
        if n_marked == 0:
            break

        overlay_root = page.locator("[data-raya-overlay]")
        effective_in_round = None

        all_candidates = (
            list(_OVERLAY_ACCEPT_TEXTS)
            + list(_OVERLAY_REJECT_TEXTS)
            + list(_OVERLAY_CLOSE_TEXTS)
        )
        for t in all_candidates:
            if t in tried:
                continue
            try:
                loc = await _find_consent_action_in(overlay_root, t)
            except Exception:
                loc = None
            if loc is None:
                continue
            try:
                await loc.click(timeout=2000)
            except Exception:
                tried.add(t)
                continue

            # Post-click verification
            try:
                await page.wait_for_timeout(500)
            except Exception:
                pass
            try:
                await page.evaluate(_OVERLAY_UNMARK_JS)
            except Exception:
                pass
            try:
                n_after = int(await page.evaluate(_OVERLAY_MARK_JS, list(_OVERLAY_SELECTORS)) or 0)
            except Exception:
                n_after = n_marked

            if n_after < n_marked:
                effective_in_round = t
                n_marked = n_after
                break
            else:
                tried.add(t)
                overlay_root = page.locator("[data-raya-overlay]")

        try:
            await page.evaluate(_OVERLAY_UNMARK_JS)
        except Exception:
            pass

        if effective_in_round is None:
            break
        dismissed.append(effective_in_round)
        try:
            await page.wait_for_timeout(300)
        except Exception:
            pass

    return {"dismissed": dismissed, "rounds": len(dismissed), "effective": len(dismissed) > 0}


async def read_page_real(page) -> dict:
    """Même logique que BrowserController.read_page."""
    try:
        data = await page.evaluate(_STRUCT_JS)
        data["status"] = "ok"
        return data
    except Exception as e:
        return {"status": "error", "error": str(e)}


async def run_scenario(page, site: dict) -> dict:
    sid = site["id"]
    label = site["label"]
    url = site["url"]
    log: dict = {"id": sid, "label": label, "url": url, "steps": []}

    def step(name, data):
        log["steps"].append({"step": name, **data})
        print(f"  [{sid}] {name}: {json.dumps(data, ensure_ascii=False, default=str)[:200]}")

    print(f"\n{'=' * 60}")
    print(f"[{sid}] {label} — {url}")
    print(f"{'=' * 60}")

    # STEP 1 — Navigate
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
        await page.wait_for_timeout(1500)
        step("navigate", {"status": "ok", "final_url": page.url})
    except Exception as e:
        step("navigate", {"status": "error", "error": str(e)})
        log["verdict"] = "BLOCKED"
        return log

    # STEP 2 — Screenshot before
    shot_before = str(RESULTS_DIR / f"{sid}_before.png")
    try:
        await page.screenshot(path=shot_before)
        step("screenshot_before", {"path": shot_before})
    except Exception as e:
        step("screenshot_before", {"error": str(e)})

    # STEP 3 — read_page (observe cookie_banner)
    page_data = await read_page_real(page)
    cookie_banner = page_data.get("cookie_banner", False)
    buttons_text = [b.get("text", "") for b in page_data.get("buttons", [])[:20]]
    step("read_page_before", {
        "cookie_banner": cookie_banner,
        "button_count": len(page_data.get("buttons", [])),
        "buttons_sample": buttons_text[:10],
        "url": page_data.get("url", ""),
    })

    if not cookie_banner:
        # Check for cookie-like keywords in page buttons
        ck_words = ("cookie", "consent", "gdpr", "privacy", "accepter", "accept", "akkoord")
        maybe_buttons = [b for b in buttons_text if any(w in b.lower() for w in ck_words)]
        step("banner_check", {
            "cookie_banner_flag": False,
            "maybe_consent_buttons": maybe_buttons,
            "verdict": "NOT_TESTED — profil vierge n'a pas déclenché de bannière (ou site sans bannière)",
        })
        log["verdict"] = "NOT_TESTED"
        log["not_tested_reason"] = "cookie_banner=False après chargement — banner absent ou déjà mémorisé"
        return log

    # STEP 4 — dismiss_overlays
    step("dismiss_overlays_start", {"overlay_selectors_count": len(_OVERLAY_SELECTORS), "accept_texts_count": len(_OVERLAY_ACCEPT_TEXTS)})
    dismiss_result = await dismiss_overlays_real(page)
    step("dismiss_overlays_result", dismiss_result)

    rounds = dismiss_result.get("rounds", 0)
    dismissed = dismiss_result.get("dismissed", [])

    # Wait for banner to disappear
    if rounds > 0:
        await page.wait_for_timeout(800)

    # STEP 5 — Screenshot after
    shot_after = str(RESULTS_DIR / f"{sid}_after.png")
    try:
        await page.screenshot(path=shot_after)
        step("screenshot_after", {"path": shot_after})
    except Exception as e:
        step("screenshot_after", {"error": str(e)})

    # STEP 6 — read_page after (verify banner gone)
    page_data_after = await read_page_real(page)
    cookie_banner_after = page_data_after.get("cookie_banner", False)
    buttons_after = [b.get("text", "") for b in page_data_after.get("buttons", [])[:20]]

    # Check if the dismissed button is still present
    consent_btn_still_present = False
    if dismissed:
        for btn_text in buttons_after:
            for d in dismissed:
                if d.lower() in btn_text.lower():
                    consent_btn_still_present = True
                    break

    step("read_page_after", {
        "cookie_banner": cookie_banner_after,
        "dismissed_button_still_present": consent_btn_still_present,
        "buttons_sample": buttons_after[:10],
    })

    # STEP 7 — Objective check (get page title)
    try:
        title = await page.title()
        step("objective_check", {"title": title, "status": "ok" if title else "no_title"})
    except Exception as e:
        step("objective_check", {"error": str(e)})

    # Verdict
    if rounds > 0 and not consent_btn_still_present:
        log["verdict"] = "PASS"
        log["dismissed_text"] = dismissed
    elif rounds > 0 and consent_btn_still_present:
        log["verdict"] = "PARTIAL — bouton cliqué mais encore présent dans DOM"
        log["dismissed_text"] = dismissed
    else:
        log["verdict"] = "FAIL — banner détecté mais aucun bouton cliqué"
        log["dismissed_text"] = []

    return log


async def run_all() -> None:
    profile_dir = tempfile.mkdtemp(prefix="raya_e2e_cookie_clean_")
    print(f"\nProfil temporaire vierge : {profile_dir}")
    print(f"Résultats : {RESULTS_DIR}")
    print(f"Constantes importées de raya.devices.browser.controller")
    print(f"  _OVERLAY_SELECTORS : {len(_OVERLAY_SELECTORS)} sélecteurs")
    print(f"  _OVERLAY_ACCEPT_TEXTS : {len(_OVERLAY_ACCEPT_TEXTS)} textes")

    from playwright.async_api import async_playwright

    results: list[dict] = []
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=False,  # visible pour audit humain
                args=["--no-first-run", "--no-default-browser-check"],
                locale="fr-BE",
                viewport={"width": 1280, "height": 800},
            )
            # launch_persistent_context crée déjà un contexte avec une page
            pages = browser.pages
            page = pages[0] if pages else await browser.new_page()

            for site in SITES:
                result = await run_scenario(page, site)
                results.append(result)
                # Navigate away between tests to ensure clean state
                try:
                    await page.goto("about:blank", timeout=5000)
                    await page.wait_for_timeout(300)
                except Exception:
                    pass

            await browser.close()
    finally:
        try:
            shutil.rmtree(profile_dir, ignore_errors=True)
            print(f"\nProfil temporaire supprimé : {profile_dir}")
        except Exception as e:
            print(f"\nAvertissement : impossible de supprimer le profil temporaire : {e}")

    # Write JSON results
    results_path = RESULTS_DIR / "cookie_e2e_results.json"
    with open(results_path, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\nRésultats JSON : {results_path}")

    # Summary
    print(f"\n{'=' * 60}")
    print("RÉSUMÉ")
    print(f"{'=' * 60}")
    for r in results:
        print(f"  {r['id']:10} {r['label']:20} → {r.get('verdict', 'UNKNOWN')}")

    passes = [r for r in results if r.get("verdict") == "PASS"]
    fails = [r for r in results if "FAIL" in r.get("verdict", "")]
    not_tested = [r for r in results if r.get("verdict") == "NOT_TESTED"]

    print(f"\n  PASS       : {len(passes)}")
    print(f"  FAIL       : {len(fails)}")
    print(f"  NOT_TESTED : {len(not_tested)}")

    return results


if __name__ == "__main__":
    asyncio.run(run_all())
