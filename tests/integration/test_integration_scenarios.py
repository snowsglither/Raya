"""Scénarios d'intégration bout-en-bout demandés explicitement (RAYA_V2_MIGRATION_PLAN.md
§18 de la consigne Phase 1, TEST 1 à TEST 7). Chacun exerce plusieurs subsystems
réels ensemble (pas de mock), avec le vrai backend SQLite.
"""

from __future__ import annotations

import time

from raya.contracts import (
    Channel,
    ChannelScope,
    Confidence,
    Event,
    HarnessRequest,
    InterfaceInput,
    MemoryEntry,
    MemoryLayer,
    MemoryType,
    TaskState,
    WorldStateFact,
)
from raya.persistence import SqliteBackend
from raya.runtime.bootstrap import bootstrap
from raya.runtime.config import load_config


def _handles(tmp_path, db_name: str = "integration.sqlite3"):
    cfg = load_config()
    cfg.db_path = tmp_path / db_name
    return bootstrap(config=cfg, backend=SqliteBackend(cfg.db_path)), cfg


# --- TEST 1 — Restart persistence ------------------------------------------

def test_scenario_1_restart_persistence(tmp_path):
    (handles, cfg) = _handles(tmp_path)
    mem = handles.harness.create_memory("fait important à retenir", channel="cli")
    fact = handles.harness.set_world_fact("pc", "app", "vscode")
    task = handles.harness.create_task("obj durable", channel="cli", session_id="s1")
    handles.harness.checkpoint_task(task.id, {"current_step": "step_1", "percent": 10})
    handles.shutdown()

    handles2, _ = _handles(tmp_path)
    try:
        assert handles2.memory.get(mem.id) is not None
        restored_fact = handles2.world_state.retrieve_fact("pc", "app")
        assert restored_fact.value == "vscode"
        restored_task = handles2.tasks.get(task.id)
        assert restored_task.checkpoint == {"current_step": "step_1", "percent": 10}
    finally:
        handles2.shutdown()


# --- TEST 2 — Context construction ------------------------------------------

def test_scenario_2_context_construction(tmp_path):
    handles, _ = _handles(tmp_path)
    try:
        handles.memory.write(MemoryEntry(
            type=MemoryType.PREFERENCE, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.CHAT,
            content="Ruben préfère Chrome", provenance="test",
        ))
        handles.memory.write(MemoryEntry(
            type=MemoryType.FACT, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.CHAT,
            content="détail totalement hors sujet sur la météo martienne", provenance="test",
        ))
        handles.world_state.apply_update(WorldStateFact(
            domain="pc", key="fresh", value="ok", source="s", confidence=Confidence.KNOWN_FACT
        ))
        handles.world_state.apply_update(WorldStateFact(
            domain="pc", key="old", value="obsolète", source="s", confidence=Confidence.KNOWN_FACT, freshness_ttl_s=0
        ))
        time.sleep(0.01)
        task = handles.harness.create_task("comprendre Chrome", channel="cli", session_id="s1")

        from raya.context_engine import assemble

        ctx = assemble(
            session_id="s1", channel_scope=ChannelScope.CHAT, world_state=handles.world_state,
            memory=handles.memory, world_state_domains=("pc",), task=task, query_text="chrome",
            budget_tokens=2000,
        )

        contents = [str(s.content) for s in ctx.sections]
        assert any("Chrome" in c for c in contents)  # info pertinente sélectionnée
        assert not any("météo martienne" in c for c in contents)  # info non pertinente absente

        from raya.contracts import FactStatus, SectionKind

        ws_sections = {s.content["key"]: s.freshness.status for s in ctx.sections if s.kind == SectionKind.WORLD_STATE}
        assert ws_sections["fresh"] == FactStatus.ACTIVE
        assert ws_sections["old"] == FactStatus.STALE  # correctement marqué stale, pas exclu

        assert all(s.provenance for s in ctx.sections)
        assert ctx.used_tokens_estimate <= ctx.budget_tokens
    finally:
        handles.shutdown()


# --- TEST 3 — Channel isolation ------------------------------------------

def test_scenario_3_channel_isolation(tmp_path):
    handles, _ = _handles(tmp_path)
    try:
        handles.harness.create_memory("info chat privée", channel="cli")  # cli -> CHAT
        handles.memory.write(MemoryEntry(
            type=MemoryType.FACT, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.VOICE,
            content="info voice privée", provenance="test",
        ))
        handles.memory.write(MemoryEntry(
            type=MemoryType.FACT, layer=MemoryLayer.PERSONAL, channel_scope=ChannelScope.SHARED,
            content="info partagée", provenance="test",
        ))

        from raya.context_engine import assemble

        ctx_chat = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=handles.world_state, memory=handles.memory, query_text="info")
        ctx_voice = assemble(session_id="s1", channel_scope=ChannelScope.VOICE, world_state=handles.world_state, memory=handles.memory, query_text="info")

        chat_contents = {s.content.get("content") for s in ctx_chat.sections if s.kind.value == "memory"}
        voice_contents = {s.content.get("content") for s in ctx_voice.sections if s.kind.value == "memory"}

        assert "info chat privée" in chat_contents
        assert "info chat privée" not in voice_contents
        assert "info voice privée" in voice_contents
        assert "info voice privée" not in chat_contents
        assert "info partagée" in chat_contents
        assert "info partagée" in voice_contents
    finally:
        handles.shutdown()


