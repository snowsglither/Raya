# RAYA V2 — CONTRACTS

**Statut :** Contrats de données définitifs, prêts pour revue avant implémentation.
**Document parent :** `RAYA_V2_TECHNICAL_ARCHITECTURE.md` (frontières des subsystems qui produisent/consomment ces contrats).

---

## 0. Conventions communes à tous les contrats

- **ID :** ULID (26 caractères, base32 Crockford, lexicographiquement triable par temps de création). Choisi plutôt qu'un UUID4 aléatoire car les IDs doivent rester ordonnables dans les logs/l'event store sans colonne timestamp séparée pour le tri naturel. Préfixé par type pour lisibilité en debug : `evt_<ulid>`, `task_<ulid>`, `tc_<ulid>` (tool call), `sess_<ulid>` (session Harness), `mem_<ulid>`, `fact_<ulid>`.
- **Timestamp :** ISO 8601 UTC, précision milliseconde. `"2026-09-04T14:32:10.123Z"`. Jamais d'heure locale sérialisée — la conversion d'affichage est une responsabilité d'`interfaces`.
- **Sérialisation :** JSON pour tout ce qui traverse une frontière process (persistence, event bus, API). Les objets Python internes peuvent être des dataclasses/pydantic models, mais le contrat *sur le fil* est JSON avec les champs exacts documentés ici — pas de dépendance à un format binaire propriétaire.
- **correlation_id :** un ULID unique généré à la racine d'une chaîne causale (ex : une requête utilisateur) et propagé tel quel à travers tous les `Event`, `Task`, `ToolCall`, `ModelRequest` qui en découlent. Permet la reconstruction complète d'une timeline dans `observability`.
- **provenance :** toujours une chaîne structurée `"<subsystem>:<mechanism>"`, ex : `"perception:foreground_window"`, `"user:explicit_correction"`, `"model:deepseek-v4-flash"` — jamais une chaîne libre non structurée.
- **confidence :** énumération `known_fact | inferred | hypothesis`, jamais un float non expliqué (le blueprint ne demande pas de probabilité calibrée, juste une distinction qualitative claire — cohérent avec `brain/cognition/verification.py` qui utilise déjà un tri-état SUCCESS/UNKNOWN/FAILURE plutôt qu'un score).
- **Erreurs :** tout contrat qui peut échouer porte un champ `error: ErrorInfo | null` structuré, jamais une exception non typée qui traverse une frontière de subsystem :
  ```
  ErrorInfo {
    code: string          # ex: "tool_timeout", "model_unavailable", "permission_denied"
    message: string       # message humain, pour logs/debug
    retryable: bool
    details: object | null
  }
  ```

---

## 1. Event

Unité de communication asynchrone entre subsystems. Tout ce qui traverse une frontière sans être un appel synchrone direct est un `Event`.

```
Event {
  id: ULID                        # evt_...
  type: string                    # ex: "perception.window_changed", "task.completed"
  timestamp: ISO8601
  source: string                  # subsystem émetteur, ex: "perception"
  correlation_id: ULID | null     # lie l'event à une chaîne causale (requête utilisateur d'origine)
  payload: object                 # schéma spécifique au type, documenté par catalogue (voir Architecture §1)
}
```

**Invariants :**
- `type` suit strictement le format `<subsystem>.<event_name>` en snake_case, jamais de type ad hoc non catalogué.
- `payload` ne contient jamais de secret/credential (règle absolue — voir Invariants Architecturaux).
- Un `Event` est immuable une fois émis — pas de mutation après publication, une correction est un nouvel `Event`.

**Lifecycle :** créé → publié sur le bus d'events → consommé par 0..N subsystems abonnés → archivé par `observability` (rétention configurable, pas indéfinie — le blueprint interdit explicitement le "uncontrolled permanent event log").

**Sérialisation :** JSON, `payload` doit être sérialisable sans référence circulaire.

### 1.1 EventBus — contrat d'infrastructure transversal

**Ajouté lors de la revue de cohérence finale (voir `RAYA_V2_ARCHITECTURE_FINAL_REVIEW.md` §1).** Ce n'est pas un 17e contrat métier — c'est l'infrastructure qui transporte des `Event`. Détail complet du mécanisme (ordering, backpressure, propagation) dans `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §13 ; ici uniquement la forme de l'API.

```
EventBus {
  publish(event: Event) -> void
  subscribe(event_type_pattern: string, handler: Callable[[Event], None],
            subscriber: string, backpressure_policy: drop_oldest | block_publisher_with_timeout) -> SubscriptionHandle
  unsubscribe(handle: SubscriptionHandle) -> void
}
```

**Invariants :** l'EventBus ne modifie jamais `Event.correlation_id` ni `Event.payload` en transit ; il ne retient jamais un `Event` pour une raison métier (pas de filtrage par état de `Task`/session) ; `backpressure_policy` est déclarée explicitement à chaque `subscribe()`, jamais un défaut implicite.

---

## 2. WorldStateFact

```
WorldStateFact {
  domain: string              # "pc" | "app" | "window" | "process" | "browser" |
                               # "device" | "network" | "battery" | "notification" |
                               # "task" | "system"
  key: string                 # identifiant du fait dans son domaine, ex: "foreground_window"
  value: any                  # valeur structurée, schéma dépendant de domain+key
  timestamp: ISO8601           # quand ce fait a été observé/mis à jour
  source: string               # provenance, ex: "perception:foreground_window"
  confidence: known_fact | inferred | hypothesis
  freshness_ttl_s: int | null   # après combien de secondes ce fait est considéré périmé (null = pas de péremption, ex: identité device)
  status: active | stale | superseded
}
```

**Invariants :**
- Clé composite `(domain, key)` unique dans le World State courant — une nouvelle valeur pour la même clé REMPLACE l'ancienne (status → `superseded`), elle ne s'accumule pas en historique. Le World State n'est PAS un journal, c'est une photo courante.
- Un fait dont `freshness_ttl_s` est dépassé passe automatiquement en `status: stale` — il reste consultable (avec son statut) mais ne doit plus être considéré comme fiable par `context.assemble()` sans le signaler.
- `confidence: hypothesis` ne peut jamais provenir de `source` commençant par `perception:` sans passage explicite par une étape de vérification — la perception observe des faits ou des inférences immédiates, pas des hypothèses non fondées (celles-ci viennent de `cognition`).

**Lifecycle :** `active` → (`stale` si TTL dépassé) → `superseded` (remplacé par une nouvelle valeur pour la même clé) — les faits `superseded`/`stale` anciens sont purgés périodiquement, pas conservés indéfiniment.

**Sérialisation :** JSON. Persistence optionnelle (snapshot pour reprise après crash), pas un stockage durable au même titre que Memory.

---

## 3. MemoryEntry

```
MemoryEntry {
  id: ULID                     # mem_...
  type: fact | preference | rule | experience
  layer: working | conversation | personal | project | task | experience
  channel_scope: shared | voice | chat | ios   # isolation stricte par défaut
  content: string | object      # texte libre ou structure selon type
  confidence: known_fact | inferred | hypothesis
  provenance: string             # ex: "user:explicit_correction", "harness:task_completion_summary"
  created_at: ISO8601
  updated_at: ISO8601
  lifecycle: candidate | active | confirmed | aging | obsolete
  related_task_id: ULID | null   # si issu d'une Task (ex: Experience Memory)
  supersedes: ULID | null        # id d'une MemoryEntry précédente que celle-ci corrige/remplace
}
```

**Invariants :**
- `channel_scope: shared` est le SEUL cas où une entrée traverse les canaux automatiquement — tout autre accès cross-canal passe par un appel explicite en lecture seule équivalent à `modules/context_bridge/bridge.py` (**ADAPT**), jamais par une fuite silencieuse.
- Une correction utilisateur (`provenance` commençant par `user:`) a priorité de traitement absolue sur toute inférence automatique contradictoire — au moment d'une contradiction, l'entrée `user:*` la plus récente gagne, l'ancienne passe en `lifecycle: obsolete` avec `supersedes` pointant vers elle.
- `lifecycle: candidate` n'est jamais injecté par `context.assemble()` sans le marquer explicitement comme non confirmé — évite qu'une hypothèse non vérifiée soit présentée au modèle comme un fait établi.
- Ne stocke JAMAIS : bruit conversationnel brut, credentials/secrets temporaires, résultats de recherche facilement re-récupérables sans valeur durable (repris explicitement du blueprint §11).

**Lifecycle détaillé :** `candidate` (observation/inférence non confirmée) → `active` (utilisée normalement) → `confirmed` (renforcée par répétition ou confirmation explicite) → `aging` (pas revue depuis longtemps, candidate à la purge) → `obsolete` (remplacée ou invalidée, conservée pour audit mais exclue du contexte actif).

**Sérialisation :** JSON pour `content` structuré ; SQLite pour Conversation Memory (schéma proche de `modules/conversations/store.py`, **ADAPT**), store structuré pour Personal/Experience Memory.

---

## 4. AttentionDecision

```
AttentionDecision {
  event_ref: ULID                # id de l'Event évalué
  decision: PROCESS_NOW | BACKGROUND | INTERRUPT | IGNORE
  reasoning: string                # courte justification, pour audit/debug, jamais montrée à l'utilisateur telle quelle
  factors: {
    urgency: float 0..1
    importance: float 0..1
    novelty: float 0..1
    confidence: float 0..1
    cost: float 0..1
    user_relevance: float 0..1
  }
  timestamp: ISO8601
  target_session_id: ULID | null   # si INTERRUPT, quelle session est affectée
}
```

**Invariants :**
- `decision` est TOUJOURS une des 4 valeurs de l'énumération, jamais une action concrète (Attention ne dit jamais "exécute X").
- `factors` sont journalisés pour permettre l'audit/tuning de la politique, mais jamais exposés directement à l'utilisateur (ce sont des poids internes, pas une explication utilisateur).
- Le calcul d'une `AttentionDecision` ne doit jamais invoquer le Model Layer dans le chemin critique (règle de latence — voir Architecture §1.4).

**Lifecycle :** éphémère — calculée, consommée immédiatement par le Harness (ou pour créer/mettre à jour une Task si BACKGROUND), journalisée par `observability`, jamais persistée comme entité de premier ordre.

---

## 5. Task

```
Task {
  id: ULID                        # task_...
  objective: string                 # description de l'objectif, lisible humain
  state: PENDING | RUNNING | PAUSED | COMPLETED | FAILED | CANCELLED
  priority: int                     # fourni par Attention au moment de la création/mise à jour
  context: object                    # état métier propre à la tâche (pas le Context Engine — le "quoi j'accomplis")
  progress: { current_step: string, percent: float | null }
  dependencies: list[ULID]           # autres Task devant être COMPLETED avant démarrage
  created_at: ISO8601
  updated_at: ISO8601
  checkpoint: object | null           # dernier état d'exécution sauvegardé, format libre par type de tâche
  cancellation_requested: bool
  result: object | null
  error: ErrorInfo | null
  owner: { channel: string, session_id: ULID }
  correlation_id: ULID
}
```

**Invariants :**
- Transition d'état stricte : `PENDING → RUNNING → (PAUSED ⇄ RUNNING) → (COMPLETED | FAILED | CANCELLED)`. Un état terminal (`COMPLETED`/`FAILED`/`CANCELLED`) est final — aucune transition sortante n'est permise (repris explicitement de l'invariant validé en V1 : "tâche annulée reste CANCELLED, pas de réactivation").
- `dependencies` ne doit jamais contenir de cycle — validé à la création.
- `cancellation_requested: true` doit être respecté par le worker d'exécution au prochain point de contrôle, sans délai arbitraire.
- `checkpoint` doit être suffisant pour reprendre l'exécution SANS recommencer depuis zéro — un checkpoint vide/null pour une tâche `PAUSED` est un défaut d'implémentation, pas un état valide.
- `result`/`error` sont mutuellement exclusifs — un `Task` `COMPLETED` a `result` non-null et `error` null ; un `Task` `FAILED` l'inverse.

**Lifecycle :** voir Architecture §6. Persisté dès la création (pas seulement au premier checkpoint).

**Sérialisation :** JSON, `context`/`checkpoint`/`result` sont des sous-objets dont le schéma dépend du type de tâche (pas de schéma unique imposé au-delà de "doit être JSON-sérialisable").

---

## 6. TaskEvent

Spécialisation d'`Event` pour les transitions de `Task` — catalogue fermé de types.

```
TaskEvent : Event {
  type: "task.created" | "task.started" | "task.progress" | "task.checkpoint" |
        "task.paused" | "task.resumed" | "task.cancelled" | "task.completed" | "task.failed"
  payload: {
    task_id: ULID
    previous_state: string | null
    new_state: string
    detail: object | null
  }
}
```

**Invariants :** un `TaskEvent` est toujours émis en même temps que la mutation d'état correspondante sur le `Task` — jamais l'un sans l'autre (source unique de vérité = le store `Task`, l'event est une notification, pas une source de vérité alternative).

---

## 7. HarnessRequest

Entrée du point d'API central `harness.handle_request()`.

```
HarnessRequest {
  id: ULID
  correlation_id: ULID
  channel: cli | web | desktop | mobile | voice | api
  session_id: ULID                  # session de conversation/exécution existante, ou nouvelle si absente
  input: {
    text: string | null
    attachments: list[AttachmentRef] | null
    audio_ref: string | null        # si canal voice, référence à un segment déjà transcrit par Perception
  }
  steering: bool                     # true si ceci est une instruction mid-turn sur une session déjà active
  profile: standard | fast_minimal_context   # "fast_minimal_context" = cas Ghost overlay, contexte réduit explicite
  timestamp: ISO8601
}
```

**Invariants :**
- `profile: fast_minimal_context` réduit le budget du Context Engine et peut sauter certaines étapes de vérification lourdes, mais ne bypasse JAMAIS `safety.check_permission()` — un profil "rapide" change le budget de contexte, pas les garanties de sécurité.
- `steering: true` exige un `session_id` correspondant à une `HarnessState` existante et non terminale — sinon erreur `invalid_steering_target`.

---

## 8. HarnessState

État d'exécution d'une session, persistable, checkpointable.

```
HarnessState {
  session_id: ULID
  correlation_id: ULID
  status: IDLE | ASSEMBLING_CONTEXT | AWAITING_MODEL | EXECUTING_TOOL |
          VERIFYING | AWAITING_USER_INPUT | PAUSED | COMPLETED | FAILED
  current_turn: int
  active_task_ids: list[ULID]         # Task(s) déclenchées par cette session
  pending_confirmation: object | null   # si AWAITING_USER_INPUT pour une confirmation safety
  last_checkpoint_at: ISO8601
  channel: string
  history_ref: ULID                    # référence vers la Conversation Memory associée, pas l'historique inline
  error: ErrorInfo | null
}
```

**Invariants :**
- `history_ref` pointe vers `memory` (Conversation Memory) — `HarnessState` ne duplique JAMAIS l'historique de conversation en interne (c'est exactement le défaut de `core/orchestrator.py` en V1, qui maintient ses propres historiques par canal séparément de la persistance).
- `status` reflète toujours l'étape réelle de la boucle décrite en Architecture §3.1 — utilisable directement par `observability`/debug pour savoir où une session est bloquée.
- Une `HarnessState` `COMPLETED`/`FAILED` est terminale pour CE tour, mais le `session_id` peut être réutilisé pour un tour suivant (nouvelle requête sur la même conversation) — ce n'est pas la même sémantique de finalité qu'un `Task`.

