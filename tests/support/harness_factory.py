"""Construit un runtime RÉEL complet (SQLite, EventBus, Safety, Tools avec le
catalogue de démonstration, Harness) pour les tests Phase 3, avec un
FakeScriptedProvider enregistré à la place d'un vrai Ollama — seul le modèle
est scripté, tout le reste (persistence/tools/safety/execution_records) est
le vrai système (consigne §23/§45)."""

from __future__ import annotations

from pathlib import Path

from raya.persistence import SqliteBackend
from raya.runtime.bootstrap import RuntimeHandles, bootstrap
from raya.runtime.config import load_config

from .fake_provider import FakeScriptedProvider, ScriptEntry


def build_test_harness(
    tmp_path: Path, script: list[ScriptEntry], *, max_tool_iterations: int = 4, db_name: str = "test.sqlite3",
    enable_windows_device: bool = False, enable_browser_device: bool = False, enable_perception: bool = False,
    enable_phone_device: bool = False,
) -> tuple[RuntimeHandles, FakeScriptedProvider]:
    cfg = load_config()
    cfg.db_path = tmp_path / db_name
    cfg.tool_workspace_dir = tmp_path / "workspace"
    cfg.ollama_api_key = None  # pas de vrai Ollama Cloud dans ces tests
    cfg.model_pool = []
    cfg.enable_ollama_local = False
    cfg.max_tool_iterations = max_tool_iterations
    # Phase 4 : désactivés par défaut (ces tests scriptent le modèle et ne
    # testent PAS forcément les Device Agents réels) — activables
    # explicitement pour tests/integration/test_phase4_scenarios.py, évite de
    # lancer un vrai Edge/UIA à chaque test Phase 3 qui n'en a pas besoin.
    cfg.enable_windows_device = enable_windows_device
    cfg.enable_browser_device = enable_browser_device
    # Phase 7 : idem pour le poller perception (thread réel + appels win32 à
    # chaque cycle) — désactivé par défaut, activable explicitement pour les
    # tests d'intégration perception qui en ont réellement besoin.
    cfg.enable_perception = enable_perception
    # Chantier 13C (root cause) : `RuntimeConfig.enable_phone_device` par
    # défaut à True (Chantier 13) n'était PAS repris ici lors de son ajout —
    # tout test utilisant ce helper héritait donc silencieusement d'un VRAI
    # PhoneLinkDeviceAgent (real Phone Link/appels/SMS réels atteignables),
    # contrairement aux trois autres Device Agents ci-dessus, délibérément
    # désactivés par défaut dans un harness de test. Même politique
    # appliquée ici : désactivé par défaut, activable explicitement.
    cfg.enable_phone_device = enable_phone_device
    cfg.device_screenshot_dir = tmp_path / "screens"

    handles = bootstrap(config=cfg, backend=SqliteBackend(cfg.db_path))
    fake = FakeScriptedProvider(script)
    handles.models.register(fake)
    return handles, fake
