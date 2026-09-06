# RAYA V2 — Targeted Fix Pass

## 1. Cause Belgique

**FOUND.**

Cause exacte : `context_engine/assembler.py::_identity_baseline_sections()` (le mécanisme "faits d'identité toujours disponibles, jamais filtrés par mot-clé") appelait `memory.search(query="", channel_scope=..., limit=50)`. Ce `limit=50` reste un **tri par score** (lifecycle + petit bonus de récence, jamais un filtre par provenance) appliqué **avant** le filtre `provenance == "profile_migration:identity"`. Le fait "Réside à Evere, Bruxelles, Belgique" a été migré une seule fois en début d'usage ; à mesure que des dizaines d'échanges de conversation (plus récents) se sont accumulés, il est sorti des 50 entrées les mieux scorées et n'a plus jamais atteint le filtre de provenance — quel que soit le budget de contexte disponible.

**Vérifié empiriquement contre la vraie base** (166 entrées réelles) : pour la requête "trouve-moi une manette PS5", le fait Belgique était absent du prompt système alors que seulement 837/4096 tokens de budget étaient utilisés (pas un problème de budget).

**Correction** : `limit=50` → `limit=2000` sur cet appel spécifique uniquement (aucun autre point d'appel touché). Réalise fidèlement l'intention déjà documentée dans le code ("toujours disponibles, jamais évincés"). Aucune nouvelle architecture, aucun hardcoding de pays/site.

Revérifié empiriquement après correction : le fait apparaît désormais dans le prompt rendu.

## 2. Cause Telegram

**FOUND.**

Chemin inspecté : `Harness.touch_device()` → `DeviceRegistry.touch(metadata=...)` → `TelegramChannel.send_proactive()` → `tools/catalog/notify.py::telegram.send_message`. Aucun problème de Tool Registry, de Cognition ou de Safety — le tool refusait **correctement** faute de destinataire connu.

Cause exacte : `last_chat_id` est une métadonnée **en mémoire** (`DeviceRegistry`, jamais persistée), écrite uniquement quand un message **entrant** est reçu. À chaque redémarrage du serveur, tant qu'aucun message Telegram n'a été reçu depuis, ce signal est vide → `send_proactive()` retournait toujours `False` → "aucun chat Telegram connu". Le modèle gérait déjà correctement cette situation (pas d'invention), mais RAYA n'avait aucun moyen de résoudre "moi" alors qu'un propriétaire unique et déjà autorisé existe.

**Correction** : `TelegramChannel.send_proactive()` accepte un repli — si `last_chat_id` est inconnu **et** qu'un seul ID figure dans `RAYA_TELEGRAM_ALLOWED_USER_IDS`, ce même ID (déjà explicitement autorisé pour les messages entrants, fail-closed) est utilisé comme destinataire. Pour un chat privé Telegram, `chat_id == user_id` (propriété stable de l'API) — ce n'est jamais une invention ni un élargissement de permissions. Si plusieurs IDs sont autorisés, le repli est refusé (choisir parmi plusieurs serait une supposition arbitraire). Message d'erreur également clarifié pour indiquer l'action minimale ("envoyer un message une fois au bot") sans jamais demander un nom de canal/contact inutile.

Revérifié en réel : envoi réussi confirmé par log (`telegram.message_sent chat_id=8782090842`) sur un serveur fraîchement redémarré, sans aucun message entrant préalable.

## 3. Tests ajoutés

**9 tests** au total :

- Sujet 1 (1) : `tests/context_engine/test_identity_context.py::test_identity_fact_survives_heavy_conversation_accumulation` — reproduit exactement le bug (80 entrées CONVERSATION accumulées), confirmé qu'il échoue sur l'ancien code et passe sur le nouveau.
- Sujet 2 (3) : `tests/telegram/test_telegram_channel.py` — repli vers propriétaire unique connu ; refus honnête si plusieurs IDs autorisés (jamais un choix arbitraire) ; le chat_id frais reste prioritaire sur le repli.
- Sujet 3 (5) : `tests/devices/windows/test_browser_detect.py` — détection réelle sans crash ; mapping ProgId connu ; ProgId inconnu jamais deviné ; clé absente → `None` sans exception ; non-régression explicite (Edge dédié reste l'unique navigateur d'automatisation, aucune bascule de comportement).

## 4. Résultat

**PASS** pour les 3 sujets (cause démontrée + corrigée + test de régression qui échoue sur l'ancien code + vérification réelle où possible).

## 5. Tests réels

- **Exécuté** : "trouve une manette PS5, privilégie un vendeur pertinent pour la Belgique" → RAYA a explicitement cité "à Bruxelles"/"à Evere" et choisi Amazon.com.be comme "vendeur le plus pertinent" (aucun achat effectué, lien fourni). **PASS.**
- **Exécuté** : "envoie-moi ce lien sur Telegram" (juste après, sur le même serveur fraîchement redémarré, aucun message entrant préalable) → envoi réel réussi, confirmé par log. **PASS.**
- **Non exécuté** : Sujet 3 n'appelait pas de test réel supplémentaire (portée volontairement limitée à un mécanisme de détection informationnel, aucun changement de comportement à valider en conditions réelles).

## 6. Architecture

- `arch_lint.py` → **PASS, 0 violation**.
- **V1** : `git status --porcelain` sur `RAYA/` identique avant/après (mêmes 4 fichiers non suivis, même commit `6855d01`). Aucun fichier V1 modifié, déplacé ou reformaté.
- Aucun nouvel orchestrateur/agent (`BelgiumShoppingAgent` absent, aucune logique `if country == Belgium` dans le Harness — le contexte passe entièrement par Memory → Context → Cognition, déjà existant). Aucun contournement Safety. Aucun chat Telegram inventé ni permission élargie. Sujet 3 : aucune bascule Edge→Chrome, aucune copie/extraction de cookies, isolation CDP inchangée — uniquement une fonction de lecture pure, non câblée à une décision.

Régression ciblée (context_engine + telegram + notify + devices/windows + harness, 247 tests) : 1 seul échec, pré-existant et déjà documenté (clé Ollama réelle dans `.env` cassant l'hypothèse NullProvider d'un test antérieur, sans rapport avec cette passe).
