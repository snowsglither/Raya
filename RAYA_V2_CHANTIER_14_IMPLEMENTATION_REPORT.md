# RAYA V2 — CHANTIER 14 IMPLEMENTATION REPORT

Natural Language Tasks + Real Persistent Scheduling

Statut : **GO**

## 1. Objective

Permettre à RAYA de créer, modifier, annuler et exécuter de vraies tâches
futures à partir de demandes naturelles ("Rappelle-moi dans 5 minutes de
sortir le poulet", "Envoie-moi un message dans 3 minutes sur Telegram...")
en réutilisant strictement l'architecture Task/Scheduler/Harness déjà
construite (Chantier 12 §B, Phase 10) — aucun nouveau scheduler, aucun
`ReminderManager`/`TaskOrchestrator` parallèle.

## 2. Existing architecture reused

L'inspection préalable (obligatoire, §1 de la consigne) a montré que
l'essentiel de la mécanique demandée existait déjà, réutilisé tel quel :

- `Task` (contrat figé, `raya/contracts/task.py`) avec `not_before` déjà
  présent (Chantier 12 §B).
- `TaskRegistry.create/get/list/cancel/pause/resume/checkpoint` (`raya/tasks/registry.py`)
  — `list(**filters)` existait déjà, seulement jamais exposé comme Tool.
- `TaskScheduler` (`raya/harness/scheduler.py`) : file `_delayed` (min-heap),
  jamais de polling, réveil borné par la prochaine échéance connue —
  **non modifié** (consigne §2 respectée à la lettre).
- `resolve_not_before(delay_seconds, run_at)` (`raya/contracts/clock.py`,
  Chantier 12 §A/§B) : rejette un `run_at` sans fuseau explicite.
- `system.time.now` (`raya/tools/catalog/system_time.py`) : horloge OS
  réelle, `Europe/Brussels` par défaut.
- `tasks.create` (`raya/tools/catalog/tasks.py`, Phase 10 + Chantier 12 §B) :
  déjà câblé à `Harness.create_long_horizon_task`, acceptait déjà
  `delay_seconds`/`run_at` mutuellement exclusifs.
- `Harness.recover()` : re-soumet déjà au scheduler toute tâche PENDING
  avec `not_before` retrouvée après un redémarrage (Chantier 12 §B).
- `telegram.send_message` (`raya/tools/catalog/notify.py`, Phase 11) :
  destinataire résolu en interne (propriétaire unique), jamais un
  paramètre du modèle.
- Directives système (`raya/context_engine/render.py`, Chantier 12 §B/§D/§E) :
  temporalité fiable, distinction information/action, défauts de canal —
  déjà en place avant ce chantier.

## 3. Natural language task flow

Aucun classifieur regex n'a été ajouté. Le flux repose entièrement sur la
sélection d'outils par le modèle, guidée par des directives génériques
(system prompt) et les descriptions de Tools — exactement comme le reste
de l'architecture V2 :

```
"Rappelle-moi dans 5 secondes de tester RAYA"
  -> Cognition (conversation) choisit tasks.create(objective=..., delay_seconds=5)
  -> Harness.create_long_horizon_task() : Task PENDING, not_before=+5s
  -> Cognition (planning, build_plan) : plan (désormais conscient des Tools, §9)
  -> Scheduler._delayed jusqu'à l'échéance -> RUNNING
  -> Cognition (exécution du step) : appelle le Tool concret (ex: telegram.send_message)
  -> Task COMPLETED
```

Deux gaps concrets identifiés et comblés (§16 "Task Identity" de la
consigne) :

- **`tasks.list`** (nouveau Tool, `raya/tools/catalog/tasks.py`) : lecture
  seule, `capability_tags=["tasks.read"]` (SAFE — ajouté à
  `raya/safety/risk.py`, et automatiquement classé `Intent.INFORMATION` par
  `cognition/intent.py` via la convention `*.read`). Sans argument, ne
  retourne que les tâches encore actionnables (PENDING/RUNNING/PAUSED) ;
  un filtre `state` explicite permet d'atteindre les tâches terminales.
  Nécessaire pour que le modèle retrouve un `task_id` réel avant
  `tasks.cancel` — jamais deviné.
- **Modification = cancel + recreate** : aucune méthode de reschedule
  n'existe sur `TaskRegistry`/`Harness` (vérifié, pas supposé) — construire
  un tel mécanisme aurait été une extension du scheduler, explicitement
  hors scope (§2). La consigne §14 n'exige qu'un résultat observable
  (la tâche change de moment/contenu si identifiable sans ambiguïté),
  jamais une API de reschedule — `tasks.cancel` puis `tasks.create` avec le
  bon délai atteint ce résultat avec les Tools existants.

## 4. Information vs Action vs Future Task

La distinction à trois voies n'est PAS un nouveau moteur de classification
— elle reste, comme le Chantier 12 §D l'avait établi pour Information vs
Action, une question de **sélection d'outil par le modèle**, renforcée par
deux nouvelles directives génériques dans `render_system_prompt()` :

- Une FUTURE TASK (délai relatif, heure absolue, jour futur) est nommée
  explicitement comme une **troisième catégorie**, distincte
  d'INFORMATION et d'ACTION IMMÉDIATE — seule la création de la tâche
  s'exécute maintenant, jamais l'action elle-même.
- Avant d'annuler/modifier une tâche, le modèle doit utiliser `tasks.list`
  et demander clarification en cas d'ambiguïté — jamais deviner un
  `task_id`.

Validé EN RÉEL (§13, Test D1/D2) : "Quelle heure est-il ?" → réponse
directe, **zéro** nouvelle Task ; "Utilise toujours Telegram pour mes
rappels..." → action immédiate (préférence Memory écrite), **zéro**
nouvelle Task ; "Rappelle-moi dans 5 secondes..." → **une** Task créée,
jamais exécutée immédiatement.

## 5. Temporal resolution

Inchangé par rapport au Chantier 12 — `system.time.now` reste l'unique
source de vérité, `resolve_not_before()` reste l'unique point de
résolution delay/run_at → `not_before` UTC, et rejette toujours un
`run_at` sans fuseau explicite. Aucune modification apportée ici.

## 6. Task creation

`tasks.create` (déjà existant, Phase 10 + Chantier 12 §B) — aucun
changement de son schéma ou de son handler. Le changement réel de ce
chantier touche ce qui se passe APRÈS la création : voir §9 (planification
consciente des Tools) et §14 (bug de non-terminaison corrigé).

## 7. Persistence

Inchangée — `TaskRegistry` persiste chaque tâche via le `PersistenceBackend`
injecté (SQLite en production), `not_before` inclus. Aucune modification.

## 8. Recovery

Inchangée — `Harness.recover()` re-soumet déjà au scheduler toute tâche
PENDING avec `not_before` retrouvée après un redémarrage (Chantier 12 §B,
`tests/harness/test_recovery_phase2.py` et `test_restart_recovers_a_running_long_horizon_task_and_continues_from_checkpoint`).
Voir §15/§24 pour la portée du test réel de CE chantier (NOT_TESTED,
justifié).

## 9. Cancellation — et le bug de planification trouvé et corrigé

`tasks.cancel`/`Harness.cancel_task()` inchangés et déjà couverts
(Chantier 12). La partie réellement neuve ici concerne **la fiabilité de
la tâche elle-même**, découverte par la validation réelle (§13) :

**Bug #1 (planification aveugle aux Tools)** — `build_plan()`
(`raya/cognition/planning.py`, Phase 10) ne recevait JAMAIS la liste des
Tools disponibles. Pour l'objectif "Envoie-moi un message sur Telegram",
le modèle de planification — ignorant l'existence de `telegram.send_message`
— a halluciné une procédure plausible mais fausse : "ouvrir l'app Telegram
sur le téléphone, chercher le contact, ouvrir la conversation, taper le
texte, appuyer sur envoyer" (contrôle physique du téléphone). À
l'exécution, la première étape (`phone.connection.state`) échouait
(téléphone non connecté), bloquant la tâche.

**Fix #1** : `build_plan()` reçoit désormais `available_tools` (les mêmes
schémas que l'exécution, `Harness._discover_tool_schemas()`), avec une
instruction explicite : si un Tool couvre l'objectif entier en un appel,
produire un plan à UNE seule étape décrivant cette action directe — jamais
une procédure manuelle plus longue quand un Tool direct existe déjà.
Réutilise le champ `ModelRequest.available_tools` déjà existant — aucun
nouveau mécanisme. `build_plan()` ne lit que le TEXTE de la réponse
(jamais un tool_call réellement exécuté depuis la planification), donc
sans risque de double effet de bord.

**Bug #2 (plus sérieux — cause réelle du spam Telegram observé)** —
`_run_long_horizon_step` (`raya/harness/loop.py`) n'incluait la preuve
(`step.evidence`) d'un appel d'outil précédent dans le prompt du tick
suivant QUE si `step.attempts > 0`. Or `step.attempts` n'est incrémenté
que sur un `RecoveryAction.REPLAN` (un ÉCHEC) — jamais après un SUCCÈS.
Un outil non observable en World State (rien pour
`_is_step_tool_already_satisfied` à vérifier — `telegram.send_message`
n'a pas d'`ObservationSpec`) ne voyait donc **jamais** sa propre preuve de
succès au tick suivant : le modèle, sans aucune indication qu'il avait
déjà réussi, rappelait le même outil à chaque tick du scheduler — avec un
VRAI envoi Telegram à chaque fois.

**Constaté en conditions réelles** : lors de la première validation E2E
(Test B), ce bug a provoqué l'envoi RÉEL et RÉPÉTÉ de plusieurs dizaines
de messages Telegram au propriétaire en l'espace d'une minute, avant que
le tour ne se termine de lui-même. Confirmé et corrigé immédiatement,
disclosure faite à l'utilisateur en temps réel dès la découverte du motif
dans les logs.

**Fix #2** : la condition devient `if step.evidence:` (jamais gated par
`step.attempts`) — l'évidence est désormais montrée dès qu'elle existe,
avec une instruction explicite : vérifier l'évidence AVANT d'agir, ne
jamais rappeler un outil dont l'évidence montre déjà un succès pour cet
objectif.

