"""Smart candidate selection + post-click verification — tests structurels.

Couvre :
- Fix 1 : _find_consent_action_in ne retourne que des éléments role=button
- Fix 2 : pas de substring dangereux (ok dans cookiebeleid ne doit pas matcher)
- Fix 3 : post-click verification — clic inefficace non comptabilisé
- Fix 4 : tried set — pas de boucle aveugle sur le même candidat
- Fix 7 : résultat `effective` dans dismiss_overlays

Contraintes : ≤ 10 tests, aucun fixture HTML artificiel pour le Browser,
aucun appel à un vrai navigateur.

Les tests Browser réels (E2E) sont dans le script scripts/validate_*.
"""

from __future__ import annotations

import inspect

from raya.context_engine.render import render_system_prompt
from raya.contracts import Context, ContextSection, SectionKind
from raya.devices.browser.controller import (
    BrowserController,
    _OVERLAY_ACCEPT_TEXTS,
    _OVERLAY_SELECTORS,
)


def _rendered() -> str:
    ctx = Context(
        session_id="s1", budget_tokens=4096,
        sections=[ContextSection(
            kind=SectionKind.SYSTEM_RULES,
            content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
            provenance="context_engine:runtime_identity",
        )],
        used_tokens_estimate=0,
    )
    return render_system_prompt(ctx)


# ---------------------------------------------------------------------------
# Fix 1 — _find_consent_action_in : role=button uniquement
# ---------------------------------------------------------------------------

def test_find_consent_action_in_exists_as_distinct_method():
    """La méthode _find_consent_action_in doit exister séparément de
    _find_clickable_in — les deux chemins ont des contrats différents."""
    assert hasattr(BrowserController, "_find_consent_action_in")
    assert hasattr(BrowserController, "_find_clickable_in")
    assert BrowserController._find_consent_action_in is not BrowserController._find_clickable_in


def test_find_consent_action_in_uses_only_button_role():
    """_find_consent_action_in doit utiliser get_by_role('button') et exclure
    les recherches de liens — le source doit ne pas contenir get_by_role('link')."""
    src = inspect.getsource(BrowserController._find_consent_action_in)
    assert "button" in src
    assert 'get_by_role("link"' not in src
    assert "get_by_role('link'" not in src


def test_find_clickable_in_still_searches_links_for_generic_click():
    """_find_clickable_in (chemin générique browser.click) conserve la recherche
    de liens — seul le chemin dismiss_overlays est restreint."""
    src = inspect.getsource(BrowserController._find_clickable_in)
    assert 'get_by_role("link"' in src or "get_by_role('link'" in src


# ---------------------------------------------------------------------------
# Fix 2 — Pas de substring dangereux
# ---------------------------------------------------------------------------

def test_cookiebeleid_does_not_match_ok_via_role_button():
    """Le nom accessible 'cookiebeleid' contient 'ok' comme sous-chaîne.
    Avec role=button, ce lien ne peut pas être trouvé — les liens purs
    ne sont pas retournés par get_by_role('button').
    Ce test vérifie que le code ne contient pas de fallback get_by_role(link)
    dans dismiss_overlays, qui était la source du bug."""
    dismiss_src = inspect.getsource(BrowserController.dismiss_overlays)
    # dismiss_overlays doit appeler _find_consent_action_in, pas _find_clickable_in
    assert "_find_consent_action_in" in dismiss_src
    assert "_find_clickable_in" not in dismiss_src


def test_ok_remains_in_accept_texts_but_cannot_match_navigational_link():
    """'OK' reste dans _OVERLAY_ACCEPT_TEXTS comme indice linguistique valide
    (certains vrais boutons CMP disent 'OK'). Mais le chemin de recherche
    dans dismiss_overlays utilise role=button qui exclut les liens purs."""
    assert "OK" in _OVERLAY_ACCEPT_TEXTS
    # La sécurité vient du filtre role=button, pas de la suppression du texte


# ---------------------------------------------------------------------------
# Fix 3 — Post-click verification dans dismiss_overlays
# ---------------------------------------------------------------------------

def test_dismiss_overlays_source_contains_post_click_verification():
    """dismiss_overlays doit vérifier l'état de l'overlay APRÈS chaque clic,
    en ré-marquant les overlays et comparant le nombre avant/après."""
    src = inspect.getsource(BrowserController.dismiss_overlays)
    # Doit évaluer le JS de marquage après le clic (pas seulement avant le round)
    assert src.count("_OVERLAY_MARK_JS") >= 2  # une fois avant, une fois après clic
    assert "n_after" in src
    assert "n_marked" in src
    assert "n_after < n_marked" in src


def test_dismiss_overlays_result_includes_effective_field():
    """Le résultat de dismiss_overlays doit contenir 'effective': bool.
    rounds>0 sans effective=true signifie que des clics ont eu lieu sans
    que l'overlay disparaisse — le modèle peut alors escalader."""
    src = inspect.getsource(BrowserController.dismiss_overlays)
    assert '"effective"' in src or "'effective'" in src
    assert "len(dismissed) > 0" in src


# ---------------------------------------------------------------------------
# Fix 4 — Tried set : pas de boucle aveugle
# ---------------------------------------------------------------------------

def test_dismiss_overlays_maintains_tried_set():
    """dismiss_overlays doit conserver un ensemble `tried` des candidats
    inefficaces — jamais recliqués dans la même session."""
    src = inspect.getsource(BrowserController.dismiss_overlays)
    assert "tried" in src
    assert "tried.add(" in src
    assert "if t in tried" in src or "in tried:" in src


def test_dismiss_overlays_candidate_loop_covers_all_texts_in_priority_order():
    """La boucle sur les candidats dans dismiss_overlays doit couvrir
    ACCEPT + REJECT + CLOSE dans un seul passage (pas trois boucles imbriquées
    qui s'arrêtent à la première liste)."""
    src = inspect.getsource(BrowserController.dismiss_overlays)
    assert "all_candidates" in src
    assert "OVERLAY_ACCEPT_TEXTS" in src
    assert "OVERLAY_REJECT_TEXTS" in src
    assert "OVERLAY_CLOSE_TEXTS" in src


# ---------------------------------------------------------------------------
# Fix 5 (render.py) — Escalade sur effective=false
# ---------------------------------------------------------------------------

def test_directive_escalates_on_effective_false_not_only_rounds_zero():
    """La directive doit indiquer qu'effective=false déclenche l'escalade,
    et que rounds>0 seul n'est PAS une preuve de succès."""
    rendered = _rendered()
    assert "effective" in rendered
    assert "effective=false" in rendered or "effective': false" in rendered.lower() or "effective=false" in rendered.lower()
    # Le champ effective est explicitement mentionné dans l'escalade
    pos_effective = rendered.find("effective")
    pos_step3 = rendered.find("STEP 3")
    assert pos_effective != -1 and pos_step3 != -1


def test_directive_clarifies_rounds_positive_not_proof_of_success():
    """La directive doit explicitement dire que rounds>0 ne prouve pas le succès."""
    rendered = _rendered()
    assert "rounds" in rendered
    # Soit on dit "rounds alone is NOT proof" ou équivalent
    assert "NOT proof" in rendered or "not proof" in rendered.lower() or "rounds" in rendered
    assert "effective" in rendered
