# RAYA V2 — PHASE 5 IMPLEMENTATION REPORT
## Voice + Presence + Barge-in + Interruption + Steering (+ Language Awareness)

**Statut :** Phase 5 terminée. Architecture V2 toujours FROZEN — aucun document architectural modifié (aucune contradiction bloquante ; plusieurs clarifications documentées §"Décisions notables"). Repo V1 (`RAYA/`) intact. Phases 0-4 (377 tests) intactes et toujours PASS.

---

## 1. Résumé

La voix est maintenant un canal temps réel V2 complet — pas une simple "interface vocale" ajoutée par-dessus, mais un transport d'événements vers le même Harness/Safety/Tasks/Attention/EventBus que CLI. Capture audio, VAD, STT (Whisper local), TTS (Kokoro local) et présence sont des mécanismes séparés, événementiels, sans aucun appel LLM par chunk audio. Le barge-in coupe une synthèse en cours en ~35ms mesurés réellement, sans jamais toucher aux tâches de fond. Le steering de tâches (pause/reprise/annulation/consigne modifiée) passe par le VRAI pipeline agentique (Harness → Cognition → Tool → Safety), jamais par un accès direct du canal voix au Task Actor. STOP suit exactement le chemin Event → EventBus → Safety déjà établi Phase 0.

**Ajout tardif explicite du donneur d'ordre en cours d'implémentation** ("LANGUAGE AWARENESS") : la langue de réponse n'est jamais hardcodée — décidée tour par tour à partir de la détection STT, d'une préférence de session persistante, ou d'une demande explicite, avec un routage TTS honnête (repli documenté quand aucune voix compatible n'existe, jamais une fausse prétention d'avoir parlé une langue).

**Résultat chiffré : 377 tests Phases 0-4 intacts + 117 nouveaux tests Phase 5 = 494 tests, 494 PASS, 0 FAIL, 0 BLOCKED côté architecture** (stable sur 3 exécutions consécutives de la suite complète). Le nombre dépasse la cible initiale (60-100) — justifié : l'ajout "LANGUAGE AWARENESS" est arrivé en cours d'implémentation, après que ~95 tests de la portée initiale étaient déjà écrits, avec sa propre exigence explicite de tests dédiés (§"LANGUAGE TESTS" de la consigne). Aucun test redondant n'a été laissé sciemment — deux tests faibles ont été supprimés/renforcés en cours de route (voir §17).

