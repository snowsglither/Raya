# RAYA V2 — Chantier 20A : Personal Context & Ambiguous Referent Audit

**Date** : 2026-09-14  
**Session** : Chantier 20A (suite Chantier Context Real E2E)  
**Mode** : AUDIT ONLY — AUCUN CODE MODIFIÉ  
**Modèle** : deepseek-v4-flash:cloud (réel, production)  
**Plateforme** : Windows 11 Pro Education — machine réelle  
**DB** : `C:\Users\ruben\OneDrive\RAYA_DATA\raya_v2.sqlite3`  
**Résultat** : 2 findings confirmés, 2 corrections minimales proposées

---

## 1. Executive Summary

Deux problèmes résiduels de l'audit Context Real E2E ont été examinés :

- **F1 — Personal Memory Retrieval** : Les mémoires personnelles non-identitaires sont
  invisible pour des requêtes indirectes à cause du filtre lexical ≥4 chars.
  Confirmé sur la DB réelle. Le modèle compense en cherchant le vault Obsidian
  (latence + fiabilité dégradée). **Fix minimal recommandé** : section PERSONAL_ALWAYS
  analogue à `_identity_baseline_sections()`.

- **F2 — Ambiguous Referent** : 4/5 cas testés : comportement correct. Le cas
  problématique de l'audit précédent (S5) était un artefact de seuil flou dans
  la directive. La directive manque d'une définition précise de "dominant".
  **Fix minimal recommandé** : renforcement d'une ligne dans la directive §18.

---

## 2. Environment

| Composant | Détail |
|-----------|--------|
| Runtime | RAYA V2 complet (Windows Device + Browser Device) |
| Modèle | deepseek-v4-flash:cloud (Ollama Cloud) |
| DB production | 362 entrées total (358 CONVERSATION, 4 PERSONAL) |
| Budget tokens | 4096 (utilisé : 342 tokens — 8.3%) |
| PERSONAL entries | 2 identity + 2 non-identity (CONFIRMED, KNOWN_FACT) |

**DB état — entrées PERSONAL réelles :**

| ID | Provenance | Type | Contenu |
|----|-----------|------|---------|
| mem_01M1XQDZ0Q... | profile_migration:identity | fact | "Nom complet : Jane Doe" |
| mem_01M1XQDZ3N... | profile_migration:identity | fact | "Réside à Testville" |
| mem_01M1XQDZ3W... | profile_migration:centres_d_interet | fact | "ÉCHECS : joue le dimanche" |
| mem_01M1XQDZ3Z... | profile_migration:preferences_de_comportement_comment_agir | preference | "Tutoiement, ton direct" |

Note : les entrées identity ("Jane Doe", "Testville") sont probablement du profil
de test — le nom réel est Ruben Lukusa. L'essentiel pour l'audit est que 2 entrées
non-identitaires existent et sont l'objet d'observation.

---

## 3. Tests exécutés

| ID | Scénario | Résultat | Evidence |
|----|---------|----------|---------|
| F1-K1 | 8 queries indirectes → keyword miss sur "ÉCHECS" | CONFIRMED MISS | 0/8 queries trouvent l'entrée |
| F1-K2 | Query avec "échecs" (accentué) → match | HIT | 1 entry found |
| F1-K3 | Query "echecs" (sans accent) → miss | MISS | accent-sensitivity confirmée |
| F1-K4 | Query "tutoiement" → match | HIT | 1 entry found |
| F1-K5 | Query "comment tu me parles ?" → miss sur "Tutoiement" | CONFIRMED MISS | 0 non-identity |
| F1-C1 | Context assembly avec query indirecte | 0 non-identity sections | tokens=342/4096 |
| F1-E1 | Real E2E : "centres d'intérêt ?" | MEMORY_MISSING | model compensates via Obsidian |
| F1-E2 | Real E2E : "parties d'échecs" | MEMORY_MISSING + vault empty | model: "aucune trace d'échecs" |
| F1-E3 | Real E2E : "comment tu me parles ?" | MEMORY_MISSING | tutoiement déduit du comportement |
| F2-A | CAS A : Calculator seule, "ferme-le" | DIRECT ACTION ✓ | WS active_window = Calculator |
| F2-B | CAS B : Calc+Notepad, WS = Chrome, "ferme-le" | ASKED CLARIFICATION ✓ | "Le mot ferme-le est ambigu" |
| F2-C | CAS C : Calc+Notepad, Calc mise en foreground, "ferme-le" | DIRECT ACTION ✓ | WS active_window = Calculator |
| F2-D | CAS D : typing dans Notepad, "ferme-le" | DIRECT ACTION ✓ | last tool = Notepad |
| F2-E | CAS E : Notepad real env, pas de contexte, "ferme-le" | DIRECT ACTION (débatable) | WS = Notepad réel |

