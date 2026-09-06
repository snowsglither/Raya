# RAYA V2 — ARCHITECTURE FINAL REVIEW

**Statut :** Revue de cohérence finale avant Phase 0. Aucune modification du repo V1. Aucun code écrit. Aucune migration exécutée.
**Portée des corrections appliquées :** pour les points 1, 2 et 3 ci-dessous, l'utilisateur a explicitement autorisé l'ajout de contrats/règles manquants directement dans les documents d'architecture existants ("ajoute", "décide et documente", "ajoute un contrat explicite"). Ces corrections ont été appliquées par édition ciblée de `RAYA_V2_TECHNICAL_ARCHITECTURE.md` et `RAYA_V2_CONTRACTS.md` (nouvelles sections §13/§14 et §1.1/§18, plus corrections de formulation aux endroits contradictoires). Pour les points 4 à 11, la portée demandée était la vérification ("vérifie que...") — les constats sont reportés ici avec correction minimale recommandée, **non appliqués aux 5 documents**, pour rester strictement dans le périmètre demandé et éviter de réécrire l'architecture.

---

## 1. EVENT BUS

**MISSING CONTRACT — CORRIGÉ.**

**Localisation :** le concept d'`Event` était défini dans `RAYA_V2_CONTRACTS.md` §1 et catalogué par subsystem dans `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §1 ("Events émis"/"Events consommés" pour chacun des 16 subsystems), mais aucun document ne définissait le MÉCANISME de transport (publish/subscribe, ordering, backpressure, propagation). Chaque subsystem décrivait ce qu'il émet/consomme comme si le transport allait de soi.

