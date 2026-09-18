"""Audit DOM ciblé — standaard.be : pourquoi "OK" bat "Akkoord".

Ce script :
1. Ouvre standaard.be avec un profil vierge
2. Marque tous les overlays avec _OVERLAY_MARK_JS
3. Cherche et liste TOUS les candidats textuels dans l'overlay marqué
4. Pour chaque candidat : texte, rôle, tag, aria-label, position, visibilité, parent
5. Simule la recherche _find_clickable_in pour chaque texte de _OVERLAY_ACCEPT_TEXTS
   et affiche quel élément serait sélectionné EN PREMIER

Résultat : comprendre exactement quel "OK" est trouvé et pourquoi.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import shutil
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
from raya.devices.browser.controller import (
    _OVERLAY_ACCEPT_TEXTS,
    _OVERLAY_MARK_JS,
    _OVERLAY_SELECTORS,
    _OVERLAY_UNMARK_JS,
)

RESULTS_DIR = pathlib.Path(__file__).parent.parent / "e2e_results"
RESULTS_DIR.mkdir(exist_ok=True)

# JS pour inspecter en détail TOUS les éléments interactifs dans les overlays marqués
_DEEP_INSPECT_JS = """
() => {
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) < 0.05) return false;
    return r.bottom > 0 && r.right > 0 && r.top < innerHeight + 200 && r.left < innerWidth + 200;
  };

  const BTN_SEL = 'button, [role=button], a, input[type=submit], input[type=button], [tabindex]';
  const overlays = document.querySelectorAll('[data-raya-overlay]');
  const results = [];

  for (const overlay of overlays) {
    const overlayInfo = {
      tag: overlay.tagName.toLowerCase(),
      id: overlay.id || '',
      class: overlay.className || '',
      rect: (() => { const r = overlay.getBoundingClientRect(); return {top: Math.round(r.top), left: Math.round(r.left), width: Math.round(r.width), height: Math.round(r.height)}; })(),
      text_preview: (overlay.innerText || '').slice(0, 200).replace(/\s+/g, ' ').trim(),
      children: []
    };

    for (const el of overlay.querySelectorAll(BTN_SEL)) {
      if (!vis(el)) continue;
      const r = el.getBoundingClientRect();
      const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '').replace(/\s+/g, ' ').trim().slice(0, 100);
      if (!text) continue;
      overlayInfo.children.push({
        tag: el.tagName.toLowerCase(),
        role: el.getAttribute('role') || '',
        text: text,
        aria_label: el.getAttribute('aria-label') || '',
        title: el.getAttribute('title') || '',
        class: (el.className || '').slice(0, 80),
        id: el.id || '',
        rect: {top: Math.round(r.top), left: Math.round(r.left), width: Math.round(r.width), height: Math.round(r.height)},
        parent_text: (el.parentElement ? (el.parentElement.innerText || '').slice(0, 80) : '').trim().replace(/\s+/g, ' '),
      });
    }
    results.push(overlayInfo);
  }
  return results;
}
"""

# Simule _find_clickable_in avec get_by_text/get_by_role et retourne ce qui est trouvé en premier
async def simulate_find_clickable_in(frame, target: str) -> dict | None:
    esc = target.replace("\\", "\\\\").replace("'", "\\'")
    methods = [
        ("get_by_role(button)", lambda: frame.get_by_role("button", name=target, exact=False)),
        ("get_by_role(link)", lambda: frame.get_by_role("link", name=target, exact=False)),
        ("get_by_label", lambda: frame.get_by_label(target, exact=False)),
        ("get_by_title", lambda: frame.get_by_title(target, exact=False)),
        (f"aria-label*=", lambda: frame.locator(f"[aria-label*='{esc}' i]")),
        ("get_by_text", lambda: frame.get_by_text(target, exact=False)),
    ]
    for method_name, getter in methods:
        try:
            loc = getter().first
            count = await loc.count()
            if count > 0 and await loc.is_visible():
                try:
                    inner_text = await loc.inner_text()
                except Exception:
                    inner_text = "?"
                try:
                    tag = await loc.evaluate("el => el.tagName.toLowerCase()")
                except Exception:
                    tag = "?"
                try:
                    rect = await loc.bounding_box()
                except Exception:
                    rect = None
                return {
                    "found": True,
                    "method": method_name,
                    "text": inner_text.strip()[:80],
                    "tag": tag,
                    "rect": rect,
                }
        except Exception:
            continue
    return None


async def audit_standaard() -> None:
    profile_dir = tempfile.mkdtemp(prefix="raya_audit_standaard_")
    print(f"Profil vierge : {profile_dir}")

    from playwright.async_api import async_playwright

    report = {}
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=False,
                args=["--no-first-run", "--no-default-browser-check"],
                locale="nl-BE",  # langue néerlandaise — comme un vrai utilisateur belge flamand
                viewport={"width": 1280, "height": 800},
            )
            pages = browser.pages
            page = pages[0] if pages else await browser.new_page()

            print("\n[1/6] Navigation vers standaard.be...")
            await page.goto("https://www.standaard.be", wait_until="domcontentloaded", timeout=20_000)
            await page.wait_for_timeout(2000)

            # Screenshot initial
            shot_initial = str(RESULTS_DIR / "audit_standaard_initial.png")
            await page.screenshot(path=shot_initial)
            print(f"  Screenshot initial : {shot_initial}")

            print("\n[2/6] Marquage des overlays...")
            n_marked = int(await page.evaluate(_OVERLAY_MARK_JS, list(_OVERLAY_SELECTORS)) or 0)
            print(f"  {n_marked} overlay(s) marqué(s)")
            report["n_overlays_marked"] = n_marked

            if n_marked == 0:
                print("  AUCUN overlay détecté — site sans bannière sur ce profil ?")
                report["verdict"] = "NO_BANNER"
                await browser.close()
                return

            print("\n[3/6] Inspection profonde des éléments dans les overlays...")
            deep = await page.evaluate(_DEEP_INSPECT_JS)
            report["overlays"] = deep

            for i, ov in enumerate(deep):
                print(f"\n  Overlay {i}: tag={ov['tag']} id={ov['id']!r} class={ov['class'][:60]!r}")
                print(f"  Rect : {ov['rect']}")
                print(f"  Texte (200 chars) : {ov['text_preview'][:100]!r}")
                print(f"  Enfants cliquables ({len(ov['children'])}) :")
                for ch in ov["children"]:
                    print(f"    [{ch['tag']}] text={ch['text']!r} role={ch['role']!r} aria={ch['aria_label']!r} rect={ch['rect']}")

            print("\n[4/6] Simulation de _find_clickable_in pour chaque texte d'acceptation...")
            overlay_root = page.locator("[data-raya-overlay]")
            candidates: list[dict] = []
            first_found = None
            for text in _OVERLAY_ACCEPT_TEXTS:
                result = await simulate_find_clickable_in(overlay_root, text)
                if result:
                    result["searched_text"] = text
                    candidates.append(result)
                    if first_found is None:
                        first_found = result
                        print(f"  [FIRST MATCH] '{text}' -> method={result['method']} found_text={result['text']!r} tag={result['tag']}")
                    else:
                        print(f"  [ALSO FOUND]  '{text}' -> method={result['method']} found_text={result['text']!r} tag={result['tag']}")
                else:
                    print(f"  [NOT FOUND]   '{text}'")

            report["candidates"] = candidates
            report["first_found"] = first_found

            print("\n[5/6] Screenshot avec overlays marqués (debug visuel)...")
            shot_marked = str(RESULTS_DIR / "audit_standaard_marked.png")
            await page.screenshot(path=shot_marked)
            print(f"  {shot_marked}")

            print("\n[6/6] Nettoyage...")
            await page.evaluate(_OVERLAY_UNMARK_JS)
            await browser.close()

    finally:
        shutil.rmtree(profile_dir, ignore_errors=True)
        print(f"Profil supprimé : {profile_dir}")

    report_path = RESULTS_DIR / "audit_standaard_dom.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=str)
    print(f"\nRapport JSON : {report_path}")

    print("\n" + "=" * 60)
    print("CONCLUSION AUDIT")
    print("=" * 60)
    if first_found:
        print(f"  Premier texte trouve : '{first_found['searched_text']}'")
        print(f"  Element cible        : tag={first_found['tag']} text={first_found['text']!r}")
        print(f"  Methode              : {first_found['method']}")
    print(f"  Total candidats trouves : {len(candidates)}/{len(_OVERLAY_ACCEPT_TEXTS)}")


if __name__ == "__main__":
    asyncio.run(audit_standaard())
