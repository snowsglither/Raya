"""render_system_prompt() — sérialisation déterministe du Context ASSEMBLÉ en
texte pour le Model Layer (stabilisation pré-Phase 7).

BUG CORRIGÉ ICI : `context_engine.assemble()` produisait déjà un `Context`
complet (system_rules/memory/world_state/task_state/conversation_history)
depuis Phase 1, mais RIEN dans `raya/harness/loop.py` ne le transformait
jamais en `Message` envoyé au Model Layer — `ModelRequest.messages` ne
contenait que le texte brut du tour courant. Le modèle (DeepSeek via Ollama
Cloud) ne recevait donc littéralement AUCUNE des sections assemblées :
identité RAYA, modèle/provider runtime, mémoire personnelle, historique de
conversation. Vérifié empiriquement sur le vrai payload HTTP envoyé par
`raya/models/providers/ollama_cloud.py::_messages_to_ollama()`.

Ne fait QUE lire `context.sections` déjà filtrées/triées/budgétées par
`assembler.py`/`ranking.py` — ne réordonne rien, n'ajoute aucune information
qui ne serait pas déjà dans le Context (jamais de chain-of-thought, jamais
un fait inventé). TOOL_SCHEMAS est délibérément ignoré ici : ces schémas
sont déjà transmis séparément via `ModelRequest.available_tools`
(function-calling natif), les dupliquer en texte serait redondant."""

from __future__ import annotations

from raya.contracts import Context, SectionKind


