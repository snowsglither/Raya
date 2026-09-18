# RAYA V2 — Audit : Confirmations + Latence Agentique

---

## Executive Summary

RAYA V2 classifie correctement les actions SAFE/SENSITIVE via `raya/safety/risk.py`. Les clics de navigation simples (`browser.click` sur texte sans verbe dangereux) sont déjà classés **SAFE** et ne devraient pas déclencher de confirmation. La latence observée de 20–30 secondes provient de **2–5 model calls par tour** (3–5s chacun) combinés à l'exécution Playwright (1–3s) et à l'assemblage de contexte (0,5–2s). Le problème de confirmation signalé est probablement lié aux **arguments passés par le modèle** qui contiennent des stems détectés comme dangereux plutôt qu'à une classification incorrecte. La directive modèle interdit explicitement de demander "Is it OK to proceed?" avant une action SAFE — la confirmation vient du layer Safety, pas du modèle lui-même.

---

## Problem A — Confirmation

### Current Risk Classification

| Tool | Tag | PermissionLevel | Confirmation ? |
|------|-----|-----------------|----------------|
| `browser.navigate` | browser.read | SAFE | Non |
| `browser.read_page` | browser.read | SAFE | Non |
| `browser.screenshot` | browser.read | SAFE | Non |
| `browser.list_tabs` | browser.read | SAFE | Non |
| `browser.click` | browser.interact | **CONTEXTUEL** (SAFE si pas de stem dangereux) | Seulement si verbe dangereux détecté |
| `browser.type` | browser.interact | **CONTEXTUEL** | Seulement si contenu contient stem dangereux |
| `browser.dismiss_overlay` | browser.interact | SAFE (intrinsèquement inoffensif) | Non |
| `pc.application.launch` | pc.launch | SAFE | Non |
| `pc.application.close` | pc.interact | SENSITIVE | Oui |
| `pc.window.list` | pc.read | SAFE | Non |
| `pc.window.focus` | pc.launch | SAFE | Non |
| `pc.window.close` | pc.interact | SENSITIVE | Oui |
| `pc.ui.inspect` | pc.read | SAFE | Non |
| `pc.ui.click` | pc.interact | **CONTEXTUEL** (SENSITIVE par défaut, SAFE si sélecteur non-dangereux) | Dépend du sélecteur |
| `pc.ui.type` | pc.interact | **CONTEXTUEL** | Dépend du contenu |
| `pc.keyboard.type` | pc.interact | **CONTEXTUEL** | Dépend du contenu |
| `pc.keyboard.press` | pc.interact | SENSITIVE (pas de contexte sémantique) | Oui |
| `pc.mouse.click` | pc.interact | SENSITIVE (coordonnées opaques) | Oui |
| `pc.mouse.move` | pc.interact | SENSITIVE | Oui |
| `pc.screen.capture` | pc.read | SAFE | Non |
| `pc.process.list` | pc.read | SAFE | Non |
| `pc.filesystem.find_folder` | pc.read | SAFE | Non |
| `pc.filesystem.open_path` | pc.launch | SAFE | Non |
| `pc.capability.discover` | pc.read | SAFE | Non |
| `pc.shell.execute` | pc.shell | SENSITIVE (arbitraire) | Oui |

**Stems dangereux détectés** (`raya/safety/risk.py:56–64`) :
- Suppression : `"delet"`, `"supprim"`, `"effac"`, `"remov"`
- Achat : `"buy"`, `"achet"`, `"purchas"`, `"commande"`, `"checkout"`, `"order"`, `"payer"`
- Envoi/Publication : `"send"`, `"envoi"`, `"envoy"`, `"publi"`, `"post"`
- Validation : `"confirm"`, `"valid"`, `"submit"`

---

### Current Confirmation Path

Flux exact, dans l'ordre d'exécution :

**1. Harness lance l'outil** — `raya/harness/loop.py:540`
```python
tool_result = execute_tool(self._tools_registry, self._safety, tool_call, bus=self._bus)
```

