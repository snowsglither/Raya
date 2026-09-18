"""Actions DOM génériques — EXTRACT du mécanisme de modules/pc_control/browser.py
(V1, classé EXTRACT dans RAYA_V2_MIGRATION_MAP.md #20/#78). Toutes les
fonctions ici sont des MÉCANISMES : elles reçoivent une cible déjà décidée
(URL, texte de bouton, sélecteur) et exécutent — zéro décision "quoi
chercher/cliquer".

Simplification assumée (documentée, rapport Phase 4) : clic/saisie utilisent
les locators natifs Playwright (`.click()`/`.fill()`, auto-wait intégré) au
lieu de l'émulation souris physique de V1 (`web_mouse.py`, "règle Ruben" —
anti-détection) — non requis par la consigne Phase 4, simplification
délibérée pour rester dans le périmètre "petit nombre de capacités réelles"
(§38).

`dismiss_overlays` reprend TEL QUEL le correctif de production du
2026-08-30 : le clic accepter/refuser/fermer est restreint à l'INTÉRIEUR de
l'élément overlay détecté — jamais une recherche page entière qui peut
tomber dans une publicité/carte produit sans rapport à côté du bandeau
(bug réel documenté en V1, exactement le scénario du benchmark cookie
banner de la consigne Phase 4 §9)."""

from __future__ import annotations

from .session import BrowserSession
from .worker import BrowserWorker

_OVERLAY_SELECTORS = (
    "[id*=cookie i]", "[class*=cookie i]", "[id*=consent i]", "[class*=consent i]",
    "[role=dialog]", "[aria-modal=true]", ".modal", ".popup", ".overlay",
    ".gdpr", ".cookie-banner",
)
_OVERLAY_ACCEPT_TEXTS = ("Tout accepter", "Accepter tout", "Accepter", "J'accepte", "Accept all", "I agree", "Agree", "Got it", "OK")
_OVERLAY_REJECT_TEXTS = ("Refuser", "Tout refuser", "Reject all", "Decline")
_OVERLAY_CLOSE_TEXTS = ("Fermer", "Close", "Non merci", "No thanks", "Plus tard", "Not now", "Skip")

_OVERLAY_MARK_JS = r"""
(selectors) => {
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) < 0.05) return false;
    return r.bottom > 0 && r.right > 0 && r.top < innerHeight + 200 && r.left < innerWidth + 200;
  };
  let n = 0;
  for (const sel of selectors) {
    try {
      for (const el of document.querySelectorAll(sel)) {
        if (vis(el)) { el.setAttribute('data-raya-overlay', '1'); n++; }
      }
    } catch (e) {}
  }
  return n;
}
"""
_OVERLAY_UNMARK_JS = "() => { for (const el of document.querySelectorAll('[data-raya-overlay]')) el.removeAttribute('data-raya-overlay'); }"

_STRUCT_JS = r"""
() => {
  const vis = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return false;
    const s = getComputedStyle(el);
    if (s.visibility === 'hidden' || s.display === 'none' || parseFloat(s.opacity) < 0.05) return false;
    return r.bottom > 0 && r.right > 0 && r.top < innerHeight + 200 && r.left < innerWidth + 200;
  };
  const label = (el) => (
    (el.innerText || el.value || el.getAttribute('aria-label') ||
     el.getAttribute('title') || el.getAttribute('placeholder') || '')
    .replace(/\s+/g, ' ').trim().slice(0, 90)
  );
  const grab = (sel, kind, cap, root) => {
    const out = [];
    for (const el of (root || document).querySelectorAll(sel)) {
      if (!vis(el)) continue;
      const t = label(el);
      if (!t && kind !== 'input') continue;
      out.push({ kind, text: t, tag: el.tagName.toLowerCase() });
      if (out.length >= cap) break;
    }
    return out;
  };
  const BTN_SEL = 'button, [role=button], input[type=submit], input[type=button]';
  const buttons = grab(BTN_SEL, 'button', 120);
  const links = grab('a[href]', 'link', 60);
  const grab_inputs = (sel, cap) => {
    const out = [];
    for (const el of document.querySelectorAll(sel)) {
      if (!vis(el)) continue;
      const it = el.getAttribute('type');
      const t = (it === 'password')
        ? (el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || '').replace(/\s+/g, ' ').trim().slice(0, 90)
        : label(el);
      const item = { kind: 'input', text: t, tag: el.tagName.toLowerCase() };
      if (it) item.input_type = it;
      if (el.disabled) item.disabled = true;
      const ar = el.getAttribute('role') || el.getAttribute('aria-role');
      if (ar) item.aria_role = ar;
      out.push(item);
      if (out.length >= cap) break;
    }
    return out;
  };
  const inputs = grab_inputs('input:not([type=hidden]):not([type=submit]):not([type=button]), textarea, [role=searchbox], [contenteditable=true]', 20);
  let cookieBanner = false;
  for (const el of document.querySelectorAll('[id*=cookie i],[class*=cookie i],[id*=consent i],[class*=consent i]')) {
    if (vis(el)) { cookieBanner = true; break; }
  }
  return { url: location.href, title: document.title, cookie_banner: cookieBanner, buttons, links, inputs };
}
"""