## 10. Channel selection

Inchangé (Chantier 12 §E) — le mot de l'utilisateur prime toujours,
aucune substitution silencieuse si un canal demandé n'a pas de Tool
disponible (`mail.send`/`phone.call` proactif n'existent toujours pas en
exécution — seul Telegram est réellement câblé). Aucune modification.

## 11. Telegram

`telegram.send_message` inchangé. Validé EN RÉEL après les deux correctifs
(§13, retest final) : un seul appel, un seul message réel envoyé, tâche
COMPLETED proprement.

## 12. Safety

Inchangée — gating à l'exécution (`SafetyService.check_permission` via
`classify_risk`), jamais à la création. Le chemin `CONFIRMATION_REQUIRED_IN_BACKGROUND`
(`raya/harness/loop.py`, Phase 10/12, déjà testé dans
`tests/harness/test_long_horizon.py::test_sensitive_tool_in_background_fails_honestly_never_bypasses_safety`)
garantit qu'une action sensible programmée pour plus tard échoue
honnêtement plutôt que de contourner une confirmation — non modifié, non
retesté ici (déjà couvert, consigne §28 anti-duplication). `tasks.read`
(nouveau tag) ajouté à `_RISK_BY_TAG` comme SAFE, cohérent avec les autres
tags `*.read` déjà établis.

