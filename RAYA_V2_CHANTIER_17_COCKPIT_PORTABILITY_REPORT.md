# RAYA V2 — CHANTIER 17 IMPLEMENTATION REPORT

Cockpit Chat Natif + Orb Presence + Portabilité Desktop ↔ Laptop

Statut : **GO**

## 1. Status

Inspection (read-only) → implémentation ciblée → tests → QA réelle sur ce
PC → audit de portabilité → documentation → vérification V1 → rapport.
Aucun Chantier 18 démarré.

## 2. Initial Cockpit Architecture

Lu en entier avant tout code : `raya/interfaces/ui/static/index.html`
(127 lignes), `app.js` (576 lignes), `styles.css` (365 lignes),
`raya/interfaces/ui/channel.py`/`events.py`/`viewmodels.py`,
`raya/runtime/entrypoints/web.py`, et les tests existants
(`tests/ui/test_events_bridge.py`, `test_ui_channel.py`,
`tests/runtime/test_web_entrypoint.py`, `tests/architecture/
test_cockpit_conversation_panel_proof.py`, `test_phase8_architecture_proof.py`).

Constat clé : le panneau Conversation était déjà, architecturalement, UN
panneau contextuel parmi sept (`panel-conversation`, `panel-tasks`,
`panel-world`, `panel-browser`, `panel-computer`, `panel-attention`,
`panel-spatial`) — jamais une colonne fixe à deux volets. Une passe UX
antérieure ("fix cockpit conversation panel", Phase 6/11) avait
délibérément fait en sorte qu'un échange normal n'ouvre JAMAIS ce panneau
automatiquement — seul le raccourci "C" ou le modèle (`ui.show_view`)
pouvaient l'ouvrir. Ce chantier **inverse** cette règle précédente sur
demande explicite de la consigne.

## 3. Existing UI Mechanisms Reused

- `openPanel(name)`/`closePanel(name)` (un panneau à la fois, fondu
  260ms) — réutilisés tels quels ; le panneau Conversation continue de
  passer par ces mêmes fonctions.
- `setPresence(state)` + `#app[data-presence]` (9 états déjà existants :
  idle/listening/processing/speaking/working/interrupted/waiting/
  needs_attention/unavailable) — **inchangé**, reste l'unique mécanisme
  pour le glow/l'animation de l'orbe.
- `@keyframes breathe` (état `working`) — **déjà** l'indicateur visuel
  "RAYA travaille" pour une tâche de fond (A9) ; aucun nouveau state à
  construire pour ce besoin.
- `handleNotification()`/WebSocket — inchangé ; les événements
  `task.started/paused/resumed/completed/failed/cancelled` continuaient
  d'appeler seulement `refreshPresence()`/`refreshTasks()`, jamais
  l'ouverture d'un panneau — confirmé déjà correct avant ce chantier.
- `PresenceTracker` (`raya/interfaces/voice/presence.py`) — dérive déjà
  `WORKING` des vrais events `task.*`, zéro polling ; réutilisé sans
  modification.

## 4. New Visual State Behavior

