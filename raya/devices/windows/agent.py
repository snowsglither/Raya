"""WindowsDeviceAgent — implémente `DeviceAgent` (RAYA_V2_TECHNICAL_ARCHITECTURE.md
§1.13). Dispatche une `Command` déjà décidée vers le mécanisme approprié —
AUCUNE décision "quoi faire", uniquement "comment le faire". N'importe jamais
`raya.models` (invariant #5).

Capacités exposées volontairement réduites par rapport à la liste complète
de la consigne Phase 4 §3 (application.*, window.*, keyboard.*, mouse.*,
screen.capture, ui.*, process.list) — process.start/stop/mouse.scroll/
ui.select non implémentés (§38 : peu de capacités réellement validées plutôt
que beaucoup de façade), voir le rapport Phase 4 §"Known limitations".
`filesystem.find_folder`/`filesystem.open_path` AJOUTÉS Chantier 12 §C
(découverte ciblée, jamais un scan massif — voir mechanisms/filesystem.py)."""

from __future__ import annotations

import time
from pathlib import Path

import psutil

from raya.contracts import Capability, Command, CommandStatus, Health, Result, ErrorInfo

from ..base import DeviceAgent, ShouldStop, _never_stop
from . import strategy
from .mechanisms import applications, capability_discovery, filesystem, keyboard, launcher_resolver, mouse, package_manager, screen, shell, software_discovery, uia, window_mgmt

DEVICE_ID = "windows_agent"