**Lifecycle :** créée à la première `HarnessRequest` d'une session → mutée à chaque étape de la boucle → checkpointée à chaque transition significative → `COMPLETED`/`FAILED` en fin de tour, réactivable au tour suivant.

---

## 9. Tool

Déclaration statique d'une capacité, dans le registre.

```
Tool {
  name: string                       # ex: "browser.navigate", "pc.application_launch"
  description: string                 # pour le modèle
  capability_tags: list[string]        # ex: ["browser", "navigation"]
  input_schema: JSONSchema
  output_schema: JSONSchema
  permission_level: safe | sensitive | destructive
  default_timeout_ms: int
  retryable: bool
  idempotent: bool                      # AJOUTÉ — voir §18 ExecutionRecord. true = un retry avec le même
                                          # idempotency_key est sûr sans vérification préalable ; false = le
                                          # recovery après crash exige une vérification (cognition.verify())
                                          # avant tout retry, jamais un retry aveugle
  requires_device: string | null       # nom du Device Agent requis, null si tool "pur" (ex: calculatrice)
}
```

**Invariants :**
- `permission_level` n'est jamais laissé à la valeur par défaut implicite — chaque `Tool` déclare explicitement son niveau à l'enregistrement (pas de fallback silencieux à "safe").
- `idempotent` n'est jamais laissé à une valeur par défaut implicite — un tool avec un effet externe (fichier, réseau, device) doit déclarer explicitement `false` sauf preuve du contraire (ex: un `PUT` idempotent par nature) ; le défaut prudent en l'absence de déclaration explicite est `false`, jamais `true`.
- `input_schema`/`output_schema` sont validés à CHAQUE appel, pas seulement en développement.

