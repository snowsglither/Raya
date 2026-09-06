"""tools/catalog/pc.py — délégation réelle au WindowsDeviceAgent + gating
Safety réel (consigne Phase 4 §21 : SAFE pour lecture/lancement réversible,
SENSITIVE pour l'interaction mutante — un Tool ne s'auto-autorise jamais,
même délégué à un Device)."""

from __future__ import annotations

from raya.contracts import PermissionLevel, ToolCall, ToolCallRequester, ToolResultStatus
from raya.devices.windows import WindowsDeviceAgent
from raya.event_bus import EventBus
from raya.safety import AuditTrail, SafetyService, StopController
from raya.tools import ToolRegistry, execute
from raya.tools.catalog import register_pc_tools


def _setup(tmp_path):
    bus = EventBus()
    registry = ToolRegistry()
    agent = WindowsDeviceAgent(tmp_path / "screens")
    safety = SafetyService(StopController(bus), AuditTrail())
    register_pc_tools(registry, agent, should_stop=safety.should_stop)
    return registry, safety, bus, agent


def _call(name: str, arguments: dict) -> ToolCall:
    return ToolCall(tool_name=name, arguments=arguments, correlation_id="c1",
                     requested_by=ToolCallRequester(subsystem="harness", session_id="s1"))


def test_all_pc_tools_registered_with_windows_device_requirement(tmp_path):
    registry, safety, bus, agent = _setup(tmp_path)
    tools = {t.name: t for t in registry.all() if t.name.startswith("pc.")}
    assert "pc.window.list" in tools
    assert "pc.application.launch" in tools
    for t in tools.values():
        assert t.requires_device == "windows_agent"


def test_window_focus_declares_active_window_observation_spec(tmp_path):
    """Partie 12 (consigne 'pc.window.focus exécuté sans changement de
    fenêtre -> ne pas considérer SUCCESS') : vérifie le câblage réel de
    `pc.window.focus` au mécanisme générique Phase 7 (déjà prouvé
    abstraitement par tests/harness/test_observation_promotion.py)."""
    registry, safety, bus, agent = _setup(tmp_path)
    tool = registry.get("pc.window.focus")
    assert len(tool.observation) == 1
    spec = tool.observation[0]
    assert spec.domain == "pc" and spec.key == "active_window"
    assert spec.expected_argument == "target"


def test_safe_read_tool_executes_for_real_through_full_pipeline(tmp_path):
    """pc.window.list est SAFE -> jamais bloqué par Safety, exécute
    réellement contre le VRAI WindowsDeviceAgent via execute()."""
    registry, safety, bus, agent = _setup(tmp_path)
    result = execute(registry, safety, _call("pc.window.list", {}))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["count"] >= 1


# --- Chantier 12 §C : pc.filesystem.find_folder / pc.filesystem.open_path ---

def test_filesystem_tools_registered_as_safe(tmp_path):
    registry, safety, bus, agent = _setup(tmp_path)
    find_tool = registry.get("pc.filesystem.find_folder")
    open_tool = registry.get("pc.filesystem.open_path")
    assert find_tool is not None and find_tool.permission_level == PermissionLevel.SAFE
    assert open_tool is not None and open_tool.permission_level == PermissionLevel.SAFE


def test_filesystem_open_path_declares_key_from_argument_observation(tmp_path):
    registry, safety, bus, agent = _setup(tmp_path)
    tool = registry.get("pc.filesystem.open_path")
    assert len(tool.observation) == 1
    spec = tool.observation[0]
    assert spec.domain == "filesystem" and spec.key_from_argument == "name"
    assert spec.evidence_field == "path"


def test_find_folder_executes_for_real_and_never_guesses_a_match(tmp_path, monkeypatch):
    """pc.filesystem.find_folder est SAFE -> exécute réellement contre le
    VRAI WindowsDeviceAgent ; une recherche sans correspondance retourne une
    liste vide, jamais un chemin inventé."""
    from raya.devices.windows.mechanisms import filesystem as fs_mechanism

    monkeypatch.setattr(fs_mechanism.Path, "home", staticmethod(lambda: tmp_path))
    registry, safety, bus, agent = _setup(tmp_path)
    result = execute(registry, safety, _call("pc.filesystem.find_folder", {"name": "dossier-inexistant-xyz"}))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["matches"] == []


