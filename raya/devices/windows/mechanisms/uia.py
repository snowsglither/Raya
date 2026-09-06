"""Windows UI Automation — EXTRACT quasi verbatim de modules/pc_control/elements.py
(V1, classé EXTRACT dans RAYA_V2_MIGRATION_MAP.md #52/#78 — "le mécanisme UIA",
pas "l'ancien agent qui décidait quoi cliquer", consigne Phase 4 §2).

Contrainte COM/threading : uiautomation repose sur COM (apartment cohérent
requis). TOUTES les opérations passent par un unique thread worker
COM-initialisé (`_UIAWorker`) — sérialise les accès, et aucun objet COM ne
franchit la frontière du worker (chaque opération fait find+action de bout en
bout et ne renvoie que des dicts sérialisables).

`invoke()` est le mécanisme le plus important du fichier : escalade
Invoke→Toggle→SelectionItem→ExpandCollapse→Legacy, et `verified` reste
honnête (True/False si le pattern permet réellement de constater un
changement d'état, None sinon — jamais une vérification inventée, cohérent
avec la règle Phase 3 "no claim without evidence").
"""

from __future__ import annotations

import difflib
import logging
import queue
import threading

from . import window_mgmt as _win

_log = logging.getLogger("raya.devices.windows.uia")

_SEARCH_TIMEOUT_S = 6.0
_MAX_NODES = 8000
_DEFAULT_MAX_DEPTH = 22


class _Task:
    __slots__ = ("fn", "result", "error", "done")

    def __init__(self, fn):
        self.fn = fn
        self.result = None
        self.error = None
        self.done = threading.Event()


class _UIAWorker:
    def __init__(self) -> None:
        self._q: "queue.Queue[_Task | None]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._init_error: str | None = None

    def _ensure(self) -> None:
        with self._lock:
            if self._thread is None:
                self._thread = threading.Thread(target=self._loop, daemon=True, name="raya-uia")
                self._thread.start()
        self._ready.wait(timeout=10)

    def _loop(self) -> None:
        try:
            import uiautomation as auto

            auto.SetGlobalSearchTimeout(_SEARCH_TIMEOUT_S)
            auto.GetRootControl()
        except Exception as exc:
            self._init_error = str(exc)
            self._ready.set()
            _log.error(f"[UIA] init worker echec : {exc}")
            return
        self._ready.set()
        while True:
            task = self._q.get()
            if task is None:
                return
            try:
                task.result = task.fn()
            except Exception as exc:
                task.error = exc
            finally:
                task.done.set()

    def submit(self, fn, timeout: float = 30.0):
        self._ensure()
        if self._init_error:
            return {"status": "error", "error": f"UIA indisponible : {self._init_error}"}
        task = _Task(fn)
        self._q.put(task)
        if not task.done.wait(timeout):
            return {"status": "error", "error": f"UIA timeout ({timeout}s)"}
        if task.error is not None:
            raise task.error
        return task.result


_WORKER = _UIAWorker()


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def _ctype(control) -> str:
    try:
        name = control.ControlTypeName or ""
    except Exception:
        return ""
    return name[:-7].lower() if name.endswith("Control") else name.lower()


def _rect(control) -> dict | None:
    try:
        r = control.BoundingRectangle
        if r is None:
            return None
        left, top, right, bottom = int(r.left), int(r.top), int(r.right), int(r.bottom)
        if right <= left or bottom <= top:
            return None
        return {
            "left": left, "top": top, "right": right, "bottom": bottom,
            "width": right - left, "height": bottom - top,
            "center_x": (left + right) // 2, "center_y": (top + bottom) // 2,
        }
    except Exception:
        return None


def _patterns(control) -> list[str]:
    out = []
    probes = (
        ("invoke", "GetInvokePattern"), ("value", "GetValuePattern"),
        ("toggle", "GetTogglePattern"), ("selectionitem", "GetSelectionItemPattern"),
        ("expandcollapse", "GetExpandCollapsePattern"), ("legacy", "GetLegacyIAccessiblePattern"),
    )
    for label, meth in probes:
        try:
            fn = getattr(control, meth, None)
            if fn and fn() is not None:
                out.append(label)
        except Exception:
            pass
    return out


def _value_of(control) -> str | None:
    try:
        vp = control.GetValuePattern()
        if vp is not None:
            return vp.Value
    except Exception:
        pass
    try:
        lp = control.GetLegacyIAccessiblePattern()
        if lp is not None and lp.Value:
            return lp.Value
    except Exception:
        pass
    return None


def _focused(control) -> bool:
    try:
        return bool(control.HasKeyboardFocus)
    except Exception:
        return False


def _to_dict(control, depth: int = 0, with_value: bool = False) -> dict:
    d = {
        "name": (getattr(control, "Name", "") or "")[:120],
        "control_type": _ctype(control),
        "automation_id": getattr(control, "AutomationId", "") or "",
        "class_name": getattr(control, "ClassName", "") or "",
        "enabled": bool(getattr(control, "IsEnabled", True)),
        "offscreen": bool(getattr(control, "IsOffscreen", False)),
        "focused": _focused(control),
        "rect": _rect(control),
        "patterns": _patterns(control),
        "depth": depth,
    }
    if with_value:
        d["value"] = _value_of(control)
    return d