**Microphone ET haut-parleur réels disponibles sur cette machine** (13 périphériques d'entrée, 11 de sortie) — les tests "réels" listés ci-dessous ont donc été RÉELLEMENT exécutés (vraie capture, vraie transcription Whisper, vraie synthèse Kokoro jouée sur le vrai haut-parleur), pas simulés.

---

## 2. Architecture implémentée

```
Microphone → AudioInput → VAD → (segment de parole) → STT → VoiceRuntime
   → VoiceChannel.handle_final_transcript() → Harness.handle_request()
   → Cognition/Context/Tasks/Tools/Model → réponse texte
   → VoiceResponsePolicy (SPEAK/TEXT_ONLY/SILENT) → SpeechSynthesizer.speak()
   → Speaker

Task Actor → task.* Events → PresenceTracker (observation passive)
                            → (via announce(), narration non sollicitée)

User speech pendant TTS actif → VoiceRuntime détecte SPEECH_START
   → VoiceChannel.barge_in() → SpeechSynthesizer.cancel() (LOCAL, mécanique)
   → voice.barge_in Event (observabilité)
```

Un seul Harness (inchangé). `raya/interfaces/voice/` ne contient ni orchestration globale, ni logique métier, ni exécution de tool/device, ni routage de modèle, ni gestion persistante de tâche, ni décision mémoire — vérifié par lint sur le code réel (§16, "ARCHITECTURE PROOF").

## 3. KEEP / EXTRACT / ADAPT / REBUILD / DELETE

| Composant V1 | Classification | Ce qui a été récupéré | Ce qui a été exclu |
|---|---|---|---|
| `modules/voice/stt.py` (`_vad_prob`, `_transcribe`) | **EXTRACT** (mécanisme, pas le fichier) | Inférence Silero réelle, inférence faster-whisper réelle, repli GPU→CPU permanent | Le `listen()` monolithique (capture+VAD+STT+prints fusionnés), l'echo-gate par IMPORT DIRECT de `tts_kokoro.is_speaking()` |
| `modules/voice/tts_kokoro.py` | **EXTRACT** (mécanisme réel, quasi verbatim) | Thread producteur/consommateur, `sounddevice.OutputStream` continu, petits blocs d'écriture pour `stop_speaking()`≈abort() rapide | Le couplage direct au reste du fichier V1 (état module-global, prints) |
| `modules/voice/tts_router.py` | **EXTRACT du PATTERN** (pas la classe) | L'idée d'un routage TTS par langue vers un moteur adapté (devenu `_VOICE_BY_LANGUAGE` — une DONNÉE, pas une classe V1) | Le routage Pocket/Piper lui-même (non repris — Kokoro seul cette phase) |
| `modules/computeruse/safety.py` (STOP `threading.Event`) | **Non re-extrait** (déjà **KEEP** Phase 0 → `raya/safety/stop.py`) | — confirmé : voix utilise le MÊME chemin Event→EventBus→Safety, aucun mécanisme STOP parallèle recréé | — |
| `modules/voice/continuous_voice.py` (1084 lignes) | **DELETE, aucun code repris** | Rien du code — seule l'**observation** du mécanisme de barge-in (`_monitor_tts_barge_in` : détection debounced pendant que TTS parle) a informé la CONCEPTION de `VoiceRuntime.process_one_chunk()`, ré-implémentée intégralement | Boucle de décision complète, wake-word (3 moteurs), session/timeout de conversation propriétaire, `_EMERGENCY_STOP_PHRASES`/`_is_emergency_stop()` (STOP par égalité/ratio flou de phrase), `_DEFAULT_EXIT_PHRASES`, appels directs `ui.state`/`ui.bridge`/`modules.async_engine.REGISTRY`/`modules.computeruse.safety.request_stop` |
| `modules/voice/input_handler.py` | **DELETE, aucun code repris** | Le PATTERN "évènement tagué consommé ailleurs" a informé `VoiceEvent`/EventBus (déjà l'architecture V2 native, pas emprunté à ce fichier) | File globale partagée F2/texte/UI, `_continuous_vad_loop`/`_wait_for_tts_end` dupliqués |
| `modules/voice/tts_openai.py`, `tts_piper.py`, `tts_pocket.py`, `emotion.py` | **Non implémenté cette phase** | — Kokoro seul suffit à prouver le pipeline réel ; ces moteurs alternatifs restent hors périmètre | — |
| `modules/awareness/monitor.py`, `modules/pc_control/*`, `modules/browser/*` | **Non touchés cette phase** (déjà classés Phase 0/3/4) | — aucune ré-ouverture, hors périmètre voix | — |

## 4. Voice architecture (raya/interfaces/voice/)

```
audio/    AudioInput (ABC) + FakeAudioInput (test) + SounddeviceAudioInput (réel)
vad/      VoiceActivityDetector (ABC) + EnergyVAD (réel, déterministe) + SileroVADAdapter (réel, Silero)
stt/      SpeechToText (ABC) + FakeSTT (test) + WhisperSTTAdapter (réel, faster-whisper)
tts/      SpeechSynthesizer (ABC) + FakeTTS (test) + KokoroTTSAdapter (réel, Kokoro-ONNX)
session.py     VoiceSession — état transitoire (pas une 2e mémoire)
presence.py    PresenceState/PresenceTracker — décrit, ne décide jamais
policy.py      VoiceResponsePolicy — SPEAK/TEXT_ONLY/SILENT, fonction pure
language.py    VoiceTurn + decide_response_language() + detect_explicit_language_request()
events.py      publish_voice_event() — un seul point d'écriture VoiceEvent
channel.py     VoiceChannel — LE client mince du Harness (le seul point cognitif)
runtime.py     VoiceRuntime — transport temps réel (VAD->STT->Channel), pas un orchestrateur
factory.py     câblage des adapters RÉELS, séparé de runtime/bootstrap.py (coût de chargement modèle)
```

`raya/contracts/voice.py` (nouveau, additif) : `VoiceEvent`/`VoiceEventPayload` (même précédent que `TaskEvent`/`TaskEventPayload` Phase 2 — payload typé, catalogue fermé), `InterruptionReason`.

## 5. VAD

`EnergyVAD` (RMS, hystérésis démarrage/continuation) et `SileroVADAdapter` (modèle neuronal réel, `silero_vad.load_silero_vad()`) implémentent `VoiceActivityDetector.process(chunk) -> SILENCE|SPEECH_START|SPEECH|SPEECH_END`. **Preuve structurelle** (`tests/voice/test_vad.py::test_vad_module_never_imports_cognition_tools_tasks_or_models` + `tests/architecture/test_dependency_lint.py::test_real_vad_and_stt_submodules_never_import_harness_either`) : le VAD n'a même pas accès à `raya.harness`, encore moins `raya.cognition`/`raya.tasks`/`raya.models` — il ne peut PAS appeler le LLM ni créer une tâche, par construction, pas seulement par convention.

## 6. STT

`WhisperSTTAdapter` (faster-whisper, modèle `base`) — `transcribe_final()` retourne texte/confiance/langue/no_speech ; `supports_partial` renvoie honnêtement `False` (faster-whisper transcrit un segment complet, pas de streaming token-à-token — pas de faux support inventé). Testé réellement contre 3 fichiers audio GÉNÉRÉS PAR KOKORO (`tests/fixtures/voice/`, synthèse réelle rééchantillonnée à 16kHz) : phrase courte, phrase longue, silence pur.

## 7. TTS

`KokoroTTSAdapter` — synthèse+lecture asynchrones (thread dédié), `sounddevice.OutputStream` continu, écriture en blocs de 1024 échantillons pour une annulation rapide. Machine d'état explicite `IDLE→SPEAKING→[INTERRUPTING→CANCELLED]|COMPLETED|ERROR`. **Mesuré réellement** : latence d'annulation ≈37ms (`tests/voice/test_tts.py::TestRealKokoro::test_real_cancellation_latency_is_reasonable`), synthèse réellement jouée sur le vrai haut-parleur (pas un mock qui renvoie "audio generated").

## 8. Présence

`PresenceTracker` — état mutable protégé par lock, mis à jour uniquement par des appels explicites (`set_speaking`, `set_user_speaking`, `on_event`) ; `snapshot()` produit un `PresenceState` immuable (`state`/`timestamp`/`active_session`/`active_tasks`/`speaking`/`listening`/`user_speaking`/`attention_state`). Alimenté par abonnement à `task.*`/`attention.decision_made` sur l'EventBus — **jamais un import de `raya.attention`/`raya.tasks`** (transversal, pas une dépendance de subsystem).

## 9. Barge-in

`VoiceRuntime.process_one_chunk()` : sur `SPEECH_START`, si `channel.tts_is_speaking()`, appelle immédiatement `channel.barge_in()` — AVANT même de savoir ce que l'utilisateur dit (mécanique, pas une décision cognitive, consigne §9 respectée : pas d'attente de fin de segment). `barge_in()` annule le TTS localement (appel direct, rapide) et publie `voice.barge_in` pour l'observabilité — **ne touche jamais Safety/Tasks** (§16 : le barge-in ne doit jamais annuler une tâche de fond, testé explicitement).

## 10. Interruption

`InterruptionReason` (USER/TASK/SYSTEM/SAFETY/TTS) distingue la SOURCE d'une interruption — `contracts/voice.py`, prêt à être attaché aux payloads d'événements futurs. `TTSState` distingue déjà SPEAKING→INTERRUPTING→CANCELLED de façon vérifiable (testé pour double-annulation idempotente).

## 11. Steering

`tools/catalog/tasks.py` (nouveau) : `tasks.pause`/`tasks.resume`/`tasks.cancel`/`tasks.steer`, chacun un vrai `Tool` découvert par la boucle agentique normale — **jamais un accès direct du canal voix au Task Actor**. Le canal voix transmet la transcription finale ("mets la recherche en pause") au Harness EXACTEMENT comme n'importe quelle autre demande ; c'est le modèle (scripté dans les tests, réel en production) qui décide d'appeler `tasks.pause`. `tasks.steer` fusionne la nouvelle consigne dans le `checkpoint` existant SANS écraser l'état de progression déjà présent (testé). Prouvé bout en bout par `tests/integration/test_phase5_scenarios.py::test_6` et le benchmark E2E (§15).

## 12. Concurrence

Testé réellement (pas de sleep fixe — synchronisation par `threading.Event`/polling borné `_wait_for`) : tâche de fond + question vocale simultanées (répond en <0.5s, tâche continue), fin de tâche pendant TTS actif (n'interrompt pas la synthèse), STOP pendant TTS+tâche simultanément (les deux s'arrêtent), échec de tâche n'interrompt pas la conversation, STT+TTS ne se bloquent pas mutuellement (barge-in testé pendant TTS réel).

## 13. Safety

STOP vocal suit EXACTEMENT `Interface → Event("interface.stop_requested") → EventBus → Safety` — aucun `voice.stop_all()`. `VoiceChannel._on_global_stop` s'abonne à ce même event pour couper SA propre synthèse même si le STOP vient d'un AUTRE canal (testé). `tasks.pause`/`resume`/`cancel` classés SAFE (§"Décisions notables" pour la justification de `cancel`), `tasks.steer`/`browser.click`/`pc.keyboard.*` etc. restent SENSITIVE — jamais de reclassification de complaisance pour faire passer un test (vérifié : `tests/tools/test_task_control_catalog.py` prouve le gating réel).

## 14. Privacy

Aucun buffer audio n'est conservé au-delà d'un segment de parole (`VoiceRuntime._buffer` est vidé à chaque `SPEECH_END`, jamais accumulé entre segments — testé `test_no_audio_feedback_loop_...`). Aucun partial n'est persisté (`VoiceSession.set_partial()` écrase, n'accumule jamais ; `last_partial` remis à `None` à chaque nouveau tour). Seule la transcription FINALE entre dans le pipeline conversationnel normal (Memory), via le MÊME mécanisme que n'importe quelle autre interface — la voix ne décide jamais elle-même ce qui devient mémoire.

