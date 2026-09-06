"""CLI — première interface officielle RAYA V2 (consigne Phase 0 §7, commandes
Phase 1 §15, commandes Phase 2 §37).

Règle stricte : Interface -> Harness, jamais Interface -> tools/models/memory/
safety/devices/tasks/attention internals. La SEULE exception est la
publication d'un Event sur l'EventBus (STOP, interface.request_received) —
publier un event n'est pas un appel de fonction vers le subsystem qui le
consomme (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.14).

Les commandes /state /memory /tasks /context /events /attention ci-dessous ne
font QUE parser des arguments et appeler une méthode du Harness — aucune
logique métier ici.
"""

from __future__ import annotations

from raya.contracts import Channel, Event, HarnessRequest, InterfaceInput
from raya.event_bus import EventBus
from raya.harness import Harness

_EXIT_COMMANDS = {"exit", "quit", "/exit", "/quit"}
_STOP_COMMANDS = {"stop", "/stop", "arrête-toi", "stoppe"}


def _build_request(session_id: str, text: str) -> HarnessRequest:
    return HarnessRequest(channel=Channel.CLI, session_id=session_id, input=InterfaceInput(text=text))


def _handle_state(harness: Harness, args: list[str]) -> str:
    if not args:
        return "usage: /state set <domain> <key> <value> | /state get <domain> <key>"
    if args[0] == "set" and len(args) >= 4:
        fact = harness.set_world_fact(args[1], args[2], " ".join(args[3:]))
        return f"world_state: {fact.domain}.{fact.key} = {fact.value!r} ({fact.status.value})"
    if args[0] == "get" and len(args) >= 3:
        fact = harness.get_world_fact(args[1], args[2])
        if fact is None:
            return f"world_state: {args[1]}.{args[2]} inconnu"
        return f"world_state: {fact.domain}.{fact.key} = {fact.value!r} ({fact.status.value})"
    return "usage: /state set <domain> <key> <value> | /state get <domain> <key>"


def _handle_memory(harness: Harness, args: list[str], channel: str) -> str:
    if not args:
        return "usage: /memory add <texte> | /memory list [requête]"
    if args[0] == "add" and len(args) >= 2:
        entry = harness.create_memory(" ".join(args[1:]), channel=channel)
        return f"memory: {entry.id} écrite ({entry.lifecycle.value})"
    if args[0] == "list":
        query = " ".join(args[1:]) if len(args) > 1 else ""
        hits = harness.list_memory(channel=channel, query=query)
        if not hits:
            return "memory: aucune entrée"
        return "\n".join(f"  - [{e.id}] {e.content!r} ({e.lifecycle.value})" for e in hits)
    return "usage: /memory add <texte> | /memory list [requête]"


_PRIORITY_WORDS = {"low", "normal", "high", "critical"}


def _handle_tasks(harness: Harness, args: list[str], channel: str, session_id: str) -> str:
    if not args:
        return "usage: /tasks create <objectif> [low|normal|high|critical] | list | inspect <id> | pause|resume|cancel <id>"
    if args[0] == "create" and len(args) >= 2:
        rest = args[1:]
        priority = harness.priority_from_name("normal")
        if rest and rest[-1].lower() in _PRIORITY_WORDS:
            priority = harness.priority_from_name(rest[-1])
            rest = rest[:-1]
        if not rest:
            return "usage: /tasks create <objectif> [low|normal|high|critical]"
        task = harness.start_background_task(" ".join(rest), channel=channel, session_id=session_id, priority=priority)
        return f"task: {task.id} créée et soumise au scheduler (priority={harness.priority_to_name(priority)}, state={task.state.value})"
    if args[0] == "list":
        tasks = harness.list_tasks()
        if not tasks:
            return "tasks: aucune"
        return "\n".join(
            f"  - [{t.id}] {t.objective!r} state={t.state.value} priority={harness.priority_to_name(t.priority)}"
            f" progress={t.progress.percent}" for t in tasks
        )
    if args[0] == "inspect" and len(args) >= 2:
        task = None
        for t in harness.list_tasks():
            if t.id == args[1]:
                task = t
                break
        if task is None:
            return f"tasks: {args[1]} inconnu"
        return (
            f"task {task.id}\n  objective={task.objective!r}\n  state={task.state.value}\n"
            f"  priority={harness.priority_to_name(task.priority)}\n  progress={task.progress.current_step} ({task.progress.percent})\n"
            f"  checkpoint={task.checkpoint}\n  cancellation_requested={task.cancellation_requested}\n"
            f"  owner={task.owner.channel}/{task.owner.session_id}"
        )
    if args[0] in ("pause", "resume", "cancel") and len(args) >= 2:
        method = {"pause": harness.pause_task, "resume": harness.resume_task, "cancel": harness.cancel_task}[args[0]]
        try:
            task = method(args[1])
            return f"task: {task.id} -> {task.state.value}" + (
                " (cancellation demandée, finalisation par le scheduler)"
                if args[0] == "cancel" and task.state.value == "RUNNING" else ""
            )
        except (KeyError, ValueError) as exc:
            return f"task error: {exc}"
    return "usage: /tasks create <objectif> [low|normal|high|critical] | list | inspect <id> | pause|resume|cancel <id>"


