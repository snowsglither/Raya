"""cognition/intent.py (Chantier 12 §D) — dérivation structurelle
information vs action, jamais un classifieur de texte."""

from __future__ import annotations

from raya.cognition import Intent, derive_intent


def test_no_tool_called_is_information():
    assert derive_intent([]) == Intent.INFORMATION


def test_only_read_tags_is_information():
    assert derive_intent(["pc.read"]) == Intent.INFORMATION
    assert derive_intent(["pc.read", "browser.read"]) == Intent.INFORMATION


def test_any_non_read_tag_is_action():
    assert derive_intent(["pc.launch"]) == Intent.ACTION
    assert derive_intent(["pc.read", "pc.interact"]) == Intent.ACTION


def test_new_read_tag_convention_generalizes_without_hardcoding():
    """Un futur tag '*.read' jamais listé explicitement ici doit quand même
    être reconnu comme lecture pure — preuve qu'aucune liste de tags codée
    en dur n'existe dans derive_intent()."""
    assert derive_intent(["documents.read"]) == Intent.INFORMATION
    assert derive_intent(["some_future_domain.read"]) == Intent.INFORMATION
