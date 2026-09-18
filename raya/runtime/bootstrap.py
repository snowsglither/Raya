"""Composition root (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.1, RAYA_V2_REPOSITORY_STRUCTURE.md §1).

Phase 3 : câble les VRAIS providers Ollama (Cloud si OLLAMA_API_KEY présente,
Local si activé/disponible) selon le pool DATA-driven de config.py — jamais
"DeepSeek = toujours cerveau" gravé ici, uniquement des ModelDescriptor
enregistrés (consigne Phase 3 §6). Câble aussi le catalogue d'outils de
démonstration réel. NullProvider reste toujours enregistré en dernier
recours honnête (jamais de fausse intelligence si aucun provider réel n'est
disponible — consigne §0, §29).

Aucune logique métier ici — uniquement du câblage.
"""

from __future__ import annotations

from dataclasses import dataclass

from raya.contracts import DeviceType, Event
from raya.devices import DeviceRegistry
from raya.event_bus import EventBus
from raya.harness import ExecutionRecordRepository, Harness
from raya.memory import MemoryStore
from raya.models import ModelRegistry
from raya.models.providers import NullProvider, OllamaCloudAdapter, OllamaLocalAdapter
from raya.observability import EventStore, ObservabilityTracer, log
from raya.perception import ActiveWindowSensor, IncomingCallNotificationSensor, PerceptionRuntime, PhoneCallActivitySensor
from raya.persistence import PersistenceBackend, SqliteBackend
from raya.safety import AuditTrail, SafetyService, StopController
from raya.spatial import SceneStore
from raya.tasks import TaskRegistry
from raya.tools import ToolRegistry
from raya.tools.catalog import (
    PreferenceOps,
    TaskControlOps,
    register_browser_tools,
    register_demo_tools,
    register_pc_tools,
    register_phone_tools,
    register_preference_tools,
    register_spatial_tools,
    register_system_time_tool,
    register_task_control_tools,
    register_ui_view_tools,
    register_visual_tools,
)
from raya.world_state import WorldStateStore

from .config import RuntimeConfig, load_config


