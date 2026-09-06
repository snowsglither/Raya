"""BrowserDeviceAgent — implémente `DeviceAgent` (RAYA_V2_TECHNICAL_ARCHITECTURE.md
§1.13). Dispatche une `Command` déjà décidée vers `BrowserController` — AUCUNE
décision "quoi chercher/cliquer", uniquement "comment". N'importe jamais
`raya.models` (invariant #5).

Capacités exposées : navigate/read_page/screenshot/list_tabs (SAFE — lecture/
navigation réversible), click/type/dismiss_overlay (SENSITIVE — interaction
mutante, voir `raya/safety/risk.py`)."""

from __future__ import annotations

from pathlib import Path

from raya.contracts import Capability, Command, CommandStatus, DeviceStatus, ErrorInfo, Health, Result

from ..base import DeviceAgent, ShouldStop, _never_stop
from .controller import BrowserController
from .session import BrowserSession
from .worker import BrowserWorker

DEVICE_ID = "browser_agent"

_CAPABILITIES = [
    Capability(name="browser.navigate", input_schema={"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}, mechanism_hint="cdp_goto"),
    Capability(name="browser.read_page", input_schema={"type": "object", "properties": {}}, mechanism_hint="cdp_dom_eval"),
    Capability(name="browser.screenshot", input_schema={"type": "object", "properties": {"filename": {"type": "string"}}}, mechanism_hint="cdp_screenshot"),
    Capability(name="browser.list_tabs", input_schema={"type": "object", "properties": {}}, mechanism_hint="cdp_context_pages"),
    Capability(name="browser.click", input_schema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}, mechanism_hint="playwright_locator_click"),
    Capability(name="browser.type", input_schema={"type": "object", "properties": {"target": {"type": "string"}, "text": {"type": "string"}, "submit": {"type": "boolean"}}, "required": ["target", "text"]}, mechanism_hint="playwright_locator_fill"),
    Capability(name="browser.dismiss_overlay", input_schema={"type": "object", "properties": {"max_rounds": {"type": "integer"}}}, mechanism_hint="scoped_overlay_click"),
]


class BrowserDeviceAgent(DeviceAgent):
    def __init__(self, screenshot_dir: Path) -> None:
        self._screenshot_dir = screenshot_dir
        self._session = BrowserSession()
        self._worker = BrowserWorker()
        self._controller = BrowserController(self._session, self._worker)

    def list_capabilities(self) -> list[Capability]:
        return list(_CAPABILITIES)

    def health(self) -> Health:
        try:
            is_open = self._worker.run_sync(self._session.is_open, timeout=5.0)
            return Health(device_id=DEVICE_ID, status=DeviceStatus.ONLINE if is_open else DeviceStatus.DEGRADED,
                          detail=None if is_open else "session non attachée (lazy — attend la première commande)")
        except Exception as exc:
            return Health(device_id=DEVICE_ID, status=DeviceStatus.OFFLINE, detail=str(exc))

    def execute(self, command: Command, should_stop: ShouldStop = _never_stop) -> Result:
        if should_stop():
            return Result(command_id=command.id, status=CommandStatus.CANCELLED,
                           error=ErrorInfo(code="STOP_ACTIVE", message="Interrompu par STOP avant exécution", retryable=False))
        handler = _DISPATCH.get(command.capability_name)
        if handler is None:
            return Result(command_id=command.id, status=CommandStatus.FAILURE,
                           error=ErrorInfo(code="UNKNOWN_CAPABILITY", message=f"Capacité inconnue: {command.capability_name!r}", retryable=False))
        try:
            return handler(self, command)
        except Exception as exc:
            return Result(command_id=command.id, status=CommandStatus.FAILURE,
                           error=ErrorInfo(code="DEVICE_EXCEPTION", message=str(exc), retryable=True),
                           mechanism_used=command.capability_name)

    def shutdown(self) -> None:
        try:
            self._worker.run_sync(self._session.close, timeout=10.0)
        except Exception:
            pass


def _ok(command: Command, output: dict, evidence: dict, mechanism: str) -> Result:
    return Result(command_id=command.id, status=CommandStatus.SUCCESS, output=output, evidence=evidence, mechanism_used=mechanism)


def _fail(command: Command, code: str, message: str, mechanism: str, retryable: bool = True) -> Result:
    return Result(command_id=command.id, status=CommandStatus.FAILURE,
                   error=ErrorInfo(code=code, message=message, retryable=retryable), mechanism_used=mechanism)


def _navigate(agent: BrowserDeviceAgent, command: Command) -> Result:
    url = command.arguments["url"]
    r = agent._controller.navigate(url)
    return _ok(command, {"url": r["url"], "title": r["title"]}, {"url": r["url"]}, "cdp_goto")


def _read_page(agent: BrowserDeviceAgent, command: Command) -> Result:
    r = agent._controller.read_page()
    evidence = {"url": r["url"], "cookie_banner": r["cookie_banner"]}
    return _ok(command, r, evidence, "cdp_dom_eval")


def _screenshot(agent: BrowserDeviceAgent, command: Command) -> Result:
    filename = command.arguments.get("filename", "browser_screenshot.png")
    agent._screenshot_dir.mkdir(parents=True, exist_ok=True)
    path = str(agent._screenshot_dir / filename)
    r = agent._controller.screenshot(path)
    return _ok(command, {"path": r["path"]}, {}, "cdp_screenshot")


def _list_tabs(agent: BrowserDeviceAgent, command: Command) -> Result:
    r = agent._controller.list_tabs()
    return _ok(command, r, {"count": r["count"]}, "cdp_context_pages")


def _click(agent: BrowserDeviceAgent, command: Command) -> Result:
    target = command.arguments["target"]
    r = agent._controller.click(target)
    if r["status"] == "not_found":
        return _fail(command, "ELEMENT_NOT_FOUND", f"aucun élément cliquable pour {target!r}", "playwright_locator_click", retryable=False)
    return _ok(command, {"clicked": target}, {}, "playwright_locator_click")


def _type(agent: BrowserDeviceAgent, command: Command) -> Result:
    target = command.arguments["target"]
    text = command.arguments["text"]
    submit = bool(command.arguments.get("submit", False))
    r = agent._controller.type_text(target, text, submit=submit)
    if r["status"] == "not_found":
        return _fail(command, "FIELD_NOT_FOUND", f"aucun champ pour {target!r}", "playwright_locator_fill", retryable=False)
    return _ok(command, {"typed": target, "submitted": submit}, {}, "playwright_locator_fill")


def _dismiss_overlay(agent: BrowserDeviceAgent, command: Command) -> Result:
    max_rounds = int(command.arguments.get("max_rounds", 3))
    r = agent._controller.dismiss_overlays(max_rounds=max_rounds)
    return _ok(command, r, {"dismissed": r["dismissed"]}, "scoped_overlay_click")


_DISPATCH = {
    "browser.navigate": _navigate,
    "browser.read_page": _read_page,
    "browser.screenshot": _screenshot,
    "browser.list_tabs": _list_tabs,
    "browser.click": _click,
    "browser.type": _type,
    "browser.dismiss_overlay": _dismiss_overlay,
}