---

## 10. ToolCall

```
ToolCall {
  id: ULID                          # tc_...
  correlation_id: ULID
  operation_id: ULID                  # AJOUTÉ — voir §18. STABLE à travers les retries de la MÊME action
                                        # logique (généré une fois, réutilisé, jamais régénéré à chaque tentative)
  idempotency_key: string              # AJOUTÉ — dérivée de operation_id + arguments, transmise telle quelle
                                        # au Device/provider externe quand celui-ci supporte la déduplication
  tool_name: string
  arguments: object                    # validé contre Tool.input_schema
  requested_by: { subsystem: "harness", session_id: ULID }
  timestamp: ISO8601
  timeout_ms: int
}
```

**Invariant ajouté :** `operation_id` ne change JAMAIS entre deux tentatives d'une même action logique après un crash/retry — c'est la clé qui permet à `ExecutionRecord` (§18) de retrouver l'état d'exécution réel plutôt que de traiter chaque retry comme une action nouvelle.

## 11. ToolResult

```
ToolResult {
  tool_call_id: ULID
  status: success | failure | timeout | cancelled | permission_denied
  output: object | null                # validé contre Tool.output_schema si status=success
  evidence: object | null               # preuve de vérification quand disponible (ex: screenshot ref, texte lu)
  error: ErrorInfo | null
  duration_ms: int
  retried_count: int
}
```

