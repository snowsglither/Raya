"""Serveur HTTP local pour les benchmarks Browser Device Agent (consigne
Phase 4 §27 : "créer des environnements locaux contrôlés lorsque les sites
externes ne sont pas adaptés aux tests" — cookie banner/shopping ambigus,
sans jamais dépendre d'Amazon réel ni d'un achat réel, §22).

`/slow.html` répond avec un délai artificiel — sert à prouver que le
mécanisme d'auto-wait (Playwright natif, consigne §12) attend réellement
plutôt que d'échouer sur un sleep fixe insuffisant."""

from __future__ import annotations

import functools
import http.server
import threading
import time
from pathlib import Path

_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "browser"


class _DelayingHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (nom imposé par http.server)
        if self.path.startswith("/slow.html"):
            time.sleep(1.2)
        super().do_GET()

    def log_message(self, format: str, *args) -> None:  # silence les logs de test
        pass


class LocalFixtureServer:
    def __init__(self) -> None:
        handler = functools.partial(_DelayingHandler, directory=str(_FIXTURES_DIR))
        self._httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        host, port = self._httpd.server_address
        return f"http://127.0.0.1:{port}"

    def url_for(self, filename: str) -> str:
        return f"{self.base_url}/{filename}"

    def shutdown(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
