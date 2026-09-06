# RAYA V2

Runtime agentique headless-first — assistant personnel avec exécution réelle
(PC, navigateur, Telegram, voix), sûreté (Safety/STOP) et mémoire persistante.

RAYA V2 est une reconstruction complète de l'architecture V1, pensée pour
séparer strictement perception, décision et exécution, avec un seul point
d'exécution réel (le Harness) et une couche Safety obligatoire devant toute
action à conséquence.

## Statut

Chantiers complétés (voir les rapports `RAYA_V2_*_IMPLEMENTATION_REPORT.md`
à la racine) :

- Phases 1–11 : fondations (Harness, Tools, Safety, Devices, Memory, World
  State, Attention, Cognition, Context Engine, Telegram, Voix, Cockpit web).
- Chantier 12 : temps réel, tâches différées persistantes, distinction
  Information/Action/Channel defaults.
- Chantier 13 (+13B–13G) : intégration téléphone (Phone Link), perception
  événementielle des appels entrants.
- Chantier 14 : création de tâches en langage naturel + scheduling
  persistant.
- Chantier 15 : fiabilité agentique (sélection d'outil, exactly-once,
  preuve/vérification, sémantique des états de tâche).
- Chantier 16 : contextualisation (tâches actives en conversation normale),
  découverte de capacités CLI, exécution shell cachée (USE ≠ SHOW).

Tous marqués **GO** dans leurs rapports respectifs.

## Architecture (vue d'ensemble)

```
Interface (CLI / Cockpit web / Telegram / Voix)
      ↓
   Attention        — décide IGNORE / BACKGROUND / PROCESS_NOW / INTERRUPT
      ↓
   Harness          — SEUL runtime d'exécution réel
      ↓
Cognition / Tasks / Context Engine
      ↓
World State / Memory
      ↓
   Tools  →  Safety  →  Devices (Windows / Browser / iOS-Phone)
      ↓
Environnement réel
```

Principes structurants :
- Un seul point d'exécution (le Harness) — Cognition ne lance jamais un Tool
  directement, Attention ne devient jamais un second orchestrateur.
- Le Context Engine (`raya/context_engine/`) est strictement en lecture
  seule : il sélectionne et assemble un contexte pertinent depuis World
  State/Memory/Tasks, jamais il n'exécute ni n'écrit.
- Toute action à conséquence passe par Safety (`raya/safety/`) — aucun
  bypass, aucune confirmation contournée.
- World State reste la source de vérité de l'environnement observé, Memory
  celle des préférences/faits durables, Tasks celle des travaux en cours.

Documentation complète : `RAYA_V2_TECHNICAL_ARCHITECTURE.md`,
`RAYA_V2_ARCHITECTURAL_BLUEPRINT.md`, `RAYA_V2_CONTRACTS.md`,
`RAYA_V2_ARCHITECTURAL_INVARIANTS.md`, `RAYA_V2_REPOSITORY_STRUCTURE.md`
(également dupliqués sous `docs/`).

## Installation

```bash
pip install -e ".[dev]"
```

Copier `.env.example` en `.env` et renseigner au minimum une clé de modèle
(`OLLAMA_API_KEY` pour Ollama Cloud, ou configurer Ollama local). Telegram
et la voix sont optionnels (`RAYA_TELEGRAM_ENABLED`, `RAYA_ENABLE_VOICE`).

## Lancer RAYA

```bash
raya          # CLI headless (raya.bat sous Windows)
raya-web      # Cockpit web (raya-web.bat sous Windows) — http://127.0.0.1:8765
```

## Tests

```bash
pytest
python scripts/arch_lint.py   # vérifie le graphe de dépendances entre subsystems
```

## Structure du dépôt

```
raya/
├── runtime/        # composition root, config, points d'entrée CLI/web
├── contracts/       # dataclasses pures — aucune logique métier
├── harness/           # LE runtime d'exécution (boucle, scheduler, session)
├── cognition/           # planification, vérification, recovery
├── context_engine/        # assemblage du contexte modèle (read-only)
├── attention/               # décision d'urgence/pertinence
├── tasks/                     # Task Actors persistants
├── world_state/                 # état observé courant
├── memory/                        # préférences/faits durables
├── tools/                           # catalogue de capacités + exécution
├── safety/                            # permissions, risque, STOP
├── devices/                              # Windows / Browser / iOS agents
├── perception/                              # capteurs événementiels
├── interfaces/                                # CLI, Telegram, voix, Cockpit UI
├── models/                                      # providers Ollama, routage
└── persistence/                                   # backend SQLite
tests/            # miroir de raya/, ~1000+ tests ciblés
scripts/          # arch_lint.py, outils d'ingestion
```

## Note

`data/` (mémoire persistée, captures d'écran) et `.env` (secrets) sont
volontairement exclus du dépôt (`.gitignore`) — jamais commités.
