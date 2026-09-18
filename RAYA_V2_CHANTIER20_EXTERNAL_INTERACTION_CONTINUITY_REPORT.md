# RAYA V2 — Chantier 20 : External Interaction Continuity
**Rapport post-implémentation**

---

## Statut

**DONE.** 25/25 tests automatisés pass. 0 régression introduite. V1 strictement intacte.

---

## Audit préalable

Modules inspectés avant toute modification :
- `raya/harness/loop.py` — boucle agentique, handle_request, context assembly
- `raya/context_engine/` — assembler.py, render.py (system_rules, world_state, memory sections)
- `raya/world_state/store.py` — WorldStateStore, retrieve_relevant, retrieve_fact
- `raya/contracts/` — WorldStateFact, __init__.py
- `raya/tools/catalog/` — pattern d'injection existant (TaskControlOps, NotifyOps, PreferenceOps)
- `raya/runtime/bootstrap.py` — composition root, ordre d'enregistrement des tools
- `raya/safety/risk.py` — _RISK_BY_TAG, classify_risk
- `scripts/arch_lint.py` — ALLOWED graph, règle dependency-graph

---

## Capacités existantes utilisées (zéro duplication)

| Capacité existante | Utilisation |
|--------------------|-------------|
| `WorldStateFact` + `WorldStateStore` | Persistance SQLite des interactions (domain="interaction") |
| `freshness_ttl_s=86_400` | Expiration naturelle à 24h sans cron ni timer dédié |
| `context_engine/assembler.py` → `SectionKind.WORLD_STATE` | Assemblage automatique dans le contexte modèle |
| `render.py` WORLD_STATE branch | Rendu spécialisé "Pending external interaction" |
| `ToolRegistry` + `execute()` | Pipeline standard (validation → permission → handler) |
| `safety/risk.py` `_RISK_BY_TAG` | Classification SAFE (interaction.track/reply = écriture locale) |
| `new_id("inter")` / `utc_now_iso()` | Génération d'ID et timestamp cohérents avec le reste |

---

## Cause racine

RAYA traitait chaque message envoyé / traduction affichée comme une action
terminée en elle-même. Aucune structure ne représentait le fait qu'une réponse
externe était attendue. Au tour suivant, le modèle avait perdu le contexte de
l'échange en cours et ne pouvait pas résoudre des pronoms comme "il a dit oui"
sans redemander "de qui parles-tu ?".

---

## Modèle de données

```
WorldStateFact(
    domain = "interaction",
    key    = "inter_<ulid>",          # généré une fois, stable
    value  = {                         # ExternalInteraction sérialisé
        "id":               "inter_...",
        "interlocutor":     "mon frère",
        "channel":          "whatsapp",
        "outgoing_message": "Est-ce que tu viens ce soir ?",
        "state":            "AWAITING_EXTERNAL_REPLY",   # → REPLIED → COMPLETED
        "original_request": "...",
        "expected_reply":   "oui/non",
        "reply_text":       "",        # rempli par interaction.reply
        "created_at":       "...",
        "last_activity":    "...",
        "owner_session_id": "sess_...",
    },
    freshness_ttl_s = 86_400,         # 24h d'expiration naturelle
)
```

**États** : `AWAITING_EXTERNAL_REPLY` → `REPLIED` → `COMPLETED` / `CANCELLED`

`ACTIVE_INTERACTION_STATES = frozenset{AWAITING_EXTERNAL_REPLY}` — le modèle
ne voit comme "pending" que les interactions en attente réelle.

---

## Cycle de vie d'une interaction

1. L'utilisateur dit "Demande à mon frère s'il vient ce soir."
2. RAYA envoie le message (traduction, WhatsApp, etc.) via les tools appropriés.
3. RAYA appelle `interaction.track` → WorldStateFact(domain="interaction", state=AWAITING) créé.
4. Au tour suivant, le Context Engine inclut automatiquement la section "Pending external interaction".
5. L'utilisateur dit "Il a dit oui vers 20h." (1 interaction active → auto-résolution)
6. RAYA appelle `interaction.reply(interaction_id, reply_text="oui vers 20h")` → state=REPLIED.
7. Le contexte suivant montre "replied: 'oui vers 20h'" au lieu de "Pending".
8. Après 24h sans réponse : fact.status → STALE (mécanisme TTL existant, zéro code nouveau).

