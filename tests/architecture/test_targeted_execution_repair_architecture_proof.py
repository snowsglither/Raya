"""Preuve architecturale — passe 'Targeted Execution Repair' (Parties 15-16 :
aucun nouvel agent, aucun hardcoding de site, réutilisation stricte du
pipeline existant)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import arch_lint  # noqa: E402

_FORBIDDEN_CLASSES = (
    "BrowserAgentV2", "NavigationAgent", "SearchAgent", "SafetyAgent",
    "ExecutionOrchestrator", "TaskOrchestrator",
)


def test_no_forbidden_new_agent_class_anywhere():
    for path in arch_lint.RAYA_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for forbidden in _FORBIDDEN_CLASSES:
            assert f"class {forbidden}" not in source, f"{path} définit {forbidden}"


def test_explain_blocked_turn_lives_only_in_harness():
    """Un seul endroit architectural produit l'explication détaillée d'un
    blocage — jamais dupliqué ailleurs (même principe que
    `_natural_response_for_tool_result`, Phase 11)."""
    hits = [
        p for p in arch_lint.RAYA_ROOT.rglob("*.py")
        if "def _explain_blocked_turn" in p.read_text(encoding="utf-8") and p.parent.name != "harness"
    ]
    assert hits == []


def test_explain_blocked_turn_reuses_model_route_never_a_second_call_mechanism():
    source = (arch_lint.RAYA_ROOT / "harness" / "loop.py").read_text(encoding="utf-8")
    assert source.count("def model_route") == 0  # jamais redéfini localement
    assert "model_route(self._model_registry" in source


def test_nudge_mechanism_never_introduces_a_second_loop_detector():
    """Le nudge anti-répétition (Partie 6) reste un compteur local à
    `_run_agentic_loop`, jamais une nouvelle classe de détection — LoopDetector
    et detect_repeating_cycle/detect_no_progress restent les seuls mécanismes
    de détection nommés."""
    source = (arch_lint.RAYA_ROOT / "harness" / "loop.py").read_text(encoding="utf-8")
    assert source.count("class LoopDetector") == 0
    assert "consecutive_failures" in source  # le compteur reste une variable locale


def test_no_search_tool_or_new_browser_capability_was_added():
    """La navigation adaptative (Parties 2-3) est résolue par une directive de
    prompt système, PAS par un nouveau Tool/capacité navigateur — le
    catalogue browser.py reste inchangé dans son nombre de capacités."""
    source = (arch_lint.RAYA_ROOT / "tools" / "catalog" / "browser.py").read_text(encoding="utf-8")
    assert "browser.search" not in source
    assert source.count('("browser.') == 7  # navigate/read_page/screenshot/list_tabs/click/type/dismiss_overlay


_SITE_LITERALS = ("amazon", "netflix", "youtube", "coolblue", "disney")
_SITE_CONDITIONAL_RE = re.compile(
    r'if\s+.*["\'](?:' + "|".join(_SITE_LITERALS) + r')["\']', re.IGNORECASE,
)


def test_no_hardcoded_per_site_conditional_in_render_or_browser_or_safety():
    for path in (
        arch_lint.RAYA_ROOT / "context_engine" / "render.py",
        arch_lint.RAYA_ROOT / "safety" / "risk.py",
        arch_lint.RAYA_ROOT / "tools" / "catalog" / "browser.py",
        arch_lint.RAYA_ROOT / "devices" / "browser" / "controller.py",
        arch_lint.RAYA_ROOT / "devices" / "browser" / "agent.py",
        arch_lint.RAYA_ROOT / "harness" / "loop.py",
    ):
        source = path.read_text(encoding="utf-8")
        assert not _SITE_CONDITIONAL_RE.search(source), f"{path} contient un branchement par nom de site"


def test_pc_interact_contextual_classification_never_named_by_tool():
    """§7 : la classification étendue à pc.interact reste fondée sur le
    CONTENU (mots-clés), jamais un `if tool_name == "pc.ui.click"`."""
    source = (arch_lint.RAYA_ROOT / "safety" / "risk.py").read_text(encoding="utf-8")
    assert 'tool_name ==' not in source
    assert '== "pc.ui.click"' not in source


def test_full_arch_lint_zero_violations():
    assert arch_lint.run() == []
