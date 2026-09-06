"""Sérialisation thread — EXTRACT quasi verbatim de modules/browser/worker.py
(V1, classé EXTRACT dans RAYA_V2_MIGRATION_MAP.md #20/#78). L'API sync de
Playwright est liée au thread qui l'a démarrée (greenlets) — impossible
d'appeler le navigateur depuis un autre thread. Toutes les opérations
navigateur sont donc sérialisées dans UN SEUL thread dédié ; garde
anti-deadlock si un appel est déjà imbriqué dans ce thread."""

from __future__ import annotations

import queue
import threading


class _Job:
    __slots__ = ("fn", "done", "result", "error")

    def __init__(self, fn):
        self.fn = fn
        self.done = threading.Event()
        self.result = None
        self.error = None


class BrowserWorker:
    def __init__(self) -> None:
        self._q: "queue.Queue[_Job]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def _ensure(self) -> None:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, daemon=True, name="raya-browser")
                self._thread.start()

    def _loop(self) -> None:
        while True:
            job = self._q.get()
            try:
                job.result = job.fn()
            except BaseException as exc:
                job.error = exc
            finally:
                job.done.set()

    def in_worker_thread(self) -> bool:
        return threading.current_thread() is self._thread

    def run_sync(self, fn, timeout: float = 60.0):
        if self.in_worker_thread():
            return fn()
        self._ensure()
        job = _Job(fn)
        self._q.put(job)
        if not job.done.wait(timeout):
            raise TimeoutError(f"browser worker: timeout ({timeout:.0f}s)")
        if job.error is not None:
            raise job.error
        return job.result