_CART_CONFIRM_PATTERNS = (
    "ajouté au panier", "ajoutée au panier", "ajouté à votre panier",
    "added to cart", "added to your cart", "item added", "was added",
    "dans votre panier", "in your cart",
)


class BrowserController:
    def __init__(self, session: BrowserSession, worker: BrowserWorker) -> None:
        self._session = session
        self._worker = worker

    def _page(self):
        return self._session.get_or_create()

    def navigate(self, url: str, timeout_ms: int = 15_000) -> dict:
        def _op():
            page = self._page()
            page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            return {"status": "ok", "url": page.url, "title": page.title()}

        return self._worker.run_sync(_op)

    def read_page(self) -> dict:
        def _op():
            page = self._page()
            data = page.evaluate(_STRUCT_JS)
            data["status"] = "ok"
            return data

        return self._worker.run_sync(_op)

    def screenshot(self, path: str) -> dict:
        def _op():
            page = self._page()
            page.screenshot(path=path)
            width, height = None, None
            try:
                from PIL import Image
                with Image.open(path) as img:
                    width, height = img.size
            except Exception:
                try:
                    vp = page.viewport_size
                    if vp:
                        width, height = vp.get("width"), vp.get("height")
                except Exception:
                    pass
            result: dict = {"status": "ok", "path": path}
            if width is not None:
                result["width"] = width
            if height is not None:
                result["height"] = height
            try:
                result["url"] = page.url
                result["title"] = page.title()
            except Exception:
                pass
            return result

        return self._worker.run_sync(_op)

    def list_tabs(self) -> dict:
        def _op():
            page = self._page()
            pages = self._session._context.pages
            return {"status": "ok", "count": len(pages), "active_url": page.url,
                    "tabs": [{"index": i, "url": pg.url, "raya": self._session.is_raya_tab(pg) if hasattr(self._session, "is_raya_tab") else pg in self._session._raya_pages}
                             for i, pg in enumerate(pages)]}

        return self._worker.run_sync(_op)

    def _find_clickable_in(self, frame, target: str):
        esc = target.replace("\\", "\\\\").replace("'", "\\'")
        for getter in (
            lambda: frame.get_by_role("button", name=target, exact=False),
            lambda: frame.get_by_role("link", name=target, exact=False),
            lambda: frame.get_by_label(target, exact=False),
            lambda: frame.get_by_title(target, exact=False),
            lambda: frame.locator(f"[aria-label*='{esc}' i]"),
            lambda: frame.get_by_text(target, exact=False),
        ):
            try:
                loc = getter().first
                if loc.count() > 0 and loc.is_visible():
                    return loc
            except Exception:
                continue
        return None

    def find_clickable(self, target: str):
        """Localise un élément cliquable par sa DESCRIPTION — jamais un
        sélecteur CSS codé en dur (pas de table par site)."""
        page = self._page()
        loc = self._find_clickable_in(page, target)
        if loc is not None:
            return loc
        try:
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                loc = self._find_clickable_in(frame, target)
                if loc is not None:
                    return loc
        except Exception:
            pass
        return None

    def click(self, target: str, timeout_ms: int = 5_000) -> dict:
        def _op():
            loc = self.find_clickable(target)
            if loc is None:
                return {"status": "not_found", "target": target}
            loc.click(timeout=timeout_ms)
            try:
                url = self._page().url
            except Exception:
                url = ""
            return {"status": "ok", "target": target, "url": url}

        return self._worker.run_sync(_op)

    def click_at_position(self, x: int, y: int) -> dict:
        def _op():
            page = self._page()
            page.mouse.click(x, y)
            return {"status": "ok", "x": x, "y": y}

        return self._worker.run_sync(_op)

    def type_text(self, target: str, text: str, timeout_ms: int = 5_000, submit: bool = False) -> dict:
        """`submit` (passe 'Post-Repair Validation', trouvé en E2E réel —
        recherche Coolblue jamais soumise) : `.fill()` seul ne déclenche
        JAMAIS la recherche d'un champ (contrairement à une vraie saisie
        clavier) — beaucoup de champs de recherche n'ont pas de bouton
        visible et n'agissent qu'au clavier (touche Entrée). Générique,
        jamais un cas par site : simple option du mécanisme `type` déjà
        existant, jamais un nouveau Tool."""
        def _op():
            page = self._page()
            for getter in (
                lambda: page.get_by_label(target, exact=False),
                lambda: page.get_by_placeholder(target, exact=False),
                lambda: page.locator(f"[aria-label*='{target}' i]"),
            ):
                try:
                    loc = getter().first
                    if loc.count() > 0 and loc.is_visible():
                        loc.fill(text, timeout=timeout_ms)
                        if submit:
                            loc.press("Enter", timeout=timeout_ms)
                        return {"status": "ok", "target": target}
                except Exception:
                    continue
            return {"status": "not_found", "target": target}

        return self._worker.run_sync(_op)

    def dismiss_overlays(self, max_rounds: int = 3) -> dict:
        """Ferme bannière cookies/consentement/modale. Clic RESTREINT à
        l'intérieur de l'overlay détecté (correctif de production V1
        2026-08-30) — jamais une recherche page entière."""

        def _op():
            page = self._page()
            dismissed: list[str] = []
            for round_i in range(max(1, max_rounds)):
                try:
                    n_marked = int(page.evaluate(_OVERLAY_MARK_JS, list(_OVERLAY_SELECTORS)) or 0)
                except Exception:
                    n_marked = 0
                if n_marked == 0 and round_i == 0:
                    try:
                        page.wait_for_timeout(400)
                        n_marked = int(page.evaluate(_OVERLAY_MARK_JS, list(_OVERLAY_SELECTORS)) or 0)
                    except Exception:
                        n_marked = 0
                if n_marked == 0:
                    break
                overlay_root = page.locator("[data-raya-overlay]")
                clicked = None
                for texts in (_OVERLAY_ACCEPT_TEXTS, _OVERLAY_REJECT_TEXTS, _OVERLAY_CLOSE_TEXTS):
                    for t in texts:
                        try:
                            loc = self._find_clickable_in(overlay_root, t)
                        except Exception:
                            loc = None
                        if loc is None:
                            continue
                        try:
                            loc.click(timeout=2000)
                            clicked = t
                        except Exception:
                            pass
                        break
                    if clicked:
                        break
                try:
                    page.evaluate(_OVERLAY_UNMARK_JS)
                except Exception:
                    pass
                if not clicked:
                    break
                dismissed.append(clicked)
                try:
                    page.wait_for_timeout(300)
                except Exception:
                    pass
            return {"status": "ok", "dismissed": dismissed, "rounds": len(dismissed)}

        return self._worker.run_sync(_op)

    def check_confirmation(self, url_contains: tuple[str, ...] = ("/cart", "/panier"),
                            text_patterns: tuple[str, ...] = _CART_CONFIRM_PATTERNS) -> bool | None:
        """Vérification HONNÊTE générique — True SEULEMENT sur un signal réel
        (URL ou texte de confirmation visible), None si inconclusif (jamais
        un succès inventé, consigne Phase 4 §17/§18)."""

        def _op():
            page = self._page()
            from urllib.parse import urlparse

            path = urlparse(page.url or "").path.lower()
            if any(u in path for u in url_contains):
                return True
            try:
                body_text = page.evaluate("() => document.body ? document.body.innerText.toLowerCase() : ''")
            except Exception:
                body_text = ""
            if any(p in body_text for p in text_patterns):
                return True
            return None

        return self._worker.run_sync(_op)