**Pourquoi c'est un problème :** sans mécanisme explicite, l'implémentation aurait pu dériver de deux façons dangereuses — soit un appel de fonction déguisé en "event" (recréant du couplage direct point-à-point, contradiction avec la règle de dépendance descendante), soit un composant central qui inspecte le contenu des events pour "décider" quoi faire (recréant un second orchestrateur, exactement le risque que l'utilisateur signale).

**Correction appliquée :**
- `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §13 (nouvelle section) — API `publish/subscribe/unsubscribe`, règle d'ordering par `(correlation_id, subscriber)` sans ordre global, deux politiques de backpressure explicites (`drop_oldest` pour l'observability best-effort, `block_publisher_with_timeout` pour les abonnés critiques comme `safety`), et une règle absolue : l'EventBus ne lit jamais `payload`, ne filtre jamais par état métier, ne devient jamais un second orchestrateur.
- `RAYA_V2_CONTRACTS.md` §1.1 (nouvelle sous-section) — forme du contrat `EventBus`.
- Clarification explicite : l'EventBus n'est **pas un 17e subsystem métier**, c'est une infrastructure transversale au même titre que `contracts/` — tout le monde peut la utiliser, elle ne dépend de rien, elle ne possède aucune logique de décision.

**Statut final :** PASS après correction.

---

## 2. STOP / SAFETY / INTERFACES

**CONTRADICTION — CORRIGÉE.**

**Localisation exacte de la contradiction (avant correction) :**
- `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §1.14 (interfaces) listait `safety` parmi les "dépendances interdites" — "tout passe par le Harness".
- Mais §1.12 (safety), §3.3 et §10.3 décrivaient tous les trois `continuous_voice.py` comme "appelant `safety.request_stop()` directement" et qualifiaient ce comportement de "VALIDE en tant que déclencheur".

**Pourquoi c'est un problème :** deux règles contradictoires dans le même document — une interface qui appelle `safety.request_stop()` par import direct viole l'invariant "Interfaces ne parlent qu'au Harness" (Invariant #20 de `RAYA_V2_ARCHITECTURAL_INVARIANTS.md`), qui existe précisément pour empêcher qu'un canal développe sa propre logique de décision hors du Harness — c'est l'anti-pattern central identifié dans l'audit V1 (`continuous_voice.py` court-circuitant déjà `safety`/`async_engine` directement). Laisser cette contradiction non résolue aurait garanti sa reproduction en V2 dès le premier canal voix implémenté.

**Correction appliquée — règle unique verrouillée :**
```
Interface → publie Event "interface.stop_requested" (jamais un appel direct)
    → EventBus (in-process, livraison quasi instantanée)
    → Safety s'abonne, met à jour SON flag STOP interne (une fois, à la réception)
    → Harness / Tasks / Tools / Devices continuent de lire ce flag en SYNCHRONE
      direct via safety.should_stop() — jamais via l'EventBus
```
Une interface peut donc **déclencher** STOP (en publiant un Event — ce n'est pas une dépendance vers `safety`, seulement vers l'EventBus + `contracts.Event`, tous deux transversaux), mais ne décide jamais QUOI annuler (ça reste `attention`+`harness`, cf. §3.3) et n'importe jamais `safety` par un appel de fonction direct. La garantie de latence de la vérification STOP (invariant le plus strict du système) reste intacte car seule l'ÉCRITURE initiale du flag transite par un event — chaque LECTURE reste un appel synchrone.

**Documents corrigés :** `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §1.12, §1.14, §3.3, §10.3 (formulations alignées) ; `RAYA_V2_ARCHITECTURAL_INVARIANTS.md` nouvel invariant #21 + mise à jour de la ligne `continuous_voice.py` dans le tableau §4.1.

**Statut final :** PASS après correction.

---

## 3. IDEMPOTENCE / CRASH RECOVERY

**MISSING CONTRACT — CORRIGÉ.**

**Localisation :** `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §3.2 (tableau "Qui possède chaque responsabilité transversale", ligne "Recovery") décrivait la reprise au niveau du PLAN ("`harness` recharge le dernier `HarnessState` checkpointé et reprend à l'étape appropriée") mais ne traitait pas explicitement le cas d'une action à EFFET EXTERNE dont l'issue est inconnue au moment du crash.

**Pourquoi c'est un problème :** un checkpoint de plan ("j'en étais à l'étape 3") ne dit rien sur si l'ACTION de l'étape 3 elle-même (ex : téléchargement d'un fichier, envoi d'un email, écriture d'un fichier) a réellement abouti avant le crash. Sans mécanisme dédié, la reprise naïve la plus probable — "relancer l'étape non confirmée comme terminée" — peut soit sauter une action jamais faite (perte silencieuse), soit rejouer une action déjà faite (effet de bord dangereux : fichier téléchargé deux fois, email envoyé deux fois). C'est exactement le scénario que le point 10 (long-running task) doit supporter sans risque.

**Correction appliquée :** nouveau contrat `ExecutionRecord` (`RAYA_V2_CONTRACTS.md` §18) avec `operation_id` (stable à travers les retries), `idempotency_key`, `execution_state` (NOT_STARTED/EXECUTING/COMPLETED/UNKNOWN), `verification_state` (NOT_VERIFIED/VERIFIED_SUCCESS/VERIFIED_FAILURE/UNVERIFIABLE), plus `Tool.idempotent: bool` (nouveau champ, `RAYA_V2_CONTRACTS.md` §9) et `ToolCall.operation_id`/`ToolCall.idempotency_key` (nouveaux champs, §10). Règle de récupération complète dans `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §14 :
- `EXECUTING` retrouvé après crash → toujours réinterprété comme `UNKNOWN`, jamais `COMPLETED` ni `NOT_STARTED`.
- `Tool.idempotent=true` → retry direct sûr avec le même `idempotency_key`.
- `Tool.idempotent=false` → `cognition.verify()` obligatoire avant toute décision ; si `UNVERIFIABLE` → escalade utilisateur via l'ambiguïté bloquante (`cognition.resolve_ambiguity()`), **jamais** de retry silencieux ni de faux `COMPLETED`.
- L'écriture `execution_state=EXECUTING` est **synchrone et durable, avant** l'appel au Device — c'est cette séquence qui rend le crash détectable.

**Documents corrigés :** `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §3.2 (ligne Recovery) + nouvelle §14 ; `RAYA_V2_CONTRACTS.md` §9, §10, nouvelle §18 ; `RAYA_V2_ARCHITECTURAL_INVARIANTS.md` nouvel invariant #22.

**Statut final :** PASS après correction.

---

## 4. TOOL → SAFETY → DEVICE

**PASS.**

Le flux documenté dans `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §8.1 ("Pipeline d'un appel d'outil") correspond exactement à celui demandé : Harness demande une capability → Tool Discovery → Tool Validation → Permission Check (Safety) → Execution (délégué au Device) → Verification (Cognition) → Retry/ToolResult → Audit. Aucun champ des contrats `Tool`/`ToolCall`/`ToolResult` ne permet à un outil de s'auto-autoriser, et `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §1.10 liste explicitement "bypasser `safety`" dans les "Ne doit JAMAIS faire" de `tools`.

**RECOMMENDATION (mineure, non bloquante) :** `ToolCall` (`RAYA_V2_CONTRACTS.md` §10) ne porte pas de référence explicite vers la `Permission` qui a autorisé l'appel — l'audit trail peut aujourd'hui reconstruire ce lien via `correlation_id` + `audit_id` de `Permission`, mais un champ `permission_ref: ULID | null` directement sur `ToolCall` rendrait la traçabilité "quelle permission a autorisé cet appel précis" triviale sans reconstruction. Non bloquant pour Phase 0 — à considérer si l'audit trail s'avère difficile à requêter en pratique.

---

## 5. ATTENTION

**PASS.**

`RAYA_V2_TECHNICAL_ARCHITECTURE.md` §1.4 liste explicitement en "dépendances interdites" `models`, `tools`, `devices`, `harness`, et §7.3 formule quasi mot pour mot la règle demandée : *"L'Attention ne doit pas devenir un deuxième orchestrateur. Elle décide CE QUI mérite de l'attention. Le Harness décide COMMENT l'exécuter."* `AttentionDecision` (`RAYA_V2_CONTRACTS.md` §4) est strictement limité aux 4 valeurs `PROCESS_NOW/BACKGROUND/INTERRUPT/IGNORE` — aucun champ ne permet d'y encoder un choix d'outil ou une instruction d'exécution. Aucune correction nécessaire.

---

## 6. WORLD STATE / STALENESS

**ISSUE.**

**Localisation :** `RAYA_V2_CONTRACTS.md` §2 (`WorldStateFact`) définit correctement `status: active | stale | superseded` et l'invariant "un fait `stale` reste consultable mais ne doit plus être considéré comme fiable par `context.assemble()` sans le signaler". Mais `RAYA_V2_CONTRACTS.md` §14 (`Context.sections[]`) ne porte, pour une section `kind: world_state`, qu'un `content: object` générique et un `rank_score` — aucun champ garanti ne transporte explicitement le `status`/`freshness_ttl_s` du fait jusque dans le contexte assemblé.

**Pourquoi c'est un problème :** le principe "un fait stale peut être fourni au modèle uniquement avec son statut de fraîcheur clairement conservé" dépend aujourd'hui d'une convention informelle (mettre `status` dans le `content` libre) plutôt que d'un champ garanti par le contrat — un point d'implémentation pourrait facilement l'omettre sans qu'aucune validation de schéma ne le détecte.

**Correction minimale recommandée (non appliquée, à valider) :** ajouter à `Context.sections[]` un champ explicite `freshness: { status: active | stale | superseded, as_of: ISO8601 } | null` (null pour les sections qui ne sont pas de type `world_state`, ex : `tool_schemas`). Le Context Engine devient alors structurellement incapable d'omettre l'information de fraîcheur pour une section `world_state` — la validation de schéma peut l'exiger dès que `kind=world_state`.

---

## 7. MODEL ROUTER

**RECOMMENDATION.**

**Localisation :** `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §4.4 définit déjà un ordre en 4 étapes (capability match → availability → contrainte explicite → score pondéré qualité/latence/coût), ce qui est proche de la simplicité demandée, mais ne nomme pas explicitement une étape "préférence local/cloud" ni "fallback" comme demandé — la préférence local/cloud est actuellement noyée dans le "score pondéré", et le fallback multi-provider n'est mentionné qu'en §4.6 (connaissances opérationnelles à préserver de `RoutedMessages`), pas dans le pipeline de routing lui-même.

**Pourquoi ce n'est pas bloquant :** le comportement résultant est déjà simple (4 étapes, pas de scoring multi-critère complexe type VRAM/coût précis dès V2 initial) — c'est une question de LISIBILITÉ du pipeline documenté, pas de complexité réelle cachée.

**Correction minimale recommandée (non appliquée) :** reformuler §4.4 en exactement 5 étapes nommées explicitement — `capability match (filtre dur) → availability (filtre dur) → qualité minimale (seuil, pas score) → préférence local/cloud (poids configurable, cloud par défaut car RAYA est Ollama Cloud-first) → fallback (si le premier choix échoue à l'exécution, essayer le suivant dans l'ordre)` — et déplacer la note "les critères avancés (VRAM précis, coût monétaire fin, latence mesurée en continu) sont V2+ futurs, pas requis Phase 3" en tête de §4.4 plutôt qu'implicite.

---

## 8. CONTRACTS — top-level / subtypes / internal implementation types

**PASS, avec une clarification recommandée.**

Vérification : sur les 18 contrats de `RAYA_V2_CONTRACTS.md` (17 initiaux + `ExecutionRecord`), la distinction est globalement respectée — `ContentPart` (sous-type de `ModelRequest`/`ModelResponse`) et `ErrorInfo` (type commun §0) ne sont pas comptés comme contrats de premier niveau séparés ; `Capability`/`Command`/`Result`/`Health` sont regroupés sous `Device` (§16) comme une famille plutôt que 4 contrats indépendants. Aucun cas trouvé d'une structure interne triviale artificiellement élevée au rang de contrat public.

**RECOMMENDATION (mineure) :** le document ne déclare nulle part EXPLICITEMENT cette distinction à trois niveaux (top-level / subtype / implémentation interne d'un Provider Adapter par ex.) — elle est appliquée en pratique mais pas énoncée en règle. Ajouter une phrase en tête de `RAYA_V2_CONTRACTS.md` §0 : *"Un nouveau contrat de premier niveau ne se justifie que s'il traverse une frontière entre deux subsystems distincts documentée en §1 de l'Architecture Technique. Une structure interne à un seul subsystem (ex : le format de payload spécifique à `providers/ollama_cloud.py`) n'est jamais un contrat public — elle reste un détail d'implémentation non documenté ici."* Non bloquant, utile pour éviter la dérive "un contrat par structure" en Phase 3+ quand le nombre de tools/devices concrets augmentera.

---

## 9. DEPENDENCY GRAPH

**Constat global : PASS sur l'essentiel des règles, une ISSUE de cohérence inter-documents trouvée.**

Vérification point par point demandée par l'utilisateur :

| Vérification demandée | Résultat |
|---|---|
| Circular dependencies | **PASS** — le graphe déclaré en `RAYA_V2_REPOSITORY_STRUCTURE.md` §20 est strictement descendant (`interfaces → harness → {cognition,tasks,context_engine} → {memory,world_state} → tools → safety → devices`, `models`/`contracts`/`observability` en feuilles), aucun cycle trouvé en le parcourant avec les "dépendances autorisées/interdites" déclarées par chaque subsystem en Architecture §1. |
| Hidden imports | **1 ISSUE trouvée** (détail ci-dessous — `context_engine → tools`) |
| Event bus qui deviendrait une dépendance métier | **PASS après correction du point 1** — §13 précise explicitement que l'EventBus ne porte aucune sémantique métier et que s'y abonner n'équivaut pas à dépendre du subsystem émetteur. |
| `interfaces → safety` direct | **CONTRADICTION corrigée** — voir point 2 ci-dessus. |
| `devices → models` | **PASS** — explicitement interdit en Architecture §1.13 ("Dépendances interdites : `models`"), renforcé par l'invariant #5. |
| `models → runtime/tasks/harness` | **PASS** — Architecture §1.11 : "`models` est une feuille, rien au-dessus ne doit fuiter dedans", renforcé par l'invariant #10. |
| `context → write operations` | **PASS** — Invariant #9 l'interdit explicitement ("Context Engine sélectionne, ne persiste jamais"). Note mineure : la liste "Ne doit JAMAIS faire" du subsystem `context` en Architecture §1.7 ne répète pas cette interdiction en toutes lettres (elle ne mentionne que "décider de la logique métier"/"appeler le modèle") — l'invariant #9 la couvre déjà correctement, donc **pas bloquant**, mais une reformulation ajoutant explicitement "ni écrire dans `memory`/`world_state`" à la liste §1.7 renforcerait la symétrie de lecture entre les deux documents. **RECOMMENDATION mineure, non appliquée.** |
| `attention → execution` | **PASS** — Architecture §1.4 : "Ne doit JAMAIS faire : exécuter quoi que ce soit elle-même". |
| `cognition → tools/devices` | **PASS** — Architecture §1.6 liste explicitement `tools`, `devices` dans les dépendances interdites de `cognition`. |

**ISSUE détaillée — `context_engine → tools` :**

**Localisation :** `RAYA_V2_TECHNICAL_ARCHITECTURE.md` §1.7 (context) déclare explicitement `tools` comme dépendance AUTORISÉE ("lecture des schémas, pas exécution" — nécessaire pour construire `ModelRequest.available_tools`). Mais `RAYA_V2_REPOSITORY_STRUCTURE.md` §20 (diagramme de dépendance) ne montre PAS d'arête `context_engine → tools` — le diagramme place `tools` uniquement après `{memory, world_state}` dans la chaîne, sans lien direct depuis `context_engine`.

**Pourquoi c'est un problème :** un lecteur qui implémente `context_engine/assembler.py` en suivant uniquement le diagramme de `RAYA_V2_REPOSITORY_STRUCTURE.md` §20 (le document destiné à configurer le lint d'import en CI) omettrait l'import légitime vers `tools.discover()`, et le lint échouerait sur du code architecturalement correct — ou, pire, quelqu'un ajusterait le lint pour l'autoriser sans revenir vérifier que c'est bien la seule exception voulue.

**Correction minimale recommandée (non appliquée) :** ajouter dans `RAYA_V2_REPOSITORY_STRUCTURE.md` §20 la ligne `context_engine → tools (lecture seule, discovery de schémas uniquement — jamais tools.call())`, à côté de `context_engine → {memory, world_state}` déjà présent implicitement dans le bloc `{cognition, tasks, context_engine} → {memory, world_state}`.

---

## 10. LONG-RUNNING TASK — scénario central

**PASS, conditionnel à la correction du point 3 (déjà appliquée).**

Vérification du scénario demandé contre les contrats/architecture actuels :

| Exigence du scénario | Couverture |
|---|---|
| "Télécharge ce fichier, vérifie, traite, dis-moi quand c'est fini" démarre une tâche de fond sans bloquer | **PASS** — Architecture §3.1 étape 5 : "Action = création/délégation à une Task de fond → `TASKS.create(...)`, réponse immédiate à l'utilisateur." |
| "Quelle est la capitale du Japon ?" pendant que la tâche tourne → réponse immédiate | **PASS** — Architecture §3.2 ligne "Concurrency" : plusieurs `HarnessState` simultanées, explicitement "AUCUN lock global façon `core.orchestrator._process_lock` de V1", et Invariant #7 + `tests/concurrency/` dédiés. |
| STOP interrompt la tâche | **PASS** — `safety.should_stop()` vérifié avant chaque `ToolCall`/`Command` (Architecture §3.1, §1.13), `Task.cancellation_requested` propagé coopérativement (Contracts §5). |
| Crash → reprise depuis checkpoint | **PASS** — `Task.checkpoint` + `HarnessState` persistés (Architecture §3.2 ligne Checkpoint/Recovery). |
| Une action déjà effectuée n'est pas rejouée aveuglément | **PASS uniquement après la correction du point 3** — sans le contrat `ExecutionRecord` ajouté aujourd'hui, ce point précis était un **MISSING CONTRACT** réel (un crash entre le téléchargement effectif et l'écriture du checkpoint aurait pu provoquer un re-téléchargement silencieux au resume). Avec `ExecutionRecord` + la règle de récupération §14.3, le pas "vérifie/retry/escalade" couvre exactement ce cas. |

**Conclusion :** le scénario est intégralement supportable par les contrats tels qu'ils existent maintenant (après correction des points 1-3). Aucune correction supplémentaire nécessaire pour ce point.

---

## 11. MONOLITHE

**PASS, avec une extension recommandée.**

`RAYA_V2_REPOSITORY_STRUCTURE.md` §6 (`harness/`) contient déjà explicitement la règle demandée : *"Aucun fichier ici ne doit dépasser ~400 lignes sans déclencher une revue de découpage — c'est une règle de discipline explicite pour ne pas reconstruire le monolithe sous un nouveau nom."* Le dossier `harness/` est lui-même déjà éclaté en 4 fichiers à responsabilité unique (`loop.py`/`session.py`/`steering.py`/`cancellation.py`), contrairement à `core/orchestrator.py` (2583 lignes, 1 fichier, 1 classe). Aucun autre subsystem de `RAYA_V2_REPOSITORY_STRUCTURE.md` ne présente un fichier unique concentrant plusieurs responsabilités distinctes — `tools/`, `models/`, `devices/windows/`, `memory/`, `safety/` sont tous déjà éclatés par sous-responsabilité.

**RECOMMENDATION (mineure, non appliquée) :** la règle des ~400 lignes n'est explicitement écrite QUE pour `harness/`. `cognition/` (raisonnement + appels modèle, le deuxième candidat le plus probable à accumuler de la complexité au fil du développement) et `tools/execution.py` (retry/timeout/cancellation/audit d'exécution, logique non triviale) n'ont pas la même règle explicite. Étendre la même phrase de discipline à ces deux dossiers dans `RAYA_V2_REPOSITORY_STRUCTURE.md` §7 et §11 renforcerait la garantie sans changer l'architecture elle-même.

---

## Synthèse

| # | Point | Statut final |
|---|---|---|
| 1 | Event Bus | **MISSING CONTRACT → CORRIGÉ** |
| 2 | STOP / Safety / Interfaces | **CONTRADICTION → CORRIGÉE** |
| 3 | Idempotence / Crash Recovery | **MISSING CONTRACT → CORRIGÉ** |
| 4 | Tool → Safety → Device | **PASS** (1 recommandation mineure) |
| 5 | Attention | **PASS** |
| 6 | World State / Staleness | **ISSUE** (correction minimale proposée, non appliquée) |
| 7 | Model Router | **RECOMMENDATION** (clarté de présentation, pas de complexité cachée) |
| 8 | Contracts (top-level/subtype/interne) | **PASS** (1 recommandation mineure) |
| 9 | Dependency Graph | **1 ISSUE** (`context_engine → tools` absent du diagramme §20) + tout le reste **PASS** |
| 10 | Long-running Task | **PASS** (conditionnel à la correction #3, déjà appliquée) |
| 11 | Monolithe | **PASS** (1 recommandation mineure d'extension) |

**Aucune contradiction non résolue ne subsiste.** Les deux `MISSING CONTRACT` et l'unique `CONTRADICTION` identifiés ont été corrigés directement dans `RAYA_V2_TECHNICAL_ARCHITECTURE.md` et `RAYA_V2_CONTRACTS.md` (nouvelles sections §13/§14 et §1.1/§18, formulations §1.12/§1.14/§3.2/§3.3/§10.3 alignées), avec les invariants #21/#22 ajoutés à `RAYA_V2_ARCHITECTURAL_INVARIANTS.md`. Les 3 `ISSUE`/`RECOMMENDATION` restantes (point 6, point 9, plus les recommandations mineures des points 4/7/8/11) sont documentées ci-dessus avec correction minimale précise mais **non appliquées** — hors du périmètre explicitement autorisé pour cette revue, à trancher séparément si tu veux les intégrer avant Phase 0.

**ARCHITECTURE READY FOR IMPLEMENTATION**, sous réserve de ta décision sur les points 6 et 9 (les deux seuls écarts non purement cosmétiques restants) — ni l'un ni l'autre ne remet en cause une frontière ou un contrat déjà écrit, ce sont des compléments de précision.