def test_find_folder_finds_a_real_directory_created_for_the_test(tmp_path, monkeypatch):
    from raya.devices.windows.mechanisms import filesystem as fs_mechanism

    monkeypatch.setattr(fs_mechanism.Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / "ProjetsTest").mkdir()
    registry, safety, bus, agent = _setup(tmp_path)
    result = execute(registry, safety, _call("pc.filesystem.find_folder", {"name": "projetstest"}))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["count"] == 1
    assert result.output["matches"][0].endswith("ProjetsTest")


def test_benign_interact_tool_executes_without_confirmation(tmp_path):
    """Passe 'Targeted Execution Repair' (§7, preuve concrète : cliquer '7'
    dans la Calculatrice exigeait une confirmation) : `pc.interact` est
    désormais CONTEXTUEL comme `browser.interact` (Phase 11) — un texte
    ordinaire sans verbe dangereux (ex: 'test') n'est plus bloqué."""
    registry, safety, bus, agent = _setup(tmp_path)
    result = execute(registry, safety, _call("pc.keyboard.type", {"text": "test"}))
    assert result.status != ToolResultStatus.PERMISSION_DENIED


def test_dangerous_interact_content_still_requires_confirmation_safety_never_bypassed(tmp_path):
    """Non-régression : une action mentionnant un verbe à conséquence
    significative (ex: 'supprimer') reste bloquée sans confirmation, même
    via `pc.interact` — le contenu prime, jamais un bypass générique (§15)."""
    registry, safety, bus, agent = _setup(tmp_path)
    result = execute(registry, safety, _call("pc.keyboard.type", {"text": "supprimer le fichier"}))
    assert result.status == ToolResultStatus.PERMISSION_DENIED


def test_ui_click_on_ordinary_calculator_button_is_safe(tmp_path):
    """Scénario réel exact de la consigne : cliquer '7'/'+' dans la
    Calculatrice via `pc.ui.click` (sélecteur imbriqué `selector.name`) ne
    doit plus exiger de confirmation."""
    registry, safety, bus, agent = _setup(tmp_path)
    for name in ("7", "Plus", "Égal"):
        result = execute(registry, safety, _call("pc.ui.click", {"window": "Calculatrice", "selector": {"name": name}}))
        assert result.status != ToolResultStatus.PERMISSION_DENIED, name


def test_ui_click_on_dangerous_selector_still_requires_confirmation(tmp_path):
    registry, safety, bus, agent = _setup(tmp_path)
    result = execute(registry, safety, _call(
        "pc.ui.click", {"window": "Paramètres", "selector": {"name": "Supprimer le compte"}},
    ))
    assert result.status == ToolResultStatus.PERMISSION_DENIED


def test_raw_coordinate_mouse_click_stays_sensitive_no_semantic_target(tmp_path):
    """Un clic aveugle sur coordonnées brutes n'a AUCUNE description de sa
    cible (contrairement à browser.dismiss_overlay, dont l'action reste
    intrinsèquement bornée) — reste prudent (SENSITIVE) par défaut."""
    registry, safety, bus, agent = _setup(tmp_path)
    result = execute(registry, safety, _call("pc.mouse.click", {"x": 100, "y": 200}))
    assert result.status == ToolResultStatus.PERMISSION_DENIED


# --- Chantier 16 : pc.capability.discover / pc.shell.execute ---

def test_capability_discover_is_safe_never_requires_confirmation(tmp_path):
    registry, safety, bus, agent = _setup(tmp_path)
    assert registry.get("pc.capability.discover").permission_level == PermissionLevel.SAFE
    result = execute(registry, safety, _call("pc.capability.discover", {"name": "ping"}))
    assert result.status != ToolResultStatus.PERMISSION_DENIED


def test_capability_discover_finds_a_real_known_windows_executable(tmp_path):
    """`ping` est garanti présent sur PATH sur n'importe quelle install
    Windows — preuve réelle (pas mockée) que la découverte fonctionne."""
    registry, safety, bus, agent = _setup(tmp_path)
    result = execute(registry, safety, _call("pc.capability.discover", {"name": "ping"}))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["available"] is True
    assert result.output["path"]  # un vrai chemin résolu, jamais None si available
    assert result.output["capability"] == "network.probe"


def test_capability_discover_never_invents_a_path_for_an_unknown_tool(tmp_path):
    registry, safety, bus, agent = _setup(tmp_path)
    result = execute(registry, safety, _call("pc.capability.discover", {"name": "definitely_not_a_real_tool_xyz123"}))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["available"] is False
    assert result.output["path"] is None
    assert result.output["capability"] is None


