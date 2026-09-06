"""Point d'entrée Cockpit web — `python -m raya.runtime.entrypoints.web` (RAYA
V2 Phase 6 ADAPTIVE USER INTERFACE). Composition root (comme cli.py) : câble
`bootstrap()` + `UIChannel`/`UIEventBridge` (raya/interfaces/ui/) derrière une
API HTTP/WebSocket FastAPI, et sert le Cockpit statique (raya/interfaces/ui/static/).

Architecture (consigne "UI AS CLIENT") :
    UI (navigateur) -> HTTP/WS (ce fichier) -> UIChannel -> Harness -> Core

Ce fichier ne construit AUCUNE nouvelle boucle agentique : il ne fait
QUE (1) démarrer le runtime réel via bootstrap(), (2) exposer son état via
UIChannel/UIEventBridge, (3) servir des fichiers statiques. Chaque appel
potentiellement bloquant (Harness.handle_request()) passe par
`run_in_threadpool` pour ne jamais geler la boucle asyncio — les connexions
WebSocket restent réactives pendant qu'un tour agentique tourne en arrière-plan
(consigne PERFORMANCE)."""

from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from raya.contracts import to_dict
from raya.interfaces.ui import UIChannel, UIEventBridge
from raya.observability import log
from raya.runtime.bootstrap import RuntimeHandles, bootstrap

_STATIC_DIR = Path(__file__).resolve().parents[2] / "interfaces" / "ui" / "static"
_DEFAULT_SESSION_ID = "cockpit-default"


class _SessionRegistry:
    """Une session Cockpit = un UIChannel (conversation/présence propres) +
    un UIEventBridge (flux d'events filtré). Isolation par session_id
    (consigne CHANNEL ISOLATION) — jamais d'état partagé entre deux sessions
    au niveau de cette classe (le Harness/EventBus sous-jacents restent
    partagés, comme toute interface RAYA)."""

    def __init__(self, handles: RuntimeHandles) -> None:
        self._handles = handles
        self._lock = threading.Lock()
        self._sessions: dict[str, tuple[UIChannel, UIEventBridge]] = {}

    def get_or_create(self, session_id: str) -> tuple[UIChannel, UIEventBridge]:
        with self._lock:
            existing = self._sessions.get(session_id)
            if existing is not None:
                return existing
            channel = UIChannel(self._handles.harness, self._handles.bus, session_id=session_id)
            bridge = UIEventBridge(self._handles.bus, session_id=session_id)
            self._sessions[session_id] = (channel, bridge)
            return channel, bridge

    def shutdown(self) -> None:
        with self._lock:
            for _channel, bridge in self._sessions.values():
                bridge.close()
            self._sessions.clear()


