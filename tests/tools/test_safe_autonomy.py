"""Safe Autonomy + Execution Latency — tests de régression Safety et
vérification des changements structurels (URL dans evidence browser.click,
ObservationSpec current_url, directives modèle hors scope de ce fichier).

Couverture :
1.  browser.click navigation → SAFE (pas de stem dangereux)
2.  browser.click "buy now" → SENSITIVE
3.  browser.click "delete" → SENSITIVE
4.  browser.click "submit order" → SENSITIVE
5.  pc.ui.click sélecteur neutre → SAFE
6.  pc.mouse.click coordonnées pures → SENSITIVE (pas de texte)
7.  pc.keyboard.press combo neutre → SAFE
8.  controller.click() retourne url dans le dict de retour (mock Playwright)
9.  ObservationSpec de browser.click expose current_url
10. Régression — tous les stems dangereux restent SENSITIVE
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from raya.contracts import ObservationSpec, PermissionDecision, PermissionLevel
from raya.safety.risk import classify_risk
from raya.tools.catalog.browser import _LAST_CLICKED_OBSERVATION


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _risk(tags: list[str], args: dict) -> PermissionLevel:
    return classify_risk(tags, args)


def _is_safe(tags: list[str], args: dict) -> bool:
    return _risk(tags, args) == PermissionLevel.SAFE


def _is_sensitive(tags: list[str], args: dict) -> bool:
    return _risk(tags, args) == PermissionLevel.SENSITIVE


# ---------------------------------------------------------------------------
# 1. browser.click navigation SAFE
# ---------------------------------------------------------------------------

class TestBrowserClickSafeNavigation:
    """browser.click avec une cible de navigation neutre → SAFE."""

    def test_library_link(self):
        assert _is_safe(["browser.interact"], {"target": "library link"})

    def test_home_tab(self):
        assert _is_safe(["browser.interact"], {"target": "Home"})

    def test_search_button(self):
        assert _is_safe(["browser.interact"], {"target": "search button"})

    def test_play_button(self):
        assert _is_safe(["browser.interact"], {"target": "play"})

    def test_next_link(self):
        assert _is_safe(["browser.interact"], {"target": "Next"})

    def test_no_confirmation_required(self):
        from raya.safety import AuditTrail, SafetyService, StopController
        svc = SafetyService(StopController(), AuditTrail())
        p = svc.check_permission(
            action_ref="browser.click",
            capability_tags=["browser.interact"],
            arguments={"target": "library link"},
        )
        assert p.decision == PermissionDecision.ALLOWED


# ---------------------------------------------------------------------------
# 2-4. browser.click → SENSITIVE pour stems dangereux
# ---------------------------------------------------------------------------

class TestBrowserClickDangerousSensitive:

    def test_buy_now(self):
        assert _is_sensitive(["browser.interact"], {"target": "buy now"})

    def test_delete(self):
        assert _is_sensitive(["browser.interact"], {"target": "delete"})

    def test_submit_order(self):
        assert _is_sensitive(["browser.interact"], {"target": "submit order"})

    def test_checkout(self):
        assert _is_sensitive(["browser.interact"], {"target": "checkout"})

    def test_confirm_purchase(self):
        assert _is_sensitive(["browser.interact"], {"target": "confirm purchase"})

    def test_send_message(self):
        assert _is_sensitive(["browser.interact"], {"target": "send message"})

    def test_publish(self):
        assert _is_sensitive(["browser.interact"], {"target": "publish"})


# ---------------------------------------------------------------------------
# 5. pc.ui.click sélecteur neutre → SAFE
# ---------------------------------------------------------------------------

class TestPcUiClickSafeSelector:

    def test_library_selector(self):
        assert _is_safe(["pc.interact"], {"window": "Steam", "selector": {"name": "Library"}})

    def test_settings_selector(self):
        assert _is_safe(["pc.interact"], {"window": "App", "selector": {"name": "Settings"}})

    def test_digit_button(self):
        assert _is_safe(["pc.interact"], {"window": "Calculatrice", "selector": {"name": "7"}})

    def test_cancel_button_is_safe(self):
        # "cancel" n'est pas dans _DANGEROUS_ACTION_STEMS — une annulation locale
        # (fermer une boîte de dialogue) n'est pas une action irréversible conséquente.
        assert _is_safe(["pc.interact"], {"window": "App", "selector": {"name": "Cancel"}})


# ---------------------------------------------------------------------------
# 6. pc.mouse.click coordonnées pures → SENSITIVE
# ---------------------------------------------------------------------------

class TestPcMouseClickNoTextSensitive:

    def test_integer_coords_only(self):
        # Aucun texte dans les arguments → repli SENSITIVE du tag pc.interact
        assert _is_sensitive(["pc.interact"], {"x": 100, "y": 200})

    def test_x_y_only(self):
        assert _is_sensitive(["pc.interact"], {"x": 500, "y": 300})


# ---------------------------------------------------------------------------
# 7. pc.keyboard.press combo neutre → SAFE
# ---------------------------------------------------------------------------

class TestPcKeyboardPressSafe:

    def test_ctrl_c(self):
        assert _is_safe(["pc.interact"], {"combo": "ctrl+c"})

    def test_ctrl_v(self):
        assert _is_safe(["pc.interact"], {"combo": "ctrl+v"})

    def test_enter(self):
        assert _is_safe(["pc.interact"], {"combo": "Enter"})

    def test_escape(self):
        assert _is_safe(["pc.interact"], {"combo": "Escape"})

    def test_ctrl_z(self):
        assert _is_safe(["pc.interact"], {"combo": "ctrl+z"})


# ---------------------------------------------------------------------------
# 8. controller.click() retourne url dans le dict de retour
# ---------------------------------------------------------------------------

class TestBrowserControllerClickReturnsUrl:
    """Vérifie que BrowserController.click() expose l'URL courante après clic."""

    def test_click_result_contains_url(self):
        from raya.devices.browser.controller import BrowserController

        mock_session = MagicMock()
        mock_worker = MagicMock()

        fake_page = MagicMock()
        fake_page.url = "https://example.com/library"
        fake_page.frames = []

        mock_locator = MagicMock()
        mock_locator.count.return_value = 1
        mock_locator.is_visible.return_value = True
        fake_page.get_by_role = MagicMock(return_value=MagicMock(first=mock_locator))
        fake_page.get_by_label = MagicMock(return_value=MagicMock(first=MagicMock(count=MagicMock(return_value=0))))
        fake_page.get_by_title = MagicMock(return_value=MagicMock(first=MagicMock(count=MagicMock(return_value=0))))
        fake_page.locator = MagicMock(return_value=MagicMock(first=MagicMock(count=MagicMock(return_value=0))))
        fake_page.get_by_text = MagicMock(return_value=MagicMock(first=MagicMock(count=MagicMock(return_value=0))))

        mock_session.get_or_create = MagicMock(return_value=fake_page)

        def fake_run_sync(fn, timeout=60.0):
            return fn()

        mock_worker.run_sync = fake_run_sync

        ctrl = BrowserController(mock_session, mock_worker)
        result = ctrl.click("Library")

        assert result["status"] == "ok"
        assert "url" in result, "click() doit retourner 'url' dans son dict de résultat"
        assert result["url"] == "https://example.com/library"


