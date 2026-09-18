"""RAYA V2 — Chantier Audit : Context & Conversational Continuity (Real E2E).

RÈGLES ABSOLUES :
- Aucun mock pour le modèle (Ollama Cloud réel, skip si clé absente)
- Aucun mock pour Windows / Browser
- Aucun HTML local / localhost
- NE MODIFIE AUCUN CODE SOURCE — audit et diagnostic uniquement

Objectif : déterminer PRÉCISÉMENT pourquoi RAYA perd parfois le contexte,
les référents, le scope, la tâche active, ou les informations récentes lors
de conversations multi-tours naturelles.

Catégories de failure (A–N) :
  A  CONTEXT_MISSING          — section absente du contexte assemblé
  B  CONTEXT_STALE            — fait présent mais périmé (status=STALE)
  C  CONTEXT_TRUNCATED        — section évincée par le budget tokens
  D  CONTEXT_WRONG_PRIORITY   — section présente mais mal classée
  E  CONTEXT_PRESENT_MODEL_MISINTERPRETATION — contexte OK, modèle n'en tient pas compte
  F  REFERENT_RESOLUTION_FAILURE  — référent ambigu mal résolu
  G  SCOPE_CORRECTION_FAILURE     — correction de scope ignorée
  H  WORLD_STATE_FAILURE          — fait d'env. absent ou stale dans WS
  I  MEMORY_RETRIEVAL_FAILURE     — souvenir non récupéré
  J  TASK_CONTEXT_FAILURE         — contexte de tâche active absent
  K  CONVERSATION_HISTORY_FAILURE — historique de conversation tronqué ou absent
  L  TOOL_RESULT_CONTEXT_FAILURE  — résultat d'outil non disponible tour suivant
  M  MODEL_REQUEST_SERIALIZATION_FAILURE — context assemblé non sérialisé correctement
  N  MODEL_BEHAVIOR_FAILURE        — contexte présent, modèle comporte mal

Run: pytest tests/audit/test_context_real_e2e_audit.py -v -s
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

# Force UTF-8 output on Windows where the default console codec is CP1252.
# reconfigure() is available on Python 3.7+ and is a no-op on UTF-8 consoles.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


# ─── Helpers pour lire la clé API ────────────────────────────────────────────

def _read_api_key() -> str | None:
    for candidate in (
        Path(r"C:\Users\ruben\OneDrive\Bureau\RAYA\.env"),
        Path(__file__).resolve().parents[2] / ".env",
    ):
        if not candidate.exists():
            continue
        for line in candidate.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("OLLAMA_API_KEY="):
                val = line.split("=", 1)[1].split("#")[0].strip().strip('"').strip("'")
                return val or None
    return None


OLLAMA_KEY = _read_api_key()
skip_no_key = pytest.mark.skipif(OLLAMA_KEY is None, reason="OLLAMA_API_KEY not available")
needs_key = pytest.mark.skipif(OLLAMA_KEY is None, reason="needs OLLAMA_API_KEY for real model")


# ─── Fixture RAYA lightweight (sans devices lourds) ──────────────────────────

@pytest.fixture()
def raya_lite(tmp_path):
    """RAYA instance légère : vrai modèle Ollama Cloud, mémoire fraîche, pas
    de devices Windows/Browser (évite les timeouts sur CI). Les tests de
    devices ouvrent leur propre fixture spécifique."""
    import dataclasses
    if OLLAMA_KEY is None:
        pytest.skip("OLLAMA_API_KEY not available")

    from raya.runtime.bootstrap import bootstrap
    from raya.runtime.config import load_config

    base = load_config()
    cfg = dataclasses.replace(
        base,
        # Modèle cloud réel
        ollama_api_key=OLLAMA_KEY,
        # Pas de devices lourds pour les tests de contexte pur
        enable_windows_device=False,
        enable_browser_device=False,
        enable_phone_device=False,
        enable_perception=False,
        enable_screen_sensor=False,
        enable_camera_sensor=False,
        # Base fraîche isolée
        db_path=tmp_path / "audit.db",
        log_dir=tmp_path / "logs",
        tool_workspace_dir=tmp_path / "workspace",
        device_screenshot_dir=tmp_path / "screenshots",
        vision_screenshot_dir=tmp_path / "vision",
    )
    handles = bootstrap(config=cfg)
    yield handles
    handles.shutdown()


@pytest.fixture()
def raya_full(tmp_path):
    """RAYA instance complète : vrai modèle + devices Windows + Browser.
    Utilisée pour les scénarios nécessitant l'interaction réelle avec le PC."""
    import dataclasses
    if OLLAMA_KEY is None:
        pytest.skip("OLLAMA_API_KEY not available")

    from raya.runtime.bootstrap import bootstrap
    from raya.runtime.config import load_config

    base = load_config()
    cfg = dataclasses.replace(
        base,
        ollama_api_key=OLLAMA_KEY,
        enable_windows_device=True,
        enable_browser_device=True,
        enable_phone_device=False,
        enable_perception=True,
        enable_screen_sensor=False,
        enable_camera_sensor=False,
        # Base fraîche isolée
        db_path=tmp_path / "audit.db",
        log_dir=tmp_path / "logs",
        tool_workspace_dir=tmp_path / "workspace",
        device_screenshot_dir=tmp_path / "screenshots",
        vision_screenshot_dir=tmp_path / "vision",
    )
    handles = bootstrap(config=cfg)
    yield handles
    handles.shutdown()


# ─── Helpers d'audit ─────────────────────────────────────────────────────────

def _send(handles, session_id: str, text: str) -> dict[str, Any]:
    """Envoie un message et capture tout le contexte observable."""
    from raya.contracts import Channel, HarnessRequest, InterfaceInput
    from raya.context_engine import render_system_prompt

    req = HarnessRequest(
        channel=Channel.CLI,
        session_id=session_id,
        input=InterfaceInput(text=text),
    )
    state = handles.harness.handle_request(req)
    ctx = handles.harness.last_context(session_id)
    response = handles.harness.response_text(session_id)
    trace = handles.harness.last_tool_trace(session_id)

    # Sérialise le system prompt réel envoyé au modèle
    system_prompt = render_system_prompt(ctx) if ctx else ""

    # Résumé des sections assemblées
    sections_summary = []
    if ctx:
        for s in ctx.sections:
            sections_summary.append({
                "kind": s.kind.value,
                "provenance": s.provenance,
                "rank": round(s.rank_score, 3),
                "tokens_est": len(json.dumps(s.content, ensure_ascii=False)) // 4,
            })

    return {
        "input": text,
        "response": response,
        "tool_trace": trace,
        "context_budget": ctx.budget_tokens if ctx else 0,
        "context_used": ctx.used_tokens_estimate if ctx else 0,
        "sections": sections_summary,
        "system_prompt_len": len(system_prompt),
        "system_prompt_snippet": system_prompt[:500],
        "state_status": state.status.value,
        "state_error": state.error.code if state.error else None,
    }


def _has_section_kind(turn_data: dict, kind: str) -> bool:
    return any(s["kind"] == kind for s in turn_data["sections"])


def _get_section(turn_data: dict, kind: str) -> dict | None:
    for s in turn_data["sections"]:
        if s["kind"] == kind:
            return s
    return None


