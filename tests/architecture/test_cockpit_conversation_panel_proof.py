"""Preuve — panneau Conversation du Cockpit CONTEXTUEL, pas auto-ouvert
(fix UX post-Phase 6). Aucun runner JS dans ce dépôt (RAYA_V2_REPOSITORY_
STRUCTURE.md, Phase 6-10) — même précédent que
`test_phase8_architecture_proof.py::test_ui_cockpit_has_a_spatial_panel_...`
: assertions textuelles/structurelles ciblées sur `app.js`/`index.html`,
complétées par le vrai test Cockpit en conditions réelles (voir rapport)."""

from __future__ import annotations

import re
from pathlib import Path

_UI_STATIC = Path(__file__).resolve().parents[2] / "raya" / "interfaces" / "ui" / "static"


def _app_js() -> str:
    return (_UI_STATIC / "app.js").read_text(encoding="utf-8")


def _extract_function(source: str, name: str) -> str:
    """Extrait le corps texte d'une fonction nommée `function name(...) { ... }`
    en comptant les accolades — suffisant pour ce fichier (pas de littéraux
    à accolades non appariées dans ces fonctions)."""
    match = re.search(rf"function {re.escape(name)}\s*\([^)]*\)\s*{{", source)
    assert match, f"fonction {name} introuvable dans app.js"
    start = match.end() - 1
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"accolade fermante jamais trouvée pour {name}")


def test_send_message_never_forces_the_conversation_panel_open():
    """Cause exacte du bug : `sendMessage()` appelait `openPanel("conversation")`
    inconditionnellement, avant même la réponse — supprimé."""
    body = _extract_function(_app_js(), "sendMessage")
    assert 'openPanel("conversation")' not in body
    assert "renderConversation(view)" in body  # le contenu reste à jour, juste jamais forcé visible


def test_websocket_sync_never_forces_the_conversation_panel_open():
    """Deuxième cause trouvée à l'audit : la resynchronisation WS (reconnect/
    refresh) ouvrait aussi Conversation si un historique existait — supprimé,
    même règle que sendMessage (une resync n'est pas une intention d'ouverture)."""
    source = _app_js()
    sync_case = source[source.index('case "sync":') : source.index('case "harness.confirmation_required"')]
    assert 'openPanel("conversation")' not in sync_case
    assert "renderConversation(msg.payload.conversation)" in sync_case  # contenu toujours synchronisé


def test_conversation_has_an_explicit_user_facing_open_mechanism():
    """Consigne : le panneau doit rester ouvrable explicitement (raccourci/
    bouton/vue demandée). Après le fix, le raccourci clavier "C" est le seul
    déclencheur UTILISATEUR direct restant (en plus du mécanisme modèle
    ui.show_view, inchangé, voir test suivant)."""
    source = _app_js()
    assert re.search(r'e\.key\.toLowerCase\(\)\s*===\s*"c"[^}]*openPanel\("conversation"\)', source)


def test_model_driven_ui_show_view_still_opens_conversation_explicitly():
    """Le mécanisme EXISTANT (Phase 6, ui.show_view -> ui.view_requested ->
    openViewByName) reste intact — c'est la voie "le modèle demande
    explicitement" que la consigne exige de préserver."""
    body = _extract_function(_app_js(), "openViewByName")
    assert 'view === "conversation"' in body
    assert 'openPanel("conversation")' in body
    assert "refreshConversation()" in body


def test_other_contextual_views_are_not_broken_by_the_fix():
    """Non-régression explicite : Tasks/World gardent leur raccourci, et
    toutes les vues restent gérées par openViewByName (browser/computer/
    attention/spatial inclus) — le fix reste scopé à Conversation."""
    source = _app_js()
    assert 'e.key.toLowerCase() === "t") { openPanel("tasks")' in source
    assert 'e.key.toLowerCase() === "w") { openPanel("world")' in source
    body = _extract_function(source, "openViewByName")
    for view in ("tasks", "world", "browser", "computer", "attention", "spatial"):
        assert f'view === "{view}"' in body, f"vue {view} manquante dans openViewByName"


def test_close_panel_mechanism_is_unchanged():
    """Le panneau n'est pas supprimé — fermeture explicite (bouton X /
    data-close-panel, Escape) reste fonctionnelle."""
    source = _app_js()
    assert "closePanel(btn.dataset.closePanel)" in source
    assert 'if (e.key === "Escape") { closeAllPanels(); return; }' in source


def test_index_html_still_declares_the_conversation_panel():
    """Consigne explicite : "Ne supprime PAS le panneau Conversation"."""
    html = (_UI_STATIC / "index.html").read_text(encoding="utf-8")
    assert 'id="panel-conversation"' in html
    assert 'data-close-panel="conversation"' in html


def test_hint_row_advertises_the_new_conversation_shortcut():
    html = (_UI_STATIC / "index.html").read_text(encoding="utf-8")
    assert "<kbd>C</kbd>" in html