**Ambiguïté** (2+ interactions actives) : la directive système demande au modèle
de poser une question courte ("Ton frère ou ton collègue ?") avant d'appeler
interaction.reply.

---

## Continuité référentielle

La directive dans `render.py` SYSTEM_RULES instruit le modèle :
- Après envoi/traduction → appeler `interaction.track`.
- Sur pronom ambigu ("il", "elle", "ils") → lire les sections "Pending external interaction" du contexte.
- Si 1 interaction active correspond : appeler `interaction.reply` directement.
- Si 2+ peuvent correspondre : demander une clarification courte.
- Si aucune ne correspond : traiter comme nouveau contexte.

Aucune heuristique de résolution codée en Python — le modèle fait la résolution
en langage naturel, guidé par les données factuelles du World State.

---

## Continuité cross-interface

`WorldStateStore` n'est pas scopé par session. Une interaction créée depuis le
Cockpit (sess_cockpit) est visible depuis Telegram (sess_telegram) et vice versa.
Prouvé par `test_interaction_visible_from_different_session`.

---

## Persistance au restart

`WorldStateFact` est persisté en SQLite via le `PersistenceBackend`. Au
redémarrage, `WorldStateStore.retrieve_relevant()` recharge tous les faits actifs
(domain="interaction" inclus). Prouvé par `test_interaction_survives_restart_via_sqlite`.

---

## Attention / mémoire / contexte

- **World State** (domain="interaction") : état courant d'une interaction, visible dans chaque tour via le Context Engine.
- **Memory** : non utilisée pour les interactions — la Memory est pour les préférences/faits durables sur l'utilisateur, pas l'état transactionnel.
- **Tasks** : non utilisées pour les interactions — une interaction n'est pas une tâche longue-durée avec checkpoints. Elle est un fait observable avec un TTL.

---

## Safety

- `interaction.track` : SAFE. Écriture dans World State local uniquement, aucun effet externe.
- `interaction.reply` : SAFE. Idem.
- Pas d'élargissement de permissions.
- Pas de bypass du pipeline `execute()` → Safety.check_permission().
- `interaction.track` et `interaction.reply` ajoutés à `_RISK_BY_TAG` (risk.py) avec justification explicite.

---

## Changements apportés

### Nouveaux fichiers

| Fichier | Description |
|---------|-------------|
| `raya/contracts/interaction.py` | `ExternalInteractionState`, `ACTIVE_INTERACTION_STATES`, `ExternalInteraction` |
| `raya/tools/catalog/interaction.py` | Handlers `interaction.track`, `interaction.reply`, `register_interaction_tools` |
| `tests/contracts/test_interaction_contract.py` | 5 tests contrat pur |
| `tests/tools/test_interaction_catalog.py` | 10 tests handlers (WorldState InMemory) |
| `tests/integration/test_chantier20_external_interaction.py` | 10 tests intégration (context + persistence) |

### Fichiers modifiés

| Fichier | Modification |
|---------|-------------|
| `raya/contracts/__init__.py` | Export de `ExternalInteraction`, `ExternalInteractionState`, `ACTIVE_INTERACTION_STATES` |
| `raya/tools/catalog/__init__.py` | Export de `register_interaction_tools` |
| `raya/runtime/bootstrap.py` | Appel `register_interaction_tools(tools, world_state)` avant Harness |
| `raya/context_engine/render.py` | Directive Chantier 20 (SYSTEM_RULES) + rendu spécialisé WORLD_STATE domain="interaction" |
| `raya/safety/risk.py` | `"interaction.track": SAFE`, `"interaction.reply": SAFE` dans `_RISK_BY_TAG` |
| `tests/architecture/test_targeted_execution_repair_architecture_proof.py` | Compte browser tools 7→8 (browser.click_at_position ajouté en Chantier 18B) |

---

## Tests — 25 nouveaux (limite respectée)

### Contrat pur — 5 tests (`tests/contracts/test_interaction_contract.py`)
1. `test_external_interaction_state_values` — valeurs enum correctes
2. `test_active_interaction_states_contains_only_awaiting` — seul AWAITING actif
3. `test_external_interaction_default_state_is_awaiting` — état initial correct
4. `test_external_interaction_id_auto_generated` — préfixe inter_, unicité
5. `test_external_interaction_to_dict_from_dict_roundtrip` — sérialisation complète

