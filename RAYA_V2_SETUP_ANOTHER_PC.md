# RAYA V2 — Setup sur un autre PC (Desktop → Laptop)

Ce document explique comment faire fonctionner RAYA V2 sur une deuxième
machine Windows, après audit de portabilité (Chantier 17). L'architecture
ne suppose déjà aucun chemin machine-spécifique (pas d'utilisateur Windows,
pas de dossier `OneDrive`/`Bureau` codé en dur, pas de nom de projet
« RayaV2 » codé en dur) — la racine du projet se déduit toujours de
l'emplacement réel du code (`raya/runtime/config.py::load_config()`), donc
le dossier peut être copié n'importe où, sur n'importe quel compte Windows.

## 1. Copier le dossier

Copier tout le dossier `RayaV2/` vers la nouvelle machine, à l'endroit de
ton choix (ex: `C:\RayaV2`, `D:\Projets\RayaV2`, un autre `OneDrive\Bureau\`).

**Ne pas copier** (inutile, spécifique à la machine source, ou volontairement
exclu) :
- `.env` (secrets — recréé à l'étape 3) ;
- `data/` (mémoire/tâches/captures — voir §"Emporter la mémoire" plus bas
  si tu veux VRAIMENT la transférer) ;
- `__pycache__/`, `.pytest_cache/` (caches Python, régénérés seuls).

## 2. Installer les dépendances

Python ≥ 3.11 requis.

```powershell
cd C:\RayaV2   # ou l'endroit où tu as copié le dossier
pip install -e ".[dev]"
```

## 3. Créer `.env`

Copier `.env.example` en `.env` et renseigner au minimum une clé de modèle.
Toutes les autres variables ont des défauts raisonnables — laisse-les
commentées si tu ne sais pas quoi mettre (voir `.env.example`, chaque ligne
documente son défaut réel).

Minimum pour démarrer avec un vrai modèle :

```
OLLAMA_API_KEY=ta_vraie_cle
```

Rien d'autre n'est obligatoire — le CLI fonctionne même sans clé (Model
Layer = NullProvider honnête, aucun appel réseau, aucun crash).

## 4. Vérifier Python

```powershell
python --version   # >= 3.11
```

## 5. Vérifier Ollama Cloud

Si `OLLAMA_API_KEY` est renseignée, RAYA l'utilise directement (aucune
installation locale nécessaire — Ollama Cloud est un service distant).

Si tu veux plutôt un Ollama LOCAL sur cette machine :

```
RAYA_ENABLE_OLLAMA_LOCAL=true
RAYA_OLLAMA_LOCAL_HOST=http://localhost:11434
```

nécessite un vrai serveur Ollama lancé localement — sinon, laisse
`RAYA_ENABLE_OLLAMA_LOCAL` à `false` (défaut) et utilise Ollama Cloud.

## 6. Vérifier le navigateur (si Browser Agent utilisé)

Le Browser Agent (`raya/devices/browser/`) pilote une instance Chrome/Edge
dédiée à l'automation (CDP, port `RAYA_CDP_PORT`, défaut `9223`) — **jamais**
ton navigateur personnel. Rien à installer manuellement : le mécanisme
existant détecte/lance le navigateur d'automation lui-même. Si aucun
navigateur compatible n'est trouvé, cette capacité se désactive
proprement (pas de crash), le reste de RAYA continue de fonctionner.

## 7. Vérifier l'audio (si Voice utilisé)

Voice (`RAYA_ENABLE_VOICE=true`) est **opt-in**, désactivé par défaut. Si
activé sur une machine sans microphone/speakers, la détection existante
(`real_microphone_available()`) le signale honnêtement et Voice reste
indisponible — le Cockpit texte continue de fonctionner normalement. Rien
à configurer manuellement pour un périphérique audio standard (pas de nom
de périphérique en dur nulle part dans le code).

## 8. Lancer RAYA

```powershell
raya          # CLI headless
raya-web      # Cockpit web -- http://127.0.0.1:8765
```

(ou `raya.bat`/`raya-web.bat` si tu préfères double-cliquer — vérifie
juste le chemin Python qu'ils utilisent en interne si tu n'as pas de venv
partagé avec une V1 locale).

## 9. Vérifier les capacités réellement disponibles

Demande simplement à RAYA, une fois lancée : *"Quels outils/capacités as-tu
sur ce PC ?"* ou *"Est-ce que nmap/git est disponible ?"* — la découverte de
capacité (Chantier 16, `pc.capability.discover`) répond honnêtement à
partir de CETTE machine, jamais une supposition héritée du PC précédent.

## Emporter la mémoire (optionnel)

Par défaut, `data/raya_v2.sqlite3` (Memory/World State/Tasks/historique)
n'est PAS copié — la nouvelle machine démarre avec une mémoire vide. Si tu
veux vraiment la même mémoire sur les deux machines :

- **Option simple** : les deux machines pointent vers le même dossier
  synchronisé (ex: le même compte OneDrive) — `data/` se synchronise tout
  seul, indépendamment de RAYA. Aucune configuration RAYA nécessaire.
- **Option explicite** : copier `data/raya_v2.sqlite3` manuellement vers la
  nouvelle machine (fichier statique, pas de service à arrêter si RAYA
  n'est pas en train de tourner au moment de la copie), ou définir
  `RAYA_DATA_DIR`/`RAYA_DB_PATH` vers un chemin partagé explicite.

Distinction utile (données réellement portables vs machine-spécifiques,
si tu inspectes toi-même le contenu de la base) :

| Donnée | Portable ? |
|---|---|
| Préférence utilisateur (ex: canal par défaut) | Oui |
| Fait/mémoire personnelle | Oui |
| Telegram user ID | Oui |
| Historique de conversation | Oui |
| Handle de fenêtre Windows observé | Non — recréé à la volée par le prochain scan |
| PID de processus observé | Non — idem |
| Chemin de fichier absolu observé sur l'ancienne machine | À vérifier au cas par cas (peut ne plus exister) |

## Secrets — rappel

`.env` n'est **jamais** committé (`.gitignore`). Ne copie/partage jamais son
contenu tel quel (clé Ollama, token Telegram) — recrée-le sur chaque
machine à partir de `.env.example`.