def _conversation_history_from_context(handles, session_id: str) -> list[dict]:
    """Lit directement l'historique de conversation depuis le dernier contexte."""
    ctx = handles.harness.last_context(session_id)
    if ctx is None:
        return []
    for s in ctx.sections:
        if s.kind.value == "conversation_history":
            return s.content.get("recent", [])
    return []


def _world_state_facts(handles) -> list[dict]:
    """Retourne tous les faits World State actuels."""
    from raya.contracts import Confidence
    facts = handles.world_state.retrieve_relevant(())
    return [
        {"domain": f.domain, "key": f.key, "value": f.value,
         "status": f.status.value, "source": f.source}
        for f in facts
    ]


def _safe(s: str, maxlen: int = 200) -> str:
    """Truncate and make safe for narrow-codec consoles (e.g. Windows CP1252)."""
    return s[:maxlen].encode("ascii", errors="replace").decode("ascii")


def _print_turn(label: str, turn: dict, show_prompt: bool = False) -> None:
    print(f"\n{'='*60}")
    print(f"TURN: {label}")
    print(f"  INPUT  : {turn['input']!r}")
    print(f"  RESPONSE: {_safe(turn['response'])!r}")
    print(f"  STATUS : {turn['state_status']}")
    if turn["state_error"]:
        print(f"  ERROR  : {turn['state_error']}")
    print(f"  CTX    : {turn['context_used']}/{turn['context_budget']} tokens")
    print(f"  SECTIONS ({len(turn['sections'])}):")
    for s in turn["sections"]:
        print(f"    [{s['kind']:30s}] rank={s['rank']:.3f} prov={s['provenance']}")
    if turn["tool_trace"]:
        print(f"  TOOLS ({len(turn['tool_trace'])}):")
        for t in turn["tool_trace"]:
            print(f"    {t.get('tool_name')} -> {t.get('status')} / {t.get('outcome')}")
    if show_prompt:
        print(f"  SYSTEM PROMPT SNIPPET:\n{turn['system_prompt_snippet']}")
    print(f"{'='*60}")


# ─── S1 : Référent simple (Bloc-Notes) ───────────────────────────────────────

@needs_key
def test_s1_simple_referent_notepad(raya_full):
    """S1 — 'ouvre le bloc-notes' → 'écris bonjour' → 'ferme-le'.
    Vérifie que RAYA sait que 'le' = le Bloc-Notes de T1.
    FOCUS: World State active_window, conversation history, référent résolution.
    """
    session = "audit-s1"
    print("\n\n### SCENARIO S1: Référent simple (Bloc-Notes) ###")

    # T1: Ouvre le Bloc-Notes
    t1 = _send(raya_full, session, "ouvre le bloc-notes")
    _print_turn("T1 — ouvre le bloc-notes", t1)

    ws_after_t1 = _world_state_facts(raya_full)
    print(f"\nWorld State après T1 ({len(ws_after_t1)} faits):")
    for f in ws_after_t1:
        print(f"  {f['domain']}.{f['key']} = {f['value']!r} [{f['status']}] from {f['source']}")

    # Vérifie qu'active_window est dans WS
    active_window_fact = next(
        (f for f in ws_after_t1 if f["domain"] == "pc" and f["key"] == "active_window"), None
    )
    print(f"\nDIAGNOSTIC T1:")
    print(f"  pc.active_window dans WS: {active_window_fact is not None}")
    if active_window_fact:
        print(f"  pc.active_window = {active_window_fact['value']!r}")
    print(f"  pc.application.launch appelé: {any(t.get('tool_name','').startswith('pc.') for t in t1['tool_trace'])}")

    # Attente courte pour que la fenêtre s'ouvre
    time.sleep(1.0)

    # T2: Écris bonjour (référent = Bloc-Notes ouvert en T1)
    t2 = _send(raya_full, session, "écris bonjour")
    _print_turn("T2 — écris bonjour", t2)

    conv_hist_t2 = _conversation_history_from_context(raya_full, session)
    print(f"\nConversation History dans contexte T2 ({len(conv_hist_t2)} entries):")
    for e in conv_hist_t2:
        print(f"  [{e['role']}] {_safe(str(e['content']), 100)}")

    ws_after_t2 = _world_state_facts(raya_full)
    active_window_t2 = next(
        (f for f in ws_after_t2 if f["domain"] == "pc" and f["key"] == "active_window"), None
    )
    print(f"\nDIAGNOSTIC T2:")
    print(f"  Conv history présent: {_has_section_kind(t2, 'conversation_history')}")
    print(f"  Nb entrées conv history: {len(conv_hist_t2)}")
    print(f"  T1 user ('ouvre le bloc-notes') visible: "
          f"{any('ouvre' in str(e.get('content','')) for e in conv_hist_t2)}")
    print(f"  T1 assistant response visible: "
          f"{any(e.get('role')=='assistant' for e in conv_hist_t2)}")
    print(f"  World State WORLD_STATE section: {_has_section_kind(t2, 'world_state')}")
    print(f"  active_window dans WS: {active_window_t2 is not None}")
    if active_window_t2:
        print(f"  active_window = {active_window_t2['value']!r}")
    print(f"  RAYA a tapé dans une fenêtre: "
          f"{any('type' in t.get('tool_name','') or 'write' in t.get('tool_name','') or 'ui' in t.get('tool_name','') for t in t2['tool_trace'])}")

    # T3: Ferme-le (référent = Bloc-Notes)
    t3 = _send(raya_full, session, "ferme-le")
    _print_turn("T3 — ferme-le", t3)

    conv_hist_t3 = _conversation_history_from_context(raya_full, session)
    print(f"\nDIAGNOSTIC T3:")
    print(f"  Conv history présent: {_has_section_kind(t3, 'conversation_history')}")
    print(f"  Nb entrées conv history: {len(conv_hist_t3)}")
    print(f"  Tool close/kill appelé: "
          f"{any('close' in t.get('tool_name','') or 'kill' in t.get('tool_name','') for t in t3['tool_trace'])}")
    print(f"  RAYA a bien compris 'le' = Bloc-Notes: "
          f"{('bloc' in t3['response'].lower() or 'notepad' in t3['response'].lower() or bool(t3['tool_trace']))}")

    # Assertions diagnostiques
    assert _has_section_kind(t2, "conversation_history"), \
        "FAILURE A/K: conversation_history absent du contexte T2"
    assert len(conv_hist_t2) >= 2, \
        f"FAILURE K: seulement {len(conv_hist_t2)} entrées dans conv history T2 (attendu ≥2)"

    print("\nCONCLUSION S1:")
    if not t3["tool_trace"]:
        print("  VERDICT: FAILURE F/E — 'ferme-le' n'a déclenché aucun tool_call")
    else:
        print("  VERDICT: PASS — 'ferme-le' a déclenché un tool_call")


# ─── S2 : Correction de scope ─────────────────────────────────────────────────

