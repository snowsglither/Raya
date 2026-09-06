"""Preuve architecturale — Stabilisation Phase 11 (§16 : liste explicite de
patterns interdits ; addenda Telegram outbound / Browser Robustness : jamais
de second agent par domaine, jamais de hardcoding par site/langue)."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import arch_lint  # noqa: E402

_FORBIDDEN_CLASSES = (
    "ApplicationAgent", "NetflixAgent", "ContextAgent", "BrowserOrchestrator",
    "ConfirmationManager", "TelegramToolFormatter", "CookieAgent", "LanguageAgent",
    "OverlayAgent", "AmazonAgent",
)


def test_no_forbidden_second_brain_or_per_domain_agent_class_anywhere():
    for path in arch_lint.RAYA_ROOT.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        for forbidden in _FORBIDDEN_CLASSES:
            assert f"class {forbidden}" not in source, f"{path} définit {forbidden}"


def test_no_per_interface_tool_result_formatter():
    """§4/§11 : "les interfaces ne doivent pas avoir chacune leur propre
    formatter de ToolResult" — `_natural_response_for_tool_result` ne vit
    QUE dans le Harness, jamais réimplémenté par une interface."""
    hits = [
        p for p in arch_lint.RAYA_ROOT.rglob("*.py")
        if "def _natural_response_for_tool_result" in p.read_text(encoding="utf-8")
        and p.parent.name != "harness"
    ]
    assert hits == []
    # Vérification directe : le canal Telegram ne reformate jamais lui-même
    # un ToolResult — il lit uniquement `harness.response_text()`.
    telegram_source = (arch_lint.RAYA_ROOT / "interfaces" / "telegram" / "channel.py").read_text(encoding="utf-8")
    assert "_summarize_tool_result" not in telegram_source
    assert "ToolResultStatus" not in telegram_source


def test_ui_view_tools_declare_no_forbidden_class_and_stay_presentational():
    """Non-régression architecturale minimale : ui_views.py ne définit aucun
    orchestrateur, seulement des handlers d'events présentationnels."""
    source = (arch_lint.RAYA_ROOT / "tools" / "catalog" / "ui_views.py").read_text(encoding="utf-8")
    for forbidden in _FORBIDDEN_CLASSES:
        assert f"class {forbidden}" not in source


def test_notify_catalog_never_imports_interfaces_telegram_directly():
    """Addendum Telegram outbound : `tools/` ne peut pas importer
    `raya.interfaces.telegram` (subsystem strictement au-dessus) — seul un
    callable étroit (`NotifyOps.send_telegram`) est injecté par le
    composition root (web.py)."""
    path = arch_lint.RAYA_ROOT / "tools" / "catalog" / "notify.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    violations = arch_lint.check_dependency_graph(path, tree, "tools")
    assert violations == [], f"{path}: {violations}"
    for module, _names in arch_lint._raya_imports(tree):
        assert not module.startswith("raya.interfaces"), f"{path} importe raya.interfaces"
        assert not module.startswith("raya.harness"), f"{path} importe raya.harness"


def test_telegram_send_message_tool_registered_only_when_telegram_actually_starts():
    """Interdiction explicite : "NE PAS créer un bypass directement dans
    l'interface Telegram" — le Tool est enregistré depuis le composition
    root (web.py), jamais depuis `interfaces/telegram/` lui-même."""
    web_source = (arch_lint.RAYA_ROOT / "runtime" / "entrypoints" / "web.py").read_text(encoding="utf-8")
    assert "register_notify_tools" in web_source
    telegram_dir = arch_lint.RAYA_ROOT / "interfaces" / "telegram"
    for path in telegram_dir.rglob("*.py"):
        assert "register_notify_tools" not in path.read_text(encoding="utf-8")


_SITE_LITERALS = ("amazon", "netflix", "youtube", "gmail")
_SITE_CONDITIONAL_RE = re.compile(
    r'if\s+.*["\'](?:' + "|".join(_SITE_LITERALS) + r')["\']', re.IGNORECASE,
)


def test_no_hardcoded_per_site_conditional_branch_in_browser_or_safety_code():
    """§16/addendum Browser Robustness : jamais `if site == "amazon"` (ou
    équivalent) dans le code de décision navigateur/safety — la
    classification/le comportement doivent rester fondés sur le CONTENU de
    l'action, jamais sur un nom de site en dur. Heuristique textuelle
    volontairement scopée à un `if` littéral sur un nom de site connu (une
    mention en commentaire/docstring, ex: exemples pédagogiques dans
    risk.py, n'est jamais précédée de `if`)."""
    for path in (
        arch_lint.RAYA_ROOT / "safety" / "risk.py",
        arch_lint.RAYA_ROOT / "tools" / "catalog" / "browser.py",
        arch_lint.RAYA_ROOT / "devices" / "browser" / "controller.py",
        arch_lint.RAYA_ROOT / "devices" / "browser" / "agent.py",
    ):
        source = path.read_text(encoding="utf-8")
        assert not _SITE_CONDITIONAL_RE.search(source), f"{path} contient un branchement par nom de site"


def test_no_hardcoded_language_conditional_branch_in_browser_code():
    """Addendum Multilingual Browser Navigation : jamais `if language ==
    "fr"` (ou équivalent) dans le Browser Device Agent — la navigation doit
    rester indépendante de la langue via structure/DOM/vision, jamais une
    branche par langue."""
    lang_re = re.compile(r'if\s+.*language\s*==\s*["\'](fr|en|nl|de|ja)["\']', re.IGNORECASE)
    for path in (
        arch_lint.RAYA_ROOT / "devices" / "browser" / "controller.py",
        arch_lint.RAYA_ROOT / "devices" / "browser" / "agent.py",
        arch_lint.RAYA_ROOT / "tools" / "catalog" / "browser.py",
    ):
        source = path.read_text(encoding="utf-8")
        assert not lang_re.search(source), f"{path} contient un branchement par langue en dur"


def test_full_arch_lint_zero_violations():
    assert arch_lint.run() == []