**2. `execute_tool` vérifie la permission** — `raya/tools/execution.py:69–80`
```python
permission = safety.check_permission(
    action_ref=tool.name, capability_tags=tool.capability_tags,
    arguments=call.arguments, user_confirmed=user_confirmed
)
if permission.decision != PermissionDecision.ALLOWED:
    return ToolResult(status=ToolResultStatus.PERMISSION_DENIED, ...)
```

**3. `check_permission` évalue le risque** — `raya/safety/permissions.py:32–76`
```python
risk = classify_risk(capability_tags, arguments)
if risk == PermissionLevel.SAFE:
    decision = PermissionDecision.ALLOWED          # → pas de confirmation
elif user_confirmed:
    decision = PermissionDecision.ALLOWED          # → utilisateur a dit oui
else:
    decision = PermissionDecision.REQUIRES_CONFIRMATION  # → bloqué
```

**4. `classify_risk` applique le contexte** — `raya/safety/risk.py:219–245`
```python
if tag in _CONTEXTUAL_TAGS and arguments is not None:
    texts = _extract_text_values(arguments)
    base = SENSITIVE if _mentions_dangerous_action(arguments) else SAFE
```

**5. Si REQUIRES_CONFIRMATION** — `raya/harness/loop.py:542–575`
```python
state.status = HarnessStatus.AWAITING_USER_INPUT
state.pending_confirmation = {...}
# → publish harness.confirmation_required
# → RAYA dit "Cette action nécessite ta confirmation..."
```

**Point clé** : la confirmation est levée par Safety au niveau tool, **pas par le modèle lui-même**. La directive modèle (`render.py:340–349`) interdit explicitement de demander "Is it OK to proceed?" avant une action SAFE.

---

### Root Cause

**Trois causes possibles selon que le problème est réel ou perçu :**

**Cause A — Arguments du modèle contiennent des stems détectés (la plus probable)**
Le modèle formule ses arguments en termes qui déclenchent les stems. Exemple : au lieu de passer `target="library link"` il passe `target="click on the library to navigate"` → le mot `"confirm"` ou `"submit"` apparaît ailleurs dans les arguments → SENSITIVE déclenché.