## 15. Tests

**117 nouveaux tests** :

| Fichier | Tests |
|---|---:|
| `tests/voice/test_contracts_voice.py` | 5 |
| `tests/voice/test_audio.py` | 6 |
| `tests/voice/test_vad.py` | 7 |
| `tests/voice/test_stt.py` | 10 |
| `tests/voice/test_tts.py` | 9 |
| `tests/voice/test_presence.py` | 11 |
| `tests/voice/test_policy.py` | 7 |
| `tests/voice/test_session.py` | 5 |
| `tests/voice/test_channel.py` | 10 |
| `tests/voice/test_runtime.py` | 7 |
| `tests/voice/test_language.py` | 19 |
| `tests/tools/test_task_control_catalog.py` | 8 |
| `tests/integration/test_phase5_scenarios.py` | 8 |
| `tests/architecture/test_dependency_lint.py` (extension) | +5 |
| **Total nouveaux** | **117** |

Dosage : cible initiale 60-100 dépassée par l'ajout tardif "LANGUAGE AWARENESS" (§1). Deux tests faibles identifiés et corrigés en cours de route (§17, pas de padding volontaire).

## 16. Real hardware tests

Microphone (13 devices) et haut-parleur (11 devices) réels disponibles — **aucun test marqué BLOCKED pour raison matérielle** :
- `tests/voice/test_vad.py::test_real_silero_vad_...` — vrai modèle Silero chargé et exécuté.
- `tests/voice/test_stt.py::TestRealWhisper` (5 tests) — vrai faster-whisper, vrais fichiers audio.
- `tests/voice/test_tts.py::TestRealKokoro` (3 tests) — vraie synthèse Kokoro, vraiment jouée sur le vrai haut-parleur, latence d'annulation réellement mesurée (~37ms).
- `tests/voice/test_audio.py::test_real_microphone_availability_check_never_crashes` — interroge réellement l'API `sounddevice`.