_CAPABILITIES = [
    Capability(name="application.launch", input_schema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}, mechanism_hint="startfile+wait_for_window"),
    Capability(name="application.close", input_schema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}, mechanism_hint="win32_wm_close"),
    Capability(name="application.focus", input_schema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}, mechanism_hint="win32_setforeground"),
    Capability(name="application.list", input_schema={"type": "object", "properties": {}}, mechanism_hint="win32_enumwindows"),
    Capability(name="window.list", input_schema={"type": "object", "properties": {}}, mechanism_hint="win32_enumwindows"),
    Capability(name="window.focus", input_schema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}, mechanism_hint="win32_setforeground"),
    Capability(name="window.close", input_schema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}, mechanism_hint="win32_wm_close"),
    Capability(name="keyboard.type", input_schema={"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, mechanism_hint="clipboard_paste"),
    Capability(name="keyboard.press", input_schema={"type": "object", "properties": {"combo": {"type": "string"}}, "required": ["combo"]}, mechanism_hint="pyautogui_hotkey"),
    Capability(name="mouse.click", input_schema={"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}, "button": {"type": "string"}}, "required": ["x", "y"]}, mechanism_hint="win32_mouse_event"),
    Capability(name="mouse.move", input_schema={"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]}, mechanism_hint="win32_setcursorpos"),
    Capability(name="screen.capture", input_schema={"type": "object", "properties": {"filename": {"type": "string"}}}, mechanism_hint="pyautogui_screenshot"),
    Capability(name="ui.inspect", input_schema={"type": "object", "properties": {"window": {"type": "string"}, "control_types": {"type": "string"}}, "required": ["window"]}, mechanism_hint="uia_walk"),
    Capability(name="ui.click", input_schema={"type": "object", "properties": {"window": {"type": "string"}, "selector": {"type": "object"}}, "required": ["window", "selector"]}, mechanism_hint="uia_invoke"),
    Capability(name="ui.type", input_schema={"type": "object", "properties": {"window": {"type": "string"}, "selector": {"type": "object"}, "text": {"type": "string"}}, "required": ["window", "selector", "text"]}, mechanism_hint="uia_setvalue"),
    Capability(name="process.list", input_schema={"type": "object", "properties": {}}, mechanism_hint="psutil"),
    # Chantier 12 §C (Living Environment Awareness) — découverte ciblée,
    # jamais un scan massif (voir mechanisms/filesystem.py).
    Capability(name="filesystem.find_folder", input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}, mechanism_hint="os_scandir_bounded"),
    Capability(name="filesystem.open_path", input_schema={"type": "object", "properties": {"path": {"type": "string"}, "name": {"type": "string"}}, "required": ["path"]}, mechanism_hint="os_startfile"),
    # Chantier 16 (Capability Discovery / CLI vs GUI) — découverte bornée
    # (PATH uniquement, jamais un scan disque) et exécution CLI cachée
    # (jamais une fenêtre visible, principe "USE ≠ SHOW").
    Capability(name="capability.discover", input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}, mechanism_hint="shutil_which"),
    Capability(name="shell.execute", input_schema={"type": "object", "properties": {"command": {"type": "string"}, "timeout_s": {"type": "number"}}, "required": ["command"]}, mechanism_hint="subprocess_hidden"),
    # Chantier Reliability : batterie native SAFE — évite pc.shell.execute (toujours SENSITIVE)
    # pour une simple lecture de niveau. psutil est déjà une dépendance (process.list).
    Capability(name="power.battery_level", input_schema={"type": "object", "properties": {}}, mechanism_hint="psutil_sensors_battery"),
    # Chantier 1 — Software Environment Awareness
    Capability(name="software.discover", input_schema={"type": "object", "properties": {"name": {"type": "string"}, "refresh": {"type": "boolean"}}, "required": ["name"]}, mechanism_hint="software_discovery"),
    Capability(name="software.list_installed", input_schema={"type": "object", "properties": {"query": {"type": "string"}, "level": {"type": "integer"}, "refresh": {"type": "boolean"}}}, mechanism_hint="software_discovery"),
    Capability(name="software.search_packages", input_schema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}, mechanism_hint="winget_search"),
    Capability(name="software.package_info", input_schema={"type": "object", "properties": {"package_id": {"type": "string"}}, "required": ["package_id"]}, mechanism_hint="winget_show"),
    Capability(name="software.probe_path", input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}, mechanism_hint="probe_path"),
    Capability(name="software.resolve_launch_options", input_schema={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}, mechanism_hint="launcher_resolver"),
]


class WindowsDeviceAgent(DeviceAgent):
    def __init__(self, screenshot_dir: Path) -> None:
        self._screenshot_dir = screenshot_dir

    def list_capabilities(self) -> list[Capability]:
        return list(_CAPABILITIES)

    def health(self) -> Health:
        probe = uia.probe()
        status_ok = probe.get("status") == "ok"
        from raya.contracts import DeviceStatus

        return Health(
            device_id=DEVICE_ID,
            status=DeviceStatus.ONLINE if status_ok else DeviceStatus.DEGRADED,
            detail=None if status_ok else probe.get("error"),
        )

    def execute(self, command: Command, should_stop: ShouldStop = _never_stop) -> Result:
        if should_stop():
            return Result(command_id=command.id, status=CommandStatus.CANCELLED,
                           error=ErrorInfo(code="STOP_ACTIVE", message="Interrompu par STOP avant exécution", retryable=False))

        handler = _DISPATCH.get(command.capability_name)
        if handler is None:
            return Result(command_id=command.id, status=CommandStatus.FAILURE,
                           error=ErrorInfo(code="UNKNOWN_CAPABILITY", message=f"Capacité inconnue: {command.capability_name!r}", retryable=False))

        try:
            return handler(self, command, should_stop)
        except Exception as exc:
            return Result(command_id=command.id, status=CommandStatus.FAILURE,
                           error=ErrorInfo(code="DEVICE_EXCEPTION", message=str(exc), retryable=True),
                           mechanism_used=command.capability_name)


def _ok(command: Command, output: dict, evidence: dict, mechanism: str) -> Result:
    return Result(command_id=command.id, status=CommandStatus.SUCCESS, output=output, evidence=evidence, mechanism_used=mechanism)


def _fail(command: Command, code: str, message: str, mechanism: str, retryable: bool = True) -> Result:
    return Result(command_id=command.id, status=CommandStatus.FAILURE,
                   error=ErrorInfo(code=code, message=message, retryable=retryable), mechanism_used=mechanism)


def _app_launch(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    target = command.arguments["target"]
    already, _ = window_mgmt.is_open(target)
    if already:
        r = window_mgmt.focus_window(target)
        return _ok(command, {"target": target, "already_open": True}, {"window": r.get("window")}, "win32_setforeground")

    r = applications.launch(target, wait_timeout_s=0.0)  # lance sans attendre en interne (on attend nous-mêmes, STOP-aware)
    if r.get("status") == "error":
        return _fail(command, "LAUNCH_FAILED", r.get("error", "échec du lancement"), "startfile", retryable=False)

    wait_timeout_s = float(command.arguments.get("wait_timeout_s", 20.0))
    deadline = time.time() + wait_timeout_s
    while time.time() < deadline:
        if should_stop():
            return Result(command_id=command.id, status=CommandStatus.CANCELLED,
                           error=ErrorInfo(code="STOP_ACTIVE", message="Interrompu par STOP pendant l'attente de la fenêtre", retryable=False))
        matches = window_mgmt.find_windows(target)
        if matches:
            return _ok(command, {"target": target, "already_open": False},
                        {"window": matches[0]["title"], "process": matches[0]["process"]}, "startfile+wait_for_window")
        time.sleep(0.2)
    return Result(command_id=command.id, status=CommandStatus.TIMEOUT,
                  error=ErrorInfo(code="WINDOW_NOT_APPEARED", message="lancé mais aucune fenêtre détectée à temps", retryable=True),
                  mechanism_used="startfile+wait_for_window")


def _app_close(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    target = command.arguments["target"]
    r = window_mgmt.close_window(target)
    if r["status"] != "ok":
        return _fail(command, "WINDOW_NOT_FOUND", f"fenêtre introuvable pour {target!r}", "win32_wm_close", retryable=False)
    return _ok(command, {"target": target}, {"window": r["window"]}, "win32_wm_close")


def _app_focus(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    target = command.arguments["target"]
    r = window_mgmt.focus_window(target)
    if r["status"] != "ok":
        return _fail(command, "WINDOW_NOT_FOUND", f"fenêtre introuvable pour {target!r}", "win32_setforeground", retryable=False)
    return _ok(command, {"target": target}, {"window": r.get("window")}, r.get("method", "win32_setforeground"))


def _app_list(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    r = applications.list_apps()
    return _ok(command, r, {"count": r["count"]}, "win32_enumwindows")


def _window_list(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    r = window_mgmt.list_windows()
    return _ok(command, r, {"count": r["count"]}, "win32_enumwindows")


def _keyboard_type(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    args = command.arguments
    mode = args.get("mode", "append")
    # Capture the foreground window BEFORE typing — this is the interaction target
    active_raw = window_mgmt.get_active_window()
    active_info = active_raw.get("active") if active_raw.get("status") == "ok" else None
    if mode == "replace":
        # Ctrl+A sélectionne tout le contenu existant — le Ctrl+V suivant le remplace.
        # Documenté comme comportement voulu (Write semantics directive §19).
        keyboard.press("ctrl+a")
    ok = keyboard.type_text(args["text"])
    if not ok:
        return _fail(command, "TYPE_FAILED", "échec de la saisie clavier", "clipboard_paste")
    evidence: dict = {"length": len(args["text"]), "mode": mode}
    if active_info:
        evidence["window"] = active_info["title"]
    return _ok(command, {"typed": True, "mode": mode}, evidence, "clipboard_paste")


def _keyboard_press(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    ok = keyboard.press(command.arguments["combo"])
    if not ok:
        return _fail(command, "PRESS_FAILED", f"combo invalide ou échec: {command.arguments['combo']!r}", "pyautogui_hotkey", retryable=False)
    return _ok(command, {"pressed": command.arguments["combo"]}, {}, "pyautogui_hotkey")


def _mouse_click(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    args = command.arguments
    ok = mouse.click_at(args["x"], args["y"], button=args.get("button", "left"), clicks=int(args.get("clicks", 1)))
    if not ok:
        return _fail(command, "CLICK_FAILED", "échec SetCursorPos/mouse_event", "win32_mouse_event")
    return _ok(command, {"x": args["x"], "y": args["y"]}, {}, "win32_mouse_event")


def _mouse_move(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    args = command.arguments
    ok = mouse.move_to(args["x"], args["y"])
    if not ok:
        return _fail(command, "MOVE_FAILED", "échec SetCursorPos", "win32_setcursorpos")
    return _ok(command, {"x": args["x"], "y": args["y"]}, {}, "win32_setcursorpos")


def _screen_capture(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    filename = command.arguments.get("filename", "screenshot.png")
    r = screen.capture(agent._screenshot_dir, filename)
    return _ok(command, {"path": r["path"]}, {"width": r["width"], "height": r["height"]}, "pyautogui_screenshot")


def _ui_inspect(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    args = command.arguments
    r = uia.read_elements(args["window"], control_types=args.get("control_types"), max_count=int(args.get("max_count", 60)))
    if r.get("status") == "not_found":
        return _fail(command, "WINDOW_NOT_FOUND", f"fenêtre introuvable pour {args['window']!r}", "uia_walk", retryable=False)
    if r.get("status") == "error":
        return _fail(command, "UIA_ERROR", r.get("error", "erreur UIA"), "uia_walk")
    return _ok(command, {"elements": r["elements"], "window": r["window"]}, {"count": r["count"]}, "uia_walk")


def _ui_click(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    args = command.arguments
    r = strategy.click_element(args["window"], args["selector"])
    if r["status"] == "not_found":
        return _fail(command, "WINDOW_NOT_FOUND", f"fenêtre introuvable pour {args['window']!r}", r.get("method", "uia"), retryable=False)
    if r["status"] != "ok":
        return _fail(command, "CLICK_FAILED", r.get("reason", "échec du clic"), r.get("method", "unknown"))
    return _ok(command, {"clicked": True}, {"verified": r.get("verified"), "method": r["method"]}, r["method"])


def _ui_type(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    args = command.arguments
    mode = args.get("mode", "replace")
    r = strategy.type_into_element(args["window"], args["selector"], args["text"], mode=mode)
    if r["status"] == "not_found":
        return _fail(command, "WINDOW_NOT_FOUND", f"fenêtre introuvable pour {args['window']!r}", r.get("method", "uia"), retryable=False)
    if r["status"] != "ok":
        return _fail(command, "TYPE_FAILED", r.get("reason", "échec de la saisie"), r.get("method", "unknown"))
    return _ok(command, {"typed": True, "mode": mode}, {"method": r["method"], "window": args["window"]}, r["method"])


def _process_list(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    names = sorted({p.info["name"] for p in psutil.process_iter(["name"]) if p.info.get("name")})
    return _ok(command, {"processes": names}, {"count": len(names)}, "psutil")


def _filesystem_find_folder(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    r = filesystem.find_folder(command.arguments["name"])
    if r.get("status") != "ok":
        return _fail(command, "INVALID_QUERY", r.get("error", "requête invalide"), "os_scandir_bounded", retryable=False)
    return _ok(command, r, {"count": r["count"]}, "os_scandir_bounded")


def _filesystem_open_path(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    r = filesystem.open_path(command.arguments["path"])
    if r.get("status") != "ok":
        return _fail(command, "PATH_NOT_FOUND", r.get("error", "chemin introuvable"), "os_startfile", retryable=False)
    return _ok(command, r, {"path": r["path"]}, "os_startfile")


def _capability_discover(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    r = capability_discovery.find(command.arguments["name"])
    return _ok(command, r, {"available": r["available"]}, "shutil_which")


def _battery_level(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    battery = psutil.sensors_battery()
    if battery is None:
        return _fail(command, "NO_BATTERY",
                     "No battery sensor found — this machine has no battery (desktop PC or VM).",
                     "psutil_sensors_battery", retryable=False)
    output: dict = {
        "percent": round(battery.percent, 1),
        "charging": battery.power_plugged,
    }
    if battery.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN):
        output["remaining_seconds"] = int(battery.secsleft)
    return _ok(command, output,
               {"percent": round(battery.percent, 1), "charging": battery.power_plugged},
               "psutil_sensors_battery")


def _shell_execute(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    args = command.arguments
    r = shell.run(args["command"], timeout_s=float(args.get("timeout_s", 15.0)))
    if r["status"] == "timeout":
        return Result(command_id=command.id, status=CommandStatus.TIMEOUT,
                       error=ErrorInfo(code="SHELL_TIMEOUT", message=f"dépassement du délai ({r['timeout_s']}s)", retryable=True),
                       mechanism_used="subprocess_hidden")
    if r["status"] == "error":
        return _fail(command, "SHELL_ERROR", r.get("error", "erreur d'exécution"), "subprocess_hidden", retryable=False)
    return _ok(command, {"exit_code": r["exit_code"], "stdout": r["stdout"], "stderr": r["stderr"], "truncated": r["truncated"]},
               {"exit_code": r["exit_code"]}, "subprocess_hidden")


def _software_discover(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    name = command.arguments.get("name", "")
    refresh = bool(command.arguments.get("refresh", False))
    if not name:
        return _fail(command, "INVALID_ARGS", "name required", "software_discovery", retryable=False)
    snap = software_discovery.get_cached_or_scan(level=1, refresh=refresh)
    matches = software_discovery.find_installed(name, snap.apps)
    if not matches:
        return _ok(command, {"found": False, "name": name, "matches": []}, {"found": False}, "software_discovery")
    serialized = []
    for app in matches[:5]:
        serialized.append({
            "name": app.name,
            "display_name": app.display_name,
            "version": app.version,
            "source": app.source,
            "confidence": app.confidence,
            "launch_methods": [
                {
                    "type": m.type, "value": m.value, "launcher": m.launcher,
                    "verified": m.verified, "launcher_available": m.launcher_available,
                    "launcher_running": m.launcher_running, "requires_launcher": m.requires_launcher,
                }
                for m in app.launch_methods
            ],
        })
    return _ok(command, {"found": True, "name": name, "matches": serialized, "count": len(matches)},
               {"found": True, "count": len(matches)}, "software_discovery")


def _software_list_installed(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    query = command.arguments.get("query", "")
    level = int(command.arguments.get("level", 1))
    refresh = bool(command.arguments.get("refresh", False))
    snap = software_discovery.get_cached_or_scan(level=min(max(level, 1), 2), refresh=refresh)
    apps = snap.apps
    if query:
        apps = software_discovery.find_installed(query, apps)
    serialized = []
    for app in apps[:100]:
        serialized.append({
            "name": app.name,
            "display_name": app.display_name,
            "version": app.version,
            "source": app.source,
            "confidence": app.confidence,
            "launch_methods_count": len(app.launch_methods),
            "has_verified_method": any(m.verified for m in app.launch_methods),
        })
    return _ok(command,
               {"count": len(serialized), "total_discovered": len(snap.apps),
                "apps": serialized, "scanned_at": snap.scanned_at,
                "sources_used": snap.sources_used, "scan_level": snap.scan_level},
               {"count": len(serialized), "scan_level": snap.scan_level}, "software_discovery")


def _software_search_packages(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    query = command.arguments.get("query", "")
    if not query:
        return _fail(command, "INVALID_ARGS", "query required", "winget_search", retryable=False)
    result = package_manager.search(query)
    if result["status"] == "unavailable":
        return _fail(command, "WINGET_UNAVAILABLE", result.get("error", "winget not available"), "winget_search", retryable=False)
    return _ok(command, result, {"count": result.get("count", 0)}, "winget_search")


def _software_package_info(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    package_id = command.arguments.get("package_id", "")
    if not package_id:
        return _fail(command, "INVALID_ARGS", "package_id required", "winget_show", retryable=False)
    result = package_manager.show(package_id)
    if result.get("status") == "unavailable":
        return _fail(command, "WINGET_UNAVAILABLE", result.get("error", "winget not available"), "winget_show", retryable=False)
    return _ok(command, result, {}, "winget_show")


def _software_probe_path(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    path = command.arguments.get("path", "")
    if not path:
        return _fail(command, "INVALID_ARGS", "path required", "probe_path", retryable=False)
    result = launcher_resolver.probe_path(path)
    if result.get("status") == "not_found":
        return _ok(command, result, {"exists": False}, "probe_path")
    if result.get("status") == "error":
        return _fail(command, "PROBE_ERROR", result.get("error", "probe failed"), "probe_path")
    return _ok(command, result, {"exists": True, "is_executable_type": result.get("is_executable_type", False)}, "probe_path")


def _software_resolve_launch_options(agent: WindowsDeviceAgent, command: Command, should_stop: ShouldStop) -> Result:
    name = command.arguments.get("name", "")
    if not name:
        return _fail(command, "INVALID_ARGS", "name required", "launcher_resolver", retryable=False)
    methods = launcher_resolver.resolve_launch_options(name)
    if not methods:
        return _ok(command, {"found": False, "name": name, "options": []}, {"found": False}, "launcher_resolver")
    serialized = [
        {
            "type": m.type, "value": m.value, "launcher": m.launcher,
            "verified": m.verified, "launcher_available": m.launcher_available,
            "launcher_running": m.launcher_running, "requires_launcher": m.requires_launcher,
        }
        for m in methods
    ]
    return _ok(command, {"found": True, "name": name, "options": serialized, "count": len(serialized)},
               {"found": True, "count": len(serialized)}, "launcher_resolver")


_DISPATCH = {
    "application.launch": _app_launch,
    "application.close": _app_close,
    "application.focus": _app_focus,
    "application.list": _app_list,
    "window.list": _window_list,
    "window.focus": _app_focus,
    "window.close": _app_close,
    "keyboard.type": _keyboard_type,
    "keyboard.press": _keyboard_press,
    "mouse.click": _mouse_click,
    "mouse.move": _mouse_move,
    "screen.capture": _screen_capture,
    "ui.inspect": _ui_inspect,
    "ui.click": _ui_click,
    "ui.type": _ui_type,
    "process.list": _process_list,
    "filesystem.find_folder": _filesystem_find_folder,
    "filesystem.open_path": _filesystem_open_path,
    "capability.discover": _capability_discover,
    "shell.execute": _shell_execute,
    "power.battery_level": _battery_level,
    "software.discover": _software_discover,
    "software.list_installed": _software_list_installed,
    "software.search_packages": _software_search_packages,
    "software.package_info": _software_package_info,
    "software.probe_path": _software_probe_path,
    "software.resolve_launch_options": _software_resolve_launch_options,
}
