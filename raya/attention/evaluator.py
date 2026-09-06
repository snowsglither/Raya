"""Attention.evaluate() (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.4, §7 ; consigne
Phase 2 §3-§9, §22-§26).

RÈGLE ABSOLUE (consigne Phase 2 §3) : Attention n'est PAS un second Harness.
- Aucun appel modèle, aucune exécution d'outil, aucun device.
- Aucune modification directe d'une Task (elle ne fait QUE lire World State/Tasks).
- Elle ne contacte jamais l'utilisateur — elle produit une AttentionDecision,
  publiée sur l'EventBus ; c'est au Harness d'agir dessus.

`evaluate()` reste une fonction PURE testable en isolation (aucun I/O, aucun
accès bus) — c'est `AttentionEngine` (classe ci-dessous) qui s'abonne à
l'EventBus et republie la décision comme "attention.decision_made".
"""

from __future__ import annotations

import threading
import time

from raya.contracts import AttentionDecision, AttentionFactors, AttentionOutcome, Event, TaskState, to_dict
from raya.event_bus import EventBus
from raya.tasks import TaskRegistry
from raya.tasks import priority as prio
from raya.world_state import WorldStateStore

from .policy import FocusTracker

# Événements sur lesquels Attention est abonnée — jamais "*" (consigne §5/§46 :
# "ne doit pas parcourir arbitrairement toute la base"). Uniquement ce qui peut
# légitimement mériter une décision d'attention.
SUBSCRIBED_PATTERNS = ("interface.request_received", "task.*", "safety.*", "perception.*")

# Anti-spam (consigne §49) : un événement task.progress n'est BACKGROUND que
# lorsqu'il franchit un palier non encore signalé pour cette tâche — tout le
# reste (les ticks intermédiaires) est IGNORE.
_PROGRESS_MILESTONES = (25, 50, 75, 100)

_ROUTINE_TASK_EVENTS = {"task.created", "task.ready", "task.started", "task.checkpoint"}
_INFORMATIONAL_TASK_EVENTS = {"task.paused", "task.resumed", "task.cancelled"}

# Chantier 13B (Event-Driven Phone Awareness) : SEUL sous-type de
# perception.* qui mérite mieux que le IGNORE générique de routine
# (_decide_perception_event) — une activité téléphonique réelle doit pouvoir
# réveiller RAYA même à l'idle complet, contrairement à un changement de
# fenêtre active. Jamais une décision d'AGIR (Attention ne décide jamais
# COMMENT, §3 du module) — seulement que ça MÉRITE d'être remonté.
_PHONE_ACTIVITY_EVENT_TYPE = "perception.phone_call_activity"

# Chantier 13G : signal FIABLE pour le CONTENU d'un appel entrant (bannière
# Windows réelle, ShellExperienceHost.exe — voir investigation Chantier
# 13F) — distinct de `_PHONE_ACTIVITY_EVENT_TYPE` ci-dessus (13D,
# PhoneExperienceHost.exe, jamais considéré fiable pour le contenu). Les
# deux restent gérés séparément, jamais fusionnés (des formes de valeur
# différentes, une confusion romprait la déduplication de l'un ou l'autre).
_INCOMING_CALL_NOTIFICATION_EVENT_TYPE = "perception.incoming_call_notification"


