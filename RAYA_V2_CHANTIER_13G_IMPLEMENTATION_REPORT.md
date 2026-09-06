# RAYA V2 — Chantier 13G — Real Incoming Call Event Bridge + Contextual Attention

Statut : **GO** (avec une limitation explicitement documentée, NOT_IMPLEMENTED, §14)

## 1. Objectif

Transformer la preuve d'investigation du Chantier 13F (le contenu structuré
d'une bannière d'appel entrant Windows est lisible en UI Automation sur
`ShellExperienceHost.exe` / `Windows.UI.Core.CoreWindow`) en une capacité de
perception réelle, câblée dans le pipeline existant (capteur → EventBus →
World State → Attention → Harness), sous un principe central corrigé en
cours de chantier :

> **PERCEPTION ≠ INTERRUPTION ≠ RÉACTION.**

Un appel entrant réel doit être perçu et versé au World State, mais ne doit
**jamais**, par défaut, interrompre l'utilisateur, produire une phrase
automatique, ou déclencher une action téléphonique. Seule une justification
contextuelle explicite (préférence utilisateur, tâche dépendante) pourrait
un jour légitimer une interruption — cette capacité contextuelle n'est
**pas** construite dans ce chantier (§9, §14).

## 2. Architecture réutilisée

Aucun nouveau composant d'orchestration. Réutilisation stricte de
l'existant :

- `raya.perception.LightSensor` (ABC) + `PerceptionRuntime` (thread de poll
  unique partagé, `raya/runtime/bootstrap.py`) — aucun second thread créé.
- `raya.event_bus.EventBus` — le capteur publie un `Event` standard,
  jamais de canal parallèle.
- `raya.world_state.WorldStateStore` — ingestion **générique** déjà
  existante (s'abonne à `perception.*`) ; aucun code dédié n'a été ajouté
  pour promouvoir ce fait spécifiquement.
- `raya.attention.AttentionEvaluator` — un nouveau cas dans le switch
  existant sur `event.type`, pas un second évaluateur.
- `raya.harness.Harness._on_attention_decision` — chemin déjà existant
  (13B/13D), réexaminé mais pas dupliqué.

Aucune modification de `RAYA/` (V1) — vérifié (§15).

## 3. Implémentation

Fichiers nouveaux :

- `raya/perception/notification_extraction.py` — extraction/classification
  pure (aucun import de `raya.devices`, conforme au lint architectural).
- `raya/perception/incoming_call_sensor.py` — `IncomingCallNotificationSensor(LightSensor)`.

Fichiers modifiés :

- `raya/perception/phone_events.py` — `PhoneEventHook` généralisé pour
  cibler un `(process_name, class_name)` configurable, au lieu du seul
  `PhoneExperienceHost.exe` figé (13D). `_DEFAULT_TARGET_PROCESS_NAME`
  conservé pour rétrocompatibilité avec `PhoneCallActivitySensor` (13D).
- `raya/perception/__init__.py` — export de `IncomingCallNotificationSensor`.
- `raya/runtime/bootstrap.py` — ajout du capteur à la liste `PerceptionRuntime`.
- `raya/attention/evaluator.py` — `_decide_incoming_call_notification`
  (voir §9 pour le détail de la décision).
- `raya/harness/loop.py` — `_on_attention_decision` : le branchement vers
  `_on_phone_activity_interrupt` a été **restreint** à
  `perception.phone_call_activity` (13D) uniquement — voir §9, ce chemin
  n'est structurellement plus jamais atteint pour
  `perception.incoming_call_notification`.

## 4. Flux d'événement

```
Windows (vrai appel entrant)
  -> bannière ShellExperienceHost.exe / Windows.UI.Core.CoreWindow
  -> SetWinEventHook (PhoneEventHook, ctypes, non-bloquant)
  -> IncomingCallNotificationSensor.sample() (tick du PerceptionRuntime existant)
  -> lecture UIA + retry borné (notification_extraction.py)
  -> classification structurelle (PriorityToastView + boutons -> "incoming")
  -> Event("perception.incoming_call_notification") publié sur l'EventBus
  -> WorldStateStore (ingestion générique, inchangée) : fait "phone/incoming_call"
  -> AttentionEvaluator._decide_incoming_call_notification -> IGNORE (par défaut)
  -> Harness._on_attention_decision : payload.decision != "INTERRUPT" -> return immédiat
  -> AUCUN event harness.external_event_noticed, AUCUNE phrase, AUCUNE action
```

## 5. Extraction du contenu de la notification

`read_notification_content(hwnd)` parcourt l'arbre UIA du `CoreWindow` et
extrait par `AutomationId` (jamais par texte) :
`toast_view_type` (`PriorityToastView`/`NormalToastView`), `sender_name`,
`title`, `message_text`, `attribution`, `has_action_buttons`,
`action_button_texts`.

Classification (`classify_notification`) **structurelle uniquement** :

- `incoming_call` ⟺ `toast_view_type == "PriorityToastView"` **ET**
  `has_action_buttons`.
- Sinon `"other"` (ex : notification manquée après-coup).
- `"not_identified"` si le contenu n'est jamais stabilisé — jamais deviné,
  jamais publié.

Aucune inspection de `title`/`message_text`/`sender_name` dans la logique
de décision (prouvé par `test_classification_never_inspects_text_fields`).

## 6. Déduplication

Deux mécanismes de déduplication **séparés**, jamais fusionnés :

- `_seen_phone_activity_keys` (13B/13D, clé dérivée de
  `in_call`/`latest_call_log_name`/`latest_call_log_time`).
- `_seen_incoming_call_keys` (13G, clé dérivée du contenu structuré complet
  : `call_state`/`caller`/`toast_view_type`/`source_text`/`sender_category`
  — jamais `caller` seul, pour éviter qu'un appelant homonyme écrase un
  autre appel distinct).

Sous ce chantier révisé, les DEUX branches (nouvelle notification vs
notification déjà vue) résolvent en `IGNORE` — la déduplication reste
utile pour la propreté du journal d'attention (`reasoning` distinct :
"appel entrant réel détecté" vs "déjà remontée (déduplication)"), pas pour
éviter une interruption répétée (il n'y a plus d'interruption à éviter).

