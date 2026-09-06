"""Gestion de fenêtres Windows — EXTRACT quasi verbatim de
modules/pc_control/windows.py (V1, classé EXTRACT dans RAYA_V2_MIGRATION_MAP.md
#52/#78). Mécanisme pur : `resolve_window()` classe les candidats de façon
déterministe (titre exact > commence par > contient > process, z-order en
tie-break) et ne choisit JAMAIS silencieusement "la première fenêtre" en cas
d'égalité — `ambiguous=True` remonte l'ambiguïté à l'appelant (Cognition/
Harness), ce n'est pas ce module qui décide. Aucune ligne de décision "QUOI
faire" ici, uniquement "COMMENT résoudre/agir sur une fenêtre donnée".

Ce qui n'a PAS été repris de V1 : `move_window_to_monitor` (dépend de
modules/system/displays, hors scope Phase 4).
"""

from __future__ import annotations

import time

import win32con
import win32gui
import win32process
import psutil


def _iter_visible_windows() -> list[dict]:
    out: list[dict] = []

    def _cb(hwnd, _lparam):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd) or ""
            if not title.strip():
                return
            pid = 0
            proc = ""
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                proc = psutil.Process(pid).name()
            except Exception:
                pass
            try:
                cls = win32gui.GetClassName(hwnd) or ""
            except Exception:
                cls = ""
            try:
                minimized = bool(win32gui.IsIconic(hwnd))
            except Exception:
                minimized = False
            out.append({
                "hwnd": int(hwnd), "title": title, "pid": int(pid),
                "process": proc, "class_name": cls, "minimized": minimized,
            })
        except Exception:
            pass

    try:
        win32gui.EnumWindows(_cb, None)  # ordre de z : premier = plus haut
    except Exception:
        pass
    for i, w in enumerate(out):
        w["z"] = i
    return out


def _clean(w: dict) -> dict:
    return {k: w[k] for k in ("hwnd", "title", "pid", "process", "class_name", "minimized")}


def find_windows(query: str, match_class: str | None = None) -> list[dict]:
    q = (query or "").strip().lower()
    q_proc = q if q.endswith(".exe") else q + ".exe"
    mc = (match_class or "").lower()
    matches = []
    for w in _iter_visible_windows():
        if mc and mc not in (w["class_name"] or "").lower():
            continue
        if not q:
            if mc:
                matches.append(w)
            continue
        title = (w["title"] or "").lower()
        proc = (w["process"] or "").lower()
        if q in title or q in proc or proc == q_proc:
            matches.append(w)
    return matches


def resolve_window(query: str = "", pid: int | None = None, hwnd: int | None = None,
                    exact_title: str | None = None, match_class: str | None = None) -> dict:
    """Choisit UNE fenêtre de façon déterministe.
    Priorité : hwnd explicite > filtres (pid/exact_title/classe) > score du query.
    Renvoie {status, chosen, candidates[], ambiguous, reason} — ne devine jamais."""
    wins = _iter_visible_windows()

    if hwnd:
        for w in wins:
            if w["hwnd"] == int(hwnd):
                return {"status": "ok", "chosen": _clean(w), "candidates": [_clean(w)],
                        "ambiguous": False, "reason": "hwnd exact"}
        return {"status": "not_found", "reason": f"hwnd {hwnd} introuvable", "candidates": []}

    cands = wins
    if pid is not None:
        cands = [w for w in cands if w["pid"] == int(pid)]
    if exact_title:
        t = exact_title.strip().lower()
        cands = [w for w in cands if (w["title"] or "").strip().lower() == t]
    if match_class:
        mc = match_class.lower()
        cands = [w for w in cands if mc in (w["class_name"] or "").lower()]

    if query:
        q = query.strip().lower()
        q_proc = q if q.endswith(".exe") else q + ".exe"
        scored = []
        for w in cands:
            title = (w["title"] or "").lower()
            proc = (w["process"] or "").lower()
            score = 0
            if title == q:
                score = 100
            elif title.startswith(q):
                score = 70
            elif q in title:
                score = 50
            if proc == q_proc or (q and q in proc):
                score = max(score, 40)
            if score == 0:
                continue
            if not w["minimized"]:
                score += 5
            scored.append((score, -w["z"], w))
        if not scored:
            return {"status": "not_found", "query": query,
                    "candidates": [_clean(w) for w in cands[:5]],
                    "reason": "aucune fenêtre ne correspond au query"}
        scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
        chosen = scored[0][2]
        ambiguous = len(scored) > 1 and scored[0][0] == scored[1][0]
        return {"status": "ok", "chosen": _clean(chosen),
                "candidates": [_clean(s[2]) for s in scored[:5]],
                "ambiguous": ambiguous,
                "reason": f"score={scored[0][0]}" + (" (ÉGALITÉ)" if ambiguous else "")}

    if not cands:
        return {"status": "not_found", "reason": "aucun candidat (filtres pid/titre/classe)", "candidates": []}
    return {"status": "ok", "chosen": _clean(cands[0]),
            "candidates": [_clean(w) for w in cands[:5]],
            "ambiguous": len(cands) > 1, "reason": "premier candidat (z-order) après filtres"}