def _handle_context(harness: Harness, session_id: str) -> str:
    ctx = harness.last_context(session_id)
    if ctx is None:
        return "context: aucun contexte assemblé pour l'instant (envoie un message d'abord)"
    lines = [f"context: budget={ctx.budget_tokens} used={ctx.used_tokens_estimate}"]
    for s in ctx.sections:
        lines.append(f"  - [{s.kind.value}] provenance={s.provenance} rank={s.rank_score:.2f}")
    return "\n".join(lines)


def _handle_events(event_store: object | None, limit: int = 10) -> str:
    if event_store is None:
        return "events: EventStore non disponible"
    events = event_store.recent(limit)
    if not events:
        return "events: aucun"
    return "\n".join(f"  - {e.timestamp} {e.type} (source={e.source})" for e in events)


def _handle_attention(harness: Harness, limit: int = 10) -> str:
    decisions = harness.recent_attention_decisions(limit)
    if not decisions:
        return "attention: aucune décision récente"
    lines = []
    for d in decisions:
        lines.append(
            f"  - [{d.get('decision')}] {d.get('source_event_type')} task={d.get('task_id')} — {d.get('reasoning')}"
        )
    return "\n".join(lines)


def run(harness: Harness, bus: EventBus, session_id: str = "cli-session-1", event_store: object | None = None) -> None:
    channel = "cli"
    # Étiquette de phase retirée ici (stabilisation pré-Phase 7 §10) — décrit
    # les commandes réellement disponibles plutôt qu'un numéro de phase qui
    # redevient faux dès la phase suivante.
    print("RAYA V2 — ONLINE")
    print("(tape 'exit' pour quitter, 'stop' pour STOP,")
    print(" '/state', '/memory', '/tasks', '/context', '/events', '/attention' pour inspecter le runtime)\n")

    while True:
        try:
            raw = input("You > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not raw:
            continue
        if raw.lower() in _EXIT_COMMANDS:
            break
        if raw.lower() in _STOP_COMMANDS:
            # Jamais safety.request_stop() en direct — toujours via un Event
            # (RAYA_V2_ARCHITECTURE_FINAL_REVIEW.md §2, invariant #21).
            bus.publish(Event(type="interface.stop_requested", source="interfaces.cli", payload={}))
            print("RAYA > [STOP publié sur l'EventBus]")
            continue

        if raw.startswith("/"):
            parts = raw[1:].split()
            command, args = (parts[0], parts[1:]) if parts else ("", [])
            if command == "state":
                print(f"RAYA > {_handle_state(harness, args)}")
            elif command == "memory":
                print(f"RAYA > {_handle_memory(harness, args, channel)}")
            elif command == "tasks":
                print(f"RAYA > {_handle_tasks(harness, args, channel, session_id)}")
            elif command == "context":
                print(f"RAYA > {_handle_context(harness, session_id)}")
            elif command == "events":
                print(f"RAYA > {_handle_events(event_store)}")
            elif command == "attention":
                print(f"RAYA > {_handle_attention(harness)}")
            else:
                print("RAYA > commande inconnue (/state /memory /tasks /context /events /attention /stop)")
            continue

        # RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.14 : c'est l'interface qui émet
        # interface.request_received (pas le Harness) — déclenche Attention,
        # sans jamais retarder ni conditionner la réponse synchrone ci-dessous.
        bus.publish(Event(
            type="interface.request_received", source="interfaces.cli",
            correlation_id=None, payload={"channel": "cli", "session_id": session_id},
        ))

        request = _build_request(session_id, raw)
        state = harness.handle_request(request)
        response = harness.response_text(session_id)
        print(f"RAYA > {response}")
        if state.error is not None:
            print(f"        (state={state.status.value}, error={state.error.code})")
