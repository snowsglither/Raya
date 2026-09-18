"""Cookie consent intelligent — tests structurels (Fixes A-I).

Aucun test ici ne lance un vrai navigateur : on vérifie les
sélecteurs/textes déclarés dans controller.py et les directives
injectées dans le system prompt via render.py.

Limite volontaire : ≤12 tests, zéro site-specific logic, zéro
manager/handler class introduit par ces fixes."""

from __future__ import annotations

import re

from raya.context_engine.render import render_system_prompt
from raya.contracts import Context, ContextSection, SectionKind
from raya.devices.browser.controller import (
    _OVERLAY_ACCEPT_TEXTS,
    _OVERLAY_SELECTORS,
    _STRUCT_JS,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _system_rules_ctx() -> Context:
    return Context(
        session_id="s1",
        budget_tokens=4096,
        sections=[ContextSection(
            kind=SectionKind.SYSTEM_RULES,
            content={"assistant_identity": {"name": "RAYA"}, "runtime": {"provider": None, "model": None}},
            provenance="context_engine:runtime_identity",
        )],
        used_tokens_estimate=0,
    )


def _rendered() -> str:
    return render_system_prompt(_system_rules_ctx())


# ---------------------------------------------------------------------------
# Fix B — sélecteur GDPR (substring i vs token strict)
# ---------------------------------------------------------------------------

def test_overlay_selector_uses_gdpr_substring_not_token():
    """[class*=gdpr i] doit être présent — il correspond à 'gdpr-lmd-wall'.
    Le sélecteur token strict '.gdpr' ne doit plus exister seul."""
    assert "[class*=gdpr i]" in _OVERLAY_SELECTORS
    # .gdpr token-only matcherait uniquement class="gdpr" exact — absent de la liste
    assert ".gdpr" not in _OVERLAY_SELECTORS


def test_overlay_selector_gdpr_case_insensitive_flag():
    """Le flag 'i' garantit que GDPR/Gdpr/gdpr sont tous capturés."""
    matching = [s for s in _OVERLAY_SELECTORS if "gdpr" in s.lower()]
    assert len(matching) >= 1
    for sel in matching:
        assert "i]" in sel, f"sélecteur gdpr sans flag insensible à la casse : {sel}"


# ---------------------------------------------------------------------------
# Fix C — textes d'acceptation multilingues
# ---------------------------------------------------------------------------

def test_accept_texts_include_dutch():
    lower = {t.lower() for t in _OVERLAY_ACCEPT_TEXTS}
    assert "alle accepteren" in lower
    assert "akkoord" in lower


def test_accept_texts_include_german():
    lower = {t.lower() for t in _OVERLAY_ACCEPT_TEXTS}
    assert "alles akzeptieren" in lower
    assert "zustimmen" in lower


def test_accept_texts_include_spanish_and_italian():
    lower = {t.lower() for t in _OVERLAY_ACCEPT_TEXTS}
    assert "aceptar todo" in lower or "aceptar" in lower   # ES
    assert "accetta tutto" in lower or "accetto" in lower  # IT


# ---------------------------------------------------------------------------
# Fix A — _STRUCT_JS : détection cookie_banner contextuelle
# ---------------------------------------------------------------------------

def test_struct_js_uses_specific_cookie_selectors_for_high_confidence():
    """Les sélecteurs haute-confiance doivent être dans _STRUCT_JS directement
    (pas seulement dans _OVERLAY_SELECTORS)."""
    assert "[id*=cookie i]" in _STRUCT_JS
    assert "[class*=consent i]" in _STRUCT_JS
    assert "[class*=gdpr i]" in _STRUCT_JS


def test_struct_js_guards_generic_dialogs_with_consent_keywords():
    """Les overlays génériques (role=dialog etc.) ne doivent déclencher
    cookie_banner que si l'élément contient du texte de consentement — vérifié
    par la présence du tableau de mots-clés dans le JS."""
    assert "cookie" in _STRUCT_JS
    assert "rgpd" in _STRUCT_JS
    assert "données personnelles" in _STRUCT_JS
    # Le guard doit être conditionnel : on cherche le pattern "if (!cookieBanner)"
    assert "if (!cookieBanner)" in _STRUCT_JS or "!cookieBanner" in _STRUCT_JS


# ---------------------------------------------------------------------------
# Fixes D/E/F/G/H/I — directives render.py
# ---------------------------------------------------------------------------

def test_directive_requires_multiple_signals_not_single_button_word():
    """Fix D : la directive doit explicitement interdire la logique
    'Accept seul suffit' et exiger plusieurs signaux convergents."""
    rendered = _rendered()
    assert "MULTIPLE convergent signals" in rendered or "multiple" in rendered.lower()
    # La directive doit citer au moins un exemple de faux positif
    assert "Accept invitation" in rendered or "false positive" in rendered.lower()


def test_directive_escalation_mentions_dismiss_overlay_then_click_then_vision():
    """Fix E+F : la directive doit ordonner dismiss_overlay AVANT click
    AVANT vision — jamais en parallèle."""
    rendered = _rendered()
    pos_dismiss = rendered.find("dismiss_overlay")
    pos_click_direct = rendered.find("browser.click(target)")
    pos_vision = rendered.find("vision.find_in_browser")
    assert pos_dismiss != -1 and pos_click_direct != -1 and pos_vision != -1
    assert pos_dismiss < pos_click_direct < pos_vision, (
        "Ordre d'escalade incorrect : dismiss_overlay doit précéder browser.click "
        "qui doit précéder vision.find_in_browser"
    )


def test_directive_post_action_verification_uses_button_absence_not_flag():
    """Fix H : la vérification post-action ne doit pas dépendre uniquement
    de cookie_banner=false — elle doit tester l'ABSENCE du bouton."""
    rendered = _rendered()
    # La directive doit parler d'absence du bouton dans buttons[]
    assert "ABSENT" in rendered or "absent" in rendered.lower()
    # Elle doit mettre en garde contre cookie_banner=false comme seul signal
    assert "cookie_banner" in rendered
    assert "do NOT rely" in rendered or "not rely" in rendered.lower()


def test_directive_resume_objective_not_restart():
    """Fix I : la directive doit explicitement ordonner de reprendre
    l'objectif depuis le point d'interruption, jamais de tout relancer."""
    rendered = _rendered()
    assert "resume" in rendered.lower()
    assert "interrupted" in rendered.lower() or "interruption" in rendered.lower()
    assert "Do NOT restart" in rendered or "do not restart" in rendered.lower()


def test_no_site_specific_logic_in_browser_module():
    """Ni CookieManager/ConsentManager/SiteHandler/AmazonCookieHandler
    ni aucune classe dédiée par site ne doit exister dans devices/browser/."""
    import importlib
    import raya.devices.browser as browser_pkg

    banned = re.compile(
        r"(CookieManager|ConsentManager|SiteHandler|AmazonCookie|"
        r"LemondeCookie|SpecificSite)",
        re.IGNORECASE,
    )
    # Vérifie que les noms publics du package ne correspondent pas
    public_names = dir(browser_pkg)
    for name in public_names:
        assert not banned.search(name), (
            f"Classe/objet site-spécifique interdit trouvé dans browser pkg : {name}"
        )
