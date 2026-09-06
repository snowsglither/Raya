"""Chantier 17 (Cockpit chat natif + orb presence) — vérifications
structurelles du frontend statique. Même style que
test_ui_cockpit_static_files_do_not_load_threejs_unconditionally (Phase 8) :
assertions textuelles directes sur les fichiers réels, aucun framework de
test JS disponible dans ce repo Python-only.

Ne re-teste pas ce qui existait déjà et reste inchangé (mécanisme
openPanel/closePanel générique, WebSocket, panneaux T/World/Browser/
Computer/Attention/Spatial) — uniquement le nouveau comportement : mode
conversation, disparition du raccourci C, timeout d'inactivité purement
visuel."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import arch_lint  # noqa: E402

_UI_STATIC = arch_lint.RAYA_ROOT / "interfaces" / "ui" / "static"


def _app_js() -> str:
    return (_UI_STATIC / "app.js").read_text(encoding="utf-8")


def _index_html() -> str:
    return (_UI_STATIC / "index.html").read_text(encoding="utf-8")


def _styles_css() -> str:
    return (_UI_STATIC / "styles.css").read_text(encoding="utf-8")


# --- "C" ne force plus l'ouverture de la conversation (A6) ---

def test_c_shortcut_no_longer_opens_conversation():
    app_js = _app_js()
    keydown_block = app_js.split('document.addEventListener("keydown"')[-1]
    assert 'e.key.toLowerCase() === "c"' not in keydown_block


def test_hint_row_no_longer_mentions_c_shortcut():
    assert "<kbd>C</kbd>" not in _index_html()


def test_t_and_w_and_escape_shortcuts_still_work():
    app_js = _app_js()
    keydown_block = app_js.split('document.addEventListener("keydown"')[-1]
    assert 'e.key.toLowerCase() === "t"' in keydown_block
    assert 'e.key.toLowerCase() === "w"' in keydown_block
    assert "closeAllPanels()" in keydown_block


# --- Conversation mode entré par une activité réelle, jamais par du texte deviné (A2/A11) ---

def test_send_message_enters_conversation_mode():
    app_js = _app_js()
    send_message_fn = app_js.split("async function sendMessage(")[1].split("\n  }\n")[0]
    assert "enterConversationMode()" in send_message_fn


def test_model_driven_conversation_view_also_enters_conversation_mode():
    """ui.show_view("conversation") (déclenché par le modèle, jamais par du
    texte deviné côté frontend) doit utiliser le MÊME mécanisme d'entrée."""
    app_js = _app_js()
    open_view_fn = app_js.split("function openViewByName(view)")[1].split("\n  }\n")[0]
    assert 'enterConversationMode()' in open_view_fn.split("\n")[1]


def test_background_task_events_never_enter_conversation_mode():
    """A9 : une tâche de fond ne doit jamais forcer l'ouverture du chat."""
    app_js = _app_js()
    handler = app_js.split("function handleNotification(msg)")[1].split("\n  }\n")[0]
    task_cases_start = handler.index('case "task.started"')
    task_cases_block = handler[task_cases_start:handler.index("break;", task_cases_start) + 6]
    assert "enterConversationMode" not in task_cases_block


# --- Timeout d'inactivité purement visuel (A7) ---

def test_inactivity_timeout_is_approximately_60_seconds():
    assert "CONVERSATION_IDLE_TIMEOUT_MS = 60000" in _app_js()


def test_inactivity_timeout_only_closes_the_panel_never_touches_backend_state():
    """Le minuteur ne doit JAMAIS appeler l'API backend (session/task/memory)
    — purement une fermeture de panneau visuelle (consigne : "aucune donnée
    n'est supprimée")."""
    app_js = _app_js()
    reset_fn = app_js.split("function resetInactivityTimer()")[1].split("\n  }\n")[0]
    assert 'closePanel("conversation")' in reset_fn
    assert "api(" not in reset_fn


# --- Layout : orb flottant + chat plein écran, jamais une colonne fixe (A2/A3) ---

def test_css_shrinks_and_repositions_orb_in_conversation_mode():
    css = _styles_css()
    assert "#app.conversation-mode .orb-wrap" in css
    block = css.split("#app.conversation-mode .orb-wrap")[1].split("}")[0]
    assert "position: fixed" in block


def test_css_makes_conversation_panel_full_bleed_not_a_side_card():
    css = _styles_css()
    assert "#app.conversation-mode #panel-conversation" in css
    block = css.split("#app.conversation-mode #panel-conversation {")[1].split("}")[0]
    assert "inset: 0" in block


def test_no_two_column_grid_layout_introduced():
    """Consigne : jamais RAYA gauche | CHAT droite en deux colonnes fixes."""
    css = _styles_css()
    assert "grid-template-columns" not in css