def test_capability_discover_reports_available_tool_with_unknown_capability_as_none(tmp_path):
    """`attrib` est un utilitaire Windows standard réellement sur PATH mais
    absent de la table de capacités connues (volontairement petite,
    consigne §9 'pas de liste hardcodée énorme') — jamais une capability
    devinée pour combler l'absence."""
    registry, safety, bus, agent = _setup(tmp_path)
    result = execute(registry, safety, _call("pc.capability.discover", {"name": "attrib"}))
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["available"] is True
    assert result.output["capability"] is None


def test_shell_execute_is_sensitive_unconditionally(tmp_path):
    from raya.safety.risk import classify_risk

    registry, safety, bus, agent = _setup(tmp_path)
    tool = registry.get("pc.shell.execute")
    assert tool.permission_level == PermissionLevel.SENSITIVE
    # Jamais contextuel (contrairement à pc.interact) : même une commande
    # bénigne en apparence reste SENSITIVE, arguments ou non.
    assert classify_risk(tool.capability_tags, {"command": "echo hello"}) == PermissionLevel.SENSITIVE


def test_shell_execute_requires_confirmation_before_running(tmp_path):
    registry, safety, bus, agent = _setup(tmp_path)
    result = execute(registry, safety, _call("pc.shell.execute", {"command": "echo hello"}))
    assert result.status == ToolResultStatus.PERMISSION_DENIED


def test_shell_execute_runs_for_real_hidden_when_confirmed(tmp_path):
    """Preuve réelle (pas mockée) : la commande s'exécute VRAIMENT, capture
    stdout/exit_code réels, jamais de fenêtre visible (CREATE_NO_WINDOW)."""
    registry, safety, bus, agent = _setup(tmp_path)
    result = execute(registry, safety, _call("pc.shell.execute", {"command": "cmd /c echo hello"}), user_confirmed=True)
    assert result.status == ToolResultStatus.SUCCESS
    assert result.output["exit_code"] == 0
    assert "hello" in result.output["stdout"]


def test_shell_execute_reports_real_nonzero_exit_code_honestly(tmp_path):
    registry, safety, bus, agent = _setup(tmp_path)
    result = execute(registry, safety, _call("pc.shell.execute", {"command": "cmd /c exit 3"}), user_confirmed=True)
    assert result.status == ToolResultStatus.SUCCESS  # la commande a bien été exécutée...
    assert result.output["exit_code"] == 3  # ...mais son échec fonctionnel reste honnêtement rapporté


def test_launch_tool_is_safe_and_really_opens_notepad_through_full_pipeline(tmp_path):
    registry, safety, bus, agent = _setup(tmp_path)
    tool = registry.get("pc.application.launch")
    assert tool.permission_level == PermissionLevel.SAFE
    try:
        result = execute(registry, safety, _call("pc.application.launch", {"target": "notepad"}))
        assert result.status == ToolResultStatus.SUCCESS
        assert result.evidence["process"].lower() == "notepad.exe"
    finally:
        from raya.devices.windows.mechanisms import window_mgmt

        window_mgmt.close_window("notepad")


def test_device_result_error_mapped_to_tool_result_error_through_full_pipeline(tmp_path):
    """pc.ui.inspect est SAFE -> atteint réellement le WindowsDeviceAgent,
    dont l'erreur WINDOW_NOT_FOUND réelle est fidèlement remontée dans le
    ToolResult (pas absorbée/reformulée)."""
    registry, safety, bus, agent = _setup(tmp_path)
    result = execute(registry, safety, _call("pc.ui.inspect", {"window": "fenetre_totalement_inexistante_xyz"}))
    assert result.status == ToolResultStatus.FAILURE
    assert result.error.code == "WINDOW_NOT_FOUND"


def test_safety_stop_active_blocks_pc_tool_before_device_execution(tmp_path):
    registry, safety, bus, agent = _setup(tmp_path)
    safety.request_stop("test")
    result = execute(registry, safety, _call("pc.window.list", {}))
    assert result.status == ToolResultStatus.CANCELLED
    assert result.error.code == "STOP_ACTIVE"


def test_risk_classification_matches_pc_read_vs_interact_tags():
    from raya.safety.risk import classify_risk

    assert classify_risk(["pc.read"]) == PermissionLevel.SAFE
    assert classify_risk(["pc.interact"]) == PermissionLevel.SENSITIVE
    assert classify_risk(["pc.launch"]) == PermissionLevel.SAFE