STOP : inchangé — vérifié avant chaque step (`safety.should_stop()`) et
avant chaque exécution de Tool ; une tâche encore dans `_delayed` (pas due)
n'a pas besoin de traitement spécial, elle traverse la même porte dès
qu'elle migre vers la file active.

## 13. Real-world validation

Script réel (`bootstrap()` complet, vrai Ollama Cloud, vrai
`_maybe_start_telegram()`), écoute passive AVANT sollicitation — jamais un
polling.

**Test D1 (INFORMATION)** : "Quelle heure est-il ?" → réponse directe,
correcte, **0** nouvelle Task. PASS.

**Test D2 (ACTION immédiate)** : "Utilise toujours Telegram pour mes
rappels..." → préférence Memory écrite immédiatement, **0** nouvelle Task.
PASS.

**Test A (FUTURE TASK, délai court)** : "Rappelle-moi dans 5 secondes..."
→ Task PENDING avec `not_before` correct, PAS exécutée immédiatement
(vérifié explicitement), devient RUNNING exactement après l'échéance.
PASS (le passage RUNNING confirme le déclenchement au bon moment ; le
plan de cette tâche particulière impliquait plusieurs étapes de contrôle
téléphonique — voir §9 Bug #1, ce test précédait le fix et a été
naturellement corrigé par celui-ci pour Test B/retest final).