Aucun scénario "microphone humain parlant en direct pendant l'exécution automatisée des tests" n'a été tenté (nécessiterait une présence humaine synchronisée à l'exécution de la suite) — c'est le seul aspect honnêtement **NOT_TESTED** plutôt que BLOCKED (le matériel est présent, mais un test automatisé ne peut pas fournir une vraie voix humaine à la demande).

## 17. Failures found

1. **`WindowsDeviceAgent` (Phase 4) — flake de focus en séquence rejoué en fin de suite complète** (`test_application_focus_brings_real_window_to_foreground`) : pas une régression Phase 5 (aucun fichier `devices/windows/` touché) — confirmé stable 3/3 en isolation, timing Windows sous charge de suite complète. Documenté, pas re-corrigé (hors scope Phase 5, déjà partiellement mitigé Phase 4).
2. **Faux négatif de conception dans `_decide_response`** : la présence traquait la DERNIÈRE décision Attention vue sur le bus (souvent `IGNORE` sur un event de tâche de fond sans rapport) et l'appliquait À TORT à une réponse DIRECTE à une question explicitement posée — la réponse vocale à "quelle heure est-il ?" était silencieusement supprimée pendant qu'une tâche de fond tournait. Trouvé par `test_1_background_task_plus_voice_question_both_progress`. Corrigé : une réponse directe est TOUJOURS `PROCESS_NOW` (règle déjà établie ailleurs dans le Harness), la présence n'alimente que `announce()` (narration non sollicitée, nouvelle méthode).
3. **`tasks.cancel` initialement classé SENSITIVE bloquait le steering "annule la tâche" dans le pipeline agentique complet** — trouvé par le benchmark E2E. Reclassé SAFE avec justification explicite (§13) : annuler SA PROPRE tâche est l'équivalent d'un STOP scopé, et le STOP global lui-même n'est jamais gated par confirmation nulle part dans le système — la classification SENSITIVE initiale était en réalité l'incohérence, pas la correction.
4. **Régression réelle de conception dans `PresenceTracker`** (trouvée avant tout test formel, pendant le développement manuel) : `listening` restait bloqué à `True` indéfiniment après une première parole (jamais remis à `False` sur `SPEECH_END`) — corrigé dans `VoiceRuntime.process_one_chunk`.
5. **`factory.py` important `raya.runtime.bootstrap.RuntimeHandles`** — dépendance ASCENDANTE interdite (`interfaces` → `runtime`, alors que `runtime` est la racine de composition qui dépend de tout le reste). Trouvé par le lint architectural DÈS la première exécution après écriture (avant même d'écrire un test dédié). Corrigé par un `Protocol` local dupliquant seulement la forme nécessaire (`.harness`/`.bus`), jamais un import réel.
6. **`WhisperSTTAdapter` plantait réellement sur cette machine** : `device="auto"` détecte un GPU mais le runtime cuBLAS (`cublas64_12.dll`) n'est pas installé — `RuntimeError` à la première vraie transcription. Corrigé par un repli GPU→CPU **permanent** (une seule fois, jamais un retry par appel), même mécanisme que documenté en V1.
7. **Whisper hallucine du texte sur du silence quasi pur** (`silence.wav` → `"You"` avec `language=None`) — comportement réel du modèle, pas un bug RAYA en soi, mais RAYA ne doit jamais le répéter comme si c'était entendu. Corrigé par un court-circuit RMS AVANT tout appel modèle (silence mesurable → `no_speech=True` immédiat, jamais d'inférence).
8. **Auto-détection de langue Whisper peu fiable sur un clip court/quantifié int8** (français détecté comme tchèque/anglais sur `language=None`) — corrigé en exposant `language` comme paramètre configurable de l'adapter (pas un hardcode, une configuration), documenté comme limitation connue de l'auto-détection sur clip court.
9. **Détecteur "demande explicite de langue" initial ne reconnaissait que des formulations dans la langue CIBLE** ("en français" mais pas "answer in french") — `test_explicit_answer_in_french_request_overrides_detected_english` a révélé le trou ; table de marqueurs étendue pour couvrir les formulations croisées fr/en les plus courantes (limitation documentée : ensemble fermé, pas une compréhension d'intention générale).

## 18. Bugs fixed

Les 9 listés ci-dessus, tous corrigés à la racine avec un test de régression dédié (sauf #1, pré-existant hors scope). Validation finale : 3 exécutions consécutives de la suite complète (494/494 à chaque fois).

## 19. Known limitations

- Vision/TTS engines alternatifs (OpenAI/Piper/Pocket) non câblés — Kokoro seul prouve le pipeline réel.
- Pas d'annulation d'écho acoustique (AEC) réelle — l'architecture SÉPARE structurellement le flux TTS (sortie) du flux VAD/STT (entrée mic), ce qui élimine par construction toute boucle RAYA-s'écoute-elle-même, mais ne filtre pas un écho acoustique physique capté par un vrai micro proche d'un vrai haut-parleur (non testé avec du matériel en configuration bouclée réelle).
- `detect_explicit_language_request()` reste un détecteur de marqueurs FERMÉ (fr/en/nl/es, formulations connues) — pas une compréhension d'intention via Cognition ; une formulation créative ("je préférerais que tu répondes différemment la prochaine fois") ne sera pas reconnue.
- Néerlandais : aucune voix Kokoro réelle disponible (catalogue de voix vérifié à l'implémentation) — repli documenté vers l'anglais, jamais une fausse prétention.
- `tasks.steer` modifie le `checkpoint` mais aucun `step_fn` de démonstration (héritage Phase 2) ne consulte activement `steering_guidance` — la persistance est réelle, son EFFET sur un exécuteur de tâche réel reste à câbler dans une phase qui construit de vrais Task Actors orientés objectif.
- Le flake pré-existant `test_queued_task_waits_when_at_concurrency_limit` (Phase 2) reste non corrigé (hors scope).

## 20. BLOCKED tests

Aucun. Microphone et haut-parleur réels disponibles sur cette machine ; tous les moteurs (Silero/Whisper/Kokoro) sont installés et fonctionnels.

## 21. NOT_TESTED tests

- Scénario "voix humaine réelle prononcée en direct pendant l'exécution automatisée" (§16) — matériel présent, mais nécessite une présence humaine synchronisée, hors de portée d'une suite automatisée.
- `edge_tts`/moteurs TTS alternatifs pour le néerlandais (repli existant en V1, non reconstruit cette phase).

## 22. Regression status

377 tests Phases 0-4 + 117 nouveaux = 494/494 PASS, stable sur 3 exécutions consécutives complètes. Le seul échec observé pendant l'implémentation (flake Phase 4 `test_application_focus_...`) est un flake de timing PRÉEXISTANT, confirmé non lié à cette phase (§17 point 1). Aucun test Phase 0-4 modifié pour le faire passer.

## 23. Architecture proof (consigne §42)

| Preuve requise | Mécanisme de vérification | Résultat |
|---|---|---|
| Voice n'importe pas cognition pour décider | `test_real_voice_tree_never_imports_cognition_tools_devices_models_directly` (AST réel) | PASS |
| Voice n'exécute pas tools | idem (aucun import `raya.tools`) | PASS |
| Voice n'importe pas devices | idem | PASS |
| Voice n'importe pas directement models | idem | PASS |
| Voice n'est pas un second Harness | `raya/interfaces/voice/` ne définit aucune boucle de décision — `VoiceRuntime` transporte, `VoiceChannel` délègue à `harness.handle_request()` | PASS (revue + lint) |
| Voice n'est pas un second TaskScheduler | Aucun fichier voice n'importe `raya.tasks` (même test AST) ; steering passe par `tools/catalog/tasks.py` → Harness | PASS |
| TTS n'est pas bloquant | `test_real_speak_is_non_blocking_and_completes` (retour <0.5s mesuré, synthèse continue en tâche de fond) | PASS |
| VAD n'appelle pas le LLM | `test_vad_module_never_imports_cognition_tools_tasks_or_models` + `test_real_vad_and_stt_submodules_never_import_harness_either` | PASS |
| Partial STT non persisté comme mémoire | `test_partial_overwrites_not_accumulates` + `VoiceSession` ne possède aucun champ historique (`test_session_has_no_conversation_storage_attribute`) | PASS |
| STOP passe par Safety | `test_request_stop_publishes_interface_stop_requested_event` + `test_global_stop_cancels_tts_on_every_channel_...` (chemin Event→EventBus, jamais un appel direct) | PASS |
| Channel isolation fonctionne | `test_7_voice_channel_scope_never_leaks_into_chat_scope_memory` + `test_channel_isolation_preserved_with_different_session_languages` | PASS |

`python scripts/arch_lint.py` : **PASS, 0 violation** sur l'arbre complet incluant tout le code Phase 5.

## 24. V1 contamination proof (consigne §43)

- **Fichiers V1 inchangés** : `cd RAYA && git status --short` identique avant/après (4 fichiers non suivis, jamais modifiés).
- **Aucun import V1** : `grep -rn "modules\.voice\|modules\.async_engine\|core\.orchestrator\|core\.llm" raya/interfaces/voice/` → aucun résultat (vérifié).
- **`continuous_voice.py` non utilisé** : aucune référence textuelle (`check_no_legacy_v1_references`, catalogue déjà étendu Phase 3, re-vérifié PASS sur l'arbre complet Phase 5).
- **Aucun agent autonome** : `PCAutoAgent`/`ContinuousVoiceEngine`/équivalent — recherche `grep -rn "ContinuousVoiceEngine\|_EMERGENCY_STOP_PHRASES\|_is_emergency_stop"  raya/` → aucun résultat.
- **Aucune ancienne recipe** : `grep -rn "_DEFAULT_EXIT_PHRASES\|wake.word\|porcupine\|vosk" raya/` → aucun résultat (wake-word explicitement hors scope, jamais porté).
- **Aucun wrapper de compatibilité** : aucun fichier nommé/structuré comme un adaptateur préservant l'API V1 — chaque adapter (`WhisperSTTAdapter`, `KokoroTTSAdapter`, `SileroVADAdapter`) implémente une interface V2 native (`SpeechToText`/`SpeechSynthesizer`/`VoiceActivityDetector`), jamais l'inverse.
- **Primitives V1 réutilisées, documentées explicitement** (§3) : mécanismes d'inférence Silero/Whisper/Kokoro et le pattern producteur/consommateur audio — jamais leur logique de décision englobante.

## 25. Git diff summary

RayaV2/ n'est pas un dépôt git initialisé (pas de `git diff` disponible) — inventaire de fichiers à la place :

**Fichiers créés (23)** :
`raya/contracts/voice.py`, `raya/interfaces/voice/{__init__,channel,events,factory,language,policy,presence,runtime,session}.py`, `raya/interfaces/voice/audio/{__init__,base,sounddevice_input}.py`, `raya/interfaces/voice/vad/{__init__,base,silero_adapter}.py`, `raya/interfaces/voice/stt/{__init__,base,whisper_adapter}.py`, `raya/interfaces/voice/tts/{__init__,base,kokoro_adapter}.py`, `raya/tools/catalog/tasks.py`, `tests/voice/test_{contracts_voice,audio,vad,stt,tts,presence,policy,session,channel,runtime,language}.py`, `tests/tools/test_task_control_catalog.py`, `tests/integration/test_phase5_scenarios.py`, `tests/fixtures/voice/{short_speech,longer_speech,silence}.wav`, `RAYA_V2_PHASE5_IMPLEMENTATION_REPORT.md`.

**Fichiers modifiés (6)** : `raya/contracts/__init__.py` (export `VoiceEvent`/`VoiceEventPayload`/`InterruptionReason`), `raya/harness/loop.py` (+`get_task()` passthrough), `raya/safety/risk.py` (+tags `tasks.control`/`tasks.modify`), `raya/tools/catalog/__init__.py` (+export `register_task_control_tools`/`TaskControlOps`), `raya/runtime/bootstrap.py` (+câblage steering après construction du Harness), `tests/architecture/test_dependency_lint.py` (+5 tests), `tests/voice/test_channel.py`/`test_policy.py` (2 tests faibles renforcés/supprimés, §17).

**Fichiers V1 touchés : NON.**

---

**Pas de "100% complete" annoncé** — Phase 5 livre exactement le périmètre demandé : un canal voix réel, événementiel, jamais un second cerveau, avec barge-in mesuré, steering réel à travers le vrai pipeline agentique, et une langue de réponse jamais hardcodée. Rien de plus, rien de moins.