# --- TEST 4 — Pause/resume ------------------------------------------

def test_scenario_4_pause_resume(tmp_path):
    handles, _ = _handles(tmp_path)
    try:
        task = handles.harness.start_background_task("tâche longue simulée")
        time.sleep(0.06)  # laisse au moins un step s'exécuter
        handles.harness.pause_task(task.id)
        # cancellation/pause coopérative : un step déjà en vol au moment de
        # pause_task() va jusqu'à son terme avant que le worker ne remarque
        # PAUSED et cesse de ré-enfiler — laisser cette course se stabiliser
        # avant de capturer la progression "de référence".
        time.sleep(0.1)
        progress_at_pause = handles.tasks.get(task.id).progress
        time.sleep(0.15)  # le temps de plusieurs steps si ce n'était pas vraiment en pause
        assert handles.tasks.get(task.id).state == TaskState.PAUSED
        assert handles.tasks.get(task.id).progress == progress_at_pause  # aucune progression pendant la pause

        handles.harness.resume_task(task.id)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if handles.tasks.get(task.id).state == TaskState.COMPLETED:
                break
            time.sleep(0.02)
        assert handles.tasks.get(task.id).state == TaskState.COMPLETED
    finally:
        handles.shutdown()


# --- TEST 5 — STOP ------------------------------------------

def test_scenario_5_stop_via_cli_event_interrupts_task(tmp_path):
    handles, _ = _handles(tmp_path)
    try:
        task = handles.harness.start_background_task("tâche à interrompre")
        time.sleep(0.03)

        handles.bus.publish(Event(type="interface.stop_requested", source="interfaces.cli", payload={}))
        handles.bus.wait_idle(timeout_s=1.0)
        assert handles.safety.should_stop() is True

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if handles.tasks.get(task.id).state == TaskState.CANCELLED:
                break
            time.sleep(0.02)
        assert handles.tasks.get(task.id).state == TaskState.CANCELLED
    finally:
        handles.shutdown()


# --- TEST 6 — Crash recovery ------------------------------------------

def test_scenario_6_crash_recovery_does_not_mark_task_successful(tmp_path):
    handles1, _ = _handles(tmp_path)
    task = handles1.harness.create_task("obj", channel="cli", session_id="s1")
    handles1.tasks.start(task.id)

    from raya.contracts import ExecutionRecord

    handles1.execution_records.start(ExecutionRecord(
        operation_id="op_crash", tool_call_id="tc1", correlation_id=task.correlation_id, idempotency_key="k1",
    ))
    # "crash" : pas de complete(), pas de shutdown propre

    handles2, _ = _handles(tmp_path)
    try:
        restored_task = handles2.tasks.get(task.id)
        assert restored_task.state != TaskState.COMPLETED
        assert restored_task.state == TaskState.PAUSED  # recover_after_restart au boot

        record = handles2.execution_records.load_and_reinterpret("op_crash")
        from raya.contracts import ExecutionState

        assert record.execution_state == ExecutionState.UNKNOWN  # jamais COMPLETED par optimisme
    finally:
        handles2.shutdown()


# --- TEST 7 — Separate user interaction ------------------------------------------

def test_scenario_7_background_task_does_not_block_conversation(tmp_path):
    handles, _ = _handles(tmp_path)
    try:
        handles.harness.start_background_task("tâche de fond longue")

        start = time.monotonic()
        req = HarnessRequest(channel=Channel.CLI, session_id="s1", input=InterfaceInput(text="quelle heure est-il"))
        state = handles.harness.handle_request(req)
        elapsed = time.monotonic() - start

        # Phase 3 : sans clé Ollama réelle dans ce test, le tour se termine en
        # FAILED honnête (NullProvider) plutôt que COMPLETED — ce qui compte
        # ICI est que le Harness ait RÉPONDU (pas planté, pas bloqué), pas la
        # nature de la réponse (couverte par tests/harness/test_loop.py).
        assert state.status.value in ("COMPLETED", "FAILED")
        # la conversation répond largement avant la fin des 5 steps *
        # _SIMULATED_STEP_DELAY_S (~0.25s) de la tâche de fond
        assert elapsed < 0.2
    finally:
        handles.shutdown()