**Cause B — `pc.ui.click` / `pc.mouse.click` sans contexte sémantique**
Pour les actions PC (non browser), la classification contextuelle est SENSITIVE par défaut même sans stem dangereux (`raya/safety/risk.py` : `pc.interact` n'a pas de SAFE-par-défaut comme `browser.interact`). Un clic souris sur coordonnées est opaque → SENSITIVE.

**Cause C — Directive modèle manquante pour guidance comportementale**
Le system prompt (`render.py`) n'instruit pas explicitement le modèle à préférer `browser.click` (contextuel, potentiellement SAFE) à `pc.mouse.click` (toujours SENSITIVE) pour les navigations UI. Le modèle peut donc choisir la primitive plus contraignante.

---

### Classification des exemples

| Cas | Tool probable | Dangerous stem ? | Risk réel | Confirmation ? | Correct ? |
|-----|---------------|-----------------|-----------|----------------|-----------|
| A. "Va dans la bibliothèque" | `browser.click` sur lien | Non | **SAFE** | Non | ✅ |
| B. "Ouvre Steam" | `pc.application.launch` | Non | **SAFE** | Non | ✅ |
| C. "Recherche Minecraft" | `browser.type` + `browser.click` | Non | **SAFE** | Non | ✅ |
| D. "Ouvre cette vidéo" | `browser.click` sur bouton play | Non | **SAFE** | Non | ✅ |
| E. "Ajoute ce produit au panier" | `browser.click` sur "add to cart" | **Oui** (`"cart"` → `"commande"`) | **SENSITIVE** | **Oui** | ✅ |
| F. "Achète ce produit" | `browser.interact` checkout | **Oui** (`"buy"`, `"checkout"`) | **SENSITIVE** | **Oui** | ✅ |
| G. "Supprime ce fichier" | `pc.ui.click` sur "delete" | **Oui** (`"delet"`, `"supprim"`) | **SENSITIVE** | **Oui** | ✅ |
| H. "Envoie ce message" | `browser.type` + `browser.click` "send" | **Oui** (`"send"`, `"envoi"`) | **SENSITIVE** | **Oui** | ✅ |
| I. "Ferme cette fenêtre" | `pc.window.close` | Non (mais tag SENSITIVE) | **SENSITIVE** | **Oui** | ⚠️ Discutable |

**Cas I** : `pc.window.close` est classé `pc.interact` SENSITIVE alors que fermer une fenêtre est généralement réversible. C'est le seul cas discutable.

---

### Recommended Minimal Fix

**Si des confirmations apparaissent sur des cas A/C/D :** le problème est dans la formulation des arguments par le modèle. Le fix minimal est une directive dans le system prompt :

> "When calling `browser.click` or `pc.ui.click` for navigation steps, pass a concise target description without imperative verbs — prefer `target='library tab'` over `target='click on the library link to navigate there'`."

**Si `pc.ui.click` confirme systématiquement :** ajouter `"pc.interact"` dans `_CONTEXTUAL_TAGS` de `raya/safety/risk.py` avec le même comportement que `"browser.interact"` (SAFE par défaut, SENSITIVE si stem dangereux). Changement : 2 lignes dans `risk.py`.

**Ne pas toucher** : la classification de `browser.interact`, les stems détectés, `check_permission()`, ni les cas E/F/G/H qui fonctionnent correctement.

---

## Problem B — Latency

### Latency Trace

Trace estimée pour **"Va dans la bibliothèque"** (navigation simple) :

| Étape | Source | Durée estimée | Notes |
|-------|--------|---------------|-------|
| Telegram reception | Réseau externe | 0,1–0,2s | Latence réseau/polling |
| Context assembly | `harness/loop.py:219–228` → `assembler.py` | 0,5–1,5s | SQLite reads (memory + world_state + tasks) + ranking |
| Render system prompt | `context_engine/render.py` | 0,1–0,2s | Sérialisation des sections |
| **Model call #1** (décision) | `harness/loop.py:483` | **3–5s** | Réseau vers provider + inférence |
| Security check | `tools/execution.py:61–80` | 0,05s | `classify_risk()` + `check_permission()` |
| Browser.click execution | `devices/browser/agent.py:113–121` | 1–3s | Playwright locator + DOM settle |
| World State update | `harness/loop.py:333–370` | 0,1–0,2s | `_promote_observations_and_verify()` → SQLite write |
| Verification | `harness/loop.py:585–586` | 0,05s | `verify_tool_result()` |
| **Model call #2** (vérification) | `harness/loop.py:483` (reboucle) | **3–5s** | Directive render.py:373 impose `browser.read_page` après action mutante |
| browser.read_page | `devices/browser/agent.py:94–97` | 0,5–1s | Évaluation JS pour structure DOM |
| Telegram response | Réseau externe | 0,1–0,2s | |
| **TOTAL cas nominal** | | **8–16s** | |
| **TOTAL avec retry/confusion** | | **20–30s** | +1 model call si modèle hésite |

### Nombre de model calls

**Tâche simple (clic unique de navigation)** : **2 model calls minimum**
1. Model call #1 → décide `browser.click`
2. Exécution
3. Model call #2 → lit le résultat, déclare succès (directive render.py:373 force lecture de page après action mutante)

**Tâche modérée (navigation + sélection)** : **4–6 model calls**
1. → `browser.navigate` ou `browser.click`
2. → `browser.read_page` (verification)
3. → `browser.click` cible suivante
4. → `browser.read_page` (verification)
5. → déclaration succès

**Maximum configurable** : `harness/loop.py:138` — `max_tool_iterations = 4` soit jusqu'à **8 model calls par tour** dans le pire cas.

---

### Top 3 Latency Causes

#### CAUSE #1 — Model latency (dominant)

**PREUVE** : `raya/harness/loop.py:483` — chaque appel `model_route()` est un roundtrip HTTP vers un provider distant.

**IMPACT ESTIMÉ** : 3–5s × 2–5 calls = **6–25s par tour**. C'est la cause principale.

**FIX POSSIBLE** :
- Réduire le nombre de model calls en évitant la boucle observe→model→observe quand l'observation précédente est encore fraîche (TTL World State)
- Implémenter un cache de contexte entre deux model calls consécutifs dans le même tour (`self._last_context` existe déjà à `harness/loop.py:162` mais n'est pas utilisé pour éviter les re-assemblages)

**RISQUE** : Faible si le cache est invalidé correctement sur chaque tool result.

---

#### CAUSE #2 — Directive "observe après action mutante" (structurelle)

**PREUVE** : `raya/context_engine/render.py:373–381`
> *"After any mutating browser action, use browser.read_page to observe the actual effect before declaring success."*

Cette directive force systématiquement un cycle supplémentaire `browser.read_page` → model call après chaque `browser.click` ou `browser.type`. Pour une navigation en 3 clics : **3 cycles = +3 model calls = +9–15s**.

**IMPACT ESTIMÉ** : +3–5s par action mutante.

**FIX POSSIBLE** : Affiner la directive pour distinguer :
- Actions **structurellement vérifiables** sans read_page (ex: navigate → URL change déjà observé en World State) → pas de read_page obligatoire
- Actions **nécessitant confirmation visuelle** (ex: formulaire soumis → confirmation page) → read_page requis

**RISQUE** : Moyen. Réduire les observations peut masquer des échecs silencieux. À ne pas supprimer complètement.

---

#### CAUSE #3 — Context assembly à chaque itération

**PREUVE** : `raya/harness/loop.py:219–228` — `assemble()` est appelé **à chaque itération de la boucle** (avant chaque model call), pas seulement au début du tour.

```python
# Dans la boucle principale :
context = self._context_engine.assemble(session_id, task_id, ...)  # ← Appelé N fois
```

Chaque `assemble()` effectue plusieurs requêtes SQLite (memory search, world state retrieve, tasks list) + ranking + trim.

**IMPACT ESTIMÉ** : 0,5–1,5s × 2–5 itérations = **1–7,5s par tour**.

**FIX POSSIBLE** : Réutiliser le contexte assemblé du model call précédent si aucun WorldStateFact n'a changé depuis. Signal : vérifier si `_promote_observations_and_verify()` a écrit de nouveaux facts → si non, réutiliser `self._last_context`.

**RISQUE** : Faible si l'invalidation est correcte.

---

### Recommended Optimizations

Ordonné par rapport impact/risque :

1. **Context assembly incrémental** — Ne réassembler que si de nouveaux facts ont été écrits depuis le dernier model call. Gain : 0,5–1,5s par itération. Risque : faible. (`harness/loop.py` + `assembler.py`)

2. **Affiner directive "observe après action"** — Distinguer navigations (URL change = preuve suffisante) des soumissions de formulaires (read_page nécessaire). Gain : 3–5s pour navigations simples. Risque : moyen, à valider empiriquement.

3. **Métriques par model call** — Wrapper `time.monotonic()` autour de `model_route()` dans `harness/loop.py:483`. Gain direct : aucun, mais permet de localiser précisément où vont les 20–30s. Effort : 30 min.

4. **Ajouter TTL court sur les observations de navigation** — Si `browser.current_url` n'a pas changé depuis le dernier cycle, ne pas relancer `browser.read_page`. Gain : 1–2s. Risque : faible.

5. **Ne pas batcher / paralléliser les tool calls** — La dépendance séquentielle (click → observe → click suivant) rend le batching incorrect pour la plupart des séquences de navigation.

---

### Risks

**Ne pas optimiser** :

- **`verify_tool_result()`** (`harness/loop.py:585`) — Seule protection contre les hallucinations. Supprimer = risque d'actions silencieusement échouées.
- **`max_tool_iterations`** sous 2 — Même une action triviale nécessite decision + feedback.
- **La classification contextuelle de `browser.interact`** — Protège contre les achats/suppressions accidentels.
- **World State** — Contient l'état réel observé. Le supprimer ou le cacher invalide la vérification.
- **Memory retrieval** — Contient les préférences et faits confirmés. L'exclure dégrade la personnalisation.

---

## Directives Modèle

Citations exactes des directives envoyées au modèle (`raya/context_engine/render.py`) :

**Sur la confirmation (render.py:340–349)** :
> *"Ask only when it genuinely matters: ask the user to choose only when (1) two or more reasonable options exist that lead to materially different outcomes, AND (2) the user's preference for this type of choice is unknown from context or memory. Never ask 'Is it OK to proceed?' or 'Should I continue?' before a safe, reversible, clearly requested action — just do it."*

**Sur les observations (render.py:48–52)** :
> *"If you claim something about the current environment (what is open, what state something is in, whether an action already happened), base it on the Observed environment state / tool results shown to you — never invent it."*

**Sur la vérification après action mutante (render.py:373–381)** :
> *"Browser objective verification — a browser tool returning success (browser.click, browser.type, browser.navigate) is not the same as the objective being met. After any mutating browser action, use browser.read_page to observe the actual effect before declaring success."*

**Sur la complétion (render.py:59–65)** :
> *"When the user's request implies a final action (adding something to a cart, sending something, saving a change, confirming an order, etc.), merely finding or displaying the right target is NOT success — you must actually perform that final action and verify it happened before telling the user it is done."*

**Sur le steering (render.py:357–367)** :
> *"When the user provides a new instruction or constraint while a task is already running, treat it as a steering directive, not a new task. Preserve all work already verified as complete — do not undo or repeat it."*

**Observation** : Il n'existe aucune directive explicite guidant le modèle sur le choix entre `browser.click` (contextuel, potentiellement SAFE) et `pc.mouse.click` (toujours SENSITIVE). C'est un gap de directive.

---

## V1 Integrity

Aucun changement proposé dans cet audit ne touche :
- Les mécanismes V1 extraits (`mechanisms/applications.py`, `mechanisms/window_mgmt.py`)
- La structure `_CAPABILITIES` de `WindowsDeviceAgent`
- La classification Safety existante pour les cas E/F/G/H (achats, suppressions, envois)
- Le `LoopDetector` et ses seuils
- La logique de vérification (`verify_tool_result`)

---

## Implementation Plan

**Proposition uniquement — aucun code modifié dans cet audit.**

| Priorité | Action | Fichier | Effort | Impact | Risque |
|----------|--------|---------|--------|--------|--------|
| 1 | Ajouter métriques `time.monotonic()` autour de `model_route()` pour localiser la latence | `harness/loop.py:483` | 30 min | Diagnostic | Très faible |
| 2 | Valider empiriquement cas A/D/I via logs `tool_name + risk + decision` | `tools/execution.py` | 30 min | Diagnostic | Très faible |
| 3 | Ajouter directive modèle : préférer target concis sans verbes impératifs pour navigations | `context_engine/render.py` | 15 min | Réduit confirmations parasites | Faible |
| 4 | Context assembly incrémental : réutiliser si aucun fait nouveau depuis dernier model call | `harness/loop.py` + `assembler.py` | 2–3h | −0,5–1,5s par itération | Moyen |
| 5 | Affiner directive "observe après action" : URL change = preuve suffisante pour navigation | `context_engine/render.py` | 1h | −3–5s pour navigations | Moyen |

**Ordre recommandé** : 1 → 2 (diagnostic d'abord) → 3 (changement non-risqué) → décision sur 4 et 5 après validation empirique.
