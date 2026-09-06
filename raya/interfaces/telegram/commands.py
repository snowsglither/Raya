"""Commandes Telegram (RAYA V2 Phase 9, consigne §9).

Chaque commande ne fait QUE parser (rien à parser ici) et appeler l'API
PUBLIQUE du Harness — même discipline que `raya/interfaces/cli/repl.py`.
/status/help ne révèlent jamais l'architecture interne (noms d'outils,
chemins, erreurs internes) — consigne §8."""

from __future__ import annotations

from raya.harness import Harness

from .session import session_id_for_chat


def handle_start(harness: Harness) -> str:
    return (
        "Salut, je suis RAYA.\n"
        "Écris-moi normalement pour discuter ou me demander quelque chose. "
        "Commandes utiles : /help /status /stop."
    )


def handle_help(harness: Harness) -> str:
    return (
        "Je suis RAYA — la même intelligence que sur ton PC, accessible ici "
        "depuis Telegram.\n\n"
        "Tu peux :\n"
        "- me parler normalement (une question, une demande)\n"
        "- me demander d'agir sur ton PC pour les actions autorisées\n"
        "- suivre une tâche longue en cours\n\n"
        "Commandes :\n"
        "/status — état actuel de RAYA\n"
        "/stop — arrête ce que RAYA est en train de faire\n"
        "/help — ce message"
    )


def handle_status(harness: Harness, chat_id: int) -> str:
    identity = harness.active_model_identity()
    if identity.get("model"):
        model_line = f"Modèle actif : {identity['model']} (via {identity['provider']})"
    else:
        model_line = "Modèle actif : aucun (repli honnête, pas d'intelligence réelle disponible)"

    tasks = harness.list_tasks()
    active_tasks = [t for t in tasks if t.state.value in ("RUNNING", "PENDING", "PAUSED")]
    tasks_line = f"Tâches actives : {len(active_tasks)}" if active_tasks else "Tâches actives : aucune"

    stop_line = "Sécurité : STOP actif (RAYA est arrêtée)" if harness.is_stop_active() else "Sécurité : normale"

    session_id = session_id_for_chat(chat_id)
    pending = harness.session_state(session_id)
    confirm_line = None
    if pending is not None and pending.pending_confirmation is not None:
        confirm_line = "Une confirmation est en attente de ta réponse."

    lines = ["RAYA est en ligne.", model_line, tasks_line, stop_line]
    if confirm_line:
        lines.append(confirm_line)
    return "\n".join(lines)


def handle_unauthorized() -> str:
    return "Accès non autorisé."