---

## 4. F1 — Personal Memory Retrieval : Architecture complète

### Chemin observé

```
MemoryStore.search(query=query_text, channel_scope=SHARED)
    │
    ├─ filtre channel_scope (SHARED ou scope demandé)
    ├─ filtre lifecycle != OBSOLETE
    ├─ filtre keyword ← ROOT CAUSE
    │     _significant_words(query) = [w for w in query.lower().split() if len(w) >= 4]
    │     si words != [] :
    │         entries = [e for e in entries if any(w in str(e.content).lower() for w in words)]
    ├─ sort par (lifecycle_weight + text_bonus + recency_bonus, seq)
    └─ return [:limit]  # default limit=20
    
    ↓
    
_memory_sections(memory, channel_scope, query_text)
    ├─ exclut MemoryLayer.CONVERSATION
    ├─ exclut MemoryLayer.EXPERIENCE
    ├─ exclut identity_ids (déjà dans _identity_baseline_sections)
    └─ score = lifecycle_weight * confidence_weight
    
    ↓
    
render_system_prompt()
    ├─ type=fact    → "Known, confirmed fact about the user: ..."
    ├─ type=preference → "Known, confirmed user preference: ..."
    └─ type=rule     → "Persistent rule (always apply): ..."
```

### Ranking scoring

```python
_LIFECYCLE_WEIGHT = {CONFIRMED: 1.0, ACTIVE: 0.8, CANDIDATE: 0.5, AGING: 0.3, OBSOLETE: 0.0}
score = lifecycle_weight + text_match_bonus (0.1 × min(occurrences, 5)) + recency_bonus (0.2 − hours × 0.001)
```

Score pour "ÉCHECS : joue le dimanche" (CONFIRMED, KNOWN_FACT) :
- Sans keyword match : `score = 1.0 + 0 + ~0.2 = ~1.2` → MIS EN LISTE mais filtrée avant scoring

**Distinction clé** : le filtre keyword s'applique AVANT le scoring. Les entrées qui ne passent
pas le filtre ne sont jamais scorées ni limitées — elles n'existent tout simplement pas.

### Accent sensitivity

Le filtre compare `query.lower()` vs `str(content).lower()` :
- "ÉCHECS" → "échecs" dans le contenu
- "echecs" (sans accent) dans la requête ≠ "échecs"
- Conséquence : même "mes echecs" (sans accent, typique au clavier français sans effort) manque l'entrée

---

## 5. F1 — Observations réelles

### Queries testées contre "ÉCHECS : joue le dimanche"

| Query | Mots significatifs (≥4) | Match | Résultat |
|-------|------------------------|-------|---------|
| "mes echecs du dimanche" | [echecs, dimanche] | dimanche ✓ | HIT |
| "mes activités du week-end ?" | [activites, week-end] | aucun | MISS |
| "mes habitudes ?" | [quelles, sont, habitudes] | aucun | MISS |
| "mes loisirs ?" | [quels, sont, loisirs] | aucun | MISS |
| "mes centres d'intérêt ?" | [quels, sont, centres, interet] | aucun | MISS |
| "que fais-je le week-end ?" | [fais-je, pendant, week-end] | aucun | MISS |
| "qu'est-ce que tu sais de moi ?" | [est-ce, sais] | aucun | MISS |
| "bonjour, tu te souviens de moi ?" | [bonjour, souviens] | aucun | MISS |
| "echecs" (sans accent) | [echecs] | aucun | MISS |
| "échecs" (avec accent) | [échecs] | échecs ✓ | HIT |

### Queries testées contre "Tutoiement, ton direct"

| Query | Mots significatifs (≥4) | Match | Résultat |
|-------|------------------------|-------|---------|
| "tutoiement" | [tutoiement] | tutoiement ✓ | HIT |
| "comment tu me parles ?" | [comment, parles] | aucun | MISS |
| "ton style de communication ?" | [style, communication] | aucun | MISS |

### Comportement modèle observé

Pour "qu'est-ce que tu connais de mes centres d'intérêt ?" :
- Sections mémoire visibles : 0 entrées non-identitaires
- Modèle → 3 appels outils (Obsidian vault search)
- Réponse : hallucine le profil depuis le vault (guitare, gaming, anime) — données réelles du vault
  mais PAS depuis les mémoires RAYA