**Invariants (ToolCall/ToolResult) :**
- Un `ToolResult` avec `status: success` ne peut PAS avoir `error` non-null, et inversement — pas d'état ambigu.
- `evidence` doit être renseigné pour tout tool `permission_level: sensitive|destructive` quand techniquement possible — c'est la trace qui permet à `cognition.verify()` de trancher SUCCESS/UNKNOWN/FAILURE sans réexécuter l'action.
- `status: cancelled` signifie que `safety.should_stop()` ou `Task.cancellation_requested` a interrompu l'exécution — jamais confondu avec `failure` (raison métier) dans les statistiques/retry logic.

---

## 12. ModelRequest

Contrat natif RAYA — **explicitement pas Anthropic-shaped** (voir Architecture §4.5).

```
ModelRequest {
  id: ULID
  correlation_id: ULID
  capability: reasoning | planning | coding | vision | fast_response |
              classification | summarization
  messages: list[Message]              # Message { role: user|assistant|tool, content: string | list[ContentPart] }
  available_tools: list[ToolSchemaRef] | null   # sous-ensemble déjà filtré par Context Engine, jamais "tous les tools"
  context_budget_tokens: int
  constraints: {
    require_local: bool
    max_latency_ms: int | null
    max_cost: float | null
  }
  stream: bool
}
```

