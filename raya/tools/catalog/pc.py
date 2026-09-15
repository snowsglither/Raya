"""Déclarations de Tools délégant au Windows Device Agent
(RAYA_V2_REPOSITORY_STRUCTURE.md §11 : "capability_tags: ['pc'] — délègue à
devices/windows/"). Chaque handler construit une `Command` déjà décidée
(argument reçus du ToolCall, jamais réinterprétés) et l'exécute via le VRAI
Device Agent — c'est ici, et SEULEMENT ici, que `tools/` a accès à `safety`
(`should_stop`) pour le transmettre en paramètre à `device.execute()`
(devices/ ne peut pas importer raya.safety, voir raya/devices/base.py)."""

from __future__ import annotations

from typing import Callable

from raya.contracts import (
    Command,
    CommandStatus,
    Confidence,
    ObservationSpec,
    PermissionLevel,
    Tool,
    ToolCall,
    ToolResult,
    ToolResultStatus,
)
from raya.devices import DeviceAgent, ShouldStop
from raya.devices.windows import DEVICE_ID as WINDOWS_DEVICE_ID

# Faits d'observation "toujours singuliers, jamais une liste" (consigne
# Phase 7 §12 : "pas de dump massif") promus en World State par
# raya/harness/loop.py — générique, jamais un `if tool_name == ...` dans le
# Harness (consigne §13). `pc.window.list`/`pc.application.list` (résultats
# en LISTE) n'en déclarent délibérément pas : une liste de fenêtres n'est pas
# un fait canonique unique, elle reste exposée via last_tool_trace() (Cockpit
# Phase 6), jamais dupliquée dans World State.
_ACTIVE_WINDOW_OBSERVATION = (
    ObservationSpec(domain="pc", key="active_window", evidence_field="window",
                     expected_argument="target", freshness_ttl_s=60),
)

# Chantier 20C : dernière cible d'interaction PC réelle (quelle app/fenêtre RAYA
# a vraiment utilisée via frappe clavier). Distinct de active_window (focus OS
# passif) — persiste 300s pour survivre aux changements passifs de focus.
_LAST_INTERACTION_OBSERVATION = (
    ObservationSpec(domain="pc", key="last_interaction_target", evidence_field="window",
                     freshness_ttl_s=300),
)

# Chantier 12 §C : promotion UNIQUEMENT après une OUVERTURE réussie (jamais
# après une simple recherche `filesystem.find_folder`, ambiguë par nature —
# 0/1/N correspondances). `key_from_argument="name"` : le fait est indexé
# par le NOM que le modèle a réellement résolu (ex: "projets"), pas par le
# chemin — un domaine à faits MULTIPLES, contrairement à `pc.active_window`.
_FILESYSTEM_OPEN_OBSERVATION = (
    ObservationSpec(domain="filesystem", key="unused_key_from_argument_wins", evidence_field="path",
                     key_from_argument="name", confidence=Confidence.INFERRED),
)

_STATUS_MAP = {
    CommandStatus.SUCCESS: ToolResultStatus.SUCCESS,
    CommandStatus.FAILURE: ToolResultStatus.FAILURE,
    CommandStatus.TIMEOUT: ToolResultStatus.TIMEOUT,
    CommandStatus.CANCELLED: ToolResultStatus.CANCELLED,
}


def _run(agent: DeviceAgent, capability_name: str, should_stop: ShouldStop, call: ToolCall) -> ToolResult:
    command = Command(device_id=WINDOWS_DEVICE_ID, capability_name=capability_name,
                       arguments=call.arguments, correlation_id=call.correlation_id, timeout_ms=call.timeout_ms)
    result = agent.execute(command, should_stop=should_stop)
    return ToolResult(
        tool_call_id=call.id, status=_STATUS_MAP[result.status], output=result.output,
        evidence=result.evidence, error=result.error,
    )


def _tool(name: str, capability_name: str, description: str, input_schema: dict,
          permission_level: PermissionLevel, tag: str, idempotent: bool = False,
          observation: tuple[ObservationSpec, ...] = ()) -> Tool:
    return Tool(name=name, description=description, capability_tags=[tag], input_schema=input_schema,
                output_schema={"type": "object"}, permission_level=permission_level,
                idempotent=idempotent, requires_device=WINDOWS_DEVICE_ID, observation=observation)


