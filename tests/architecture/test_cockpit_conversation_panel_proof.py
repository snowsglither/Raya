"""Preuve — panneau Conversation du Cockpit (Phase 6/11, layout inversé par
Chantier 17). Aucun runner JS dans ce dépôt (RAYA_V2_REPOSITORY_STRUCTURE.md,
Phase 6-10) — même précédent que `test_phase8_architecture_proof.py::
test_ui_cockpit_has_a_spatial_panel_...` : assertions textuelles/
structurelles ciblées sur `app.js`/`index.html`, complétées par le vrai
test Cockpit en conditions réelles (voir rapport).

Chantier 17 INVERSE délibérément la règle Phase 6/11 ("un échange normal
n'ouvre jamais Conversation, seul le raccourci C ou le modèle le peuvent") :
le chat occupe désormais le Cockpit dès qu'une conversation RÉELLE a lieu
(sendMessage), avec l'orbe comme présence flottante — voir
tests/architecture/test_chantier17_cockpit_layout.py pour les preuves du
nouveau comportement. Ce fichier garde uniquement ce qui reste vrai
(fermeture, vues contextuelles T/World/etc., panneau non supprimé) et met
à jour ce qui a changé de sens."""

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


def test_send_message_now_deliberately_enters_conversation_mode():
    """Chantier 17 : inversion délibérée de la règle Phase 6/11 ci-dessus —
    un échange réel EST désormais l'intention d'occuper le Cockpit avec le
    chat (voir test_chantier17_cockpit_layout.py pour la preuve complète du
    nouveau mécanisme `enterConversationMode()`)."""
    body = _extract_function(_app_js(), "sendMessage")
    assert "enterConversationMode()" in body
    assert "renderConversation(view)" in body


def test_websocket_sync_never_forces_the_conversation_panel_open():
    """Deuxième cause trouvée à l'audit : la resynchronisation WS (reconnect/
    refresh) ouvrait aussi Conversation si un historique existait — supprimé,
    même règle que sendMessage (une resync n'est pas une intention d'ouverture)."""
    source = _app_js()
    sync_case = source[source.index('case "sync":') : source.index('case "harness.confirmation_required"')]
    assert 'openPanel("conversation")' not in sync_case
    assert "renderConversation(msg.payload.conversation)" in sync_case  # contenu toujours synchronisé


def test_c_shortcut_no_longer_opens_conversation():
    """Chantier 17 §A6 : "C" n'a plus cette fonction — la seule façon
    d'entrer en conversation reste l'usage réel (sendMessage) ou le modèle
    (ui.show_view, voir test suivant). Inspecté avant suppression (app.js) :
    ouvrir/rafraîchir Conversation était son SEUL comportement."""
    source = _app_js()
    keydown_block = source.split('document.addEventListener("keydown"')[-1]
    assert 'e.key.toLowerCase() === "c"' not in keydown_block


def test_model_driven_ui_show_view_still_opens_conversation_explicitly():
    """Le mécanisme EXISTANT (Phase 6, ui.show_view -> ui.view_requested ->
    openViewByName) reste intact — c'est la voie "le modèle demande
    explicitement" que la consigne exige de préserver. Route désormais vers
    `enterConversationMode()` (qui appelle openPanel("conversation") en
    interne) au lieu d'un appel direct, pour rester cohérent avec
    sendMessage (Chantier 17)."""
    body = _extract_function(_app_js(), "openViewByName")
    assert 'view === "conversation"' in body
    assert "enterConversationMode()" in body
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


def test_hint_row_no_longer_advertises_a_conversation_shortcut():
    """Chantier 17 : plus de raccourci dédié à ouvrir Conversation, donc
    plus de hint associé — voir test_chantier17_cockpit_layout.py pour la
    preuve complète (kbd C entièrement retiré de index.html)."""
    html = (_UI_STATIC / "index.html").read_text(encoding="utf-8")
    assert "<kbd>C</kbd>" not in html