def create_app(handles: RuntimeHandles) -> FastAPI:
    registry = _SessionRegistry(handles)

    @asynccontextmanager
    async def _lifespan(_app: FastAPI):
        yield
        registry.shutdown()

    app = FastAPI(title="RAYA V2 Cockpit", lifespan=_lifespan)
    app.state.handles = handles
    app.state.registry = registry

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        index_path = _STATIC_DIR / "index.html"
        if not index_path.exists():
            raise HTTPException(status_code=404, detail="Cockpit statique introuvable")
        return FileResponse(str(index_path))

    # ------------------------------------------------------------------
    # Conversation / présence / STOP
    # ------------------------------------------------------------------

    @app.post("/api/session/{session_id}/message")
    async def post_message(session_id: str, body: dict) -> JSONResponse:
        text = (body or {}).get("text", "")
        if not text or not isinstance(text, str):
            raise HTTPException(status_code=400, detail="'text' requis")
        channel, _bridge = registry.get_or_create(session_id)
        view = await run_in_threadpool(channel.send_message, text)
        return JSONResponse(to_dict(view))

    @app.get("/api/session/{session_id}/conversation")
    def get_conversation(session_id: str) -> JSONResponse:
        channel, _bridge = registry.get_or_create(session_id)
        return JSONResponse(to_dict(channel.conversation_view()))

    @app.get("/api/session/{session_id}/presence")
    def get_presence(session_id: str) -> JSONResponse:
        channel, _bridge = registry.get_or_create(session_id)
        return JSONResponse(to_dict(channel.presence_view()))

    @app.post("/api/session/{session_id}/stop")
    def post_stop(session_id: str) -> JSONResponse:
        channel, _bridge = registry.get_or_create(session_id)
        channel.request_stop()
        return JSONResponse({"stopped": True})

    # ------------------------------------------------------------------
    # Tasks — à la demande
    # ------------------------------------------------------------------

    @app.get("/api/session/{session_id}/tasks")
    def get_tasks(session_id: str) -> JSONResponse:
        channel, _bridge = registry.get_or_create(session_id)
        return JSONResponse(to_dict(channel.task_summary_view()))

    @app.get("/api/session/{session_id}/tasks/{task_id}")
    def get_task(session_id: str, task_id: str) -> JSONResponse:
        channel, _bridge = registry.get_or_create(session_id)
        view = channel.task_detail_view(task_id)
        if view is None:
            raise HTTPException(status_code=404, detail="task inconnue")
        return JSONResponse(to_dict(view))

    @app.post("/api/session/{session_id}/tasks/{task_id}/{action}")
    async def post_task_action(session_id: str, task_id: str, action: str) -> JSONResponse:
        channel, _bridge = registry.get_or_create(session_id)
        method = {"pause": channel.pause_task, "resume": channel.resume_task, "cancel": channel.cancel_task}.get(action)
        if method is None:
            raise HTTPException(status_code=400, detail="action inconnue (pause|resume|cancel)")
        try:
            task = await run_in_threadpool(method, task_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse({"id": task.id, "state": task.state.value})

    # ------------------------------------------------------------------
    # World / Computer / Browser / Result / Attention — à la demande
    # ------------------------------------------------------------------

    @app.get("/api/session/{session_id}/world")
    def get_world(session_id: str, domains: str = "") -> JSONResponse:
        channel, _bridge = registry.get_or_create(session_id)
        domain_tuple = tuple(d.strip() for d in domains.split(",") if d.strip())
        return JSONResponse(to_dict(channel.world_view(domain_tuple)))

    @app.get("/api/session/{session_id}/computer")
    def get_computer(session_id: str) -> JSONResponse:
        channel, _bridge = registry.get_or_create(session_id)
        return JSONResponse(to_dict(channel.computer_view()))

    @app.get("/api/session/{session_id}/browser")
    def get_browser(session_id: str) -> JSONResponse:
        channel, _bridge = registry.get_or_create(session_id)
        return JSONResponse(to_dict(channel.browser_view()))

    @app.get("/api/session/{session_id}/result")
    def get_result(session_id: str) -> JSONResponse:
        channel, _bridge = registry.get_or_create(session_id)
        return JSONResponse(to_dict(channel.result_view()))

    @app.get("/api/session/{session_id}/attention")
    def get_attention(session_id: str) -> JSONResponse:
        channel, _bridge = registry.get_or_create(session_id)
        return JSONResponse(to_dict(channel.attention_view()))

    # ------------------------------------------------------------------
    # Spatial (Phase 8) — à la demande, jamais poussé automatiquement : le
    # Cockpit n'appelle ceci que lorsqu'il reçoit réellement un
    # `ui.view_requested`/view="spatial" (voir app.js), jamais au chargement.
    # ------------------------------------------------------------------

    @app.get("/api/session/{session_id}/spatial")
    def get_spatial(session_id: str) -> JSONResponse:
        channel, _bridge = registry.get_or_create(session_id)
        return JSONResponse(to_dict(channel.spatial_view()))

    # ------------------------------------------------------------------
    # Confirmation — Safety reste seule autorité
    # ------------------------------------------------------------------

    @app.get("/api/session/{session_id}/confirmation")
    def get_confirmation(session_id: str) -> JSONResponse:
        channel, _bridge = registry.get_or_create(session_id)
        view = channel.confirmation_view()
        return JSONResponse(to_dict(view) if view is not None else None)

    @app.post("/api/session/{session_id}/confirmation/resolve")
    async def post_confirmation_resolve(session_id: str, body: dict) -> JSONResponse:
        approved = bool((body or {}).get("approved", False))
        channel, _bridge = registry.get_or_create(session_id)
        try:
            view = await run_in_threadpool(channel.confirm, approved)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JSONResponse(to_dict(view))

    # ------------------------------------------------------------------
    # WebSocket — flux d'events temps réel (jamais de polling, consigne
    # EVENT-DRIVEN UI). Au connect : resynchronisation complète depuis l'état
    # authoritatif (consigne RECONNECTION — jamais de supposition que l'état
    # côté client était encore correct).
    # ------------------------------------------------------------------

    @app.websocket("/ws/{session_id}")
    async def ws_session(websocket: WebSocket, session_id: str) -> None:
        await websocket.accept()
        channel, bridge = registry.get_or_create(session_id)
        loop = asyncio.get_running_loop()

        async def _safe_send(notification: dict) -> None:
            try:
                await websocket.send_json(notification)
            except Exception:
                pass  # connexion déjà fermée — best-effort, jamais fatal pour le bridge

        def _on_notification(notification: dict) -> None:
            # Callback appelé depuis un thread EventBus (raya/event_bus/bus.py
            # ::_Subscription._deliver_loop) — jamais le thread asyncio de
            # cette connexion : planifie l'envoi via la boucle capturée.
            asyncio.run_coroutine_threadsafe(_safe_send(notification), loop)

        bridge.add_listener(_on_notification)
        try:
            confirmation = channel.confirmation_view()
            await websocket.send_json({
                "type": "sync",
                "payload": {
                    "presence": to_dict(channel.presence_view()),
                    "conversation": to_dict(channel.conversation_view()),
                    "confirmation": to_dict(confirmation) if confirmation is not None else None,
                },
            })
            while True:
                await websocket.receive_text()  # ping/pong keepalive côté client, jamais de commande métier ici
        except WebSocketDisconnect:
            pass
        finally:
            bridge.remove_listener(_on_notification)

    return app


def _maybe_start_voice(handles: RuntimeHandles):
    """Voix opt-in (RAYA_ENABLE_VOICE=true) — best-effort, comme
    `_register_devices` dans bootstrap.py : l'absence de matériel audio ou de
    modèles Whisper/Kokoro ne doit jamais empêcher le Cockpit texte de
    démarrer (consigne 'never fake state' s'applique aussi à l'absence
    honnête de voix, pas seulement à sa présence)."""
    if not handles.config.enable_voice:
        return None
    try:
        from raya.interfaces.voice.factory import build_real_voice_runtime

        _channel, runtime = build_real_voice_runtime(handles, session_id="voice-cockpit")
        runtime.start()
        log("info", "Cockpit : voix réelle démarrée (RAYA_ENABLE_VOICE=true)")
        return runtime
    except Exception as exc:
        log("warning", "Cockpit : voix indisponible sur cette machine, dégradation honnête (texte seul)", detail=str(exc))
        return None


def _maybe_start_telegram(handles: RuntimeHandles):
    """Telegram opt-in (RAYA_TELEGRAM_ENABLED=true) — best-effort, même
    principe que `_maybe_start_voice` : un token absent/invalide ou Telegram
    injoignable ne doit jamais empêcher le Cockpit de démarrer (consigne §4 :
    'Telegram = BLOCKED / NOT_CONFIGURED mais le reste de RAYA doit continuer
    à fonctionner')."""
    if not handles.config.enable_telegram:
        return None
    if not handles.config.telegram_bot_token:
        log("warning", "Telegram activé (RAYA_TELEGRAM_ENABLED=true) mais RAYA_TELEGRAM_BOT_TOKEN absent — "
                        "Telegram reste BLOCKED/NOT_CONFIGURED, le reste de RAYA continue normalement")
        return None
    try:
        from raya.interfaces.telegram.factory import build_real_telegram_runtime
        from raya.tools.catalog import NotifyOps, register_notify_tools

        channel, runtime = build_real_telegram_runtime(
            handles, token=handles.config.telegram_bot_token,
            allowed_user_ids=handles.config.telegram_allowed_user_ids,
            poll_timeout_s=handles.config.telegram_poll_timeout_s,
        )
        runtime.start()
        # Phase 11 (addendum Telegram outbound) : le Tool `telegram.send_message`
        # n'existe QUE si Telegram est réellement démarré ici — jamais un faux
        # espoir si Telegram est BLOCKED/NOT_CONFIGURED (cf. "never fake state").
        register_notify_tools(handles.tools, NotifyOps(send_telegram=channel.send_proactive))
        log("info", "Cockpit : Telegram réel démarré (RAYA_TELEGRAM_ENABLED=true)")
        return runtime
    except Exception as exc:
        log("warning", "Cockpit : Telegram indisponible, dégradation honnête (Cockpit seul)", detail=str(exc))
        return None


def main() -> None:
    import uvicorn

    handles = bootstrap()
    voice_runtime = _maybe_start_voice(handles)
    telegram_runtime = _maybe_start_telegram(handles)
    app = create_app(handles)
    url = f"http://{handles.config.web_host}:{handles.config.web_port}"
    # uvicorn tourne avec log_level="warning" (pas de bruit par requête) — sa
    # propre bannière "Uvicorn running on ..." est donc supprimée, d'où cet
    # affichage explicite : sans lui, rien n'indique où ouvrir le Cockpit.
    # flush=True : stdout est pleinement bufferisé (pas seulement par ligne)
    # dès qu'il n'est pas un vrai terminal interactif (pipe, fichier, certains
    # lanceurs) — sans ça cette bannière peut ne jamais apparaître avant que
    # uvicorn.run() ne bloque le process pour de bon.
    print("=" * 60, flush=True)
    print("  RAYA V2 — Cockpit", flush=True)
    print(f"  Ouvre ce lien dans ton navigateur : {url}", flush=True)
    print("=" * 60, flush=True)
    try:
        uvicorn.run(app, host=handles.config.web_host, port=handles.config.web_port, log_level="warning")
    finally:
        if voice_runtime is not None:
            voice_runtime.stop()
        if telegram_runtime is not None:
            telegram_runtime.stop()
            handles.harness.mark_device_offline("telegram-mobile")
        handles.shutdown()


if __name__ == "__main__":
    main()
