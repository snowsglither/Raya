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

# Deux faits promus après un clic descriptif : l'élément activé et l'URL
# réelle post-clic. 2-tuple — jamais fusionné avec _CURRENT_URL_OBSERVATION
# (navigate a sa propre sémantique expected_argument, click non).
_LAST_CLICKED_OBSERVATION = (
    ObservationSpec(domain="browser", key="last_clicked_target", evidence_field="clicked_target",
                     freshness_ttl_s=30),
    ObservationSpec(domain="browser", key="current_url", evidence_field="url",
                     freshness_ttl_s=60),
)

# Dernière position cliquée en coordonnées pixel — grounding vision → action.
_CLICK_AT_POSITION_OBSERVATION = (
    ObservationSpec(domain="browser", key="last_clicked_at", evidence_field="clicked_at",
                     freshness_ttl_s=30),
)

# État structurel de la page après read_page : titre lisible + fingerprint de
# navigation (hash url|title — signal d'identité de navigation UNIQUEMENT, pas
# preuve de stabilité DOM face aux mutations AJAX/React).
_PAGE_STATE_OBSERVATION = (
    ObservationSpec(domain="browser", key="current_url", evidence_field="url", freshness_ttl_s=60),
    ObservationSpec(domain="browser", key="page_title", evidence_field="title", freshness_ttl_s=60),
    ObservationSpec(domain="browser", key="page_fingerprint", evidence_field="page_fingerprint", freshness_ttl_s=30),
)

# Confirmation d'action : True uniquement sur signal réel (URL panier ou texte
# de confirmation visible) — jamais un succès inventé.
_CONFIRMATION_OBSERVATION = (
    ObservationSpec(domain="browser", key="last_confirmation", evidence_field="confirmation_detected",
                     freshness_ttl_s=30),
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
        ("browser.read_page", "browser.read_page", "Lit la structure de la page (boutons/liens/champs/bannière cookies). Promeut l'URL, le titre et le fingerprint de navigation dans WorldState.", {"type": "object", "properties": {}}, PermissionLevel.SAFE, "browser.read", True, _PAGE_STATE_OBSERVATION),
        ("browser.screenshot", "browser.screenshot", "Capture d'écran de la page.", {"type": "object", "properties": {"filename": {"type": "string"}}}, PermissionLevel.SAFE, "browser.read", True, ()),
        ("browser.list_tabs", "browser.list_tabs", "Liste les onglets ouverts.", {"type": "object", "properties": {}}, PermissionLevel.SAFE, "browser.read", True, ()),
        ("browser.click", "browser.click", "Clique un élément par description.", {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}, PermissionLevel.SENSITIVE, "browser.interact", False, _LAST_CLICKED_OBSERVATION),
        ("browser.click_at_position", "browser.click_at_position", "Clique à des coordonnées pixel précises (x, y) dans la page. Utiliser après vision.find_in_browser pour activer un élément grounded par le modèle Vision.", {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]}, PermissionLevel.SENSITIVE, "browser.interact", False, _CLICK_AT_POSITION_OBSERVATION),
        ("browser.type", "browser.type", "Saisit du texte dans un champ. Remplir un champ (ex: une recherche) ne le SOUMET PAS automatiquement — passe `submit=true` pour appuyer sur Entrée juste après la saisie (utile pour la plupart des champs de recherche, qui n'ont pas toujours de bouton visible), ou clique ensuite explicitement sur un bouton de recherche/validation.", {"type": "object", "properties": {"target": {"type": "string"}, "text": {"type": "string"}, "submit": {"type": "boolean"}}, "required": ["target", "text"]}, PermissionLevel.SENSITIVE, "browser.interact", False, ()),
        ("browser.dismiss_overlay", "browser.dismiss_overlay", "Ferme bannière cookies/consentement/modale.", {"type": "object", "properties": {"max_rounds": {"type": "integer"}}}, PermissionLevel.SENSITIVE, "browser.interact", False, ()),
        ("browser.check_confirmation", "browser.check_confirmation", "Vérifie si une action a été confirmée par la page (URL de panier ou texte de confirmation visible). Retourne confirmed=true uniquement sur un signal réel — jamais un succès inventé. À appeler après un clic d'action critique pour valider l'effet observé. Retourne inconclusive=true si aucun signal détecté.", {"type": "object", "properties": {}}, PermissionLevel.SAFE, "browser.read", True, _CONFIRMATION_OBSERVATION),
    ]
    for name, capability_name, description, schema, level, tag, idem, observation in _defs:
        tool = _tool(name, description, schema, level, tag, idem, observation)
        handler: Callable[[ToolCall], ToolResult] = _make_handler(browser_agent, capability_name, should_stop)
        registry.register(tool, handler)


def _make_handler(agent: DeviceAgent, capability_name: str, should_stop: ShouldStop):
    def handler(call: ToolCall) -> ToolResult:
        return _run(agent, capability_name, should_stop, call)

    return handler