@needs_key
def test_s2_scope_correction_battery(raya_full):
    """S2 — 'combien j'ai de batterie?' → 'sur mon laptop'.
    Vérifie que la correction de scope est interprétée comme clarification
    de la question précédente, non comme une nouvelle commande.
    """
    session = "audit-s2"
    print("\n\n### SCENARIO S2: Correction de scope (batterie) ###")

    # T1: Question batterie
    t1 = _send(raya_full, session, "combien j'ai de batterie ?")
    _print_turn("T1 — combien j'ai de batterie?", t1)

    print(f"\nDIAGNOSTIC T1:")
    print(f"  Tool batterie appelé: "
          f"{any('battery' in t.get('tool_name','') or 'power' in t.get('tool_name','') for t in t1['tool_trace'])}")
    print(f"  Réponse mentionne batterie/laptop/pourcentage: "
          f"{any(kw in t1['response'].lower() for kw in ['batterie','battery','%','laptop','ordinateur'])}")

    conv_hist_t1 = _conversation_history_from_context(raya_full, session)
    print(f"  Conv history T1: {len(conv_hist_t1)} entrées")

    # T2: Correction de scope
    t2 = _send(raya_full, session, "sur mon laptop")
    _print_turn("T2 — sur mon laptop", t2)

    conv_hist_t2 = _conversation_history_from_context(raya_full, session)
    print(f"\nDIAGNOSTIC T2:")
    print(f"  Conv history présent: {_has_section_kind(t2, 'conversation_history')}")
    print(f"  Nb entrées conv history: {len(conv_hist_t2)}")
    print(f"  T1 user visible dans history: "
          f"{any('batterie' in str(e.get('content','')) or 'battery' in str(e.get('content','')) for e in conv_hist_t2)}")
    print(f"  T1 assistant response visible: "
          f"{any(e.get('role')=='assistant' for e in conv_hist_t2)}")

    # Évalue la réponse : est-ce une clarification ou une nouvelle action?
    response_lower = t2["response"].lower()
    is_scope_correction = any(kw in response_lower for kw in [
        'batterie', 'battery', '%', 'laptop', 'ordinateur', 'niveau', 'charge'
    ])
    is_wrong_action = any(kw in response_lower for kw in [
        'ouvrir', 'lancer', 'démarrer', 'sur le laptop', 'transférer'
    ]) and not is_scope_correction

    print(f"\n  Réponse interprétée comme scope correction: {is_scope_correction}")
    print(f"  Réponse interprétée comme nouvelle commande: {is_wrong_action}")
    print(f"  Aucun tool appelé en T2 (attendu pour scope correction): {not bool(t2['tool_trace'])}")

    if t2["tool_trace"]:
        print(f"  Tools appelés en T2 (inattendu): {[t.get('tool_name') for t in t2['tool_trace']]}")

    print("\nCONCLUSION S2:")
    if is_scope_correction:
        print("  VERDICT: PASS — 'sur mon laptop' interprété comme correction de scope")
    elif is_wrong_action:
        print("  VERDICT: FAILURE G — scope correction ignorée, interprétée comme nouvelle commande")
    else:
        print("  VERDICT: INCONCLUSIVE — réponse ambiguë")


# ─── S3 : Référent browser ────────────────────────────────────────────────────

@needs_key
def test_s3_browser_referent_amazon(raya_full):
    """S3 — Amazon Raspberry Pi → 'ouvre le premier résultat'.
    SAFE : navigation + lecture seulement, aucun achat.
    Vérifie que RAYA retient les résultats de recherche du tour précédent.
    """
    session = "audit-s3"
    print("\n\n### SCENARIO S3: Référent browser (Amazon search) ###")

    # T1: Cherche Raspberry Pi sur Amazon
    t1 = _send(raya_full, session, "cherche 'Raspberry Pi' sur Amazon")
    _print_turn("T1 — cherche Raspberry Pi Amazon", t1)

    ws_after_t1 = _world_state_facts(raya_full)
    print(f"\nWorld State après T1 ({len(ws_after_t1)} faits):")
    for f in ws_after_t1:
        if f["domain"] == "browser":
            print(f"  {f['domain']}.{f['key']} = {str(f['value'])[:80]!r} [{f['status']}]")

    print(f"\nDIAGNOSTIC T1:")
    print(f"  Browser.navigate appelé: "
          f"{any('navigate' in t.get('tool_name','') for t in t1['tool_trace'])}")
    print(f"  URL Amazon dans WS: "
          f"{any('amazon' in str(f['value']).lower() for f in ws_after_t1 if f['domain']=='browser')}")

    # T2: Ouvre le premier résultat
    t2 = _send(raya_full, session, "ouvre le premier")
    _print_turn("T2 — ouvre le premier", t2)

    conv_hist_t2 = _conversation_history_from_context(raya_full, session)
    ws_after_t2 = _world_state_facts(raya_full)

    print(f"\nDIAGNOSTIC T2:")
    print(f"  Conv history présent: {_has_section_kind(t2, 'conversation_history')}")
    print(f"  Nb entrées conv history: {len(conv_hist_t2)}")
    print(f"  T1 user ('cherche') visible: "
          f"{any('cherche' in str(e.get('content','')) for e in conv_hist_t2)}")
    print(f"  World State présent: {_has_section_kind(t2, 'world_state')}")
    print(f"  URL Amazon dans WS T2: "
          f"{any('amazon' in str(f['value']).lower() for f in ws_after_t2 if f['domain']=='browser')}")
    print(f"  Tool navigate appelé T2: "
          f"{any('navigate' in t.get('tool_name','') for t in t2['tool_trace'])}")
    print(f"  Tool read_page appelé T2: "
          f"{any('read_page' in t.get('tool_name','') for t in t2['tool_trace'])}")

    print("\nCONCLUSION S3:")
    if any('navigate' in t.get('tool_name','') for t in t2['tool_trace']):
        print("  VERDICT: PASS — 'le premier' a déclenché une navigation vers un résultat")
    elif not t2["tool_trace"]:
        print("  VERDICT: FAILURE F — 'ouvre le premier' n'a déclenché aucun tool_call")
    else:
        print("  VERDICT: PARTIAL — tool appelé mais pas navigate")


# ─── S4 : Action puis question ────────────────────────────────────────────────

@needs_key
def test_s4_action_then_question(raya_full):
    """S4 — 'ouvre la calculatrice' → 'tu en penses quoi?'.
    Vérifie que RAYA répond à une question méta sur sa dernière action.
    """
    session = "audit-s4"
    print("\n\n### SCENARIO S4: Action puis question méta ###")

    # T1: Ouvre la calculatrice
    t1 = _send(raya_full, session, "ouvre la calculatrice")
    _print_turn("T1 — ouvre la calculatrice", t1)

    # T2: Question méta sur l'action précédente
    t2 = _send(raya_full, session, "tu en penses quoi ?")
    _print_turn("T2 — tu en penses quoi?", t2)

    conv_hist_t2 = _conversation_history_from_context(raya_full, session)
    print(f"\nDIAGNOSTIC T2:")
    print(f"  Conv history présent: {_has_section_kind(t2, 'conversation_history')}")
    print(f"  Nb entrées conv history: {len(conv_hist_t2)}")
    print(f"  T1 user visible: "
          f"{any('calculatrice' in str(e.get('content','')) for e in conv_hist_t2)}")
    print(f"  T1 assistant visible: "
          f"{any(e.get('role')=='assistant' for e in conv_hist_t2)}")
    print(f"  Réponse mentionne calculatrice: "
          f"{'calculat' in t2['response'].lower()}")
    print(f"  Réponse est une opinion/commentaire: "
          f"{any(kw in t2['response'].lower() for kw in ['utilité','outil','calcul','pense','calculatrice','simple'])}")
    print(f"  Aucun tool appelé T2: {not bool(t2['tool_trace'])}")

    print("\nCONCLUSION S4:")
    if 'calculat' in t2['response'].lower() and not t2["tool_trace"]:
        print("  VERDICT: PASS — RAYA a répondu en rapport avec la calculatrice, sans tool call")
    elif not conv_hist_t2:
        print("  VERDICT: FAILURE K — conversation history absent, RAYA ne sait pas de quoi il parle")
    else:
        print("  VERDICT: INCONCLUSIVE — vérifier manuellement si la réponse est cohérente")