**ContentPart** (pour messages multimodaux) : `{ type: text | image_ref | file_ref, value: string }` — `image_ref`/`file_ref` sont des références (chemin/handle), jamais des blobs base64 inline dans le contrat (évite de gonfler les logs/persistence).

## 13. ModelResponse

```
ModelResponse {
  request_id: ULID
  provider_used: string                  # ex: "ollama_cloud:deepseek-v4-flash"
  content: list[ContentPart]
  tool_calls_requested: list[{ tool_name: string, arguments: object }] | null
  finish_reason: completed | truncated | tool_call_pending | refused | error
  usage: { input_tokens: int, output_tokens: int }
  latency_ms: int
  error: ErrorInfo | null
}
```

**Invariants (ModelRequest/ModelResponse) :**
- `finish_reason` utilise UNIQUEMENT le vocabulaire natif RAYA ci-dessus — jamais `stop_reason` avec des valeurs `end_turn`/`max_tokens`/`tool_use` façon Anthropic SDK. La table de correspondance interne (ex: Ollama `done_reason="length"` → `truncated`) est un détail d'implémentation du Provider Adapter, invisible au-dessus de `models`.
- `available_tools` est TOUJOURS un sous-ensemble pré-filtré par `context.assemble()` — `models` ne reçoit jamais "tous les outils du registre" par défaut.
- Un `ModelResponse` avec `finish_reason: error` a `error` non-null et `content` peut être vide.

