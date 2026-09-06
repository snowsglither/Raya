"""Session navigateur — EXTRACT du mécanisme CDP de modules/browser/session.py
(V1, classé EXTRACT dans RAYA_V2_MIGRATION_MAP.md #20/#78 : "cycle de vie
CDP"). Simplification assumée (documentée dans le rapport Phase 4) : SEUL le
mode "profil dédié" de V1 est repris (le mode fiable et déjà en prod selon
[[feedback_raya_visual_strict]]) — les modes "profil nommé"/"vrai profil"/
"autorelaunch" de V1 sont hors scope de cette phase.

Règles ABSOLUES reprises telles quelles : port de debug 127.0.0.1 UNIQUEMENT
(jamais exposé réseau), RAYA ne ferme JAMAIS le navigateur de l'utilisateur —
un profil dédié à RAYA (`%LOCALAPPDATA%/RayaV2-EdgeProfile`) garantit que le
port se lie toujours sans jamais toucher l'Edge personnel de l'utilisateur ;
RAYA ne ferme que les onglets qu'elle a elle-même ouverts (`_raya_pages`)."""

from __future__ import annotations

import os
import subprocess
import time
import urllib.request

_CDP_HOST = "127.0.0.1"
_CDP_PORT = int(os.environ.get("RAYA_CDP_PORT", "9223"))  # port dédié différent de V1 (9222) — jamais de conflit
_CDP_URL = f"http://{_CDP_HOST}:{_CDP_PORT}"
_CDP_LAUNCH_TIMEOUT = 12.0
_CDP_POLL = 0.25


def _cdp_reachable() -> bool:
    try:
        with urllib.request.urlopen(f"{_CDP_URL}/json/version", timeout=1.0) as r:
            return getattr(r, "status", 200) == 200
    except Exception:
        return False


def _dedicated_profile_dir() -> str | None:
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return None
    d = os.path.join(local, "RayaV2-EdgeProfile")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return d


def _locate_edge() -> str | None:
    pf = os.environ.get("PROGRAMFILES", r"C:\Program Files")
    pfx = os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")
    for c in (os.path.join(pfx, r"Microsoft\Edge\Application\msedge.exe"),
              os.path.join(pf, r"Microsoft\Edge\Application\msedge.exe")):
        if os.path.isfile(c):
            return c
    return None


def _launch_edge_cdp(exe: str, user_data_dir: str) -> None:
    args = [exe, f"--remote-debugging-port={_CDP_PORT}", f"--remote-debugging-address={_CDP_HOST}",
            "--no-first-run", "--no-default-browser-check", f"--user-data-dir={user_data_dir}"]
    subprocess.Popen(args)


def _wait_cdp(timeout: float = _CDP_LAUNCH_TIMEOUT) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _cdp_reachable():
            return True
        time.sleep(_CDP_POLL)
    return _cdp_reachable()


class BrowserSession:
    """Session unique attachée au vrai Edge (profil dédié RAYA) via CDP.
    Distingue les onglets ouverts PAR RAYA (seuls fermables) des onglets
    éventuels déjà présents dans ce profil dédié."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._context = None
        self._page = None
        self._raya_pages: set = set()

    def get_or_create(self):
        if self._context is not None and self._browser is not None:
            try:
                if self._browser.is_connected():
                    if self._page is not None and not self._page.is_closed():
                        return self._page
                    pages = self._context.pages
                    self._page = pages[-1] if pages else self._context.new_page()
                    return self._page
            except Exception:
                pass
            self._cleanup()
        return self._start()

    def _ensure_cdp(self) -> None:
        if _cdp_reachable():
            return
        exe = _locate_edge()
        if not exe:
            raise RuntimeError("Edge introuvable sur cette machine.")
        udd = _dedicated_profile_dir()
        if not udd:
            raise RuntimeError("LOCALAPPDATA indisponible — impossible de créer le profil dédié.")
        _launch_edge_cdp(exe, udd)
        if not _wait_cdp():
            raise RuntimeError("L'Edge dédié de RAYA n'a pas ouvert le port de pilotage à temps.")

    def _start(self):
        from playwright.sync_api import sync_playwright

        self._ensure_cdp()
        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.connect_over_cdp(_CDP_URL)
        except Exception as exc:
            self._cleanup()
            raise RuntimeError(f"Connexion CDP impossible : {exc}") from exc
        contexts = self._browser.contexts
        self._context = contexts[0] if contexts else self._browser.new_context()
        self._page = self._context.new_page()
        self._raya_pages = {self._page}
        return self._page

    def close(self) -> None:
        """Se déconnecte sans jamais fermer Edge — ferme uniquement les
        onglets que RAYA a elle-même ouverts."""
        try:
            for pg in list(self._raya_pages):
                try:
                    if not pg.is_closed():
                        pg.close()
                except Exception:
                    pass
        except Exception:
            pass
        try:
            if self._pw:
                self._pw.stop()
        except Exception:
            pass
        finally:
            self._cleanup()

    def is_open(self) -> bool:
        try:
            if self._browser is None or not self._browser.is_connected():
                return False
            if self._page is None or self._page.is_closed():
                return False
            return True
        except Exception:
            return False

    def _cleanup(self) -> None:
        self._browser = None
        self._context = None
        self._pw = None
        self._page = None
        self._raya_pages = set()