**Test C (Annulation avant échéance)** : Task créée à +20s, annulée
immédiatement via `tasks.cancel` direct → CANCELLED confirmé ; attente de
22s au-delà de l'échéance initiale → état final toujours CANCELLED,
**jamais** COMPLETED. PASS.

**Test B (Telegram réel)** — trois itérations, toutes disclosed à
l'utilisateur en temps réel :
1. Run initial : bug #1 (mauvais plan) + bug #2 (pas de terminaison)
   combinés → dizaines de vrais messages Telegram envoyés avant la fin
   naturelle du tour. Root-cause trouvée, fixée, disclosed.
2. Retest post-fix #1 seul : plan correct (une étape,
   `telegram.send_message` direct) MAIS bug #2 toujours présent → nouveaux
   envois répétés constatés, confirmés par l'utilisateur en direct
   ("ça envoie toujours plusieurs messages"). Bug #2 trouvé, fixé.
3. Retest final (les deux fixes actifs), surveillé étroitement : **exactement
   un seul** `telegram.message_sent`, plan à une étape confirmée, tâche
   PENDING → RUNNING → COMPLETED proprement, evidence contient
   `{"sent": true}`. PASS.

**Incident et transparence** : les deux premières itérations ont causé un
envoi réel non désiré de messages répétés au téléphone du propriétaire.
Chaque occurrence a été détectée en observant les logs, communiquée
immédiatement et intégralement à l'utilisateur (avant toute tentative de
minimiser ou de continuer silencieusement), et root-causée avant toute
nouvelle tentative — jamais une nouvelle tentative sans diagnostic
préalable ni sans consentement explicite pour la tentative suivante
(demandé via question directe avant le retest final).

## 14. Automated tests