---

## 14. Context

Sortie de `context.assemble()`, entrée effective construite pour un `ModelRequest`.

```
Context {
  task_id: ULID | null
  session_id: ULID
  assembled_at: ISO8601
  budget_tokens: int
  used_tokens_estimate: int
  sections: list[{
    kind: memory | world_state | conversation_history | task_state | tool_schemas | system_rules
    content: object
    provenance: string
    rank_score: float
    freshness: { status: active | stale | superseded, as_of: ISO8601 } | null
                                        # OBLIGATOIRE (non-null) si kind=world_state — reprend tel quel le
                                        # WorldStateFact.status/le moment de l'observation du fait source.
                                        # null autorisé pour tout autre kind (memory/conversation_history/
                                        # task_state/tool_schemas/system_rules n'ont pas de notion de
                                        # fraîcheur environnementale équivalente).
  }]
  cache_hit: bool
}
```

**Invariants :**
- `used_tokens_estimate <= budget_tokens` — le Context Engine tronque/compacte AVANT de dépasser le budget, jamais après coup côté modèle.
- Chaque `sections[i]` porte sa `provenance` — permet de tracer POURQUOI une information particulière est apparue dans un appel modèle donné (audit/debug).
- **`sections[i].kind == world_state` ⇒ `sections[i].freshness != null`** — validation de schéma stricte, refusée à la construction sinon. Le Context Engine est ainsi structurellement incapable de transmettre un `WorldStateFact` `stale`/`superseded` sans que son statut ne soit explicitement porté jusqu'au modèle ; il ne peut jamais le présenter silencieusement comme équivalent à `active`.
- `Context` n'est jamais persisté comme entité durable — reconstruit à chaque appel (avec cache TTL court autorisé, cf. `modules/context/recent_work.py`).

---

## 15. Permission

Résultat d'une vérification `safety.check_permission()`.

```
Permission {
  action_ref: string                    # description de l'action évaluée
  risk_level: safe | sensitive | destructive
  decision: allowed | requires_confirmation | denied
  reason: string
  granted_by: policy | user_confirmation | physical_presence | null
  audit_id: ULID                         # référence vers l'entrée d'audit trail
  timestamp: ISO8601
}
```

