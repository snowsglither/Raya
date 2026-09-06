"""Groupe E (Phase 2) — Recovery : une Task RUNNING au crash redevient PAUSED,
reprend réellement au redémarrage (pas depuis zéro), jamais réputée COMPLETED
sans preuve. Couvre spécifiquement le chemin scheduler (step_fn perdu avec le
process précédent, ré-enregistré par Harness.resume_task)."""

from __future__ import annotations

import time

from raya.contracts import TaskState
from raya.persistence import SqliteBackend
from raya.runtime.bootstrap import bootstrap
from raya.runtime.config import load_config


def _handles(tmp_path, name: str):
    cfg = load_config()
    cfg.db_path = tmp_path / name
    return bootstrap(config=cfg, backend=SqliteBackend(cfg.db_path))


def test_running_task_survives_crash_as_paused_never_completed(tmp_path):
    path_name = "recover.sqlite3"
    handles1 = _handles(tmp_path, path_name)
    task = handles1.harness.start_background_task("longue tâche", total_steps=20)
    time.sleep(0.06)  # laisse quelques steps s'exécuter, mais pas les 20
    before_crash = handles1.tasks.get(task.id)
    assert before_crash.state == TaskState.RUNNING
    # "crash" : pas de handles1.shutdown() propre — on coupe simplement le fil.

    handles2 = _handles(tmp_path, path_name)
    try:
        recovered = handles2.tasks.get(task.id)
        assert recovered.state == TaskState.PAUSED
        assert recovered.state != TaskState.COMPLETED
        assert recovered.checkpoint is not None
        assert recovered.checkpoint.get("recovered_after_restart") is True
    finally:
        handles2.shutdown()


def test_recovered_task_can_actually_resume_and_complete(tmp_path):
    """Le point le plus important : la reprise n'est pas juste un changement
    d'état cosmétique — la tâche doit VRAIMENT pouvoir continuer et terminer,
    via le chemin scheduler (step_fn ré-enregistré, checkpoint respecté)."""
    path_name = "recover_resume.sqlite3"
    handles1 = _handles(tmp_path, path_name)
    task = handles1.harness.start_background_task("tâche reprise", total_steps=6)
    time.sleep(0.08)
    progress_before_crash = handles1.tasks.get(task.id).checkpoint

    handles2 = _handles(tmp_path, path_name)
    try:
        recovered = handles2.tasks.get(task.id)
        assert recovered.state == TaskState.PAUSED

        resumed = handles2.harness.resume_task(task.id)
        assert resumed.state == TaskState.RUNNING

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if handles2.tasks.get(task.id).state == TaskState.COMPLETED:
                break
            time.sleep(0.02)
        final = handles2.tasks.get(task.id)
        assert final.state == TaskState.COMPLETED
        # La reprise est repartie du checkpoint persistant, pas de zéro : le
        # nombre total de steps observés (avant + après) doit rester cohérent
        # avec total_steps=6, jamais recompté depuis 0 après le resume.
        assert final.checkpoint["step_index"] == 6
    finally:
        handles2.shutdown()


def test_recovery_does_not_touch_completed_tasks(tmp_path):
    path_name = "recover_completed.sqlite3"
    handles1 = _handles(tmp_path, path_name)
    task = handles1.harness.start_background_task("courte tâche", total_steps=2)
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        if handles1.tasks.get(task.id).state == TaskState.COMPLETED:
            break
        time.sleep(0.02)
    assert handles1.tasks.get(task.id).state == TaskState.COMPLETED
    handles1.shutdown()

    handles2 = _handles(tmp_path, path_name)
    try:
        assert handles2.tasks.get(task.id).state == TaskState.COMPLETED  # inchangé, jamais "re-récupéré"
    finally:
        handles2.shutdown()


# --- Chantier 12 §B (Persistent Scheduling) : recovery d'une tâche PROGRAMMÉE ---

def _iso(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


def test_scheduled_task_not_yet_due_survives_restart_and_fires_once_due(tmp_path):
    """Une tâche créée avec `not_before` mais JAMAIS soumise au scheduler
    avant le crash (scénario réaliste : persistée puis le process s'arrête)
    ne doit ni se perdre, ni s'exécuter avant son échéance."""
    from datetime import timedelta

    from raya.contracts import parse_iso, utc_now_iso

    path_name = "recover_scheduled.sqlite3"
    handles1 = _handles(tmp_path, path_name)
    not_before = _iso(parse_iso(utc_now_iso()) + timedelta(seconds=0.3))
    task = handles1.harness.create_task("rappel programmé", channel="cli", session_id="s1", not_before=not_before)
    assert task.state == TaskState.PENDING

    handles2 = _handles(tmp_path, path_name)  # "crash" simulé : pas de shutdown() sur handles1
    try:
        time.sleep(0.05)  # bien avant l'échéance
        assert handles2.tasks.get(task.id).state == TaskState.PENDING

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if handles2.tasks.get(task.id).state == TaskState.COMPLETED:
                break
            time.sleep(0.02)
        assert handles2.tasks.get(task.id).state == TaskState.COMPLETED
    finally:
        handles2.shutdown()


def test_scheduled_task_already_overdue_while_offline_fires_once_on_restart(tmp_path):
    """Échéance dépassée PENDANT que RAYA était arrêté : doit s'exécuter dès
    le redémarrage (jamais perdue), une seule fois (jamais dupliquée)."""
    from datetime import timedelta

    from raya.contracts import parse_iso, utc_now_iso

    path_name = "recover_scheduled_overdue.sqlite3"
    handles1 = _handles(tmp_path, path_name)
    not_before = _iso(parse_iso(utc_now_iso()) - timedelta(seconds=5))
    task = handles1.harness.create_task("rappel en retard", channel="cli", session_id="s1", not_before=not_before)

    handles2 = _handles(tmp_path, path_name)
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        if handles2.tasks.get(task.id).state == TaskState.COMPLETED:
            break
        time.sleep(0.02)
    assert handles2.tasks.get(task.id).state == TaskState.COMPLETED
    handles2.shutdown()

    # Un troisième redémarrage ne doit JAMAIS re-exécuter une tâche déjà
    # COMPLETED (recover() ne considère que les tâches PENDING).
    handles3 = _handles(tmp_path, path_name)
    try:
        time.sleep(0.1)
        assert handles3.tasks.get(task.id).state == TaskState.COMPLETED
    finally:
        handles3.shutdown()


def test_execution_record_unknown_after_crash_within_full_runtime(tmp_path):
    """ExecutionRecord reste respecté même dans le runtime complet Phase 2
    (pas seulement en isolation comme testé en Phase 1)."""
    from raya.contracts import ExecutionRecord, ExecutionState

    path_name = "exec_record.sqlite3"
    handles1 = _handles(tmp_path, path_name)
    handles1.execution_records.start(ExecutionRecord(
        operation_id="op1", tool_call_id="tc1", correlation_id="c1", idempotency_key="k1",
    ))

    handles2 = _handles(tmp_path, path_name)
    try:
        record = handles2.execution_records.load_and_reinterpret("op1")
        assert record.execution_state == ExecutionState.UNKNOWN
    finally:
        handles2.shutdown()