# ─── S5 : Vraie ambiguïté ─────────────────────────────────────────────────────

@needs_key
def test_s5_real_ambiguity(raya_full):
    """S5 — ouvre calculatrice ET bloc-notes → 'ferme-le'.
    Vérifie que RAYA demande une clarification au lieu de choisir arbitrairement.
    """
    session = "audit-s5"
    print("\n\n### SCENARIO S5: Vraie ambiguïté (2 apps ouvertes) ###")

    # T1: Ouvre calculatrice
    t1 = _send(raya_full, session, "ouvre la calculatrice")
    _print_turn("T1 — ouvre la calculatrice", t1)
    time.sleep(0.5)

    # T2: Ouvre Bloc-Notes
    t2 = _send(raya_full, session, "et ouvre aussi le bloc-notes")
    _print_turn("T2 — ouvre aussi le bloc-notes", t2)
    time.sleep(0.5)

    # T3: Ambiguïté — "ferme-le"
    t3 = _send(raya_full, session, "ferme-le")
    _print_turn("T3 — ferme-le", t3, show_prompt=False)

    conv_hist_t3 = _conversation_history_from_context(raya_full, session)
    ws_after_t2 = _world_state_facts(raya_full)

    print(f"\nDIAGNOSTIC T3:")
    print(f"  Conv history présent: {_has_section_kind(t3, 'conversation_history')}")
    print(f"  Nb entrées conv history: {len(conv_hist_t3)}")
    print(f"  World State présent: {_has_section_kind(t3, 'world_state')}")
    print(f"  Faits WS: {[(f['domain'],f['key'],str(f['value'])[:40]) for f in ws_after_t2]}")

    response_lower = t3["response"].lower()
    asked_clarification = any(kw in response_lower for kw in [
        'laquelle', 'lequel', 'quelle', 'quel', 'laquelle des deux', 'calculatrice ou',
        'bloc-notes ou', 'notepad', 'which', 'clarif', 'précise', '?'
    ])
    closed_something = bool(t3["tool_trace"])

    print(f"\n  RAYA demande clarification: {asked_clarification}")
    print(f"  RAYA a fermé quelque chose (tool): {closed_something}")
    if closed_something:
        print(f"  Tools: {[t.get('tool_name') for t in t3['tool_trace']]}")

    print("\nCONCLUSION S5:")
    if asked_clarification and not closed_something:
        print("  VERDICT: PASS — ambiguïté reconnue, clarification demandée")
    elif closed_something and not asked_clarification:
        print("  VERDICT: FAILURE F — fermeture arbitraire sans clarification")
    elif asked_clarification and closed_something:
        print("  VERDICT: PARTIAL — clarification + action en même temps")
    else:
        print("  VERDICT: FAILURE E/N — réponse confuse, ni clarification ni action")


# ─── S6 : Tâche de fond + question parallèle ─────────────────────────────────

@needs_key
def test_s6_background_task_parallel_question(raya_lite):
    """S6 — Lance une tâche de fond → pose une question parallèle.
    Vérifie que handle_request répond immédiatement sans bloquer sur la tâche.
    Vérifie que la tâche active est visible dans le contexte.
    """
    session = "audit-s6"
    print("\n\n### SCENARIO S6: Tâche de fond + question parallèle ###")

    # T1: Lance une tâche de fond (longue)
    t1 = _send(raya_lite, session, "crée une tâche de fond : compte jusqu'à 30 une fois par seconde")
    _print_turn("T1 — crée tâche fond", t1)

    tasks_after = raya_lite.harness.list_tasks()
    print(f"\nTâches actives après T1: {len(tasks_after)}")
    for t in tasks_after:
        print(f"  [{t.id}] {t.objective!r} state={t.state.value}")

    # Pause pour laisser la tâche démarrer
    time.sleep(1.0)

    # T2: Question parallèle pendant que la tâche tourne
    t2_start = time.time()
    t2 = _send(raya_lite, session, "quelle heure est-il ?")
    t2_elapsed = time.time() - t2_start
    _print_turn("T2 — quelle heure est-il?", t2)

    conv_hist_t2 = _conversation_history_from_context(raya_lite, session)
    active_task_section = _get_section(t2, "active_tasks")

    print(f"\nDIAGNOSTIC T2:")
    print(f"  Latence T2 (attendu <5s): {t2_elapsed:.2f}s — {'OK' if t2_elapsed < 5 else 'LENT'}")
    print(f"  Conv history présent: {_has_section_kind(t2, 'conversation_history')}")
    print(f"  Section ACTIVE_TASKS présente: {active_task_section is not None}")
    if active_task_section:
        print(f"  ACTIVE_TASKS content: {active_task_section}")
    print(f"  Réponse mentionne heure: "
          f"{any(kw in t2['response'].lower() for kw in ['heure', 'h', ':', 'time', 'hour'])}")
    print(f"  system.time.now appelé: "
          f"{any('time' in t.get('tool_name','') for t in t2['tool_trace'])}")

    print("\nCONCLUSION S6:")
    if active_task_section is not None:
        print("  VERDICT: PASS — tâche active visible dans contexte T2")
    else:
        tasks_currently = raya_lite.harness.list_tasks()
        if tasks_currently:
            print("  VERDICT: FAILURE J — tâche active mais section ACTIVE_TASKS absente du contexte")
        else:
            print("  VERDICT: N/A — aucune tâche active au moment de T2")


# ─── S7 : Steering d'une tâche active ────────────────────────────────────────