**Invariants :**
- `risk_level: destructive` ne peut JAMAIS résulter en `decision: allowed` sans `granted_by` renseigné (pas d'auto-approbation silencieuse pour le risque le plus élevé).
- Toute `Permission` évaluée est journalisée via `audit_id`, y compris les `denied` — l'audit trail capture les refus, pas seulement les succès.

---

## 16. Device

```
Device {
  id: string                            # ex: "windows_agent", "browser_agent"
  type: windows | linux | browser | camera | ios | future
  capabilities: list[Capability]
  status: online | offline | degraded
  last_health_check: ISO8601
}

Capability {
  name: string                          # ex: "application.launch", "input.click"
  input_schema: JSONSchema
  mechanism_hint: string | null          # ex: "uia" | "shell" | "cdp" — pour debug/observability, JAMAIS exposé au Harness comme paramètre de décision
}

Command {
  id: ULID
  correlation_id: ULID
  device_id: string
  capability_name: string
  arguments: object
  timeout_ms: int
}

Result {
  command_id: ULID
  status: success | failure | timeout | cancelled
  output: object | null
  evidence: object | null
  error: ErrorInfo | null
  mechanism_used: string                 # quel mécanisme concret a été utilisé cette fois (audit, pas décision Harness)
}

Health {
  device_id: string
  status: online | offline | degraded
  detail: string | null
  checked_at: ISO8601
}
```

**Invariants :**
- Le Harness/Tools ne lit/n'envoie JAMAIS `mechanism_hint`/`mechanism_used` comme critère de décision — c'est un détail d'implémentation exposé pour l'observability uniquement (règle explicite : "Le Core ne doit pas dépendre de UIA/coordinates/etc.").
- `Command` est TOUJOURS émise par `tools` vers un `Device`, jamais directement par `harness`/`interfaces`.

---

## 17. InterfaceRequest / InterfaceResponse

```
InterfaceRequest {
  id: ULID
  channel: cli | web | desktop | mobile | voice | api
  session_id: ULID | null                 # null = nouvelle session
  raw_input: object                        # forme brute spécifique au canal (texte, audio buffer ref, form data)
  timestamp: ISO8601
}

InterfaceResponse {
  request_id: ULID
  session_id: ULID
  presentation: {
    text: string | null
    audio_ref: string | null
    structured_payload: object | null       # ex: JSON pour affichage riche web
  }
  requires_user_action: bool                # ex: confirmation en attente
  timestamp: ISO8601
}
```

**Invariants :**
- `InterfaceRequest` est TOUJOURS traduit en `HarnessRequest` par la couche `interfaces` avant d'atteindre le Harness — jamais transmis tel quel (les deux contrats sont volontairement distincts pour garder `interfaces` responsable de la traduction canal-spécifique).
- `InterfaceResponse.presentation` ne contient que des champs déjà finalisés pour affichage — aucune logique de mise en forme ne doit être recalculée côté canal au-delà du rendu pur (texte→bulle chat, texte→audio TTS, etc.).

---

## 18. ExecutionRecord

**Ajouté lors de la revue de cohérence finale (voir `RAYA_V2_ARCHITECTURE_FINAL_REVIEW.md` §3).** Garantit qu'une action à effet externe n'est jamais rejouée aveuglément après un crash. Détail du flux complet et de la règle de récupération dans `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §14 — ici uniquement le schéma.

```
ExecutionRecord {
  operation_id: ULID                    # même valeur que ToolCall.operation_id
  tool_call_id: ULID
  correlation_id: ULID
  execution_state: NOT_STARTED | EXECUTING | COMPLETED | UNKNOWN
  verification_state: NOT_VERIFIED | VERIFIED_SUCCESS | VERIFIED_FAILURE | UNVERIFIABLE
  idempotency_key: string
  started_at: ISO8601 | null
  completed_at: ISO8601 | null
}
```

**Invariants :**
- `execution_state=EXECUTING` est écrit de façon DURABLE (via `persistence`, synchrone) **avant** que le `Device` ne soit invoqué — jamais après. C'est cette écriture préalable qui rend un crash détectable plutôt que silencieux.
- Un `ExecutionRecord` retrouvé en `EXECUTING` après un redémarrage est immédiatement réinterprété comme `execution_state=UNKNOWN` — jamais traité comme `COMPLETED` (optimisme dangereux) ni comme `NOT_STARTED` (risque de double exécution). Cette réinterprétation est automatique, pas une option.
- `verification_state` n'est renseigné (`VERIFIED_SUCCESS`/`VERIFIED_FAILURE`) que par un appel explicite à `cognition.verify()` utilisant une vérification SANS EFFET DE BORD — jamais déduit par défaut.
- `UNVERIFIABLE` + `Tool.idempotent=false` déclenche TOUJOURS une escalade via `cognition.resolve_ambiguity()` (ambiguïté bloquante) — ce chemin ne retourne jamais silencieusement `COMPLETED` ni ne déclenche silencieusement un retry.
- `idempotency_key` identique à travers tous les retries d'un même `operation_id` — transmis tel quel au `Device`/provider externe.

**Lifecycle :** `NOT_STARTED` (implicite, avant toute écriture) → `EXECUTING` (écrit avant l'appel Device) → `COMPLETED` | `FAILED` (écrit après retour du Device) ; si crash entre les deux : relecture au redémarrage → traité comme `UNKNOWN` → résolu selon la règle de récupération de l'Architecture §14.3 avant toute décision de retry.

**Persistence :** obligatoire, via `persistence`, écriture synchrone pour la transition vers `EXECUTING` (c'est la garantie centrale de ce contrat — une écriture asynchrone/best-effort ici annulerait toute la protection).

---

*Fin des contrats. Voir `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §1 pour le catalogue complet des types d'`Event` par subsystem, §13 pour l'EventBus, §14 pour le mécanisme d'idempotence complet, et `RAYA_V2_ARCHITECTURAL_INVARIANTS.md` pour les invariants testables dérivés de ces contrats.*