Un seul concept nouveau, purement côté layout : `#app.conversation-mode`
(classe CSS + variable JS `conversationActive`), orthogonal à
`data-presence`. Entré par `enterConversationMode()` (appelé depuis
`sendMessage()` et depuis `openViewByName("conversation")`), sorti par
`closePanel("conversation")` (Escape, bouton fermer, un autre panneau qui
prend le relais, ou le minuteur d'inactivité). Aucun nouvel état ajouté à
`data-presence` — le glow/l'animation restent 100% pilotés par la
présence réelle, indépendamment du layout.

## 5. Idle

Inchangé visuellement : orbe grand et centré, glow/branding/hint-row
conservés — seule modification : le hint `<kbd>C</kbd>chat` a été retiré
(C n'ouvre plus rien). Validé réellement (§22).

## 6. Conversation

`sendMessage()` appelle désormais `enterConversationMode()` avant l'appel
API (inversion délibérée et documentée de la règle Phase 6/11 — voir §2).
Le panneau `#panel-conversation` devient plein écran
(`position:fixed;inset:0`) au lieu d'une carte latérale de 400px, avec son
contenu centré sur une largeur de lecture confortable (`max-width:720px`)
— jamais une grille à deux colonnes fixes (vérifié : aucun
`grid-template-columns` introduit, test dédié).

## 7. Orb Transition

`#app.conversation-mode .orb-wrap` : `position:fixed; left:28px;
bottom:28px; width/height:56px` (`.orb` interne réduit à 34px, contre
88px par défaut) — orbe flottant bas-gauche, glow/identité visuelle
intacts (même élément DOM, mêmes règles de présence). Transitions CSS
existantes étendues (`width`/`height` ajoutés aux `transition` de
`.orb`/`.orb-wrap`) pour un rétrécissement fluide. Limitation honnête : le
repositionnement de `.stage` lui-même (centré → ancré en bas) change de
mode de positionnement CSS (flex-centré → fixed) et ne peut pas
s'animer en douceur par transition CSS pure sans réécrire l'orbe en
JS/FLIP — jugé hors scope ("éviter de reconstruire l'orb"), documenté en
§25.

## 8. Background Tasks

Aucun code ne fait ni n'a jamais fait le lien entre un event `task.*` et
l'ouverture de Conversation (vérifié par lecture directe de
`handleNotification()` + test dédié `test_background_task_events_never_
enter_conversation_mode`). Validé réellement (§22) : un vrai rappel créé
en tâche de fond s'est exécuté (COMPLETED, visible dans le panneau Tasks)
sans jamais forcer l'ouverture ni la réouverture du chat.

## 9. Inactivity

`CONVERSATION_IDLE_TIMEOUT_MS = 60000`. `resetInactivityTimer()` est
appelé à l'entrée en conversation mode et à la fin de chaque
`sendMessage()` (l'échange terminé redonne 60s pleines) — jamais par un
event de tâche de fond. Au déclenchement, appelle uniquement
`closePanel("conversation")` — vérifié structurellement (aucun appel
`api(...)` dans cette fonction) et réellement (§22) : ni session, ni
mémoire, ni tâche, ni WebSocket ne sont affectés ; l'historique reste
entièrement disponible à la ré-ouverture.

## 10. Keyboard Shortcuts

"C" retiré du listener clavier global — inspecté avant suppression :
ouvrir/rafraîchir Conversation était son SEUL comportement (aucune autre
dépendance trouvée dans app.js). T (Tasks) et W (World) inchangés, testés
réellement (§22, T ouvre bien un panneau latéral classique, pas fusionné
avec le chat). Esc inchangé (`closeAllPanels()`), testé réellement.

## 11. WebSocket/Event Handling

Aucune modification du contrat WebSocket ni des routes REST
(`raya/runtime/entrypoints/web.py` intact). Le frontend reste un pur
client du Harness — aucune logique agentique, aucun regex/keyword
matching sur le texte utilisateur n'a été ajouté (toutes les transitions
UI restent pilotées par : soumission utilisateur réelle, réponse reçue,
événements `task.*`/`ui.view_requested` existants, minuteur
d'inactivité).

## 12. Portability Audit

Audit exhaustif (lecture seule) de `raya/` (hors tests/scripts/docs) pour
tout chemin/nom machine-spécifique : `ruben`, `OneDrive`, `Bureau`,
`RayaV2` (littéral), `C:\Users`/`C:/Users`. **Zéro résultat réel** — 4
faux positifs identifiés et écartés (le prénom "Ruben" utilisé comme
exemple illustratif dans des docstrings, jamais un chemin). Les deux
seules occurrences `C:\` réelles trouvées (`devices/browser/session.py`)
sont un repli sur le chemin Windows UNIVERSEL `Program Files`, lu depuis
la vraie variable d'environnement OS en priorité — classées (1)
nécessaire et déjà portable.

## 13. Hardcoded Paths Found

**Aucun** chemin machine-spécifique codé en dur trouvé dans `raya/`. La
racine du projet (`raya/runtime/config.py::load_config()`) est déjà
dérivée de `Path(__file__).resolve().parents[2]` — jamais un nom de
dossier supposé. Un vrai gap DOCUMENTAIRE (pas fonctionnel) a été trouvé :
13 variables d'environnement réellement lues par `config.py` n'étaient
pas documentées dans `.env.example` (voir §15) ; une incohérence mineure
dans un commentaire de `config.py` (mentionnait `RAYA_ENABLE_TELEGRAM` au
lieu du vrai nom `RAYA_TELEGRAM_ENABLED`) a aussi été corrigée.

## 14. Runtime Root Strategy

`load_config(root: Path | None = None)` : `root = root or Path(__file__).
resolve().parents[2]` — dérivé de l'emplacement réel du fichier de
config, jamais du nom du dossier ni d'un chemin absolu supposé. Vérifié
par deux tests réels : le défaut correspond bien à l'emplacement réel du
code, et un `root=` explicite (ex: `D:/ailleurs/RayaCopie`) fonctionne
sans aucune modification de code — preuve directe que le projet est
déplaçable.

## 15. Configuration/.env

`.env.example` mis à jour avec les 13 variables manquantes :
`RAYA_ENABLE_WINDOWS_DEVICE`, `RAYA_ENABLE_BROWSER_DEVICE`,
`RAYA_ENABLE_OLLAMA_LOCAL`, `RAYA_MODEL_POOL`, `RAYA_LOCAL_MODEL_POOL`,
`RAYA_OLLAMA_HOST`, `RAYA_OLLAMA_LOCAL_HOST`, `RAYA_MAX_TOOL_ITERATIONS`,
`RAYA_ENABLE_PERCEPTION`, `RAYA_PERCEPTION_POLL_INTERVAL_S`,
`RAYA_TOOL_WORKSPACE_DIR`, `RAYA_DEVICE_SCREENSHOT_DIR`, `RAYA_CDP_PORT`.
Chaque ligne documente son défaut réel (lu directement dans
`config.py`/`browser/session.py`, jamais inventé). Nuance documentée pour
`RAYA_CDP_PORT` : lu directement via `os.environ` au chargement du
module (`devices/browser/session.py`), pas via le chargeur `.env` de
`config.py` — doit être une vraie variable d'environnement OS pour
prendre effet, pas seulement une ligne `.env`. Deux tests garde-fou
ajoutés : aucune variable lue n'est non-documentée, aucune variable
documentée n'est "morte" (jamais lue nulle part).

## 16. Data Portability

Documenté dans `RAYA_V2_SETUP_ANOTHER_PC.md` : `data/` (mémoire/tâches/
captures) n'est jamais copié par défaut — nouvelle machine = mémoire
vide, sauf synchronisation explicite (même dossier cloud partagé, ou
copie manuelle du `.sqlite3`). Distinction portable/non-portable
documentée (préférence utilisateur/fait mémoire/Telegram user
ID/historique = portable ; handle de fenêtre Windows/PID observé =
non-portable, régénéré à la volée).

## 17. External Dependencies

`pip install -e ".[dev]"` — aucune dépendance système lourde ajoutée par
ce chantier. `setup_raya.ps1` créé (minimal, optionnel) : vérifie Python,
crée `.env` depuis `.env.example` si absent (jamais écrasé), installe les
dépendances Python — ne télécharge rien de lourd, n'installe aucun
logiciel système, ne touche jamais V1.

## 18. Browser/Audio/Provider Portability

Confirmé à l'inspection, aucune modification nécessaire : Browser Agent
utilise un port CDP dédié (`RAYA_CDP_PORT`, jamais le navigateur
personnel), profil dans `%LOCALAPPDATA%` (déjà portable). Audio : aucun
nom/index de périphérique en dur ; `real_microphone_available()` existe
déjà et dégrade proprement (Voice indisponible, Cockpit texte continue).
Ollama : Cloud par défaut (aucune install locale requise) ; local
détecté via `RAYA_ENABLE_OLLAMA_LOCAL`, jamais supposé présent.
`_maybe_start_voice`/`_maybe_start_telegram` (web.py) dégradent déjà
honnêtement (try/except → log + None, jamais un crash) — confirmé
inchangé et suffisant.

## 19. Setup Documentation

`RAYA_V2_SETUP_ANOTHER_PC.md` créé : copie du dossier (avec exclusions
explicites : `.env`, `data/`, caches), installation, `.env`, vérification
Python/Ollama/navigateur/audio, lancement, vérification des capacités
réelles (`pc.capability.discover`, Chantier 16), section dédiée au
transfert de mémoire (optionnel). Aucun secret réel inclus.

## 20. Changes Made

**Partie A (Cockpit)** :
- `raya/interfaces/ui/static/app.js` : nouveau mécanisme `conversation-mode`
  (`enterConversationMode()`/`resetInactivityTimer()`), `sendMessage()`
  et `openViewByName("conversation")` mis à jour, raccourci "C" retiré,
  bookkeeping de sortie centralisé dans `closePanel()`.
- `raya/interfaces/ui/static/index.html` : hint `<kbd>C</kbd>chat` retiré.
- `raya/interfaces/ui/static/styles.css` : règles `#app.conversation-mode
  .orb-wrap/.orb/.brand/.status-line/.stage/#panel-conversation`
  (position/taille/z-index), transitions étendues sur `.orb`/`.orb-wrap`.

**Partie B (Portabilité)** :
- `.env.example` : 13 variables manquantes ajoutées.
- `raya/runtime/config.py` : commentaire corrigé (`RAYA_TELEGRAM_ENABLED`
  au lieu de `RAYA_ENABLE_TELEGRAM`) — aucun changement fonctionnel.
- `RAYA_V2_SETUP_ANOTHER_PC.md` (nouveau).
- `setup_raya.ps1` (nouveau, minimal).

**Tests** :
- `tests/architecture/test_chantier17_cockpit_layout.py` (nouveau, 11 tests).
- `tests/architecture/test_chantier17_portability.py` (nouveau, 6 tests).
- `tests/architecture/test_cockpit_conversation_panel_proof.py` : 4
  assertions mises à jour pour refléter l'inversion délibérée de la règle
  Phase 6/11 (jamais supprimées silencieusement — chaque changement
  documente explicitement pourquoi l'ancien comportement n'est plus
  correct).

Aucun fichier backend (Harness/Tasks/Safety/World State/Tools) modifié.

## 21. Tests

17 nouveaux tests + 4 assertions mises à jour dans un test existant
(inversion de règle documentée, jamais une suppression silencieuse) — au
sein de la cible 15–30. Tous via lecture directe des fichiers statiques
réels (même précédent que `test_ui_cockpit_static_files_do_not_load_
threejs_unconditionally`, Phase 8) puisqu'aucun runner JS n'existe dans
ce dépôt Python-only.

| Suite | Résultat |
|---|---|
| `tests/architecture/test_chantier17_cockpit_layout.py` | 11 passed |
| `tests/architecture/test_chantier17_portability.py` | 6 passed |
| `tests/architecture/` (complet, incl. proof mis à jour) | 209 passed (avec ui/runtime) |
| `tests/ui/`, `tests/runtime/` | inclus ci-dessus, tous PASS |
| Suite quasi-complète (`tests/` hors `integration/`) | 1152 passed, 1 failed (flake pré-existant documenté depuis plusieurs chantiers, `.env` OLLAMA_API_KEY vs NullProvider), 9 skipped |
| `scripts/arch_lint.py` | PASS |

`tests/integration/` — **NOT_RERUN — unchanged dependency** (aucun
changement de ce chantier ne touche les scénarios d'intégration
Phase 2-10).

## 22. Real Cockpit QA

QA réelle effectuée sur ce PC (serveur `raya-web` réel, vraie base de
données de production `data/raya_v2.sqlite3`, Telegram réel actif —
aucune action Telegram réelle déclenchée pendant ce test) via navigation
Chrome réelle :

1. **Idle** : orbe grand centré, "Online", hint-row sans "C" — **PASS**.
2. **"Quelle heure est-il ?"** → chat apparaît en plein écran, réponse
   réelle correcte (heure réelle) — **PASS**.
3. **Orbe pendant conversation** : réduit et positionné bas-gauche,
   glow conservé — **PASS** (un bug de z-index a été trouvé et corrigé
   en direct pendant cette QA : le panneau plein écran masquait
   initialement l'orbe/l'input, voir §25).
4. **~60s d'inactivité → retour Idle** : confirmé, orbe redevenu grand/
   centré, aucune donnée perdue (historique toujours présent à la
   réouverture) — **PASS**.
5. **Tâche de fond réelle** ("Crée un rappel de test dans 10 secondes...")
   → créée en conversation (légitime, c'est un échange réel), exécutée
   pour de vrai (visible **COMPLETED** dans le panneau Tasks), **jamais**
   de réouverture forcée du chat pendant/après son exécution — **PASS**.
6. **État visuel de l'orbe pendant la tâche de fond** : cohérent (idle
   une fois le délai d'inactivité écoulé) — **PASS**.
7. **Ré-engagement** ("Merci !" après idle) → conversation ré-ouverte,
   historique complet conservé et poursuivi — **PASS**.
8. **T** (Tasks) : panneau latéral classique (pas plein écran, pas
   fusionné avec le chat) — **PASS**.
9. **Esc** : ferme les panneaux — **PASS**.

Non re-testé séparément : redimensionnement précis à une résolution
laptop (aucune règle CSS dépendant de la largeur du viewport n'a été
ajoutée au-delà des offsets fixes déjà utilisés ailleurs — jugé à faible
risque, mais honnêtement non revérifié pixel par pixel).

## 23. Desktop → Laptop QA

**NOT_TESTED — second machine unavailable.** Aucun deuxième PC/laptop
n'était physiquement disponible pendant ce chantier. Compensé par :
audit statique complet (§12-14), tests automatisés de portabilité
(§15/§21, incluant un `load_config(root=...)` pointant vers un chemin
totalement différent), et documentation complète de la procédure
(`RAYA_V2_SETUP_ANOTHER_PC.md`) listant précisément ce qui reste à
valider en conditions réelles sur la seconde machine (démarrage,
Cockpit, Ollama Cloud, Memory, conversation, system.time, une capability
PC sûre, Task, Telegram si configuré, Voice si périphériques
disponibles).

## 24. PASS / FAIL / BLOCKED / NOT_TESTED

| Item | Résultat |
|---|---|
| Chat occupe la surface principale du Cockpit | PASS (réel) |
| Orbe flottant, réduit en conversation | PASS (réel, après fix z-index) |
| Pas de grille deux colonnes | PASS (test + réel) |
| Timeout ~60s → retour Idle, aucune donnée perdue | PASS (réel) |
| Tâche de fond ne force jamais l'ouverture du chat | PASS (réel, tâche réellement exécutée) |
| T/W/Esc fonctionnels, non fusionnés avec le chat | PASS (réel) |
| "C" ne fait plus rien | PASS (test + réel) |
| Racine runtime indépendante du chemin machine | PASS (test réel avec root= arbitraire) |
| `.env.example` cohérent avec le code réel | PASS (test) |
| Aucun chemin/nom machine-spécifique dans raya/ | PASS (audit exhaustif) |
| Documentation de setup deuxième PC | PASS (document créé) |
| Validation réelle Desktop → Laptop | **NOT_TESTED — second machine unavailable** |
| Redimensionnement laptop précis | NOT_TESTED (jugé faible risque, non vérifié) |
| Architecture lint | PASS |
| V1 intacte | PASS |

## 25. Known Limitations

- **Transition `.stage` non fluide au niveau CSS** : le passage
  centré→ancré-bas change de mode de positionnement (flex→fixed), donc
  ne s'anime pas en douceur par transition CSS pure — seul l'orbe
  (taille) et le panneau (fondu) transitionnent réellement ; l'input
  "saute" à sa nouvelle position. Une animation FLIP en JS résoudrait
  cela mais aurait signifié "reconstruire l'orb", explicitement
  déconseillé par la consigne — documenté plutôt que construit.
- **Bug de z-index trouvé et corrigé pendant la QA réelle**, pas avant :
  le panneau conversation plein écran masquait initialement
  orbe+input (les deux `position:fixed` avec `z-index:auto` empilent
  par ordre du DOM). Corrigé (`.stage{z-index:10}`,
  `#panel-conversation{z-index:1}` en conversation-mode) et revalidé en
  direct — mentionné explicitement car découvert APRÈS l'implémentation
  initiale, pas anticipé à la conception.
- **Redimensionnement laptop non re-testé pixel par pixel** (§22).
- **Validation Desktop → Laptop réelle non effectuée** (§23), deuxième
  machine indisponible pendant ce chantier.
- **`RAYA_CDP_PORT`** : la seule variable dont la source réelle
  (`os.environ` direct, pas le chargeur `.env` de `config.py`) diffère
  du reste — documentée explicitement dans `.env.example`, pas modifiée
  (aurait nécessité de la faire transiter par `RuntimeConfig`, un
  changement plus large que ce chantier ne justifie pas).

## 26. Architecture Compliance

Le frontend reste un client pur du Harness (aucune logique agentique,
aucun regex/keyword matching ajouté). Aucun nouvel orchestrateur/
scheduler/Context Engine/Memory/World State/Capability Registry/
WebSocket/système d'événements UI parallèle. `raya/runtime/config.py`
reste l'unique point de résolution de configuration — pas de nouveau
`ConfigManager`. Confirmé par `scripts/arch_lint.py` (PASS) et par
lecture directe des imports des fichiers modifiés.

## 27. V1 Integrity

`RAYA/` (V1) : `git status --short` avant et après ce chantier — seuls
des fichiers non suivis préexistants (`_shopping_full.txt`,
`prompt_claude_code_*.txt`). Aucun fichier suivi modifié. V1 inchangée.

## 28. Final Verdict

**GO.**

Partie A (Cockpit) : le chat occupe désormais la surface principale du
Cockpit dès qu'une conversation réelle a lieu, avec l'orbe comme
présence flottante bas-gauche — inversion délibérée et entièrement
validée en conditions réelles d'une règle UX précédente, un bug de
z-index trouvé et corrigé pendant la QA plutôt qu'ignoré. Les tâches de
fond restent invisibles pour le layout du chat, exactement comme
demandé.

Partie B (Portabilité) : l'audit exhaustif n'a trouvé **aucun** chemin
machine-spécifique réel dans le code — l'architecture était déjà
portable (racine dérivée de `__file__`, ports/profils déjà résolus
dynamiquement). Le vrai gap trouvé était documentaire
(13 variables `.env.example` manquantes), maintenant comblé, avec un
document de setup complet pour une deuxième machine. La validation
réelle sur un second PC reste honnêtement `NOT_TESTED` faute de machine
disponible — jamais présentée comme validée.

Aucun nouvel orchestrateur, scheduler, ou architecture parallèle créée.
V1 intacte. Arrêt ici — pas de Chantier 18.