@needs_key
def test_s7_steering(raya_lite):
    """S7 — Tâche démarre → 'finalement fais-le en anglais'.
    Vérifie que le steering_guidance arrive dans le contexte de la tâche.
    """
    session = "audit-s7"
    print("\n\n### SCENARIO S7: Steering d'une tâche active ###")

    # T1: Lance une tâche avec rédaction
    t1 = _send(raya_lite, session,
               "écris un court poème de 4 lignes sur la pluie en français dans un fichier poeme.txt")
    _print_turn("T1 — crée tâche rédaction poème", t1)

    tasks = raya_lite.harness.list_tasks()
    print(f"\nTâches après T1: {[(t.id, t.state.value) for t in tasks]}")

    if not tasks:
        print("INFO: Aucune tâche créée en T1 (modèle a répondu directement)")
        # Le modèle a peut-être juste répondu sans créer de tâche de fond
        # Dans ce cas on continue quand même pour documenter le comportement
        return

    task_id = tasks[0].id

    # T2: Steering — changer la langue
    t2 = _send(raya_lite, session, "finalement fais-le en anglais")
    _print_turn("T2 — finalement en anglais (steering)", t2)

    # Vérifie que le steering_guidance a été écrit dans le checkpoint
    tasks_after_steer = raya_lite.harness.list_tasks()
    steered_task = next((t for t in tasks_after_steer if t.id == task_id), None)

    print(f"\nDIAGNOSTIC T2:")
    if steered_task:
        checkpoint = steered_task.checkpoint or {}
        print(f"  Checkpoint: {checkpoint}")
        print(f"  steering_guidance présent: {'steering_guidance' in checkpoint}")
        if 'steering_guidance' in checkpoint:
            print(f"  steering_guidance: {checkpoint['steering_guidance']!r}")
    else:
        print(f"  Tâche {task_id} non trouvée (déjà terminée?)")

    print("\nCONCLUSION S7:")
    if steered_task and 'steering_guidance' in (steered_task.checkpoint or {}):
        print("  VERDICT: PASS — steering_guidance écrit dans checkpoint")
    elif steered_task:
        print("  VERDICT: FAILURE J — tâche existe mais steering_guidance non écrit")
    else:
        print("  VERDICT: N/A — tâche terminée ou non créée")


# ─── S8 : Résultat d'outil → tour suivant ─────────────────────────────────────

@needs_key
def test_s8_tool_result_next_turn(raya_full):
    """S8 — 'ouvre la calculatrice' → 'qu'est-ce qui est ouvert maintenant?'.
    Vérifie que pc.active_window (World State) arrive au tour 2.
    FOCUS: World State freshness, section présence, résolution de question env.
    """
    session = "audit-s8"
    print("\n\n### SCENARIO S8: Tool result → tour suivant (World State) ###")

    # T1: Ouvre la calculatrice
    t1 = _send(raya_full, session, "ouvre la calculatrice")
    _print_turn("T1 — ouvre la calculatrice", t1)

    ws_after_t1 = _world_state_facts(raya_full)
    print(f"\nWorld State après T1 ({len(ws_after_t1)} faits):")
    for f in ws_after_t1:
        print(f"  {f['domain']}.{f['key']} = {str(f['value'])[:60]!r} [{f['status']}] from {f['source']}")

    active_window_t1 = next(
        (f for f in ws_after_t1 if f["domain"] == "pc" and f["key"] == "active_window"), None
    )
    print(f"\nDIAGNOSTIC T1:")
    print(f"  Tool pc.application.launch appelé: "
          f"{any('launch' in t.get('tool_name','') or 'application' in t.get('tool_name','') for t in t1['tool_trace'])}")
    print(f"  active_window dans WS: {active_window_t1 is not None}")
    if active_window_t1:
        print(f"  active_window = {active_window_t1['value']!r}")

    time.sleep(1.0)

    # T2: Question sur l'environnement
    t2 = _send(raya_full, session, "qu'est-ce qui est ouvert maintenant ?")
    _print_turn("T2 — qu'est-ce qui est ouvert maintenant?", t2)

    ws_sections_in_t2 = [s for s in t2["sections"] if s["kind"] == "world_state"]
    print(f"\nDIAGNOSTIC T2:")
    print(f"  World State sections dans contexte T2: {len(ws_sections_in_t2)}")
    for s in ws_sections_in_t2:
        print(f"    {s['provenance']} rank={s['rank']}")

    # Lit directement le contexte pour voir le WS
    ctx_t2 = raya_full.harness.last_context(session)
    if ctx_t2:
        ws_in_ctx = [s for s in ctx_t2.sections if s.kind.value == "world_state"]
        print(f"  WS facts visibles dans contexte:")
        for ws_s in ws_in_ctx:
            print(f"    {ws_s.content.get('domain')}.{ws_s.content.get('key')} = "
                  f"{str(ws_s.content.get('value'))[:50]!r}")

    response_lower = t2["response"].lower()
    mentions_calc = any(kw in response_lower for kw in ['calculat', 'calc', 'calculator'])
    tool_pc_called = any('pc.' in t.get('tool_name','') for t in t2["tool_trace"])

    print(f"\n  Réponse mentionne calculatrice: {mentions_calc}")
    print(f"  Tool PC appelé pour vérifier: {tool_pc_called}")
    if tool_pc_called:
        print(f"  Tools: {[t.get('tool_name') for t in t2['tool_trace']]}")

    print("\nCONCLUSION S8:")
    if active_window_t1 is None:
        print("  VERDICT: FAILURE H — active_window non écrit dans WS après T1 (World State non peuplé)")
    elif not ws_sections_in_t2:
        print("  VERDICT: FAILURE A/H — active_window dans WS mais sections WS absentes du contexte T2")
    elif not mentions_calc and not tool_pc_called:
        print("  VERDICT: FAILURE E — WS présent dans contexte mais modèle ne répond pas depuis WS")
    elif mentions_calc:
        print("  VERDICT: PASS — RAYA répond 'calculatrice' depuis le World State")
    else:
        print("  VERDICT: PARTIAL — tool PC appelé pour re-vérifier (peut être acceptable)")


# ─── S9 : World State + conversation ─────────────────────────────────────────

@needs_key
def test_s9_world_state_plus_conversation(raya_full):
    """S9 — ouvre calculatrice → question mixte (conversation + état environnement).
    Vérifie que le modèle utilise À LA FOIS l'historique et le World State.
    """
    session = "audit-s9"
    print("\n\n### SCENARIO S9: World State + conversation (contexte hybride) ###")

    # Pré-charge un fait de mémoire pour enrichir le test
    from raya.contracts import MemoryEntry, MemoryLayer, MemoryLifecycle, MemoryType, ChannelScope
    raya_full.memory.write(MemoryEntry(
        type=MemoryType.PREFERENCE,
        layer=MemoryLayer.PERSONAL,
        channel_scope=ChannelScope.CHAT,
        content="L'utilisateur aime la calculatrice pour faire des calculs rapides.",
        provenance="profile:audit_test",
        lifecycle=MemoryLifecycle.CONFIRMED,
    ))

    # T1: Ouvre la calculatrice
    t1 = _send(raya_full, session, "ouvre la calculatrice")
    _print_turn("T1 — ouvre la calculatrice", t1)
    time.sleep(0.5)

    # T2: Question mixte — combine historique + WS
    t2 = _send(raya_full, session,
               "quelle application est active sur mon ordinateur, et pourquoi je l'ai ouverte ?")
    _print_turn("T2 — question mixte WS + historique", t2, show_prompt=False)

    conv_hist_t2 = _conversation_history_from_context(raya_full, session)
    ctx_t2 = raya_full.harness.last_context(session)
    ws_in_ctx = []
    mem_in_ctx = []
    if ctx_t2:
        ws_in_ctx = [s for s in ctx_t2.sections if s.kind.value == "world_state"]
        mem_in_ctx = [s for s in ctx_t2.sections if s.kind.value == "memory"]

    print(f"\nDIAGNOSTIC T2:")
    print(f"  Conv history sections: {len(conv_hist_t2)} entrées")
    print(f"  World State sections dans contexte: {len(ws_in_ctx)}")
    print(f"  Memory sections dans contexte: {len(mem_in_ctx)}")

    response_lower = t2["response"].lower()
    mentions_calc = any(kw in response_lower for kw in ['calculat', 'calc'])
    mentions_reason = any(kw in response_lower for kw in [
        'ouvert', 'demandé', 'asked', 'request', 'toi', 'vous', 'tu'
    ])

    print(f"\n  Réponse mentionne calculatrice: {mentions_calc}")
    print(f"  Réponse explique pourquoi (historique): {mentions_reason}")

    print("\nCONCLUSION S9:")
    if mentions_calc and mentions_reason:
        print("  VERDICT: PASS — RAYA répond depuis WS + historique conversation")
    elif mentions_calc and not mentions_reason:
        print("  VERDICT: PARTIAL — WS utilisé mais pas l'historique")
    elif not mentions_calc and mentions_reason:
        print("  VERDICT: PARTIAL — historique utilisé mais pas WS")
    else:
        print("  VERDICT: FAILURE A/E — ni WS ni historique utilisés")