def is_open(query: str) -> tuple[bool, list[dict]]:
    m = find_windows(query)
    return (len(m) > 0, m)


def _activate_hwnd(hwnd: int, title: str, extra: dict) -> dict:
    try:
        import pygetwindow as gw

        wins = [w for w in gw.getAllWindows() if (w.title or "") == title]
        if wins:
            w = wins[0]
            try:
                if w.isMinimized:
                    w.restore()
                w.activate()
                time.sleep(0.2)
                return {"status": "ok", "window": title, "method": "pygetwindow", **extra}
            except Exception:
                pass
    except Exception:
        pass
    try:
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        except Exception:
            pass
        try:
            import ctypes

            ctypes.windll.user32.AllowSetForegroundWindow(-1)
        except Exception:
            pass
        win32gui.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        return {"status": "ok", "window": title, "method": "win32", **extra}
    except Exception as exc:
        return {"status": "error", "window": title, "error": str(exc)}


def focus_window(target: str = "", pid: int | None = None, hwnd: int | None = None,
                  exact_title: str | None = None, match_class: str | None = None) -> dict:
    r = resolve_window(query=target, pid=pid, hwnd=hwnd, exact_title=exact_title, match_class=match_class)
    if r["status"] != "ok":
        return {"status": "not_found", "query": target, "candidates": r.get("candidates", [])}
    c = r["chosen"]
    res = _activate_hwnd(c["hwnd"], c["title"], {"process": c["process"], "pid": c["pid"]})
    res["ambiguous"] = r.get("ambiguous", False)
    return res


def get_active_window() -> dict:
    try:
        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return {"status": "ok", "active": None}
        title = win32gui.GetWindowText(hwnd) or ""
        pid = 0
        proc = ""
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc = psutil.Process(pid).name()
        except Exception:
            pass
        return {"status": "ok", "active": {"hwnd": int(hwnd), "title": title, "pid": int(pid), "process": proc}}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def list_windows() -> dict:
    wins = [_clean(w) for w in _iter_visible_windows()]
    return {"status": "ok", "count": len(wins), "windows": wins}


def get_window_rect(target: str = "", **kw) -> dict:
    r = resolve_window(query=target, **kw)
    if r["status"] != "ok":
        return {"status": "not_found", "query": target}
    try:
        left, top, right, bottom = win32gui.GetWindowRect(r["chosen"]["hwnd"])
        return {"status": "ok", "window": r["chosen"]["title"],
                "rect": {"left": left, "top": top, "right": right, "bottom": bottom,
                         "width": right - left, "height": bottom - top}}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}


def wait_for_window(query: str, timeout_s: float = 12.0, poll_s: float = 0.3,
                     match_class: str | None = None) -> dict:
    """Attend qu'une fenêtre correspondante APPARAISSE — condition réelle de
    disponibilité, jamais un sleep fixe (consigne Phase 4 §12)."""
    deadline = time.time() + max(1.0, timeout_s)
    while time.time() < deadline:
        matches = find_windows(query, match_class=match_class)
        if matches:
            return {"status": "ready", "window": matches[0]["title"],
                    "process": matches[0]["process"],
                    "waited_s": round(timeout_s - (deadline - time.time()), 1)}
        time.sleep(poll_s)
    return {"status": "timeout", "query": query, "timeout_s": timeout_s}


def close_window(target: str = "", **kw) -> dict:
    """Fermeture GRACIEUSE (WM_CLOSE) — jamais taskkill (V1 le documentait déjà)."""
    r = resolve_window(query=target, **kw)
    if r["status"] != "ok":
        return {"status": "not_found", "query": target, "candidates": r.get("candidates", [])}
    try:
        win32gui.PostMessage(r["chosen"]["hwnd"], win32con.WM_CLOSE, 0, 0)
        return {"status": "ok", "action": "close", "window": r["chosen"]["title"]}
    except Exception as exc:
        return {"status": "error", "action": "close", "error": str(exc)}