def _window_control(hwnd: int):
    import uiautomation as auto

    return auto.ControlFromHandle(hwnd)


def _walk(root, max_depth: int):
    import uiautomation as auto

    n = 0
    for control, depth in auto.WalkControl(root, includeTop=False, maxDepth=max_depth):
        yield control, depth
        n += 1
        if n >= _MAX_NODES:
            return


def _matches(control, selector: dict) -> bool:
    name = selector.get("name")
    ctype = selector.get("control_type")
    aid = selector.get("automation_id")
    if aid and _norm(getattr(control, "AutomationId", "")) != _norm(aid):
        return False
    if ctype and _ctype(control) != _norm(ctype):
        return False
    if name:
        cname = _norm(getattr(control, "Name", ""))
        target = _norm(name)
        if not cname:
            return False
        if target not in cname and difflib.SequenceMatcher(None, cname, target).ratio() < 0.8:
            return False
    return True


def _score(control, selector: dict, depth: int) -> float:
    s = 0.0
    name = _norm(selector.get("name", ""))
    cname = _norm(getattr(control, "Name", ""))
    if name and cname:
        if cname == name:
            s += 10
        elif name in cname:
            s += 6
        else:
            s += 4 * difflib.SequenceMatcher(None, cname, name).ratio()
    if selector.get("automation_id"):
        s += 8
    try:
        if control.IsEnabled:
            s += 2
        if not control.IsOffscreen:
            s += 2
    except Exception:
        pass
    pats = _patterns(control)
    if {"invoke", "selectionitem", "toggle"} & set(pats):
        s += 3
    s -= depth * 0.05
    return s


def _find_document(win, max_depth: int):
    for control, _depth in _walk(win, max_depth):
        try:
            if _ctype(control) == "document":
                return control
        except Exception:
            continue
    return None


def _best_in(root, selector: dict, max_depth: int, include_root: bool = False):
    best = None
    best_score = -1e9
    seq = ([(root, 0)] if include_root else []) + list(_walk(root, max_depth))
    for control, depth in seq:
        try:
            if _matches(control, selector):
                sc = _score(control, selector, depth)
                if sc > best_score:
                    best, best_score = control, sc
        except Exception:
            continue
    return best


def _find_best(hwnd: int, selector: dict, max_depth: int):
    """Priorité au contenu WEB (Document) dans un navigateur — sinon un nom
    générique comme 'profil' matcherait la barre d'outils du navigateur au
    lieu du contenu de la page. Repli sur toute la fenêtre sinon."""
    win = _window_control(hwnd)
    if win is None:
        return None
    doc = _find_document(win, max_depth)
    if doc is not None:
        in_doc = _best_in(doc, selector, max_depth)
        if in_doc is not None:
            return in_doc
        if selector.get("_doc_only"):
            return None
    return _best_in(win, selector, max_depth, include_root=True)


def _resolve(winspec) -> tuple[int | None, dict]:
    if isinstance(winspec, dict):
        meta = _win.resolve_window(**{k: v for k, v in winspec.items() if v is not None})
    else:
        meta = _win.resolve_window(query=winspec or "")
    if meta.get("status") == "ok":
        return meta["chosen"]["hwnd"], meta
    return None, meta


def read_elements(window, control_types=None, max_count: int = 60, max_depth: int = _DEFAULT_MAX_DEPTH) -> dict:
    hwnd, meta = _resolve(window)
    if hwnd is None:
        return {"status": "not_found", "window": window, "candidates": meta.get("candidates", [])}

    wanted = None
    if control_types:
        if isinstance(control_types, str):
            control_types = [c.strip() for c in control_types.split(",") if c.strip()]
        wanted = {_norm(c) for c in control_types}

    def _op():
        win = _window_control(hwnd)
        if win is None:
            return {"status": "error", "error": "ControlFromHandle a echoue"}
        roots = []
        doc = _find_document(win, max_depth)
        if doc is not None:
            roots.append(doc)
        roots.append(win)

        elements = []
        seen = set()
        for root in roots:
            if len(elements) >= max_count:
                break
            for control, depth in _walk(root, max_depth):
                ct = _ctype(control)
                if wanted and ct not in wanted:
                    continue
                name = getattr(control, "Name", "") or ""
                aid = getattr(control, "AutomationId", "") or ""
                if not name and not aid:
                    continue
                if _rect(control) is None:
                    continue
                key = (name, aid, ct)
                if key in seen:
                    continue
                seen.add(key)
                elements.append(_to_dict(control, depth))
                if len(elements) >= max_count:
                    break
        return {"status": "ok", "window": win.Name, "count": len(elements), "elements": elements}

    return _WORKER.submit(_op)


def _toggle_state(control):
    try:
        p = control.GetTogglePattern()
        return p.ToggleState if p is not None else None
    except Exception:
        return None