# ─── S10 : Mémoire persistante + contexte courant ────────────────────────────

@needs_key
def test_s10_memory_plus_current_context(raya_lite):
    """S10 — Mémoire persistante pré-chargée + question sur l'environnement courant.
    Vérifie que la mémoire PERSONAL est récupérée et combinée avec le contexte actuel.
    """
    session = "audit-s10"
    print("\n\n### SCENARIO S10: Mémoire persistante + contexte courant ###")

    # Pré-charge des faits de mémoire (simule un profil existant)
    from raya.contracts import MemoryEntry, MemoryLayer, MemoryLifecycle, MemoryType, ChannelScope
    raya_lite.memory.write(MemoryEntry(
        type=MemoryType.FACT,
        layer=MemoryLayer.PERSONAL,
        channel_scope=ChannelScope.CHAT,
        content="L'utilisateur s'appelle Ruben et préfère qu'on le tutoie.",
        provenance="profile_migration:identity",
        lifecycle=MemoryLifecycle.CONFIRMED,
    ))
    raya_lite.memory.write(MemoryEntry(
        type=MemoryType.PREFERENCE,
        layer=MemoryLayer.PERSONAL,
        channel_scope=ChannelScope.CHAT,
        content="Ruben habite à Evere, Bruxelles, Belgique.",
        provenance="profile_migration:identity",
        lifecycle=MemoryLifecycle.CONFIRMED,
    ))
    raya_lite.memory.write(MemoryEntry(
        type=MemoryType.PREFERENCE,
        layer=MemoryLayer.PERSONAL,
        channel_scope=ChannelScope.CHAT,
        content="Ruben utilise Windows 11.",
        provenance="profile:preferences",
        lifecycle=MemoryLifecycle.CONFIRMED,
    ))

    # T1: Question mixte mémoire + env courant
    t1 = _send(raya_lite, session,
               "comment tu t'appelles, et comment je m'appelle moi ?")
    _print_turn("T1 — qui es-tu / qui suis-je?", t1)

    ctx_t1 = raya_lite.harness.last_context(session)
    identity_sections = []
    memory_sections = []
    if ctx_t1:
        for s in ctx_t1.sections:
            if s.kind.value == "memory":
                memory_sections.append(s)
                if s.provenance == "profile_migration:identity":
                    identity_sections.append(s)

    print(f"\nDIAGNOSTIC T1:")
    print(f"  Memory sections dans contexte: {len(memory_sections)}")
    print(f"  Identity sections (provenance=profile_migration:identity): {len(identity_sections)}")
    for s in identity_sections:
        print(f"    {str(s.content.get('content',''))[:80]!r}")

    response_lower = t1["response"].lower()
    knows_raya = "raya" in response_lower
    knows_ruben = "ruben" in response_lower
    uses_tutoie = any(kw in response_lower for kw in ["tu ", "toi ", "ton ", "ta "])

    print(f"\n  Sait qu'il s'appelle RAYA: {knows_raya}")
    print(f"  Sait que l'utilisateur = Ruben: {knows_ruben}")
    print(f"  Tutoiement utilisé: {uses_tutoie}")

    print("\nCONCLUSION S10:")
    if knows_raya and knows_ruben:
        print("  VERDICT: PASS — mémoire d'identité récupérée et utilisée")
    elif identity_sections and not knows_ruben:
        print("  VERDICT: FAILURE E — sections identité présentes mais ignorées par le modèle")
    elif not identity_sections and not knows_ruben:
        print("  VERDICT: FAILURE A/I — sections identité absentes du contexte")
    else:
        print("  VERDICT: PARTIAL — connaissance partielle")


# ─── S11 : Multi-tour correction ─────────────────────────────────────────────

@needs_key
def test_s11_multi_turn_correction(raya_full):
    """S11 — 'ouvre le navigateur' → 'non, Chrome' → 'sur Amazon' → 'cherche Raspberry Pi'.
    Vérifie que chaque correction s'appuie sur le contexte accumulé.
    """
    session = "audit-s11"
    print("\n\n### SCENARIO S11: Multi-tour correction (4 tours) ###")

    # T1: Ouvre le navigateur
    t1 = _send(raya_full, session, "ouvre le navigateur")
    _print_turn("T1 — ouvre le navigateur", t1)

    # T2: Correction — Chrome spécifiquement
    t2 = _send(raya_full, session, "non, Chrome spécifiquement")
    _print_turn("T2 — non, Chrome", t2)

    conv_hist_t2 = _conversation_history_from_context(raya_full, session)
    print(f"\nDIAGNOSTIC T2:")
    print(f"  T1 user visible dans history: {any('navigateur' in str(e.get('content','')) for e in conv_hist_t2)}")
    print(f"  T1 assistant visible: {any(e.get('role')=='assistant' for e in conv_hist_t2)}")
    print(f"  Tool navigate/chrome appelé T2: "
          f"{any('chrome' in str(t).lower() or 'browser' in t.get('tool_name','') for t in t2['tool_trace'])}")

    # T3: Navigue vers Amazon
    t3 = _send(raya_full, session, "sur Amazon")
    _print_turn("T3 — sur Amazon", t3)

    conv_hist_t3 = _conversation_history_from_context(raya_full, session)
    ws_t3 = _world_state_facts(raya_full)
    browser_url_t3 = next(
        (f for f in ws_t3 if f["domain"] == "browser" and f["key"] == "current_url"), None
    )

    print(f"\nDIAGNOSTIC T3:")
    print(f"  Nb conv history entries: {len(conv_hist_t3)}")
    print(f"  browser.navigate appelé T3: "
          f"{any('navigate' in t.get('tool_name','') for t in t3['tool_trace'])}")
    print(f"  Amazon URL dans WS: "
          f"{'amazon' in str(browser_url_t3['value']).lower() if browser_url_t3 else False}")

    # T4: Recherche Raspberry Pi
    t4 = _send(raya_full, session, "cherche Raspberry Pi")
    _print_turn("T4 — cherche Raspberry Pi", t4)

    conv_hist_t4 = _conversation_history_from_context(raya_full, session)
    ws_t4 = _world_state_facts(raya_full)
    browser_url_t4 = next(
        (f for f in ws_t4 if f["domain"] == "browser" and f["key"] == "current_url"), None
    )

    print(f"\nDIAGNOSTIC T4:")
    print(f"  Nb conv history entries: {len(conv_hist_t4)}")
    print(f"  Conv history limit 10, on a {min(8, len(conv_hist_t4))} attendu")
    print(f"  Tool search/navigate appelé T4: "
          f"{any(t.get('tool_name','') in ('browser.navigate','browser.search') or 'navigate' in t.get('tool_name','') for t in t4['tool_trace'])}")
    print(f"  URL résultats Raspberry Pi: "
          f"{'raspberry' in str(browser_url_t4['value']).lower() if browser_url_t4 else False}")

    print("\nCONCLUSION S11:")
    if len(conv_hist_t4) >= 6:
        print("  VERDICT: PASS — conv history contient les 4 tours précédents (≥6 entrées)")
    elif len(conv_hist_t4) >= 4:
        print("  VERDICT: PARTIAL — conv history partiel mais utilisable")
    else:
        print(f"  VERDICT: FAILURE K — conv history trop court ({len(conv_hist_t4)} entrées)")


