"""Priorité G — Harness Phase 1 : assemblage de contexte réel, invocation modèle
via l'abstraction, mise à jour d'état, persistance, association à une Task, STOP,
fondation de recovery."""

from __future__ import annotations

from raya.contracts import Channel, HarnessRequest, HarnessStatus, InterfaceInput
from raya.persistence import InMemoryBackend
from raya.runtime.bootstrap import bootstrap


def _handles(tmp_path):
    from raya.persistence import SqliteBackend
    from raya.runtime.config import load_config

    cfg = load_config()
    cfg.db_path = tmp_path / "harness.sqlite3"
    return bootstrap(config=cfg, backend=SqliteBackend(cfg.db_path))


def test_handle_request_uses_real_context_assembly(tmp_path):
    handles = _handles(tmp_path)
    try:
        handles.harness.create_memory("Ruben préfère Chrome", channel="cli")
        req = HarnessRequest(channel=Channel.CLI, session_id="s1", input=InterfaceInput(text="chrome"))
        handles.harness.handle_request(req)
        ctx = handles.harness.last_context("s1")
        assert ctx is not None
        assert any(s.kind.value == "memory" for s in ctx.sections)
    finally:
        handles.shutdown()


def test_conversation_persisted_and_survives_restart(tmp_path):
    handles = _handles(tmp_path)
    req = HarnessRequest(channel=Channel.CLI, session_id="s1", input=InterfaceInput(text="mémorise ce message"))
    handles.harness.handle_request(req)
    handles.shutdown()

    handles2 = _handles(tmp_path)
    try:
        from raya.contracts import ChannelScope

        # Phase 11 (contexte continuité) : la réponse de RAYA est désormais
        # AUSSI persistée en mémoire CONVERSATION (avant, seul le message
        # utilisateur l'était) — le nombre de hits n'est donc plus figé à 1,
        # mais le message utilisateur original doit toujours être retrouvable
        # après redémarrage.
        hits = handles2.memory.search(query="mémorise", channel_scope=ChannelScope.CHAT)
        assert len(hits) >= 1
        assert any(h.content == "mémorise ce message" for h in hits)
    finally:
        handles2.shutdown()


def test_create_memory_and_set_world_fact_are_explicit_interface_actions(tmp_path):
    """Ces méthodes ne passent PAS par le modèle — action explicite demandée
    par l'interface, honnête vis-à-vis de l'absence de Cognition réelle Phase 1."""
    handles = _handles(tmp_path)
    try:
        entry = handles.harness.create_memory("note explicite", channel="cli")
        assert entry.provenance.startswith("interface:")
        fact = handles.harness.set_world_fact("pc", "app", "vscode")
        assert fact.source == "interface:explicit"
    finally:
        handles.shutdown()


def test_task_association_visible_in_context():
    from raya.contracts import ChannelScope
    from raya.context_engine import assemble
    from raya.contracts import Task, TaskOwner
    from raya.memory import MemoryStore
    from raya.world_state import WorldStateStore

    ws = WorldStateStore(InMemoryBackend())
    mem = MemoryStore(InMemoryBackend())
    task = Task(objective="tester", owner=TaskOwner(channel="cli", session_id="s1"), correlation_id="c1")
    ctx = assemble(session_id="s1", channel_scope=ChannelScope.CHAT, world_state=ws, memory=mem, task=task)
    assert ctx.task_id == task.id


def test_stop_active_before_first_context_assembly_step(tmp_path):
    handles = _handles(tmp_path)
    try:
        from raya.contracts import Event

        handles.bus.publish(Event(type="interface.stop_requested", source="interfaces.cli", payload={}))
        handles.bus.wait_idle(timeout_s=1.0)
        req = HarnessRequest(channel=Channel.CLI, session_id="s1", input=InterfaceInput(text="x"))
        state = handles.harness.handle_request(req)
        assert state.status == HarnessStatus.FAILED
        assert state.error.code == "STOP_ACTIVE"
    finally:
        handles.shutdown()


def test_recover_on_boot_pauses_orphaned_running_tasks(tmp_path):
    handles1 = _handles(tmp_path)
    task = handles1.harness.create_task("obj", channel="cli", session_id="s1")
    handles1.tasks.start(task.id)
    # pas de shutdown propre : simulate crash (pas de complete/pause)

    handles2 = _handles(tmp_path)
    try:
        from raya.contracts import TaskState

        restored = handles2.tasks.get(task.id)
        assert restored.state == TaskState.PAUSED
    finally:
        handles2.shutdown()


def test_harness_emits_turn_started_and_completed_events(tmp_path):
    handles = _handles(tmp_path)
    try:
        req = HarnessRequest(channel=Channel.CLI, session_id="s1", input=InterfaceInput(text="salut"))
        handles.harness.handle_request(req)
        handles.bus.wait_idle(timeout_s=1.0)
        events = handles.tracer.all()
        types = [e.type for e in events]
        assert "harness.turn_started" in types
        assert "harness.turn_completed" in types
    finally:
        handles.shutdown()