def _expand_state(control):
    try:
        p = control.GetExpandCollapsePattern()
        return p.ExpandCollapseState if p is not None else None
    except Exception:
        return None


def _is_selected(control):
    try:
        p = control.GetSelectionItemPattern()
        return bool(p.IsSelected) if p is not None else None
    except Exception:
        return None


def invoke(window, selector: dict, max_depth: int = _DEFAULT_MAX_DEPTH) -> dict:
    """Active l'élément via le meilleur pattern (Invoke>Toggle>SelectionItem>
    ExpandCollapse>Legacy). `verified` : True/False quand le pattern permet
    de constater le changement d'état, None honnête sinon (jamais inventé)."""
    hwnd, meta = _resolve(window)
    if hwnd is None:
        return {"status": "not_found", "window": window, "invoked": False, "candidates": meta.get("candidates", [])}

    def _op():
        control = _find_best(hwnd, selector, max_depth)
        if control is None:
            return {"status": "ok", "invoked": False, "reason": "element introuvable", "selector": selector}
        if not getattr(control, "IsEnabled", True):
            return {"status": "ok", "invoked": False, "reason": "element desactive", "element": _to_dict(control)}
        info = _to_dict(control)

        try:
            p = control.GetInvokePattern()
            if p is not None:
                p.Invoke()
                return {"status": "ok", "invoked": True, "pattern": "invoke", "verified": None, "element": info}
        except Exception as exc:
            _log.debug(f"[UIA] invoke echec : {exc}")

        try:
            p = control.GetTogglePattern()
            if p is not None:
                before = _toggle_state(control)
                p.Toggle()
                after = _toggle_state(control)
                return {"status": "ok", "invoked": True, "pattern": "toggle",
                        "verified": (before is not None and after is not None and after != before),
                        "state_before": before, "state_after": after, "element": info}
        except Exception as exc:
            _log.debug(f"[UIA] toggle echec : {exc}")

        try:
            p = control.GetSelectionItemPattern()
            if p is not None:
                p.Select()
                return {"status": "ok", "invoked": True, "pattern": "selectionitem",
                        "verified": bool(_is_selected(control)), "element": info}
        except Exception as exc:
            _log.debug(f"[UIA] selectionitem echec : {exc}")

        try:
            p = control.GetExpandCollapsePattern()
            if p is not None:
                before = _expand_state(control)
                p.Expand()
                after = _expand_state(control)
                return {"status": "ok", "invoked": True, "pattern": "expandcollapse",
                        "verified": (before is not None and after is not None and after != before),
                        "state_before": before, "state_after": after, "element": info}
        except Exception as exc:
            _log.debug(f"[UIA] expandcollapse echec : {exc}")

        try:
            p = control.GetLegacyIAccessiblePattern()
            if p is not None:
                p.DoDefaultAction()
                return {"status": "ok", "invoked": True, "pattern": "legacy", "verified": None, "element": info}
        except Exception as exc:
            _log.debug(f"[UIA] legacy echec : {exc}")

        return {"status": "ok", "invoked": False, "reason": "aucun pattern activable", "element": info}

    return _WORKER.submit(_op)


def set_value(window, selector: dict, text: str, max_depth: int = _DEFAULT_MAX_DEPTH) -> dict:
    hwnd, meta = _resolve(window)
    if hwnd is None:
        return {"status": "not_found", "window": window, "set": False, "candidates": meta.get("candidates", [])}

    def _op():
        control = _find_best(hwnd, selector, max_depth)
        if control is None:
            return {"status": "ok", "set": False, "reason": "element introuvable"}
        try:
            vp = control.GetValuePattern()
            if vp is None:
                return {"status": "ok", "set": False, "reason": "pas de ValuePattern"}
            if getattr(vp, "IsReadOnly", False):
                return {"status": "ok", "set": False, "reason": "champ en lecture seule"}
            control.SetFocus()
            vp.SetValue(text)
            return {"status": "ok", "set": True, "element": _to_dict(control)}
        except Exception as exc:
            return {"status": "ok", "set": False, "reason": str(exc)}

    return _WORKER.submit(_op)


def get_center(window, selector: dict, max_depth: int = _DEFAULT_MAX_DEPTH) -> dict:
    """Centre écran réel de l'élément — pour le fallback souris : jamais une
    coordonnée devinée, toujours un rectangle résolu par UIA."""
    hwnd, meta = _resolve(window)
    if hwnd is None:
        return {"status": "not_found", "window": window, "candidates": meta.get("candidates", [])}

    def _op():
        control = _find_best(hwnd, selector, max_depth)
        if control is None:
            return {"status": "ok", "found": False}
        r = _rect(control)
        if r is None:
            return {"status": "ok", "found": False, "reason": "pas de rectangle visible"}
        return {"status": "ok", "found": True, "x": r["center_x"], "y": r["center_y"], "rect": r}

    return _WORKER.submit(_op)


def probe() -> dict:
    def _op():
        import uiautomation as auto

        root = auto.GetRootControl()
        return {"status": "ok", "root": root.Name if root else "", "com": "ok"}

    return _WORKER.submit(_op, timeout=12)