@dataclass
class RuntimeHandles:
    config: RuntimeConfig
    bus: EventBus
    backend: PersistenceBackend
    safety: SafetyService
    world_state: WorldStateStore
    memory: MemoryStore
    tasks: TaskRegistry
    execution_records: ExecutionRecordRepository
    models: ModelRegistry
    tools: ToolRegistry
    devices: DeviceRegistry
    harness: Harness
    tracer: ObservabilityTracer
    event_store: EventStore
    scene_store: SceneStore
    perception: PerceptionRuntime | None = None

    def shutdown(self) -> None:
        self.bus.publish(Event(type="runtime.stopping", source="runtime", payload={}))
        if self.perception is not None:
            self.perception.stop()
        self.harness.shutdown()  # annule/attend les tâches de fond AVANT de fermer le backend
        for device_id in self.devices.all_ids():
            agent = self.devices.get(device_id)
            close = getattr(agent, "shutdown", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    pass
        self.bus.wait_idle(timeout_s=0.5)
        self.backend.close()
        log("info", "RAYA V2 runtime stopped")


def _register_model_pool(models: ModelRegistry, config: RuntimeConfig) -> None:
    if config.ollama_api_key:
        for model_id, capabilities in config.model_pool:
            models.register(OllamaCloudAdapter(
                model_id, capabilities, api_key=config.ollama_api_key, host=config.ollama_host,
            ))
    if config.enable_ollama_local:
        for model_id, capabilities in config.local_model_pool:
            models.register(OllamaLocalAdapter(model_id, capabilities, host=config.ollama_local_host))
    # Toujours enregistré en dernier recours honnête (RAYA_V2_MIGRATION_PLAN.md §13 —
    # "jamais de fausse intelligence") : si aucun provider réel n'est
    # disponible/configuré, le Router retombe dessus et le dit explicitement.
    models.register(NullProvider())


def _register_devices(devices: DeviceRegistry, tools: ToolRegistry, config: RuntimeConfig, safety: SafetyService) -> None:
    """Câblage best-effort (Phase 4) : une plateforme sans Windows/Edge ne
    doit jamais empêcher le reste de RAYA de démarrer — un Device Agent
    indisponible se dégrade honnêtement (absent du DeviceRegistry, ses Tools
    ne sont jamais enregistrés) plutôt que de crasher tout le runtime."""
    if config.enable_windows_device:
        try:
            from raya.devices.windows import DEVICE_ID as _WIN_ID
            from raya.devices.windows import WindowsDeviceAgent

            windows_agent = WindowsDeviceAgent(config.device_screenshot_dir)
            devices.register(_WIN_ID, windows_agent, device_type=DeviceType.WINDOWS)
            register_pc_tools(tools, windows_agent, should_stop=safety.should_stop)
        except Exception as exc:
            log("warning", "Windows Device Agent indisponible sur cette plateforme", detail=str(exc))

    if config.enable_browser_device:
        try:
            from raya.devices.browser import DEVICE_ID as _BROWSER_ID
            from raya.devices.browser import BrowserDeviceAgent

            browser_agent = BrowserDeviceAgent(config.device_screenshot_dir)
            devices.register(_BROWSER_ID, browser_agent, device_type=DeviceType.BROWSER)
            register_browser_tools(tools, browser_agent, should_stop=safety.should_stop)
        except Exception as exc:
            log("warning", "Browser Device Agent indisponible sur cette plateforme", detail=str(exc))

    if config.enable_phone_device:
        try:
            from raya.devices.ios import DEVICE_ID as _PHONE_ID
            from raya.devices.ios import PhoneLinkDeviceAgent

            phone_agent = PhoneLinkDeviceAgent()
            devices.register(_PHONE_ID, phone_agent, device_type=DeviceType.IOS)
            register_phone_tools(tools, phone_agent, should_stop=safety.should_stop)
        except Exception as exc:
            log("warning", "Phone Device Agent indisponible sur cette plateforme", detail=str(exc))


def _register_vision_tools(
    tools: ToolRegistry, devices: DeviceRegistry, models: ModelRegistry, config: RuntimeConfig
) -> None:
    """Best-effort — vision indisponible (pyautogui manquant, pas de provider
    VISION enregistré) ne bloque jamais le démarrage du runtime."""
    try:
        import tempfile
        from pathlib import Path as _Path
        from raya.contracts import Command as _Command
        from raya.models.vision import observe_image

        def _observe_fn(path, prompt, find_target, correlation_id, prefer_local, viewport, source):
            return observe_image(models, path, prompt, find_target, correlation_id, prefer_local, viewport, source)

        def _capture_screen_fn() -> dict:
            import pyautogui
            tmp = _Path(tempfile.mkdtemp()) / "raya_screen_cap.png"
            img = pyautogui.screenshot()
            img.save(str(tmp))
            return {"path": str(tmp), "width": img.width, "height": img.height}

        def _capture_browser_fn() -> dict:
            agent = devices.get("browser_agent")
            if agent is None:
                raise RuntimeError("browser_agent non disponible pour vision.capture")
            cmd = _Command(
                device_id="browser_agent",
                capability_name="browser.screenshot",
                arguments={"filename": "raya_vision_capture.png"},
                correlation_id="vision_capture",
            )
            result = agent.execute(cmd)
            if result.status.value != "success":
                err = result.error.message if result.error else "unknown"
                raise RuntimeError(f"browser.screenshot failed: {err}")
            return {
                "path": result.output["path"],
                "width": result.output.get("width", 0),
                "height": result.output.get("height", 0),
                "url": result.output.get("url", ""),
                "title": result.output.get("title", ""),
            }

        register_visual_tools(
            tools, _observe_fn, _capture_screen_fn, _capture_browser_fn,
            prefer_local=config.enable_ollama_local,
        )
        log("info", "vision tools registered")
    except Exception as exc:
        log("warning", "Vision tools indisponibles sur cette plateforme", detail=str(exc))


def _start_perception(config: RuntimeConfig, bus: EventBus) -> PerceptionRuntime | None:
    """Best-effort, comme `_register_devices()` : une plateforme sans pywin32
    ne doit jamais empêcher le reste de RAYA de démarrer — `ActiveWindowSensor`
    se dégrade déjà honnêtement en interne (retourne toujours `None`), mais
    on protège aussi la construction/démarrage du thread par précaution."""
    if not config.enable_perception:
        return None
    try:
        # Chantier 13B/13G : même runtime/intervalle que ActiveWindowSensor
        # (jamais un second mécanisme de poll). PhoneCallActivitySensor
        # (13D, PhoneExperienceHost.exe) et IncomingCallNotificationSensor
        # (13G, ShellExperienceHost.exe — la vraie source de contenu de
        # bannière, cf. investigation 13F) coexistent : le premier n'est
        # plus considéré comme la source fiable pour le CONTENU d'un appel
        # entrant, mais reste présent (aucune régression du fait promu
        # domain=phone/key=call_activity). Les deux se dégradent
        # honnêtement en no-op si Windows/uiautomation est indisponible.
        runtime = PerceptionRuntime(
            [ActiveWindowSensor(), PhoneCallActivitySensor(), IncomingCallNotificationSensor()],
            bus, interval_s=config.perception_poll_interval_s,
        )
        runtime.start()
        return runtime
    except Exception as exc:
        log("warning", "Perception indisponible sur cette plateforme", detail=str(exc))
        return None


def bootstrap(config: RuntimeConfig | None = None, backend: PersistenceBackend | None = None) -> RuntimeHandles:
    """`backend` est injectable pour les tests (ex: SqliteBackend sur un
    tmp_path isolé, ou InMemoryBackend pour des tests rapides sans I/O)."""
    config = config or load_config()

    bus = EventBus()
    tracer = ObservabilityTracer(bus)

    backend = backend or SqliteBackend(config.db_path)
    event_store = EventStore(backend, bus)

    stop = StopController(bus)
    audit = AuditTrail()
    safety = SafetyService(stop, audit)

    world_state = WorldStateStore(backend, bus)
    memory = MemoryStore(backend, bus)
    tasks = TaskRegistry(backend, bus)
    execution_records = ExecutionRecordRepository(backend)

    models = ModelRegistry()
    _register_model_pool(models, config)

    tools = ToolRegistry()
    register_demo_tools(tools, config.tool_workspace_dir)
    # Chantier 12 §A : toujours disponible (SAFE, lecture pure de l'horloge
    # système) — aucune raison de le désactiver conditionnellement.
    register_system_time_tool(tools, default_timezone=config.timezone)
    # Phase 6 : toujours disponible (SAFE, purement présentationnel) — un
    # canal sans UI visuelle (CLI/voix) n'en fait simplement jamais usage,
    # le modèle ne l'appelle que si le contexte s'y prête.
    register_ui_view_tools(tools, bus)

    # Phase 8 : Creative/Spatial Agent — toujours disponible (SAFE, mutations
    # en mémoire uniquement), intent-driven (le modèle ne l'invoque que si le
    # contexte de la conversation s'y prête, jamais un pré-filtrage textuel).
    scene_store = SceneStore()
    register_spatial_tools(tools, scene_store, bus, config.tool_workspace_dir)

    devices = DeviceRegistry()
    _register_devices(devices, tools, config, safety)
    _register_vision_tools(tools, devices, models, config)

    perception = _start_perception(config, bus)

    harness = Harness(
        world_state=world_state,
        memory=memory,
        tasks=tasks,
        safety=safety,
        model_registry=models,
        bus=bus,
        tools_registry=tools,
        execution_records=execution_records,
        scene_store=scene_store,
        devices=devices,
        context_budget_tokens=config.context_budget_tokens,
        max_concurrent_tasks=config.max_concurrent_tasks,
        max_tool_iterations=config.max_tool_iterations,
    )

    # Phase 5 : les Tools de steering de tâches ont besoin du Harness DÉJÀ
    # construit (injection de callables liés, jamais un import raya.harness
    # depuis tools/catalog/tasks.py — voir la note de ce fichier). ToolRegistry
    # est un objet MUTABLE déjà référencé par `harness` : y ajouter des Tools
    # après coup est sûr, `_discover_tool_schemas()` le lit à chaque tour.
    register_task_control_tools(tools, TaskControlOps(
        pause=harness.pause_task, resume=harness.resume_task, cancel=harness.cancel_task,
        checkpoint=harness.checkpoint_task, get_task=harness.get_task,
        # Phase 10 : "natural language -> Task" centralisé UNE fois ici,
        # jamais par interface (consigne §13) — n'importe quel canal (via le
        # modèle) peut appeler tasks.create, qui retombe toujours sur ce
        # même create_long_horizon_task().
        create=harness.create_long_horizon_task,
        # Chantier 14 §16 : "Task Identity" — laisse le modèle retrouver un
        # task_id réel avant tasks.cancel/tasks.create de remplacement,
        # jamais deviné. Même pattern d'injection étroite que ci-dessus.
        list=harness.list_tasks,
    ))

    # Chantier 12 §E : même pattern d'injection étroite que TaskControlOps
    # ci-dessus — toujours disponible (SAFE, écrit uniquement en Memory).
    register_preference_tools(tools, PreferenceOps(set_channel=harness.set_channel_preference))

    # Étiquette "Phase N" retirée ici (stabilisation pré-Phase 7 §10) : un
    # numéro de phase fige une valeur qui redevient fausse dès la phase
    # suivante et n'est lu par aucun code (vérifié : rien ne branche sur
    # payload["phase"]) — purement informatif, jamais une logique conditionnée
    # par la phase (consigne "ne pas introduire de logique basée sur le numéro
    # de phase").
    bus.publish(Event(type="runtime.started", source="runtime", payload={}))
    log("info", "RAYA V2 runtime started", db_path=str(getattr(backend, "path", "in-memory")),
        models=[d.id for d in (p.descriptor() for p in models.all())])

    harness.recover()  # RAYA_V2_MIGRATION_PLAN.md §11.3 : RUNNING au crash -> PAUSED

    return RuntimeHandles(
        config=config,
        bus=bus,
        backend=backend,
        safety=safety,
        world_state=world_state,
        memory=memory,
        tasks=tasks,
        execution_records=execution_records,
        models=models,
        tools=tools,
        devices=devices,
        harness=harness,
        tracer=tracer,
        event_store=event_store,
        scene_store=scene_store,
        perception=perception,
    )