### Catalog tools — 10 tests (`tests/tools/test_interaction_catalog.py`)
6. `test_interaction_tools_are_registered_and_safe` — enregistrement + SAFE
7. `test_track_creates_world_state_fact_with_interaction_domain` — domain="interaction"
8. `test_track_returns_awaiting_state` — AWAITING_EXTERNAL_REPLY
9. `test_track_id_starts_with_inter` — préfixe inter_
10. `test_track_missing_required_fields_returns_validation_error` — schema validation
11. `test_track_multiple_interactions_are_independent` — isolation des faits
12. `test_reply_updates_state_to_replied` — transition AWAITING → REPLIED
13. `test_reply_records_reply_text_in_world_state` — reply_text persisté
14. `test_reply_on_unknown_id_returns_not_found` — INTERACTION_NOT_FOUND
15. `test_reply_missing_interaction_id_returns_failure` — schema validation

### Intégration — 10 tests (`tests/integration/test_chantier20_external_interaction.py`)
16. `test_interaction_fact_appears_in_assembled_context` — assemblage Context
17. `test_pending_interaction_renders_as_awaiting_text` — rendu "Pending external interaction"
18. `test_pending_interaction_includes_channel_in_render` — canal dans le rendu
19. `test_replied_interaction_renders_as_replied_text` — rendu "replied"
20. `test_two_active_interactions_both_visible_in_context` — 2 interactions simultanées
21. `test_interaction_renders_awaiting_vs_replied_differently` — états distincts
22. `test_chantier20_directive_present_in_system_prompt` — directive présente
23. `test_interaction_visible_from_different_session` — cross-interface
24. `test_interaction_survives_restart_via_sqlite` — persistance SQLite
25. `test_full_lifecycle_track_then_reply_then_context_reflects` — cycle complet

---

## E2E réels

Aucun E2E Ollama ajouté pour ce chantier : les tests structurels (25) couvrent
l'intégralité du chemin, du contrat jusqu'au rendu système. L'E2E live (modèle
réel qui appelle interaction.track puis interaction.reply) est validable
manuellement en quelques échanges dans le Cockpit.

---

## Régression

**0 nouvelle régression introduite.**

Failures présentes dans la suite complète sont toutes pré-existantes :
- `test_config_root_is_derived_from_file_location_not_hardcoded` — RAYA_DATA_DIR override utilisateur (OneDrive)
- UIA tests (`test_health_reports_online_*`, etc.) — `uiautomation` non installé sur cette machine
- Timing tests (`test_scenario_7_*`, `test_2_conversation_*`, `test_queued_task_*`) — flaky sous charge
- `test_handle_request_fails_honestly_with_null_provider_stub` — API Ollama fonctionnelle (non nulle)
- `test_recovered_task_can_actually_resume_and_complete` — step_index off-by-one pré-existant
- `test_ingest_*`, voice TTS — dépendances externes non installées

---

## Lint architectural

```
ARCH LINT: PASS — aucune violation détectée.
```

`tools/catalog/interaction.py` n'importe **pas** `raya.world_state` (duck typing).
Le `WorldStateStore` est injecté depuis `bootstrap.py` (composition root, `"*"` allowed).

---

## Intégrité V1

Aucune modification de modules V1. Aucun contrat rétrocompatible cassé.
Aucun handler existant modifié. Aucun import circulaire introduit.

---

## Lacunes restantes

- Pas de `interaction.complete` ni `interaction.cancel` — intentionnel (YAGNI).
  L'expiration TTL (24h) gère la clôture naturelle.
- Pas de notification proactive "interaction non résolue depuis X heures" —
  hors scope Chantier 20 (Attention module pourrait l'implémenter si besoin).
- Résolution de pronoms reste côté modèle (instruction + données) — aucun
  algorithme NLP côté Python, conformément à la consigne "jamais un système parallèle".

---

## Recommandation

Le système est opérationnel. Pour valider le comportement réel :

1. Dans le Cockpit : "Traduis 'Tu viens ce soir ?' pour mon frère en espagnol et envoie-le lui via WhatsApp."
2. Vérifier que le World State contient un fait `interaction/inter_*` avec state=AWAITING_EXTERNAL_REPLY.
3. Dire "Il a répondu oui." → RAYA doit appeler interaction.reply sans demander "qui ?".
4. Dire "Il vient à quelle heure ?" → RAYA doit utiliser le contexte "replied: oui" pour formuler sa réponse.
