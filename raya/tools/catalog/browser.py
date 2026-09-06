"""Déclarations de Tools délégant au Browser Device Agent
(RAYA_V2_REPOSITORY_STRUCTURE.md §11 : "capability_tags: ['browser'] —
délègue à devices/browser/"). Même pattern que pc.py : le handler construit
une `Command` et l'exécute via le VRAI Device Agent, avec `should_stop`
transmis en paramètre (jamais un import `raya.safety` dans `devices/`)."""

from __future__ import annotations

from typing import Callable

from raya.contracts import (
    Command,
    CommandStatus,
    ObservationSpec,
    PermissionLevel,
    Tool,
    ToolCall,
    ToolResult,
    ToolResultStatus,
)
from raya.devices import DeviceAgent, ShouldStop
from raya.devices.browser import DEVICE_ID as BROWSER_DEVICE_ID

# Fait canonique unique (jamais une liste — consigne Phase 7 §12) : l'URL
# réellement atteinte après navigation, comparée à l'URL demandée pour la
# vérification post-action générique (§8-9). `browser.navigate`'s evidence
# porte déjà {"url": ...} (raya/devices/browser/agent.py::_navigate).
_CURRENT_URL_OBSERVATION = (
    ObservationSpec(domain="browser", key="current_url", evidence_field="url",
                     expected_argument="url", freshness_ttl_s=60),
)

_STATUS_MAP = {
    CommandStatus.SUCCESS: ToolResultStatus.SUCCESS,
    CommandStatus.FAILURE: ToolResultStatus.FAILURE,
    CommandStatus.TIMEOUT: ToolResultStatus.TIMEOUT,
    CommandStatus.CANCELLED: ToolResultStatus.CANCELLED,
}


def _run(agent: DeviceAgent, capability_name: str, should_stop: ShouldStop, call: ToolCall) -> ToolResult:
    command = Command(device_id=BROWSER_DEVICE_ID, capability_name=capability_name,
                       arguments=call.arguments, correlation_id=call.correlation_id, timeout_ms=call.timeout_ms)
    result = agent.execute(command, should_stop=should_stop)
    return ToolResult(
        tool_call_id=call.id, status=_STATUS_MAP[result.status], output=result.output,
        evidence=result.evidence, error=result.error,
    )


def _tool(name: str, description: str, input_schema: dict, permission_level: PermissionLevel,
          tag: str, idempotent: bool = False, observation: tuple[ObservationSpec, ...] = ()) -> Tool:
    return Tool(name=name, description=description, capability_tags=[tag], input_schema=input_schema,
                output_schema={"type": "object"}, permission_level=permission_level,
                idempotent=idempotent, requires_device=BROWSER_DEVICE_ID, observation=observation)


def register_browser_tools(registry, browser_agent: DeviceAgent, should_stop: ShouldStop) -> None:
    _defs = [
        ("browser.navigate", "browser.navigate", "Navigue vers une URL.", {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}, PermissionLevel.SAFE, "browser.read", True, _CURRENT_URL_OBSERVATION),
        ("browser.read_page", "browser.read_page", "Lit la structure de la page (boutons/liens/champs/bannière cookies).", {"type": "object", "properties": {}}, PermissionLevel.SAFE, "browser.read", True, ()),
        ("browser.screenshot", "browser.screenshot", "Capture d'écran de la page.", {"type": "object", "properties": {"filename": {"type": "string"}}}, PermissionLevel.SAFE, "browser.read", True, ()),
        ("browser.list_tabs", "browser.list_tabs", "Liste les onglets ouverts.", {"type": "object", "properties": {}}, PermissionLevel.SAFE, "browser.read", True, ()),
        ("browser.click", "browser.click", "Clique un élément par description.", {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}, PermissionLevel.SENSITIVE, "browser.interact", False, ()),
        ("browser.type", "browser.type", "Saisit du texte dans un champ. Remplir un champ (ex: une recherche) ne le SOUMET PAS automatiquement — passe `submit=true` pour appuyer sur Entrée juste après la saisie (utile pour la plupart des champs de recherche, qui n'ont pas toujours de bouton visible), ou clique ensuite explicitement sur un bouton de recherche/validation.", {"type": "object", "properties": {"target": {"type": "string"}, "text": {"type": "string"}, "submit": {"type": "boolean"}}, "required": ["target", "text"]}, PermissionLevel.SENSITIVE, "browser.interact", False, ()),
        ("browser.dismiss_overlay", "browser.dismiss_overlay", "Ferme bannière cookies/consentement/modale.", {"type": "object", "properties": {"max_rounds": {"type": "integer"}}}, PermissionLevel.SENSITIVE, "browser.interact", False, ()),
    ]
    for name, capability_name, description, schema, level, tag, idem, observation in _defs:
        tool = _tool(name, description, schema, level, tag, idem, observation)
        handler: Callable[[ToolCall], ToolResult] = _make_handler(browser_agent, capability_name, should_stop)
        registry.register(tool, handler)


def _make_handler(agent: DeviceAgent, capability_name: str, should_stop: ShouldStop):
    def handler(call: ToolCall) -> ToolResult:
        return _run(agent, capability_name, should_stop, call)

    return handler