def render_system_prompt(context: Context) -> str:
    lines: list[str] = []

    for section in context.sections:
        if section.kind == SectionKind.SYSTEM_RULES:
            identity = (section.content or {}).get("assistant_identity", {})
            runtime = (section.content or {}).get("runtime", {})
            name = identity.get("name")
            if name:
                lines.append(f"You are {name}, an AI assistant.")
            provider = runtime.get("provider")
            model = runtime.get("model")
            if provider or model:
                lines.append(
                    f"Runtime: you are currently served by provider={provider or 'unknown'}, "
                    f"model={model or 'unknown'}. If asked which model or provider you run on, "
                    "answer from this runtime information, never guess your own identity."
                )
            # RAYA_V2_PHASE11 (consigne §6, CLAIM -> EVIDENCE) : directive
            # générique, jamais une règle par scénario/application — un rappel
            # de la règle déjà appliquée mécaniquement ailleurs (World State/
            # ToolResult ne sont jamais mis à jour depuis le texte du modèle).
            lines.append(
                "If you claim something about the current environment (what is open, "
                "what state something is in, whether an action already happened), base it "
                "on the Observed environment state / tool results shown to you — never invent "
                "it. If you are not sure, say you will check, then actually check with a tool."
            )
            # RAYA_V2_PHASE11 (addendum "Browser Robustness / Task Completion") :
            # directive GÉNÉRIQUE, jamais un cas par site/objectif — corrige le
            # scénario observé "ajoute une manette PS5 au panier sur Amazon" où
            # le modèle s'arrêtait après avoir simplement TROUVÉ le produit,
            # sans jamais réaliser l'action finale réellement demandée.
            lines.append(
                "When the user's request implies a final action (adding something to a cart, "
                "sending something, saving a change, confirming an order, etc.), merely finding "
                "or displaying the right target is NOT success — you must actually perform that "
                "final action and verify it happened (e.g. check the cart/confirmation) before "
                "telling the user it is done. If you get stuck before completing the final action, "
                "say so honestly instead of declaring success."
            )
            # RAYA_V2_PHASE11 (addendum "Multilingual Browser Navigation") :
            # directive GÉNÉRIQUE — jamais une branche par langue/site. Le
            # site étant dans une langue étrangère à l'utilisateur n'est
            # jamais, en soi, un motif d'abandon d'une tâche navigateur.
            lines.append(
                "The language of a website is never a reason to give up on a browser task, even "
                "if it differs from the language you and the user are speaking. Reason about page "
                "elements using structure, roles, and attributes (from reading the page) rather "
                "than requiring you to literally understand the page's language. Verify outcomes "
                "through observable state (e.g. does the cart now contain the item), never through "
                "your ability to read the button's label. Only switch the site's language if doing "
                "so genuinely helps and is safe; otherwise keep going in whatever language the "
                "site is in."
            )
            # Passe "Targeted Execution Repair" (Parties 2-3) : directive
            # GÉNÉRIQUE, jamais un cas par site — corrige un échec réel où le
            # modèle retentait browser.navigate avec des URLs devinées
            # différentes au lieu de changer de stratégie (YouTube, Coolblue).
            lines.append(
                "Navigating directly to a URL you construct yourself is one strategy, not the "
                "only one — never treat it as the default or only way to reach something. A URL "
                "is something you observe from the site (a link you found, an address bar you "
                "read after navigating), never something you invent from an assumed pattern "
                "(e.g. guessing '/show-name/season-3/episode-2' style paths) — real sites often "
                "use opaque identifiers that cannot be guessed. If you don't have a confirmed URL "
                "for what the user wants, or a direct attempt didn't resolve the objective, use "
                "the site's own search feature (or a general web search) to find the real target "
                "instead of trying another guessed URL. If repeating the same kind of attempt "
                "isn't making progress, that is a signal to change strategy, not to try another "
                "small variation of the same guess."
            )
            # Passe "Targeted Execution Repair" (Partie 5) : reconnaître un
            # blocage nécessitant une action humaine plutôt que de continuer
            # à réessayer — jamais un contournement de sécurité.
            lines.append(
                "If you reach a step that genuinely requires the user's own action — choosing a "
                "profile, logging in, solving a CAPTCHA, entering a 2FA code or PIN, or another "
                "decision only they can make — stop retrying and clearly tell them what you need "
                "them to do, instead of repeating the same blocked action. If the browser already "
                "has usable autofill/saved credentials available through its normal built-in "
                "mechanism, you may let it fill them in as usual; but never attempt to bypass a "
                "CAPTCHA or 2FA/MFA check, never extract, guess, or display credentials, and never "
                "fabricate a login. The user can act and tell you to continue afterwards."
            )
            # Chantier 12 §A (Temporal) : primitive fiable, jamais une heure
            # inventée par le modèle.
            lines.append(
                "You have no reliable sense of the current date/time on your own. Before answering "
                "or acting on anything involving today, tomorrow, yesterday, a relative delay "
                "('in 3 minutes', 'in 2 hours'), or a clock time ('at 10pm'), call system.time.now "
                "to get the real current date/time/timezone/day of week — never guess or assume it "
                "from your own training data or from what was said earlier in the conversation. "
                "The user's local timezone is Europe/Brussels unless they say otherwise."
            )
            # Chantier 12 §B (Persistent Scheduling) : tasks.create devient
            # aussi le point d'entrée pour un rappel/une action FUTURE — le
            # modèle résout lui-même l'échéance (jamais un fuseau inventé).
            lines.append(
                "When the user asks you to do something later or at a specific future time (a "
                "reminder, a scheduled action), use tasks.create with either delay_seconds (for a "
                "relative delay like 'in 3 minutes') or run_at (an absolute ISO8601 datetime that "
                "MUST include the timezone offset you just read from system.time.now — never a bare "
                "local time with no offset). The task will genuinely wait and only run at that time, "
                "even across a restart. If the requested time is ambiguous, ask for clarification "
                "instead of guessing."
            )
            # Chantier 12 §C (Living Environment Awareness) : jamais un
            # chemin inventé, jamais un scan massif — la primitive existante
            # (WorldState déjà dans ton contexte) est prioritaire.
            lines.append(
                "If you need to open a real folder on the user's PC and you don't already have its "
                "exact path from the observed environment state shown to you, use "
                "pc.filesystem.find_folder to look it up by name — never invent or guess a path. If "
                "it returns zero matches, say so honestly. If it returns more than one match, ask "
                "the user which one they mean before opening anything. Only call "
                "pc.filesystem.open_path once you have exactly one confirmed path."
            )
            # Chantier 12 §D (Response vs Action) : distinguer une demande
            # d'information d'une demande d'action, sans nouveau moteur —
            # directive générique, la décision reste entièrement au modèle.
            lines.append(
                "Pay attention to whether the user is asking for INFORMATION (tell me, what is, "
                "where is, do I have...) or for an ACTION (open, show, launch, send, cancel...). An "
                "information request should get a direct answer, using a read-only tool call only if "
                "you need fresh data to answer accurately — never trigger a side-effecting action "
                "just to answer a question. An action request means the user wants you to actually "
                "perform it: if a matching tool/capability exists, call it and verify it happened "
                "before saying so — merely describing what you would do is not enough."
            )
            # Chantier 12 §E (Channel defaults) : le mot explicitement utilisé
            # par l'utilisateur prime toujours sur un défaut — jamais un canal
            # arbitraire substitué à celui demandé.
            lines.append(
                "When the user asks you to send them something, the wording they use picks the "
                "channel: 'send me a message'/'send it to me' with no other detail defaults to "
                "Telegram; 'by mail'/'by email' means Email specifically; 'call me' means Phone. "
                "Never substitute a different channel than the one the user's own words point to, "
                "even if it's the only one you actually have a working tool for right now — if the "
                "requested channel has no available capability, say so honestly instead of silently "
                "sending through a different channel. If the user confirms they always want a given "
                "channel for a given kind of request going forward, use preferences.set_channel to "
                "remember it — only on an explicit, standing confirmation, never after a single "
                "one-off request."
            )
            # Chantier 14 (Natural Language Tasks) : rend explicite la
            # troisième catégorie (au-delà d'INFORMATION/ACTION du §D
            # ci-dessus) — une FUTURE TASK ne doit jamais devenir une
            # exécution immédiate juste parce que le modèle a compris quoi
            # faire.
            lines.append(
                "A request that implies a future moment — a relative delay ('in 3 minutes', 'in an "
                "hour'), a specific clock time ('at 10pm'), or a future day ('tomorrow', 'next "
                "Monday') — is a FUTURE TASK, a third category distinct from both INFORMATION and "
                "an IMMEDIATE ACTION. For a future task, call tasks.create with delay_seconds or "
                "run_at; only the creation of that task runs now, never the requested action itself "
                "— that action only happens later, inside the task, at the scheduled time. If the "
                "requested time/day is ambiguous (e.g. 'remind me Monday' with no time given), ask "
                "for clarification instead of picking an arbitrary time."
            )
            # Chantier 14 §13/§14/§16 : identité de tâche — jamais un
            # task_id deviné, jamais une annulation/modification arbitraire
            # quand plusieurs tâches pourraient correspondre.
            lines.append(
                "Before cancelling or changing a scheduled task/reminder ('cancel my reminder', "
                "'actually make it in 10 minutes instead'), call tasks.list to find its real "
                "task_id — never guess or assume one. If more than one active task could match "
                "what the user means, ask which one before acting, never pick one arbitrarily. "
                "There is no in-place reschedule: to change a task's timing or content, cancel the "
                "existing one (tasks.cancel) and create a new one (tasks.create) with the corrected "
                "delay/time, rather than creating a duplicate alongside the original."
            )
            # Chantier 16 §11 (CLI vs GUI) : préférer la voie la plus directe
            # et fiable, jamais une automatisation d'interface graphique
            # comme premier réflexe.
            lines.append(
                "When an objective can be achieved through a direct, reliable command-line tool "
                "(pc.shell.execute) that is actually available, prefer that over opening and clicking "
                "through a graphical application — it is faster and more reliable. Use "
                "pc.capability.discover first if you are not sure a given executable is actually "
                "installed. Only fall back to GUI automation (pc.application.launch + pc.ui.*) when "
                "the objective genuinely requires a graphical interface, or the user explicitly asked "
                "to see the application."
            )
            # Chantier 16 §10 (USE ≠ SHOW) : utiliser un outil pour accomplir
            # un objectif n'implique jamais de l'afficher à l'utilisateur.
            lines.append(
                "Using a tool or program to accomplish something is not the same as showing it to the "
                "user. Running a command via pc.shell.execute never opens a visible window — that is "
                "intentional, keep it that way. Only make an application visible (pc.application.launch) "
                "when the user explicitly asked to open/see/show it, or the task genuinely cannot be "
                "completed without a visible interface. If the user both asks for the result AND asks "
                "to see the tool ('do the scan and show me Nmap'), do both explicitly."
            )
            # Chantier 16 §14 (No tool/path hallucination) : généralise la
            # directive Chantier 12 §C (jamais un chemin de dossier inventé)
            # aux exécutables/capabilities — jamais un nom de commande deviné.
            lines.append(
                "Never invent or assume the existence of an executable, CLI tool, or capability you "
                "have not confirmed — if you are not certain a program (e.g. nmap, git) is installed, "
                "check with pc.capability.discover before trying to use it. If it is not available and "
                "no working alternative capability exists, say so honestly (e.g. 'I can do this scan, "
                "but nmap isn't installed and I didn't find an equivalent available network capability') "
                "rather than pretending to run something that does not exist."
            )

        elif section.kind == SectionKind.MEMORY:
            memory_section = section.content or {}
            content = memory_section.get("content")
            if content:
                # Chantier 12 §E : une préférence de canal (MemoryType.PREFERENCE)
                # se distingue d'un simple fait — jamais rendue de la même
                # façon générique qu'avant, pour rester actionnable par le
                # modèle sans qu'il ait à deviner le type depuis la forme du dict.
                if memory_section.get("type") == "preference":
                    lines.append(f"Known, confirmed user preference: {content}")
                else:
                    lines.append(f"Known, confirmed fact about the user: {content}")

        elif section.kind == SectionKind.WORLD_STATE:
            c = section.content or {}
            freshness = section.freshness.status.value if section.freshness else "unknown"
            lines.append(f"Observed environment state [{freshness}]: {c.get('domain')}.{c.get('key')} = {c.get('value')!r}")

        elif section.kind == SectionKind.TASK_STATE:
            c = section.content or {}
            progress = c.get("progress") or {}
            lines.append(
                f"Current background task ({c.get('task_id')}): {c.get('objective')!r}, "
                f"state={c.get('state')}, progress={progress.get('current_step')} ({progress.get('percent')})"
            )

        elif section.kind == SectionKind.ACTIVE_TASKS:
            # Chantier 16 : visible même hors d'un step de tâche de fond en
            # cours (contrairement à TASK_STATE ci-dessus) — permet de
            # résoudre "arrête ça" pendant une conversation normale.
            c = section.content or {}
            lines.append(
                f"Other active task ({c.get('task_id')}): {c.get('objective')!r}, state={c.get('state')}"
                + (f", scheduled for {c.get('not_before')}" if c.get("not_before") else "")
            )

        elif section.kind == SectionKind.CONVERSATION_HISTORY:
            # RAYA_V2_PHASE11 fix (context continuity) : distingue désormais
            # qui a dit quoi — avant cette phase, tout était rendu comme "the
            # user said", y compris les propres réponses de RAYA (impossible
            # à distinguer), et dans l'ordre récent -> ancien (illisible comme
            # transcript). Voir raya/context_engine/assembler.py::_entry_role.
            for item in (section.content or {}).get("recent", []):
                text = item.get("content")
                if not text:
                    continue
                if item.get("role") == "assistant":
                    lines.append(f"Earlier in this conversation, you (RAYA) said: {text}")
                else:
                    lines.append(f"Earlier in this conversation, the user said: {text}")

        # SectionKind.TOOL_SCHEMAS: intentionally skipped — see module docstring.

    return "\n".join(lines)