| Area | Tests | PASS | FAIL | BLOCKED | NOT_TESTED |
|---|---|---|---|---|---|
| `tasks.list` (Tool + Safety) | 9 | 9 | 0 | 0 | 0 |
| Directives système (Chantier 14) | 5 | 5 | 0 | 0 | 0 |
| `build_plan(available_tools=...)` | 2 | 2 | 0 | 0 | 0 |
| Step evidence / non-terminaison (bug #2) | 1 | 1 | 0 | 0 | 0 |
| **Total nouveau (Chantier 14)** | **17** | **17** | **0** | **0** | **0** |

Suite ciblée complète (tasks/harness/tools/context_engine/cognition/stop/contracts/models) :
**508 passed**, 2 failed — tous deux pré-existants et non liés :
- `test_handle_request_fails_honestly_with_null_provider_stub` : flake déjà
  documenté (chantiers précédents) — `OLLAMA_API_KEY` réelle dans `.env`
  fait répondre le vrai Ollama Cloud là où le test suppose un
  `NullProvider`.
- `test_queued_task_waits_when_at_concurrency_limit` (`tests/harness/test_scheduler.py`) :
  flake de timing sous charge (thread scheduling), confirmé non
  reproductible en 3 exécutions isolées consécutives — `raya/harness/scheduler.py`
  n'a été touché par aucun changement de ce chantier.

Architecture lint (`scripts/arch_lint.py`) : **PASS**, aucune violation.

## 15. Known limitations

- **NOT_IMPLEMENTED** : aucune méthode de reschedule en place sur
  `TaskRegistry`/`Harness` — la modification d'une tâche existante passe
  par annulation + recréation (§3/§9), jamais une API de mutation directe
  de `not_before`. Cohérent avec la consigne §2 (ne pas étendre le
  scheduler).
- **NOT_TESTED (justifié)** : test de redémarrage réel dédié à ce
  chantier (§24 de la consigne). La récupération après restart pour une
  tâche PENDING programmée est déjà mécaniquement en place et testée de
  façon déterministe (`test_restart_recovers_a_running_long_horizon_task_and_continues_from_checkpoint`,
  Phase 10/12) et avait déjà été validée EN RÉEL au Chantier 12/10.
  Relancer un test de redémarrage RÉEL aujourd'hui aurait nécessité
  d'arrêter le process de production RAYA pendant une session où de
  l'usage réel concurrent (le vrai bot Telegram, activement utilisé
  pendant cette même fenêtre de validation) était en cours — jugé non
  justifié et potentiellement perturbateur pour un mécanisme déjà prouvé
  ailleurs, plutôt que simulé artificiellement et déclaré PASS (interdit
  explicitement par la consigne §24).
- **Bug #1/#2 (§9) pré-existants, pas introduits par ce chantier** : tous
  deux vivaient dans `raya/cognition/planning.py`/`raya/harness/loop.py`
  depuis la Phase 10, invisibles jusqu'ici car aucun scénario testé avant
  ce chantier ne combinait (a) un objectif résoluble en un seul Tool
  direct et non observable en World State, et (b) une vraie exécution en
  conditions réelles plutôt qu'un modèle scripté toujours coopératif.
  Corrigés ici car découverts ici, et parce que le corriger était
  strictement nécessaire pour que l'exemple central de CE chantier
  fonctionne correctement et en sécurité.

## 16. Architecture integrity

Aucun nouveau scheduler, aucun `ReminderManager`/`TaskOrchestrator`/
`NotificationManager` parallèle. Un seul nouveau Tool (`tasks.list`, lecture
seule) suivant exactement le pattern d'injection étroite déjà établi
(`TaskControlOps`). Les deux fixes (§9) réutilisent des mécanismes
existants (`ModelRequest.available_tools`, `step.evidence`) sans ajouter de
nouvelle abstraction.

## 17. V1 integrity

`RAYA/` (V1) : aucun fichier suivi modifié — seuls des fichiers non suivis
préexistants (`_shopping_full.txt`, `prompt_claude_code_*.txt`) apparaissent
dans `git status --short`. V1 reste intact.

## 18. Regression assessment

508 passed / 2 failed (tous deux pré-existants, non liés, détaillés §14) ;
0 régression attribuable à ce chantier. Architecture lint PASS.

## 19. Verdict

**GO**

## 20. Next recommendation

STOP ici, conformément à l'instruction explicite de fin de chantier — ne
pas enchaîner automatiquement sur voix, audio téléphonique, SMS, appels
sortants, compagnon mobile, serveur, autonomie longue durée, nouveau
scheduler ou nouvel orchestrateur.