# ─── S12 : Contexte après échec ───────────────────────────────────────────────

@needs_key
def test_s12_context_after_failure(raya_full):
    """S12 — Action qui échoue → 'réessaie'.
    Vérifie que l'échec est mémorisé et que 'réessaie' pointe sur la bonne action.
    """
    session = "audit-s12"
    print("\n\n### SCENARIO S12: Contexte après échec (réessaie) ###")

    # T1: Action qui échouera probablement (app inexistante)
    t1 = _send(raya_full, session, "ouvre l'application FakeAppDoesNotExist2026")
    _print_turn("T1 — ouvre app inexistante", t1)

    print(f"\nDIAGNOSTIC T1:")
    print(f"  Tool appelé: {bool(t1['tool_trace'])}")
    print(f"  Tool trace: {[t.get('tool_name') for t in t1['tool_trace']]}")
    failed = any(t.get('status') in ('FAILURE', 'failure', 'error') for t in t1["tool_trace"])
    print(f"  Échec détecté dans trace: {failed}")
    print(f"  Réponse mentionne échec: "
          f"{any(kw in t1['response'].lower() for kw in ['impossible','introuvable','pas trouvé','erreur','échec','not found','failed'])}")

    # T2: Réessaie
    t2 = _send(raya_full, session, "réessaie")
    _print_turn("T2 — réessaie", t2)

    conv_hist_t2 = _conversation_history_from_context(raya_full, session)
    print(f"\nDIAGNOSTIC T2:")
    print(f"  Conv history présent: {_has_section_kind(t2, 'conversation_history')}")
    print(f"  Nb entrées: {len(conv_hist_t2)}")
    print(f"  T1 user visible: {any('FakeApp' in str(e.get('content','')) for e in conv_hist_t2)}")
    print(f"  T1 assistant (échec) visible: {any(e.get('role')=='assistant' for e in conv_hist_t2)}")

    same_tool_called_again = any(
        'application' in t.get('tool_name','') or 'launch' in t.get('tool_name','')
        for t in t2["tool_trace"]
    )
    print(f"  RAYA réessaie la même action: {same_tool_called_again}")
    print(f"  RAYA mentionne l'app du T1: "
          f"{'fakeapp' in t2['response'].lower() or 'fake' in t2['response'].lower()}")

    print("\nCONCLUSION S12:")
    if not conv_hist_t2:
        print("  VERDICT: FAILURE K — historique absent, 'réessaie' sans contexte")
    elif same_tool_called_again:
        print("  VERDICT: PASS — 'réessaie' a re-tenté la même app")
    elif any('FakeApp' in str(e.get('content','')) for e in conv_hist_t2):
        print("  VERDICT: PARTIAL — historique présent mais RAYA n'a pas réessayé (choix du modèle)")
    else:
        print("  VERDICT: FAILURE F — historique absent ou référent non résolu")


# ─── Tests diagnostics automatisés ciblés ─────────────────────────────────────