## 7. Stabilisation / retry (jamais du polling)

`read_notification_content_with_retry` : jusqu'à 3 tentatives, 0.3s
d'intervalle, **synchrone et déclenché par l'event lui-même** (jamais un
minuteur indépendant). Reproduit la découverte réelle du Chantier 13F : le
tout premier event Windows peut arriver avant que le contenu de la
bannière ne soit construit. Bornée explicitement (`test_retry_is_bounded_never_infinite`).

## 8. World State

Fait `domain="phone", key="incoming_call"`, `confidence=Confidence.INFERRED`
(jamais `KNOWN_FACT`), `freshness_ttl_s=30`. Promu par le mécanisme
générique existant (`WorldStateStore` s'abonne à `perception.*`) — **aucun
code spécifique** n'a été écrit pour ce fait, conformément à la contrainte
"réutiliser, ne jamais dupliquer".

## 9. Attention — pourquoi `incoming_call` ne déclenche JAMAIS d'INTERRUPT

`_decide_incoming_call_notification` (voir `raya/attention/evaluator.py`)
retourne inconditionnellement `AttentionOutcome.IGNORE`, que
`call_state` soit `"incoming"` ou `"other"`.

**Version initiale de ce chantier (retirée)** : la première implémentation
faisait de `call_state == "incoming"` un `INTERRUPT` automatique. Ceci
contredisait le principe central corrigé en cours de route (PERCEPTION ≠
INTERRUPTION ≠ RÉACTION) : le simple fait qu'un appel sonne n'est **pas**
en soi une information suffisante pour justifier une interruption — RAYA
n'a aucun moyen honnête de savoir si cet appel est important pour
l'utilisateur à cet instant précis, et **inventer** une telle importance
(ex : `if caller == "Papa": interrupt()`) serait exactement le type
d'heuristique arbitraire explicitement interdit.

La version livrée : `IGNORE` inconditionnel, avec un `reasoning` explicite
citant l'absence de justification contextuelle. Le `Harness` reflète ce
choix : `_on_attention_decision` retourne immédiatement dès que
`decision != "INTERRUPT"` (ligne existante, inchangée), donc
`perception.incoming_call_notification` n'atteint plus jamais
`_on_phone_activity_interrupt` — ce chemin reste réservé à
`perception.phone_call_activity` (13D), un mécanisme plus ancien et hors
scope de cette révision (voir §14 pour la tension résiduelle entre les
deux).

## 10. Politique de notification utilisateur

Aucune. Ni TTS, ni message poussé, ni notification visuelle générée par
RAYA suite à un appel entrant. Le fait est disponible **à la demande**
(interrogation du World State ou du contexte de conversation), jamais
poussé automatiquement.

## 11. Investigation : intégration contextuelle via Memory/Tasks/Attention/Context

Consigne du chantier : réutiliser un mécanisme existant si une intégration
naturelle est possible ; sinon documenter `NOT_IMPLEMENTED`, sans jamais
construire un `RuleEngine` parallèle.

Constat, après inspection réelle du code :

- **Dépendances autorisées d'`attention/`** (`scripts/arch_lint.py`) :
  `world_state`, `tasks`, `observability` uniquement. `memory` **n'y
  figure pas**. Une préférence explicite du type "préviens-moi si Papa
  m'appelle", si elle était stockée en Memory, ne serait donc pas
  naturellement lisible depuis `AttentionEvaluator` sans étendre le
  graphe de dépendances autorisé — un changement architectural réel, hors
  scope de ce chantier.
- **Tasks** sont déjà lisibles par `AttentionEvaluator` (`self._tasks`,
  `self._focus`). Il serait *techniquement* possible de vérifier si une
  tâche active mentionne sémantiquement "j'attends un appel important" —
  mais cela exigerait une correspondance sémantique/texte sur l'objectif
  de la tâche, exactement le type d'heuristique arbitraire que la consigne
  interdit explicitement (§ "ne pas inventer une importance du contact").

**Conclusion : NOT_IMPLEMENTED.** Aucune intégration contextuelle
(Memory-based ou Task-based) n'a été construite. Le comportement par
défaut reste `IGNORE` inconditionnel pour tout appel entrant, quel que
soit l'appelant. Un futur chantier dédié pourrait légitimement (a) étendre
le graphe de dépendances d'`attention/` pour inclure une lecture *stricte*
et *structurée* (jamais sémantique) d'une table de préférences explicites,
ou (b) exposer ce choix comme un paramètre de configuration explicite
plutôt qu'une préférence en langage naturel.

## 12. Tests automatisés (nouveaux/révisés pour ce chantier)

| Fichier | Tests | Portée |
|---|---|---|
| `tests/perception/test_notification_extraction.py` | 10 | Classification structurelle, retry borné, dégradation honnête |
| `tests/perception/test_incoming_call_sensor.py` | 9 | Gating par event réel (jamais de lecture sans event), pas de repli temporel, anti-bruit |
| `tests/attention/test_attention.py` | 6 | `IGNORE` par défaut (incoming **et** other), déduplication observable via `reasoning`, aucune dépendance croisée avec la dédup 13B/13D |
| `tests/harness/test_phone_awakening.py` | 4 | Harness ne se réveille JAMAIS par défaut, World State reçoit le fait, aucune Task créée automatiquement |
| **Total nouveau/révisé (13G)** | **29** | |

Suite ciblée complète (13B+13C+13D+13G + tools/phone) :
`pytest tests/perception/ tests/attention/ tests/harness/test_phone_awakening.py tests/tools/test_phone_catalog.py tests/tools/test_phone_safety_isolation.py -q`
→ **95 passed**.

## 13. Validation réelle (perception vs réaction)

Trois fenêtres passives (aucune surveillance active, listener enregistré
AVANT toute sollicitation d'appel, conformément à la correction 13E) :

**Fenêtre 1 & 2** — un vrai toast Windows non lié au téléphone (Logitech
G HUB) capturé par le même pipeline :
- Perception réelle : fait World State `call_state="other"` correctement
  écrit.
- Attention : `IGNORE`.
- Harness : `harness.external_event_noticed` = 0.

**Fenêtre 3** — vrai appel entrant reçu (appelant : "Glodi") :
- **Perception (A) — PROUVÉE** : `PriorityToastView` détecté, fait World
  State écrit :
  `{'call_state': 'incoming', 'caller': 'Glodi', 'source_text': 'via Mobile connecté', 'sender_category': 'Appels', 'toast_view_type': 'PriorityToastView'}`,
  `confidence=INFERRED`.
- **Silence par défaut (B) — PROUVÉ** : décision Attention `IGNORE` avec
  le raisonnement explicite documentant l'absence de justification
  contextuelle ; `harness.external_event_noticed` = **0** (aucun réveil,
  aucune phrase, aucune action).

Les deux preuves requises par le chantier (perception ET absence de
réaction automatique) sont donc établies sur un vrai appel réel, pas
seulement en test unitaire déterministe.

Note honnête : deux fenêtres antérieures (avant que l'appel réel
n'atterrisse) se sont terminées sans aucun événement capturé — reflet
normal d'un mécanisme purement événementiel (pas de polling, donc rien à
rapporter tant qu'aucun événement Windows réel ne se produit), pas un
signe de défaillance.

## 14. Limitations connues

- **NOT_IMPLEMENTED** (§11) : aucune préférence contextuelle explicite
  ("préviens-moi si X m'appelle") ne peut aujourd'hui faire remonter un
  appel entrant jusqu'à l'utilisateur. Le comportement est
  inconditionnellement silencieux.
- **Incohérence résiduelle, assumée et documentée** : `_decide_phone_call_activity`
  (13B/13D, `perception.phone_call_activity`) retourne encore
  `INTERRUPT` par défaut pour une "activité téléphonique" générique
  (moins fiable, cf. investigation 13E). Ce chantier ne l'a **pas**
  modifié — hors scope explicite de la révision 13G, qui porte sur le
  signal `incoming_call_notification` spécifiquement. Un futur chantier
  devrait probablement aligner les deux sous le même principe PERCEPTION
  ≠ INTERRUPTION, mais cela redéfinirait le comportement déjà validé
  (et signalé à l'utilisateur en conditions réelles) des Chantiers
  13B/13D — décision volontairement laissée à l'utilisateur plutôt
  qu'imposée ici.
- Flake pré-existant (non lié à ce chantier, déjà documenté aux
  chantiers précédents) : exécuter `tests/perception/*` dans la même
  invocation pytest que `tests/devices/windows/test_windows_agent.py`
  provoque 4 échecs `CoInitialize n'a pas été appelé` dans ce dernier
  fichier, qui disparaissent quand chaque suite tourne seule — fragilité
  COM/threading préexistante dans `devices/windows/mechanisms/uia.py`
  (singleton `_UIAWorker`), non modifiée ici.

## 15. Intégrité V1

`RAYA/` (V1) : `git status --short` confirme uniquement des fichiers
non suivis préexistants (`_shopping_full.txt`, `prompt_claude_code_*.txt`)
— aucun fichier suivi modifié. V1 reste intact.

## 16. Évaluation de régression

- `python scripts/arch_lint.py` → **PASS**, aucune violation.
- Suite complète RayaV2 : **1154 passed, 11 skipped**, 3 échecs — tous les
  trois déjà documentés dans les chantiers précédents comme des flakes
  d'environnement préexistants et non liés à ce chantier
  (`OLLAMA_API_KEY` réelle présente dans `.env` provoquant des tests
  écrits pour un `NullProvider` à échouer différemment que prévu par
  leurs auteurs : `test_handle_request_fails_honestly_with_null_provider_stub`,
  `test_scenario_7_background_task_does_not_block_conversation`,
  `test_2_conversation_answered_immediately_during_task`).
- Aucune régression attribuable à ce chantier.

## 17. Verdict

**GO** — avec la limitation NOT_IMPLEMENTED du §11/§14 assumée et
documentée, jamais contournée par un raccourci heuristique.

## 18. Recommandation suivante

Ne pas enchaîner automatiquement sur Voix, audio téléphonique, SMS,
appels sortants, compagnon mobile, serveur, ou autonomie avancée — arrêt
ici, dans l'attente d'une nouvelle direction explicite de l'utilisateur,
conformément à l'instruction reçue pour ce chantier.