class AttentionEvaluator:
    """Logique déterministe pure — un event en entrée, une AttentionDecision
    en sortie. Pas de dépendance à Harness/Models/Tools/Devices/Interfaces
    (vérifié par le lint architectural)."""

    def __init__(self, world_state: WorldStateStore, tasks: TaskRegistry, focus: FocusTracker) -> None:
        self._world_state = world_state
        self._tasks = tasks
        self._focus = focus
        self._notified_milestones: dict[str, set[int]] = {}
        # Chantier 13B : déduplication d'activité téléphonique — évite de
        # ré-émettre un INTERRUPT pour le MÊME appel à chaque tick de poll
        # (le capteur peut publier plusieurs events tant que l'état varie
        # légèrement, ex: le nom du dernier appel loggé qui se stabilise).
        self._seen_phone_activity_keys: set[str] = set()
        # Chantier 13G : déduplication SÉPARÉE pour les notifications
        # d'appel entrant réelles (contenu structuré, pas juste "activité").
        self._seen_incoming_call_keys: set[str] = set()
        self._lock = threading.Lock()

    def evaluate(self, event: Event) -> AttentionDecision:
        # RAYA_V2_CONTRACTS.md §6 : TaskEvent.payload est un TaskEventPayload
        # (dataclass typé), pas un dict brut comme Event.payload générique —
        # les deux flottent sur le même EventBus (duck typing structurel).
        # Normaliser ICI, une seule fois, permet à tout le reste du fichier de
        # supposer un dict simple sans jamais deviner le type réel de payload.
        normalized_payload = to_dict(event.payload) if not isinstance(event.payload, dict) else event.payload

        if event.type == "interface.request_received":
            return self._decide_interface_request(event)
        if event.type.startswith("task."):
            return self._decide_task_event(event, normalized_payload)
        if event.type.startswith("safety."):
            return self._decide_safety_event(event)
        if event.type.startswith("perception."):
            return self._decide_perception_event(event)
        return AttentionDecision(
            event_ref=event.id,
            decision=AttentionOutcome.BACKGROUND,
            reasoning=f"type d'event non catégorisé explicitement : {event.type!r}",
            factors=AttentionFactors(confidence=0.3),
        )

    # ------------------------------------------------------------------

    def _decide_interface_request(self, event: Event) -> AttentionDecision:
        # §7 : une demande utilisateur directe a toujours priorité élevée —
        # jamais ignorée, jamais reléguée en fond (conversation != task execution).
        return AttentionDecision(
            event_ref=event.id,
            decision=AttentionOutcome.PROCESS_NOW,
            reasoning="demande utilisateur directe — toujours traitée immédiatement (§7)",
            factors=AttentionFactors(urgency=0.8, importance=0.9, novelty=0.5, confidence=1.0, user_relevance=1.0),
        )

    def _decide_perception_event(self, event: Event) -> AttentionDecision:
        # Chantier 13B : SEULE exception au IGNORE de routine ci-dessous — une
        # activité téléphonique réelle (appel en cours/nouvelle entrée
        # d'historique) mérite de réveiller RAYA même à l'idle complet,
        # contrairement à un changement de fenêtre active.
        if event.type == _PHONE_ACTIVITY_EVENT_TYPE:
            return self._decide_phone_call_activity(event)
        if event.type == _INCOMING_CALL_NOTIFICATION_EVENT_TYPE:
            return self._decide_incoming_call_notification(event)

        # Consigne Phase 7 §10 : une observation environnementale de routine
        # (fenêtre active changée, etc.) ne mérite JAMAIS, par défaut,
        # d'interrompre l'utilisateur — elle reste disponible à la demande
        # via World State/ContextEngine, jamais poussée en interruption.
        return AttentionDecision(
            event_ref=event.id, decision=AttentionOutcome.IGNORE,
            reasoning=f"{event.type} : observation environnementale de routine, jamais une interruption automatique",
            factors=AttentionFactors(novelty=0.1, confidence=0.9),
        )

    def _decide_phone_call_activity(self, event: Event) -> AttentionDecision:
        """Chantier 13B (Event-Driven Phone Awareness) : décide seulement que
        l'activité MÉRITE d'être remontée — jamais QUOI faire (ni répondre,
        ni raccrocher, ni rappeler — §5 : pas d'auto-réponse). Déduplique par
        clé dérivée du contenu observé (jamais un event_id/call_id — le
        capteur n'en fournit pas, cf. limitation honnête documentée dans
        perception/phone_sensors.py) pour ne pas ré-interrompre en boucle
        pour le même appel pendant que le poll tourne."""
        payload = to_dict(event.payload) if not isinstance(event.payload, dict) else event.payload
        value = payload.get("value") or {}
        dedup_key = f"{value.get('in_call')}:{value.get('latest_call_log_name')}:{value.get('latest_call_log_time')}"
        with self._lock:
            already_seen = dedup_key in self._seen_phone_activity_keys
            if not already_seen:
                self._seen_phone_activity_keys.add(dedup_key)
                if len(self._seen_phone_activity_keys) > 200:  # borne la croissance mémoire, jamais illimité
                    self._seen_phone_activity_keys.clear()
                    self._seen_phone_activity_keys.add(dedup_key)
        if already_seen:
            return AttentionDecision(
                event_ref=event.id, decision=AttentionOutcome.IGNORE,
                reasoning="activité téléphonique déjà remontée (déduplication) — pas un nouvel appel",
                factors=AttentionFactors(novelty=0.0, confidence=0.9),
            )
        caller = value.get("latest_call_log_name") or "appelant inconnu"
        return AttentionDecision(
            event_ref=event.id, decision=AttentionOutcome.INTERRUPT,
            reasoning=f"activité téléphonique réelle détectée (appelant probable : {caller}) — "
                      f"jamais une action automatique, seulement une prise de conscience (§5)",
            # confidence modérée (0.6, jamais 0.9+) : le capteur ne peut pas
            # distinguer de façon certaine "sonne encore" de "vient de se
            # terminer" — reflète honnêtement cette incertitude (voir
            # perception/phone_sensors.py).
            factors=AttentionFactors(urgency=0.7, importance=0.6, novelty=0.9, confidence=0.6, user_relevance=0.8),
        )

    def _decide_incoming_call_notification(self, event: Event) -> AttentionDecision:
        """Chantier 13G (révisé) : PERCEPTION ≠ INTERRUPTION ≠ RÉACTION. Le
        World State reçoit toujours le fait (ingestion générique, en amont
        de cette méthode) — mais Attention ne décide JAMAIS INTERRUPT par
        défaut pour un simple appel entrant : le seul fait qu'un appel
        arrive n'est pas une raison suffisante pour interrompre
        l'utilisateur (§ "PERCEPTION ≠ INTERRUPTION ≠ RÉACTION"). Aucun
        mécanisme existant (Memory/Tasks/Context) n'est aujourd'hui câblé
        jusqu'à Attention pour exprimer une préférence explicite du type
        "préviens-moi si Papa m'appelle" — cette capacité contextuelle est
        NOT_IMPLEMENTED (cf. RAYA_V2_CHANTIER_13G_IMPLEMENTATION_REPORT.md) :
        ni RuleEngine parallèle, ni heuristique sur `caller` ne sont
        construits ici. Le résultat est donc IGNORE inconditionnel, que
        `call_state` soit "incoming" ou "other". Déduplique quand même par
        le contenu structuré complet (jamais `caller` seul, consigne §9)
        pour que plusieurs events Windows bruts de la même bannière (cf.
        investigation 13F) ne produisent qu'une seule entrée distincte dans
        le journal d'attention."""
        payload = to_dict(event.payload) if not isinstance(event.payload, dict) else event.payload
        value = payload.get("value") or {}
        dedup_key = (
            f"{value.get('call_state')}:{value.get('caller')}:{value.get('toast_view_type')}:"
            f"{value.get('source_text')}:{value.get('sender_category')}"
        )
        with self._lock:
            already_seen = dedup_key in self._seen_incoming_call_keys
            if not already_seen:
                self._seen_incoming_call_keys.add(dedup_key)
                if len(self._seen_incoming_call_keys) > 200:
                    self._seen_incoming_call_keys.clear()
                    self._seen_incoming_call_keys.add(dedup_key)
        if already_seen:
            return AttentionDecision(
                event_ref=event.id, decision=AttentionOutcome.IGNORE,
                reasoning="notification d'appel déjà remontée (déduplication) — pas un nouvel événement",
                factors=AttentionFactors(novelty=0.0, confidence=0.9),
            )
        if value.get("call_state") != "incoming":
            return AttentionDecision(
                event_ref=event.id, decision=AttentionOutcome.IGNORE,
                reasoning=f"notification téléphonique observée mais pas un appel entrant en cours "
                          f"(call_state={value.get('call_state')!r}) — jamais une interruption",
                factors=AttentionFactors(novelty=0.3, confidence=0.7),
            )
        caller = value.get("caller") or "unknown"
        return AttentionDecision(
            event_ref=event.id, decision=AttentionOutcome.IGNORE,
            reasoning=f"appel entrant réel détecté via la bannière de notification Windows "
                      f"(appelant : {caller}) — observé et versé au World State, mais aucune "
                      f"justification contextuelle explicite (préférence utilisateur, tâche "
                      f"dépendante) ne fait actuellement remonter ceci jusqu'à l'utilisateur : "
                      f"le simple fait qu'un appel arrive n'est pas une raison suffisante pour "
                      f"interrompre (voir NOT_IMPLEMENTED, rapport Chantier 13G)",
            # confidence plus élevée que _decide_phone_call_activity (0.6) :
            # ce signal vient du CONTENU structuré de la bannière elle-même
            # (type de vue + boutons d'action), pas d'une simple activité
            # générique du process — voir investigation Chantier 13F/13G.
            # novelty/urgency/importance reflètent la QUALITÉ du signal perçu,
            # pas une décision d'agir dessus (celle-ci reste IGNORE).
            factors=AttentionFactors(urgency=0.2, importance=0.3, novelty=0.9, confidence=0.75, user_relevance=0.5),
        )

    def _decide_safety_event(self, event: Event) -> AttentionDecision:
        # STOP est déjà géré en direct et de façon synchrone par Safety —
        # Attention se contente d'observer, jamais de décider d'agir dessus.
        return AttentionDecision(
            event_ref=event.id,
            decision=AttentionOutcome.BACKGROUND,
            reasoning="événement safety observé — le chemin STOP réel est synchrone, hors Attention",
            factors=AttentionFactors(urgency=0.5, confidence=1.0),
        )

    def _decide_task_event(self, event: Event, payload: dict) -> AttentionDecision:
        task_id = payload.get("task_id")
        task = self._tasks.get(task_id) if task_id else None
        target_session_id = task.owner.session_id if task else None
        is_focus = bool(task and target_session_id and self._focus.get_focus(target_session_id) == task_id)
        prio_level = task.priority if task else prio.NORMAL

        if event.type == "task.progress":
            return self._decide_progress(event, payload, task_id, is_focus)

        if event.type in _ROUTINE_TASK_EVENTS:
            return AttentionDecision(
                event_ref=event.id, decision=AttentionOutcome.IGNORE,
                reasoning=f"{event.type} est un événement de routine, non exploitable par l'utilisateur",
                factors=AttentionFactors(novelty=0.1, confidence=0.9),
                target_session_id=target_session_id,
            )

        if event.type in _INFORMATIONAL_TASK_EVENTS:
            return AttentionDecision(
                event_ref=event.id, decision=AttentionOutcome.BACKGROUND,
                reasoning=f"{event.type} : informatif, ne justifie pas d'interrompre l'utilisateur",
                factors=AttentionFactors(importance=0.3, confidence=0.9),
                target_session_id=target_session_id,
            )

        if event.type == "task.recovered":
            # Un crash a interrompu une tâche : mérite d'être signalé, pas juste loggé.
            return AttentionDecision(
                event_ref=event.id, decision=AttentionOutcome.PROCESS_NOW,
                reasoning="tâche récupérée après un crash — l'utilisateur doit pouvoir agir (reprendre/annuler)",
                factors=AttentionFactors(urgency=0.6, importance=0.7, novelty=0.9, confidence=0.8, user_relevance=0.8),
                target_session_id=target_session_id,
            )

        if event.type == "task.blocked":
            # Chantier 15 (Axe D/H) : une tâche BLOCKED attend une action de
            # l'utilisateur (permission/confirmation/information) pour
            # continuer — distinct d'un simple échec (task.failed ci-dessous),
            # jamais relégué en fond comme si l'utilisateur n'avait rien à
            # faire. Toujours PROCESS_NOW, même raisonnement que
            # task.recovered ci-dessus.
            return AttentionDecision(
                event_ref=event.id, decision=AttentionOutcome.PROCESS_NOW,
                reasoning="tâche bloquée en attente d'une action utilisateur (permission/confirmation/"
                          "information manquante) — distincte d'un échec technique, l'utilisateur doit "
                          "agir pour que la tâche continue",
                factors=AttentionFactors(urgency=0.6, importance=0.7, novelty=0.9, confidence=0.85, user_relevance=0.9),
                target_session_id=target_session_id,
            )

        if event.type == "task.completed":
            user_relevance = 1.0 if is_focus else (0.6 if prio_level >= prio.HIGH else 0.3)
            decision = AttentionOutcome.PROCESS_NOW if is_focus else AttentionOutcome.BACKGROUND
            return AttentionDecision(
                event_ref=event.id, decision=decision,
                reasoning=("tâche en focus terminée — résultat probablement attendu par l'utilisateur"
                           if is_focus else "tâche terminée hors focus — signalé en fond"),
                factors=AttentionFactors(importance=0.6, novelty=0.7, confidence=0.9, user_relevance=user_relevance),
                target_session_id=target_session_id,
            )

        if event.type == "task.failed":
            if prio_level >= prio.CRITICAL:
                decision = AttentionOutcome.INTERRUPT
                reasoning = "échec d'une tâche CRITICAL — interrompt l'activité courante"
            elif prio_level >= prio.HIGH or is_focus:
                decision = AttentionOutcome.PROCESS_NOW
                reasoning = "échec d'une tâche HIGH ou en focus — remonté immédiatement, sans interrompre"
            else:
                decision = AttentionOutcome.BACKGROUND
                reasoning = "échec d'une tâche de priorité normale/basse — signalé en fond"
            return AttentionDecision(
                event_ref=event.id, decision=decision, reasoning=reasoning,
                factors=AttentionFactors(
                    urgency=0.9 if prio_level >= prio.HIGH else 0.5,
                    importance=min(1.0, prio_level / prio.CRITICAL),
                    novelty=0.8, confidence=0.85, user_relevance=1.0 if is_focus else 0.5,
                ),
                target_session_id=target_session_id,
            )

        if event.type == "task.cancel_requested":
            return AttentionDecision(
                event_ref=event.id, decision=AttentionOutcome.IGNORE,
                reasoning="signal coopératif interne, pas une information pour l'utilisateur",
                factors=AttentionFactors(novelty=0.1),
                target_session_id=target_session_id,
            )

        return AttentionDecision(
            event_ref=event.id, decision=AttentionOutcome.BACKGROUND,
            reasoning=f"événement task.* non catégorisé explicitement : {event.type!r}",
            factors=AttentionFactors(confidence=0.3),
            target_session_id=target_session_id,
        )

    def _decide_progress(self, event: Event, payload: dict, task_id: str | None, is_focus: bool) -> AttentionDecision:
        percent = (payload.get("detail") or {}).get("percent")
        if task_id is None or percent is None:
            return AttentionDecision(
                event_ref=event.id, decision=AttentionOutcome.IGNORE,
                reasoning="progress sans task_id/percent exploitable", factors=AttentionFactors(),
            )
        crossed = [m for m in _PROGRESS_MILESTONES if percent >= m]
        with self._lock:
            already = self._notified_milestones.setdefault(task_id, set())
            new_milestones = [m for m in crossed if m not in already]
            already.update(new_milestones)
        if not new_milestones:
            # §49 : la très grande majorité des ticks de progression est ignorée.
            return AttentionDecision(
                event_ref=event.id, decision=AttentionOutcome.IGNORE,
                reasoning=f"progress {percent:.0f}% — aucun nouveau palier franchi, silence délibéré",
                factors=AttentionFactors(novelty=0.05, confidence=0.9),
            )
        return AttentionDecision(
            event_ref=event.id, decision=AttentionOutcome.BACKGROUND,
            reasoning=f"progress a franchi le palier {max(new_milestones)}% — signalé une fois, en fond",
            factors=AttentionFactors(novelty=0.6, confidence=0.9, user_relevance=0.4 if not is_focus else 0.7),
        )

    def forget_task(self, task_id: str) -> None:
        """Nettoyage — appelé quand une tâche atteint un état terminal, pour
        ne pas faire grossir indéfiniment le suivi des paliers déjà notifiés."""
        with self._lock:
            self._notified_milestones.pop(task_id, None)


class AttentionEngine:
    """S'abonne à l'EventBus, appelle AttentionEvaluator.evaluate(), republie
    le résultat comme `attention.decision_made` (catalogue déjà défini en
    Phase 0, RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.4). Seul point avec effets
    de bord — la logique de décision elle-même reste dans AttentionEvaluator,
    pure et testable sans bus."""

    def __init__(self, world_state: WorldStateStore, tasks: TaskRegistry, focus: FocusTracker, bus: EventBus) -> None:
        self.evaluator = AttentionEvaluator(world_state, tasks, focus)
        self._bus = bus
        for pattern in SUBSCRIBED_PATTERNS:
            bus.subscribe(pattern, self._on_event, subscriber="attention")

    def _on_event(self, event: Event) -> None:
        decision = self.evaluator.evaluate(event)
        normalized_source_payload = to_dict(event.payload) if not isinstance(event.payload, dict) else event.payload
        payload = to_dict(decision)
        payload["source_event_type"] = event.type
        payload["task_id"] = normalized_source_payload.get("task_id")
        self._bus.publish(
            Event(type="attention.decision_made", source="attention", correlation_id=event.correlation_id, payload=payload)
        )