class TestContextDiagnostics:
    """Tests automatisés ciblant les mécanismes de contexte sans device externe."""

    @needs_key
    def test_conversation_history_persists_across_turns(self, raya_lite):
        """Vérifie que l'historique de conversation s'accumule correctement sur 4 tours."""
        session = "diag-conv-hist"
        messages = [
            "bonjour, je m'appelle Alice",
            "j'ai 28 ans",
            "j'habite à Paris",
            "qui suis-je ?",
        ]
        all_turns = []
        for msg in messages:
            turn = _send(raya_lite, session, msg)
            all_turns.append(turn)

        _print_turn("DIAG — T4 (qui suis-je?)", all_turns[3], show_prompt=False)
        conv_hist = _conversation_history_from_context(raya_lite, session)

        print(f"\nConversation history apres 4 tours: {len(conv_hist)} entrees")
        for e in conv_hist:
            print(f"  [{e['role']}] {_safe(str(e['content']), 60)}")

        # Avec 4 messages user + 4 réponses assistant = 8 entrées max (limite=10)
        assert len(conv_hist) >= 6, \
            f"FAILURE K: seulement {len(conv_hist)} entrées (attendu ≥6 pour 4 tours)"

        # T4 devrait voir les 3 premiers messages
        contents = " ".join(str(e.get("content","")) for e in conv_hist)
        assert "alice" in contents.lower() or "Alice" in contents, \
            "FAILURE K: 'Alice' (T1) absent de l'historique au T4"

        response_t4 = all_turns[3]["response"].lower()
        # RAYA devrait mentionner Alice ou ses attributs dans la réponse
        mentions_context = any(kw in response_t4 for kw in ["alice", "28", "paris"])
        print(f"\nRÉSULTAT: mentions_context={mentions_context}")
        print(f"Réponse T4: {all_turns[3]['response']!r}")

        if not mentions_context:
            print("FAILURE E ou K: modèle n'a pas utilisé l'historique pour T4")

    @needs_key
    def test_assistant_response_visible_next_turn(self, raya_lite):
        """Vérifie que la réponse assistant du T1 est visible en T2 (conv history)."""
        session = "diag-assistant-turn"

        # T1
        t1 = _send(raya_lite, session, "quel est le carré de 7 ?")
        response_t1 = t1["response"]

        # T2 — demande de rappel de la réponse T1
        t2 = _send(raya_lite, session, "rappelle-moi ce que tu viens de répondre")
        conv_hist_t2 = _conversation_history_from_context(raya_lite, session)

        assistant_entries = [e for e in conv_hist_t2 if e.get("role") == "assistant"]
        print(f"\nRéponse T1: {response_t1!r}")
        print(f"Entries assistant dans hist T2: {len(assistant_entries)}")
        for e in assistant_entries:
            print(f"  {str(e['content'])[:80]!r}")

        assert len(assistant_entries) >= 1, \
            "FAILURE K: réponse assistant T1 non persistée en conv history (Phase 11 bug?)"

        # La réponse T2 devrait mentionner 49
        response_t2 = t2["response"]
        print(f"Réponse T2: {response_t2!r}")

    @needs_key
    def test_world_state_appears_in_context_sections(self, raya_lite):
        """Vérifie que les faits World State injectés manuellement arrivent en contexte."""
        session = "diag-ws-context"
        from raya.contracts import Confidence, WorldStateFact

        # Injecte manuellement un fait WS
        raya_lite.world_state.apply_update(WorldStateFact(
            domain="pc", key="active_window",
            value="Calculatrice",
            source="test:manual",
            confidence=Confidence.KNOWN_FACT,
            freshness_ttl_s=300,
        ))

        # Envoie un message quelconque pour déclencher l'assemblage du contexte
        t1 = _send(raya_lite, session, "bonjour")
        ws_sections = [s for s in t1["sections"] if s["kind"] == "world_state"]

        print(f"\nWorld State sections dans contexte: {len(ws_sections)}")
        for s in ws_sections:
            print(f"  {s['provenance']}")

        ctx = raya_lite.harness.last_context(session)
        ws_in_ctx = []
        if ctx:
            for s in ctx.sections:
                if s.kind.value == "world_state":
                    ws_in_ctx.append(s.content)

        active_window_in_ctx = any(
            c.get("domain") == "pc" and c.get("key") == "active_window"
            for c in ws_in_ctx
        )
        print(f"active_window (pc) dans contexte: {active_window_in_ctx}")

        assert active_window_in_ctx, \
            "FAILURE H/A: fait WS (pc.active_window) injecté mais absent du contexte assemblé"

    @needs_key
    def test_context_budget_not_exceeded(self, raya_lite):
        """Vérifie le budget tokens ET documente la limitation keyword-filter de mémoire.

        FINDING CLEF (catégorie I — MEMORY_RETRIEVAL_FAILURE) :
        _memory_sections() appelle memory.search(query=query_text) qui KEYWORD-FILTRE
        les entrées : seules celles partageant un mot ≥4 lettres avec la requête sont
        retournées. Si les entrées mémoire ne partagent aucun mot avec la question
        posée, elles sont ENTIÈREMENT ABSENTES du contexte — même si le budget est loin
        d'être dépassé.
        """
        session = "diag-budget"
        from raya.contracts import MemoryEntry, MemoryLayer, MemoryLifecycle, MemoryType, ChannelScope

        # Injecte 30 entrées avec du contenu lexicalement distinct de la requête
        for i in range(30):
            raya_lite.memory.write(MemoryEntry(
                type=MemoryType.FACT,
                layer=MemoryLayer.PERSONAL,
                channel_scope=ChannelScope.CHAT,
                content=f"Fait numéro {i}: préférence guitare saxophone violon contrebasse.",
                provenance=f"test:memory:{i}",
                lifecycle=MemoryLifecycle.CONFIRMED,
            ))

        # Requête lexicalement NON appariée — aucun mot en commun avec le contenu ci-dessus
        t1 = _send(raya_lite, session, "quel temps fait-il dehors aujourd'hui ?")

        print(f"\nBudget: {t1['context_budget']} tokens")
        print(f"Utilisé: {t1['context_used']} tokens")
        print(f"Sections: {len(t1['sections'])}")
        print(f"Memory sections: {sum(1 for s in t1['sections'] if s['kind']=='memory')}")
        print("FINDING I: memory sections absentes car aucun mot en commun query<->content")

        assert t1["context_used"] <= t1["context_budget"] * 1.1, \
            f"FAILURE C: budget dépassé ({t1['context_used']} > {t1['context_budget']})"
        # Chantier 20A fix: _personal_context_sections() injecte TOUJOURS les entrées
        # PERSONAL (limit=8) — indépendamment du keyword overlap.
        # Avant le fix: 0 sections (keyword-mismatch filtrait tout).
        # Après le fix: jusqu'à 8 sections (plafond _PERSONAL_ALWAYS_LIMIT).
        mem_sections = sum(1 for s in t1['sections'] if s['kind'] == 'memory')
        assert 0 < mem_sections <= 8, \
            f"Attendu 1-8 memory sections PERSONAL (fix 20A), got {mem_sections}"

        # Maintenant: requête lexicalement APPARIÉE — "guitare" partage avec le contenu
        t2 = _send(raya_lite, session, "tu te souviens de quelque chose sur ma guitare ?")
        mem_sections_t2 = sum(1 for s in t2['sections'] if s['kind'] == 'memory')
        print(f"\nRequête appariée (guitare) → memory sections: {mem_sections_t2}")
        assert mem_sections_t2 > 0, \
            "FAILURE I: aucune section mémoire même avec requête lexicalement appariée (guitare)"

    @needs_key
    def test_10_turn_history_limit(self, raya_lite):
        """Vérifie que la conv history est bornée à ~10 entrées après 8+ tours."""
        session = "diag-hist-limit"

        for i in range(7):
            _send(raya_lite, session, f"message numéro {i+1}")

        t8 = _send(raya_lite, session, "message numéro 8")
        conv_hist = _conversation_history_from_context(raya_lite, session)

        print(f"\nConv history après 8 tours: {len(conv_hist)} entrées")
        print(f"Limite configurée: 10")
        print(f"Entrées: {[(e['role'], str(e['content'])[:30]) for e in conv_hist]}")

        assert len(conv_hist) <= 10, \
            f"FAILURE C: conversation history dépasse 10 entrées ({len(conv_hist)})"
        # T8 context is assembled BEFORE T8 user message is written → T7 is the
        # most recent USER message visible. T8 user absent is CORRECT behavior.
        contents = " ".join(str(e.get("content","")) for e in conv_hist)
        assert "7" in contents, \
            "FAILURE K: message T7 (dernier tour visible à T8) absent de l'historique"

    @needs_key
    def test_identity_baseline_always_present(self, raya_lite):
        """Vérifie que les faits d'identité profile_migration:identity sont toujours en contexte."""
        session = "diag-identity"
        from raya.contracts import MemoryEntry, MemoryLayer, MemoryLifecycle, MemoryType, ChannelScope

        # Écrit un fait d'identité baseline
        raya_lite.memory.write(MemoryEntry(
            type=MemoryType.FACT,
            layer=MemoryLayer.PERSONAL,
            channel_scope=ChannelScope.CHAT,
            content="Nom: Ruben Lukusa",
            provenance="profile_migration:identity",
            lifecycle=MemoryLifecycle.CONFIRMED,
        ))

        # Simule 20 tours pour remplir la mémoire CONVERSATION
        for i in range(20):
            raya_lite.memory.write(MemoryEntry(
                type=MemoryType.FACT,
                layer=MemoryLayer.CONVERSATION,
                channel_scope=ChannelScope.CHAT,
                content=f"Message de conversation {i}: texte quelconque pour remplir la mémoire.",
                provenance="test:conv_filler",
                lifecycle=MemoryLifecycle.CONFIRMED,
            ))

        t1 = _send(raya_lite, session, "qui suis-je ?")
        ctx = raya_lite.harness.last_context(session)

        identity_present = False
        if ctx:
            for s in ctx.sections:
                if (s.kind.value == "memory"
                        and s.provenance == "profile_migration:identity"):
                    identity_present = True
                    break

        print(f"\nIdentity baseline présent dans contexte: {identity_present}")
        print(f"Nb sections memory: "
              f"{sum(1 for s in ctx.sections if s.kind.value=='memory') if ctx else 0}")

        assert identity_present, \
            "FAILURE I/A: fait d'identité (profile_migration:identity) absent du contexte malgré 20 tours"