def register_pc_tools(registry, windows_agent: DeviceAgent, should_stop: ShouldStop) -> None:
    _defs = [
        ("pc.window.list", "window.list", "Liste les fenêtres visibles.", {"type": "object", "properties": {}}, PermissionLevel.SAFE, "pc.read", True, ()),
        ("pc.window.focus", "window.focus", "Met une fenêtre au premier plan.", {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}, PermissionLevel.SAFE, "pc.launch", True, _ACTIVE_WINDOW_OBSERVATION),
        ("pc.window.close", "window.close", "Ferme une fenêtre (WM_CLOSE, jamais taskkill).", {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}, PermissionLevel.SENSITIVE, "pc.interact", False, ()),
        ("pc.application.launch", "application.launch", "Lance une application et attend sa fenêtre. `target` doit être le nom d'exécutable Windows canonique (ex: \"calc\" pour la Calculatrice, \"notepad\" pour le Bloc-notes, \"chrome\" pour Chrome, \"code\" pour VS Code, \"explorer\" pour l'Explorateur de fichiers, \"mspaint\" pour Paint) et non un nom affiché localisé (ex: ne jamais envoyer \"calculatrice\"/\"calculator\" — utilise le nom d'exécutable réel de l'application, déductible de tes connaissances générales de Windows).", {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}, PermissionLevel.SAFE, "pc.launch", False, _ACTIVE_WINDOW_OBSERVATION),
        ("pc.application.close", "application.close", "Ferme une application.", {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}, PermissionLevel.SENSITIVE, "pc.interact", False, ()),
        ("pc.application.focus", "application.focus", "Met une application au premier plan.", {"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]}, PermissionLevel.SAFE, "pc.launch", True, _ACTIVE_WINDOW_OBSERVATION),
        ("pc.application.list", "application.list", "Liste les applications actuellement ouvertes.", {"type": "object", "properties": {}}, PermissionLevel.SAFE, "pc.read", True, ()),
        ("pc.keyboard.type", "keyboard.type",
         "Saisit du texte au clavier dans l'application active. "
         "mode='replace' : sélectionne tout le contenu existant (Ctrl+A) avant de coller — "
         "remplace TOUT le contenu de la zone de saisie. À utiliser pour 'écris X', 'remplace par X', "
         "'mets X' quand l'intention est d'écrire un nouveau contenu. "
         "mode='append' (défaut) : colle à la position curseur actuelle sans effacer — "
         "conserve le contenu existant. À utiliser pour 'ajoute X', 'écris X à la suite'. "
         "Règle générale : les commandes d'écriture naturelles ('écris X', 'tape X') correspondent "
         "à mode='replace' ; réserve mode='append' quand l'utilisateur dit explicitement d'ajouter.",
         {"type": "object", "properties": {
             "text": {"type": "string"},
             "mode": {"type": "string", "enum": ["replace", "append"],
                      "description": "replace=Ctrl+A avant frappe (remplace contenu), append=frappe à curseur (défaut)"},
         }, "required": ["text"]},
         PermissionLevel.SENSITIVE, "pc.interact", False, _LAST_INTERACTION_OBSERVATION),
        ("pc.keyboard.press", "keyboard.press", "Appuie sur une combinaison de touches.", {"type": "object", "properties": {"combo": {"type": "string"}}, "required": ["combo"]}, PermissionLevel.SENSITIVE, "pc.interact", False, ()),
        ("pc.mouse.click", "mouse.click", "Clique aux coordonnées données.", {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]}, PermissionLevel.SENSITIVE, "pc.interact", False, ()),
        ("pc.mouse.move", "mouse.move", "Déplace la souris.", {"type": "object", "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}}, "required": ["x", "y"]}, PermissionLevel.SENSITIVE, "pc.interact", True, ()),
        ("pc.screen.capture", "screen.capture", "Prend une capture d'écran.", {"type": "object", "properties": {"filename": {"type": "string"}}}, PermissionLevel.SAFE, "pc.read", True, ()),
        ("pc.ui.inspect", "ui.inspect", "Liste les éléments UI d'une fenêtre.", {"type": "object", "properties": {"window": {"type": "string"}}, "required": ["window"]}, PermissionLevel.SAFE, "pc.read", True, ()),
        ("pc.ui.click", "ui.click", "Clique un élément UI par sélecteur.", {"type": "object", "properties": {"window": {"type": "string"}, "selector": {"type": "object"}}, "required": ["window", "selector"]}, PermissionLevel.SENSITIVE, "pc.interact", False, ()),
        ("pc.ui.type", "ui.type", "Saisit du texte dans un élément UI. Par défaut (mode='replace') remplace TOUT le contenu — Ctrl+A avant frappe en repli clavier. 'mode=append' insère en fin (End puis frappe). 'mode=clear' vide le champ.", {"type": "object", "properties": {"window": {"type": "string"}, "selector": {"type": "object"}, "text": {"type": "string"}, "mode": {"type": "string", "enum": ["replace", "append", "clear"]}}, "required": ["window", "selector", "text"]}, PermissionLevel.SENSITIVE, "pc.interact", False, _LAST_INTERACTION_OBSERVATION),
        ("pc.process.list", "process.list", "Liste les process en cours.", {"type": "object", "properties": {}}, PermissionLevel.SAFE, "pc.read", True, ()),
        # Chantier 12 §C : découverte ciblée d'un dossier réel PAR NOM sous le
        # dossier personnel — jamais une correspondance devinée, jamais un
        # scan de tout le disque (voir devices/windows/mechanisms/filesystem.py).
        ("pc.filesystem.find_folder", "filesystem.find_folder",
         "Cherche un dossier réel par nom exact (insensible à la casse) sous le dossier personnel de "
         "l'utilisateur. Retourne 0, 1 ou plusieurs correspondances (`matches`). Si plusieurs, demande "
         "une clarification à l'utilisateur avant d'en ouvrir une — n'invente JAMAIS un chemin.",
         {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
         PermissionLevel.SAFE, "pc.read", True, ()),
        ("pc.filesystem.open_path", "filesystem.open_path",
         "Ouvre un dossier/fichier réel (déjà résolu par pc.filesystem.find_folder ou connu de l'état "
         "du monde) dans l'application par défaut de l'OS. `name` doit être le nom sous lequel "
         "l'utilisateur a demandé ce dossier (ex: 'projets') — sert à mémoriser ce chemin pour la "
         "prochaine fois, jamais une valeur inventée.",
         {"type": "object", "properties": {"path": {"type": "string"}, "name": {"type": "string"}}, "required": ["path", "name"]},
         PermissionLevel.SAFE, "pc.launch", False, _FILESYSTEM_OPEN_OBSERVATION),
        # Chantier 16 (Capability Discovery / CLI vs GUI / USE ≠ SHOW) —
        # découverte bornée (PATH uniquement) SAFE comme les autres "pc.read" ;
        # exécution CLI SENSITIVE INCONDITIONNELLEMENT (jamais contextuel comme
        # pc.interact — une commande shell arbitraire est qualitativement plus
        # risquée qu'un clic UI, raya/safety/risk.py).
        ("pc.capability.discover", "capability.discover",
         "Vérifie si un exécutable/outil en ligne de commande est RÉELLEMENT disponible sur ce PC "
         "(recherché dans PATH, jamais un scan de disque) avant de l'utiliser — retourne son chemin "
         "réel s'il existe, et une capability générique associée si connue (ex: nmap -> network.scan). "
         "Utilise ceci AVANT pc.shell.execute quand tu n'es pas certain qu'un outil existe, plutôt que "
         "de deviner ou d'inventer un nom d'exécutable/chemin.",
         {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
         PermissionLevel.SAFE, "pc.read", True, ()),
        # Chantier Reliability : batterie SAFE native — jamais pc.shell.execute pour ça.
        ("pc.power.battery_level", "power.battery_level",
         "Reads the current battery charge level (0–100 %) and charging state of this machine. "
         "Returns no_battery if the machine has no battery (desktop PC or VM). "
         "Always SAFE — direct system call, no shell, no confirmation required.",
         {"type": "object", "properties": {}},
         PermissionLevel.SAFE, "pc.read", True,
         (ObservationSpec(domain="pc", key="battery_level", evidence_field="percent", freshness_ttl_s=120),)),
        ("pc.shell.execute", "shell.execute",
         "Exécute une commande CLI réelle, TOUJOURS en arrière-plan SANS jamais afficher de fenêtre "
         "de terminal (exécution cachée — principe USE ≠ SHOW). À privilégier sur l'automatisation "
         "d'une interface graphique quand un outil en ligne de commande suffit à l'objectif (ex: scan "
         "réseau via nmap/ping, informations réseau via ipconfig). N'exécute QUE une commande "
         "correspondant à un outil réellement disponible (vérifie avec pc.capability.discover si tu "
         "n'es pas sûr) — n'invente jamais un nom de commande/chemin. Si l'utilisateur veut VOIR "
         "l'application plutôt que juste utiliser son résultat, utilise pc.application.launch à la "
         "place (ex: 'ouvre Nmap' contre 'utilise Nmap pour scanner').",
         {"type": "object", "properties": {
             "command": {"type": "string"},
             "timeout_s": {"type": "number", "description": "borné à 30s max, défaut 15s"},
         }, "required": ["command"]},
         PermissionLevel.SENSITIVE, "pc.shell", False, ()),
        # Chantier 1 — Software Environment Awareness
        ("pc.software.discover", "software.discover",
         "Discover if a specific application is installed on this PC and how to launch it. "
         "Returns available launch methods with objective properties (type, verified, launcher_available, etc.). "
         "Use BEFORE assuming an application exists. Returns found=False (never invents) if not found. "
         "Use refresh=true to bypass the 1-hour cache.",
         {"type": "object", "properties": {
             "name": {"type": "string", "description": "Application name to search for"},
             "refresh": {"type": "boolean", "description": "Force fresh discovery (bypass cache)"},
         }, "required": ["name"]},
         PermissionLevel.SAFE, "pc.software", True,
         (ObservationSpec(domain="software", key="launcher_detected", evidence_field="found",
                          freshness_ttl_s=3600, confidence=Confidence.KNOWN_FACT),)),
        ("pc.software.list_installed", "software.list_installed",
         "List applications installed on this PC. "
         "level=1 (default): fast scan (App Paths + PATH, <200ms). "
         "level=2: complete scan (Start Menu + UWP + Registry + Launcher manifests, 500ms-2s). "
         "Use refresh=true to bypass cache. Returns up to 100 apps.",
         {"type": "object", "properties": {
             "query": {"type": "string", "description": "Optional filter by name"},
             "level": {"type": "integer", "enum": [1, 2], "description": "1=fast, 2=complete"},
             "refresh": {"type": "boolean", "description": "Force fresh scan"},
         }},
         PermissionLevel.SAFE, "pc.software", True, ()),
        ("pc.software.search_packages", "software.search_packages",
         "Search for packages available via winget (Windows Package Manager). "
         "Returns packages available for installation — does NOT install anything. "
         "Returns unavailable if winget is not installed on this system.",
         {"type": "object", "properties": {
             "query": {"type": "string"},
         }, "required": ["query"]},
         PermissionLevel.SAFE, "pc.software", True, ()),
        ("pc.software.package_info", "software.package_info",
         "Get detailed metadata for a specific package via winget (name, publisher, description, version). "
         "Requires the winget package ID (e.g. 'Microsoft.VisualStudioCode').",
         {"type": "object", "properties": {
             "package_id": {"type": "string"},
         }, "required": ["package_id"]},
         PermissionLevel.SAFE, "pc.software", True, ()),
        ("pc.software.probe_path", "software.probe_path",
         "Inspect a file at a given explicit path — SAFE, does NOT execute the file. "
         "Returns file properties: exists, size, extension, whether it is an executable type, "
         "whether it is on PATH. Use this when the user provides a specific path "
         "or when a path was found via pc.filesystem.find_folder. "
         "To probe CLI capabilities (e.g. --version), use pc.shell.execute instead.",
         {"type": "object", "properties": {
             "path": {"type": "string"},
         }, "required": ["path"]},
         PermissionLevel.SAFE, "pc.software", True, ()),
        ("pc.software.resolve_launch_options", "software.resolve_launch_options",
         "Resolve all available launch methods for an application, with real-time properties. "
         "Returns each option's type (exe/url_protocol/uwp/lnk), launcher (direct/steam/epic/etc.), "
         "whether the launcher is available and currently running, whether the method is verified. "
         "This tool returns options — it does NOT choose. Cognition chooses based on context, "
         "user preference (from memory), and objective constraints.",
         {"type": "object", "properties": {
             "name": {"type": "string"},
         }, "required": ["name"]},
         PermissionLevel.SAFE, "pc.software", True, ()),
    ]
    for name, capability_name, description, schema, level, tag, idem, observation in _defs:
        tool = _tool(name, capability_name, description, schema, level, tag, idem, observation)
        handler: Callable[[ToolCall], ToolResult] = _make_handler(windows_agent, capability_name, should_stop)
        registry.register(tool, handler)


def _make_handler(agent: DeviceAgent, capability_name: str, should_stop: ShouldStop):
    def handler(call: ToolCall) -> ToolResult:
        return _run(agent, capability_name, should_stop, call)

    return handler