# ---------------------------------------------------------------------------
# 9. ObservationSpec de browser.click expose current_url
# ---------------------------------------------------------------------------

class TestBrowserClickObservationIncludesUrl:

    def test_last_clicked_observation_has_two_specs(self):
        assert len(_LAST_CLICKED_OBSERVATION) == 2

    def test_first_spec_is_clicked_target(self):
        spec = _LAST_CLICKED_OBSERVATION[0]
        assert isinstance(spec, ObservationSpec)
        assert spec.domain == "browser"
        assert spec.key == "last_clicked_target"
        assert spec.evidence_field == "clicked_target"

    def test_second_spec_is_current_url(self):
        spec = _LAST_CLICKED_OBSERVATION[1]
        assert isinstance(spec, ObservationSpec)
        assert spec.domain == "browser"
        assert spec.key == "current_url"
        assert spec.evidence_field == "url"

    def test_url_spec_has_no_expected_argument(self):
        # Pas de vérification de conformité — on promeut l'URL observée,
        # le clic ne "demande" pas une URL cible (contrairement à navigate).
        spec = _LAST_CLICKED_OBSERVATION[1]
        assert spec.expected_argument is None

    def test_url_spec_freshness_ttl(self):
        spec = _LAST_CLICKED_OBSERVATION[1]
        assert spec.freshness_ttl_s == 60


# ---------------------------------------------------------------------------
# 10. Régression — tous les stems dangereux restent SENSITIVE
# ---------------------------------------------------------------------------

class TestSafetyRegressionDangerousStems:
    """Vérifie qu'aucun changement n'a accidentellement rendu des actions
    conséquentes SAFE. Couvre browser.interact ET pc.interact."""

    _DANGEROUS_CASES = [
        # Achats
        (["browser.interact"], {"target": "buy"}),
        (["browser.interact"], {"target": "acheter"}),
        (["browser.interact"], {"target": "purchase"}),
        (["browser.interact"], {"target": "checkout"}),
        (["browser.interact"], {"target": "payer"}),
        (["browser.interact"], {"target": "order"}),
        # Suppressions
        (["browser.interact"], {"target": "delete"}),
        (["browser.interact"], {"target": "supprimer"}),
        (["browser.interact"], {"target": "remove"}),
        (["pc.interact"], {"window": "App", "selector": {"name": "Effacer"}}),
        # Envois / publications
        (["browser.interact"], {"target": "send"}),
        (["browser.interact"], {"target": "publish"}),
        (["browser.interact"], {"target": "post"}),
        # Validations conséquentes
        (["browser.interact"], {"target": "confirm"}),
        (["browser.interact"], {"target": "submit"}),
        (["pc.interact"], {"window": "Form", "selector": {"name": "Valider"}}),
        # Transferts
        (["browser.interact"], {"target": "transfer"}),
        (["browser.interact"], {"target": "virement"}),
        # Désinstallation
        (["pc.interact"], {"window": "App", "selector": {"name": "Uninstall"}}),
    ]

    @pytest.mark.parametrize("tags,args", _DANGEROUS_CASES)
    def test_dangerous_stem_is_sensitive(self, tags, args):
        assert _is_sensitive(tags, args), (
            f"RÉGRESSION Safety : tags={tags} args={args} devrait être SENSITIVE"
        )