Pour "parle-moi de mes parties d'échecs" :
- Sections mémoire visibles : 0 entrées non-identitaires (accent mismatch)
- Modèle → vault search → "aucune trace d'échecs dans ton profil/vault"
- La mémoire "ÉCHECS : joue le dimanche" existait en DB RAYA mais était invisible
- Réponse factuellemnt incorrecte (l'info existait, mais pas accessible)

Pour "comment tu me parles normalement ?" :
- "Tutoiement, ton direct" absent du contexte
- Modèle → réponse correcte déduite du comportement de conv_history (tutoiement utilisé naturellement)
- Mais la préférence explicite n'était pas disponible

### Budget utilisation

```
tokens utilisés : 342 / 4096 (8.3%)
sections : 9 (system_rules + 2 identity + 6 world_state)
budget libre : 3754 tokens
coût des 2 entrées non-identity : ~9 tokens supplémentaires
```

---

## 6. F1 — Root Cause

**Racine** : `_memory_sections()` appelle `memory.search(query=query_text)` qui applique
un filtre lexical exact (mots ≥4 chars, case-fold, accent-sensitive) avant scoring.

**Conséquence directe** : Les entrées PERSONAL non-identitaires n'apparaissent dans
le contexte que lorsque la requête courante partage exactement les mêmes mots-clés.

**Limites supplémentaires observées** :
1. Accent-sensitivity : "echecs" ≠ "échecs" → miss même sur requête directe sans accent
2. Vocabulary gap : "habitudes" / "loisirs" / "centres d'intérêt" ne matchent pas "ÉCHECS"
3. Le modèle compense via Obsidian vault mais ajoute 2-3 appels outils et peut échouer

**Ce qui fonctionne bien** :
- Identity baseline (`_identity_baseline_sections`) : toujours présent (query=""), correct
- Experience entries : séparées, gérées indépendamment
- Le scoring (lifecycle × confidence) est correct une fois le filtre passé

**Ce qui NE doit PAS changer** :
- Le filtre lexical lui-même est utile pour les layers TASK/PROJECT/WORKING
- La limit=20 par défaut est correcte pour la recherche keyword
- L'identity baseline actuel fonctionne — ne pas fusionner

---

## 7. F1 — Options A/B/C : Comparaison

### Option A — Section PERSONAL_ALWAYS

```python
def _personal_context_sections(
    memory: MemoryStore, channel_scope: ChannelScope,
    exclude_ids: frozenset[str] = frozenset(),
    limit: int = 8,
) -> list[ContextSection]:
    hits = memory.search(query="", channel_scope=channel_scope, limit=200)
    personal = [
        e for e in hits
        if e.layer == MemoryLayer.PERSONAL
        and e.provenance != _IDENTITY_BASELINE_PROVENANCE
        and e.id not in exclude_ids
        and e.lifecycle in (MemoryLifecycle.CONFIRMED, MemoryLifecycle.ACTIVE)
    ]
    # Sort by lifecycle × confidence descending
    personal.sort(key=lambda e: _LIFECYCLE_WEIGHT.get(e.lifecycle.value, 0.5) * ..., reverse=True)
    return [ContextSection(kind=MEMORY, ...) for e in personal[:limit]]
```

| Critère | Évaluation |
|---------|-----------|
| Avantages | Zéro pollution (2 entrées actuelles = ~9 tokens) ; lifecycle guard ; suit le pattern identity ; séparé de keyword-search |
| Inconvénients | Nouvelle fonction (~20 lignes) à maintenir |
| Coût tokens | +9 tokens maintenant, +5-10 tokens/entrée future (negligeable < 0.5% budget) |
| Risque | Si profil croît à 100+ entrées CONFIRMED : limit=8 contrôle la taille |
| Pollution possible | Non — lifecycle filter (CONFIRMED/ACTIVE) exclut CANDIDATE/AGING |
| Compatibilité architecture | Parfaite — suit `_identity_baseline_sections()` exactement |
| Complexité | Faible — ~20 lignes + 1 appel dans `assemble()` |
| Impact performance | Négligeable (1 appel backend query supplémentaire, déjà en cache dans `assemble`) |
| Impact contexte | +0.2% utilisation budget |
| Migration nécessaire | Aucune |
| Tests nécessaires | 5-7 tests unitaires + 2 E2E |

### Option B — Query="" pour PERSONAL dans _memory_sections()

```python
# Dans _memory_sections() :
if entry.layer == MemoryLayer.PERSONAL and entry.provenance != _IDENTITY_BASELINE_PROVENANCE:
    # retrieved via separate pass with query="" — no keyword filter
    pass  # already included
# ...
personal_hits = memory.search(query="", channel_scope=channel_scope)
personal_non_id = [e for e in personal_hits if e.layer == PERSONAL and e.provenance != IDENTITY_PROV]
```

| Critère | Évaluation |
|---------|-----------|
| Avantages | Réutilise `_memory_sections()` existant |
| Inconvénients | Logique mixte dans une fonction (deux passes différentes) ; plus difficile à lire |
| Coût tokens | Identique à Option A |
| Risque | Si CANDIDATE entries présentes : Option A les exclut explicitement, Option B pas sans garde supplémentaire |
| Pollution possible | Possible sans lifecycle guard explicite |
| Compatibilité architecture | Acceptable mais moins propre |
| Complexité | Modérée — modification d'une fonction existante |

### Option C — Embeddings sémantiques

| Critère | Évaluation |
|---------|-----------|
| Avantages | Matching sémantique réel (résoudrait aussi accent + synonymes) |
| Inconvénients | Dépendance modèle embedding ; latence (inference) ; infrastructure | 
| Coût | Élevé — nouvelle dépendance, index à maintenir |
| Risque | Over-engineering pour 2-4 entrées PERSONAL actuelles |
| Recommandation | DEFER — overkill pour le problème identifié |

---

## 8. F1 — Recommended Approach

**Option A** est la correction minimale correcte.

Raisons :
1. Suit exactement le pattern déjà établi par `_identity_baseline_sections()`
2. Lifecycle filter explicite (CONFIRMED/ACTIVE) : zéro risque d'injecter des entrées tentatives
3. Limit configurable (8 par défaut) : protection contre un profil futur très grand
4. Séparation nette des trois catégories de mémoire permanente :
   - `_identity_baseline_sections()` : identité (provenance="profile_migration:identity") — TOUJOURS
   - `_personal_context_sections()` : contexte personnel (autres PERSONAL CONFIRMED/ACTIVE) — TOUJOURS (limité)
   - `_memory_sections()` : faits keyword-matchés — SI pertinent
5. Coût : +20 lignes dans `assembler.py` + 1 appel dans `assemble()`

**Implémentation minimale** (à valider avant modification) :

```
Fichier : raya/context_engine/assembler.py
Fonction : _personal_context_sections() — nouvelle, ~20 lignes
Appel : dans assemble(), après _identity_baseline_sections()
        exclude_ids étendu avec les ids identité ET les ids personnels
```

---

## 9. F2 — Ambiguous Referent : Architecture

### Sources d'information disponibles pour résolution de référent

```
Context à disposition du modèle pour résoudre "ferme-le" :

1. WS active_window (rank=1.0)
   → "Calculatrice", "Bloc-notes", "Chrome (inconnu)", etc.
   → DOMINANT si active_window est exactement l'app en question

2. WS last_clicked_target / last_typed_target / last_typed_text (rank=0.4-0.6)
   → "Search Amazon", "Raspberry Pi"...
   → DOMINANT si le dernier outil a agi sur l'app en question

3. Conversation history (rank=0.95)
   → "T1: ouvre calculatrice", "T2: ouvre bloc-notes", etc.
   → MRU heuristique : dernière app ouverte/utilisée

4. Directive §18 (system_rules, rank=1.0) :
   Priorité : WS → tool results → conv history → tasks → memory
   Condition : "When a single referent is clearly dominant given this context, act"
   → "clearly dominant" est NON DÉFINI explicitement
```

### Safety

Safety (`risk.py`) classe `pc.interact` (window.close, application.close) comme :
- `_CONTEXTUAL_TAGS["pc.interact"] = PermissionLevel.SENSITIVE` (défaut quand no text)
- Si arguments contiennent du texte sans verbe dangereux → SAFE (contextual)
- "fermer" n'est PAS dans `_DANGEROUS_ACTION_STEMS` → SAFE en pratique
- Safety n'est pas la raison de l'ambiguïté — elle traite le contenu de l'action, pas la résolution du référent

---

## 10. F2 — Observations réelles

### CAS A — Calculator seule, "ferme-le"

```
T1: "ouvre la calculatrice" → WS active_window='Calculatrice'
T2: "ferme-le" → DIRECT ACTION : "La calculatrice est fermée."
```

Context T2 : WS active_window='Calculatrice' (rank=1.0)
Verdict : **CORRECT** — WS est le dominant sans ambiguïté.
Root cause analysis : aucun problème.

### CAS B — Calc+Notepad ouverts, WS=Chrome, "ferme-le"

```
T1: "ouvre la calculatrice" → WS active_window='Calculatrice'
T2: "ouvre aussi le bloc-notes" → WS active_window='Bloc-notes' (puis retour Chrome)
T3: "ferme-le" → WS active_window='Claude - Google Chrome'
    → ASKED CLARIFICATION : "Le mot 'ferme-le' est ambigu ici. [...] Dis-moi laquelle"
```

Context T3 : WS active_window=Chrome (inconnu, rank=0.6), conv_history : les deux apps présentes
Verdict : **CORRECT** — WS ne pointe pas vers l'une des deux apps, conv_history montre les deux, modèle demande clarification.
Root cause analysis : comportement attendu et correct.

### CAS C — Calc+Notepad, Calculator remise au premier plan, "ferme-le"

```
T1: ouvre calculatrice | T2: ouvre bloc-notes | T3: mets la calculatrice au premier plan
    → WS active_window='Calculatrice'
T4: "ferme-le" → DIRECT ACTION : "La Calculatrice a été fermée."
```

Context T4 : WS active_window='Calculatrice' (rank=1.0)
Verdict : **CORRECT** — WS dominant, action directe.

### CAS D — Typing dans Notepad, "ferme-le"

```
T1: ouvre calc + bloc-notes
T2: "écris Bonjour dans le bloc-notes" → last_typed_target=Notepad
T3: "ferme-le" → DIRECT ACTION : "J'ai fermé le Bloc-notes."
```

Context T3 : WS last_typed_target = Notepad (tool result from T2), conv_history : typing action visible
Verdict : **CORRECT** — dernier tool result = Notepad = dominant (priorité 2 de la directive).

### CAS E — Pas de contexte conversationnel, WS = Bloc-notes réel

```
T1: "ferme-le" → WS active_window='*| # | Chantier - Bloc-notes'
    → DIRECT ACTION : "J'ai fermé le Bloc-notes."
```

Context T1 : WS active_window = Bloc-notes réel (ouvert depuis l'env Windows)
Verdict : **DÉFENDABLE** — le WS real environment fournit un dominant (Bloc-notes actif). Pas de contexte conversationnel contradictoire. Action réversible.

### S5 (précédent audit) vs CAS B — comparaison

| | S5 (précédent) | CAS B (nouveau) |
|--|----------------|----------------|
| WS active_window | IDE (Code.exe) | Chrome |
| Conv history | Calc T1, Notepad T2 | Calc T1, Notepad T2 |
| Résultat | Fermé Bloc-notes, pas demandé | Demandé clarification |
| Différence | MRU sans confirmer | Ambiguïté reconnue |

La différence S5 vs CAS B suggère que le comportement du modèle est **inconsistant** sur l'évaluation
de "MRU = dominant". Dans S5, il a traité "dernière ouverte" comme dominant. Dans CAS B, identique setup, il a demandé.

---

## 11. F2 — Root Cause

**Racine** : La directive §18 établit la priorité (WS → tool results → conv history) mais
ne définit pas le seuil de "clearly dominant" avec précision suffisante.

Cas précis non couvert :
- WS = app sans rapport + deux apps dans conv_history
- Qui est dominant ? La directive dit "dernière tool result" ou "dernière conversation"
- Le modèle interprète parfois "dernière ouverte dans conv" comme dominant (S5),
  parfois non (CAS B)

**Classification (selon le spec §19)** :
- Le contexte était **présent et suffisant** (WS + conv_history corrects)
- Le modèle avait l'information nécessaire
- **Catégorie E** : "System had information for a dominant candidate but no precise rule for when to act"

**Ce qui fonctionne :**
- Résolution via WS active_window = app RAYA-opened : STABLE et CORRECT (CAS A, C, D)
- Demande de clarification quand WS = app tierce : CORRECT (CAS B)
- Résolution via last tool result : CORRECT (CAS D)

**Ce qui est inconsistant :**
- WS = app tierce + MRU disponible dans conv_history → parfois direct, parfois demande

**Ce qui n'est PAS en cause :**
- Safety (pc.interact/window.close classé SAFE pour "ferme le bloc-notes" → pas de confirmation Safety)
- Context Engine (sections correctement assemblées)
- Cognition (LoopDetector non impliqué)
- World State persistence (correcte)

---

## 12. F2 — Recommended Approach

### Composant responsable

**System directive** (render.py, §18 Chantier 18) — pas Safety, pas Cognition, pas Context Engine.

Raison : la directive fournit déjà la bonne structure (priorité) mais manque le seuil de décision
"dominant vs ambigu". L'ajout d'une ligne de critère explicite suffit.

### Modification minimale proposée

Dans `render_system_prompt()` → directive Chantier 18, remplacer :

> "When a single referent is clearly dominant given this context, act on it without asking."

par :

> "A referent is 'clearly dominant' when: (1) the observed environment state explicitly shows it as active (WS.active_window or last clicked/typed target matches it), OR (2) the most recent tool result in this conversation acted on it specifically. If neither condition is met and two or more referents exist in the conversation, ask for clarification — even if one was opened more recently."

Cette modification :
- Rend le seuil de dominance explicite et testable
- Élimine l'inconsistance S5 vs CAS B (MRU ne compte plus comme "dominant" seul)
- Ne change pas la gestion Safety (fermeture reste un outil SAFE pour des cibles nominales)
- Ne crée pas de nouveau composant
- Reste dans la philosophie "generic rule, never hardcoded case"

---

## 13. Context vs Model vs Cognition vs Safety

### F1

| Composant | Rôle | Statut |
|-----------|------|--------|
| Context Engine | Assemble les sections mémoire via keyword filter | ROOT CAUSE — filtre trop restrictif pour PERSONAL |
| Memory Store | Implémente le filtre lexical `_significant_words()` | Mécanisme correct pour autres layers, trop restrictif pour PERSONAL |
| Model | Compense via Obsidian vault | Contournement — non fiable |
| Safety | Non impliqué | — |
| Cognition | Non impliqué | — |

### F2

| Composant | Rôle | Statut |
|-----------|------|--------|
| Context Engine | Fournit WS + conv_history | CORRECT — toutes les infos présentes |
| System Directive §18 | Définit la priorité de résolution | PARTIEL — seuil de dominance flou |
| Model | Interprète "dominant" | INCONSISTANT sur le cas MRU |
| Safety | classify_risk(pc.interact, args) | Non impliqué — fermeture d'app nommée = SAFE |
| Cognition | LoopDetector, recovery | Non impliqué |

---

## 14. Architecture Impact

### F1 — Impact de Option A

```
raya/context_engine/assembler.py
    ├─ _identity_baseline_sections()   ← INCHANGÉ
    ├─ _personal_context_sections()    ← NOUVEAU (~20 lignes)
    ├─ _memory_sections()              ← INCHANGÉ
    └─ assemble()
        ├─ identity_sections = _identity_baseline_sections(...)
        ├─ personal_sections = _personal_context_sections(...)  ← NOUVEAU appel
        ├─ exclude_ids = identity_ids | personal_ids            ← étendu
        ├─ _memory_sections(..., exclude_ids=exclude_ids)
        └─ ...
```

**Impact :** minimal — 1 nouvelle fonction, 3 lignes modifiées dans `assemble()`.
**Invariants préservés :** read-only, pas de persistence, pas de modèle, déterministe.
**Compatibilité :** complète — aucun autre composant modifié.
**Régression possible :** nulle — section supplémentaire, jamais moins de contenu qu'avant.

### F2 — Impact de la directive renforcée

```
raya/context_engine/render.py
    └─ render_system_prompt()
        └─ directive §18 Chantier 18 : 1 ligne modifiée (~20 mots supplémentaires)
```

**Impact :** une ligne de directive. Le modèle applique des règles plus précises.
**Invariants préservés :** render.py est read-only (lecture du Context, jamais de modification).
**Compatibilité :** complète.
**Régression possible :** très faible — la règle est plus précise, non plus restrictive.

---

## 15. Minimal Implementation Proposal

### Modification 1 — F1 : _personal_context_sections()

**Fichier** : `raya/context_engine/assembler.py`

**Nouvelle fonction** (après `_identity_baseline_sections`, ~20 lignes) :

```python
def _personal_context_sections(
    memory: MemoryStore,
    channel_scope: ChannelScope,
    exclude_ids: frozenset[str] = frozenset(),
    limit: int = 8,
) -> list[ContextSection]:
    """Contexte personnel confirmé — JAMAIS keyword-filtré (même discipline
    que _identity_baseline_sections). Distinct de l'identité :
    - préférences, hobbies, anecdotes, style de communication
    - provenance != _IDENTITY_BASELINE_PROVENANCE
    - lifecycle CONFIRMED ou ACTIVE uniquement (jamais CANDIDATE/AGING)
    - limité pour éviter la pollution si profil très grand."""
    hits = memory.search(query="", channel_scope=channel_scope, limit=500)
    personal = [
        e for e in hits
        if e.layer == MemoryLayer.PERSONAL
        and e.provenance != _IDENTITY_BASELINE_PROVENANCE
        and e.id not in exclude_ids
        and e.lifecycle in (MemoryLifecycle.CONFIRMED, MemoryLifecycle.ACTIVE)
    ]
    personal.sort(
        key=lambda e: _LIFECYCLE_WEIGHT.get(e.lifecycle.value, 0.5)
                      * _CONFIDENCE_WEIGHT.get(e.confidence, 0.5),
        reverse=True,
    )
    return [
        ContextSection(
            kind=SectionKind.MEMORY,
            content={"id": entry.id, "type": entry.type.value, "content": entry.content},
            provenance=entry.provenance,
            rank_score=_LIFECYCLE_WEIGHT.get(entry.lifecycle.value, 0.5),
        )
        for entry in personal[:limit]
    ]
```

**Dans `assemble()`** (après identity_sections, 3 lignes modifiées) :

```python
identity_sections = _identity_baseline_sections(memory, channel_scope)
identity_ids = frozenset(s.content["id"] for s in identity_sections)
candidates.extend(identity_sections)

# NOUVEAU — contexte personnel non-identitaire (CONFIRMED/ACTIVE, sans keyword filter)
personal_sections = _personal_context_sections(memory, channel_scope, exclude_ids=identity_ids)
personal_ids = frozenset(s.content["id"] for s in personal_sections)
candidates.extend(personal_sections)

# exclude_ids étendu
candidates.extend(_memory_sections(memory, channel_scope, query_text, exclude_ids=identity_ids | personal_ids))
```

**Raison** : toujours disponible, lifecycle guard, limite explicite, suivi du pattern existant.
**Risque** : minimal — régression nulle, 9 tokens supplémentaires actuellement.
**Compatibilité** : complète.

---

### Modification 2 — F2 : Directive dominance threshold

**Fichier** : `raya/context_engine/render.py`

**Dans `render_system_prompt()`**, directive Chantier 18 (autour de la ligne 285) :

**Remplacer** :
```
"When a single referent is clearly dominant given this context, act on it without "
"asking. Only ask for clarification when two or more referents are genuinely equally "
"plausible after consulting all available context. "
```

**Par** :
```
"A referent is 'clearly dominant' when: (1) the observed environment state shows it "
"as the active window or last interacted target (WS.active_window, last_clicked_target, "
"last_typed_target), OR (2) the most recent tool result in this conversation explicitly "
"acted on it. Act on a clearly dominant referent without asking. "
"If neither condition is met and two or more referents exist in the conversation "
"(including an app that was 'opened most recently' but not since then been the explicit "
"active target), that is genuine ambiguity — ask a short clarifying question. "
"Only ask for clarification when two or more referents are genuinely equally "
"plausible after consulting all available context. "
```

**Raison** : rend le seuil de dominance explicite — évite l'inconsistance S5 vs CAS B.
**Risque** : faible — comportement légèrement plus strict, plus prévisible.
**Compatibilité** : complète — directive uniquement, aucun autre composant modifié.

---

## 16. Automated Tests Recommended

| # | Test | Type | Protège |
|---|------|------|---------|
| T1 | `_personal_context_sections()` CONFIRMED entries returned without keyword | unit | Option A comportement de base |
| T2 | `_personal_context_sections()` exclut identity_ids | unit | Pas de doublon avec identity |
| T3 | `_personal_context_sections()` exclut CANDIDATE/AGING lifecycle | unit | Lifecycle guard |
| T4 | `_personal_context_sections()` respecte limit=8 | unit | Pollution control |
| T5 | `assemble()` avec PERSONAL entry CONFIRMED → section présente même sans keyword match | unit | Intégration avec assemble() |
| T6 | `assemble()` — PERSONAL entry présente ne génère pas doublon dans `_memory_sections()` | unit | exclude_ids étendu |
| T7 | Real E2E lite : "qu'est-ce que tu connais de mes hobbies ?" → section PERSONAL présente | E2E lite | Régression F1 |
| T8 | Real E2E lite : PERSONAL ACTIVE entry → visible | unit | Lifecycle ACTIVE (pas seulement CONFIRMED) |
| T9 | PERSONAL OBSOLETE entry → NOT visible | unit | OBSOLETE exclusion |
| T10 | F2 directive : WS active + ferme-le → direct action (no ask) | E2E | CAS A/C stabilité |
| T11 | F2 directive : WS non-app + 2 apps conv → ask clarification | E2E | CAS B stabilité |
| T12 | F2 directive : last tool result = app → direct action | unit/E2E | CAS D stabilité |

**Total** : 12 tests ciblés (dans la limite des 15 autorisées).

---

## 17. Real E2E After Implementation (Maximum 5)

| # | Scénario | Condition de réussite |
|---|---------|----------------------|
| E1 | "qu'est-ce que tu sais de mes activités du week-end ?" → RAYA mentionne les échecs | Section PERSONAL visible dans contexte + réponse correcte |
| E2 | "comment tu me parles ?" → RAYA mentionne le tutoiement/direct | Section PERSONAL visible + tutoiement cité sans Obsidian search |
| E3 | Calc + Notepad ouverts, WS = Chrome, "ferme-le" → demande clarification | Réplication CAS B stable |
| E4 | Calc + Notepad, Calc mise en foreground, "ferme-le" → ferme Calc sans demander | Réplication CAS C stable |
| E5 | Typing dans Notepad, "ferme-le" → ferme Notepad sans demander | Réplication CAS D stable |

---

## 18. Things That Should NOT Be Changed

| Composant | Raison |
|-----------|--------|
| `_identity_baseline_sections()` | Fonctionne parfaitement — jamais keyword-filtré, identité toujours présente |
| `_memory_sections()` keyword filter | Correct pour TASK/PROJECT/WORKING layers et cas keyword-match |
| `_experience_sections()` | Séparé, limit=3, handled independently — DEFER |
| Context budget (4096 tokens) | 91.7% disponible — pas de problème de budget |
| Conversation history limit (10 entries) | Correct, Phase 11 opérationnel |
| World State mechanism | Correct, rank=1.0 pour faits actifs |
| Safety risk classification | pc.interact CONTEXTUAL — ne pas reclasser pour fermetures app |
| MemoryLifecycle.OBSOLETE exclusion | Correct dans store.search() — ne pas bypasser |

---

## 19. Deferred Items

| Item | Raison | Priorité |
|------|--------|---------|
| Accent normalization dans `_significant_words()` | Améliorerait "echecs" → "échecs" match, mais F1 Option A résout déjà l'essentiel | LOW |
| Experience memory visibility rules | Actuelle : keyword-filtered, limit=3. Comportement correct pour strategies — DEFER | DEFER |
| PERSONAL limit configurable via RuntimeConfig | Permet ajustement sans code — utile si profil grand | LOW |
| Profile data correction | "Jane Doe" / "Testville" dans la DB production sont des données de test | ADMIN |
| F2 — CAS E clarification | Fermer Notepad sans contexte conv (WS dominant) est défendable mais borderline | LOW — acceptable |

---

## 20. Final Verdict

| Finding | Verdict | Justification |
|---------|---------|---------------|
| F1 — Memory Keyword Filter | **FIX** | Confirmé réel, impact observé, correction minimale disponible (Option A) |
| F1 — Accent sensitivity | **FIX (dans Option A)** | Option A contourne le problème (query="" = pas de filtre accent) |
| F2 — Ambiguous Referent (4/5 cas) | **DESIGN_OK** | CAS A/B/C/D/E : comportement correct |
| F2 — Inconsistance S5 vs CAS B | **FIX** | Directive precision gap — 1 ligne dans render.py |
| F2 — Safety involvement | **NOT_REPRODUCED** | Safety n'est pas en cause pour les fermetures d'apps nommées |
| Budget sous-utilisation | **DESIGN_OK** | Pas un problème — headroom = 91.7% |
| ACTIVE_TASKS courtes (FINDING-3) | **DESIGN_OK** | Comportement attendu, confirmé précédemment |

---

## Réponses aux questions du spec

### QUESTION A : Quelle stratégie pour les mémoires PERSONAL vraiment pertinentes ?

**Réponse** : Option A — section `_personal_context_sections()` non-keyword-filtrée,
CONFIRMED/ACTIVE uniquement, limit=8. Coût : +9 tokens actuellement. Suit le pattern
`_identity_baseline_sections()` existant. Élimine la dépendance au vault Obsidian pour
les préférences/habitudes confirmées.

### QUESTION B : Comment RAYA décide entre action directe et clarification ?

**Réponse** : La décision doit vivre dans **la directive §18 de `render.py`**, renforcée
pour définir explicitement le seuil de dominance :
- **Dominant** = WS active_window OU last tool result pointe vers l'app
- **Ambigu** = WS pointe ailleurs ET MRU seul dans conv_history

Le composant responsable est la **directive système** (render.py), pas Safety (qui traite
le contenu de l'action, pas le référent), pas Cognition (qui n't a pas à gérer ce seuil).

---

*En attente de validation avant toute modification de code.*
