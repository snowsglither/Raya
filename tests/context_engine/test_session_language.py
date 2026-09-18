"""Session language detection — tests for assembler._detect_session_language()
and render.py language fact positioning.

Verifies:
- French user messages → "fr" detected
- Chinese messages → "zh" detected
- Short messages inherit from accumulated history
- Explicit language fact appears early in the system prompt
- Language not set when no history exists
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from raya.context_engine.assembler import _detect_session_language
from raya.context_engine.render import render_system_prompt
from raya.contracts import (
    ChannelScope,
    Context,
    ContextSection,
    MemoryLayer,
    SectionKind,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _memory_with_messages(texts: list[str], is_assistant: bool = False) -> MagicMock:
    entries = []
    for text in texts:
        e = MagicMock()
        e.content = text
        e.layer = MemoryLayer.CONVERSATION
        e.provenance = "harness:assistant" if is_assistant else "harness:user"
        entries.append(e)
    store = MagicMock()
    store.search.return_value = entries
    return store


def _scope() -> ChannelScope:
    cs = MagicMock(spec=ChannelScope)
    return cs


def _ctx_with_language(lang: str | None) -> Context:
    content: dict = {
        "assistant_identity": {"name": "RAYA"},
        "runtime": {"provider": None, "model": None},
    }
    if lang:
        content["session_language"] = lang
    return Context(
        session_id="s1", budget_tokens=4096,
        sections=[ContextSection(
            kind=SectionKind.SYSTEM_RULES,
            content=content,
            provenance="test",
        )],
        used_tokens_estimate=0,
    )


# ---------------------------------------------------------------------------
# 7. French session detected from user messages
# ---------------------------------------------------------------------------

def test_french_messages_detected_as_fr():
    memory = _memory_with_messages([
        "Bonjour, quelle est la batterie de mon laptop ?",
        "Je veux ouvrir le dossier projets.",
        "Est-ce que tu peux me dire l'heure ?",
    ])
    lang = _detect_session_language(memory, _scope())
    assert lang == "fr"


def test_french_accented_chars_detected():
    memory = _memory_with_messages([
        "Où est le fichier téléchargé ?",
        "Ça marche très bien, merci.",
        "Il faut vérifier l'état de la connexion.",
    ])
    lang = _detect_session_language(memory, _scope())
    assert lang == "fr"


# ---------------------------------------------------------------------------
# 8. Short message inherits from accumulated context
# ---------------------------------------------------------------------------

def test_short_message_alone_returns_none():
    """A 2-word message is insufficient to detect language on its own."""
    memory = _memory_with_messages(["Sur le laptop ?"])
    lang = _detect_session_language(memory, _scope())
    # Too short (3 words) — should return None or fr (default); not "zh"
    assert lang != "zh"


def test_short_message_with_prior_french_context():
    """When combined with prior French messages, short message stays fr."""
    memory = _memory_with_messages([
        "Bonjour, quelle est la batterie ?",
        "Je veux voir mes fichiers.",
        "Sur le laptop ?",
    ])
    lang = _detect_session_language(memory, _scope())
    assert lang == "fr"


# ---------------------------------------------------------------------------
# 9. Chinese messages detected
# ---------------------------------------------------------------------------

def test_chinese_messages_detected_as_zh():
    memory = _memory_with_messages([
        "你好，请帮我检查电池电量。",
        "我想打开文件夹。",
        "现在是几点？",
    ])
    lang = _detect_session_language(memory, _scope())
    assert lang == "zh"


# ---------------------------------------------------------------------------
# 10. Language fact appears high in the rendered system prompt
# ---------------------------------------------------------------------------

def test_language_fact_rendered_in_prompt():
    ctx = _ctx_with_language("fr")
    rendered = render_system_prompt(ctx)
    assert "User language: fr" in rendered


def test_language_fact_appears_before_identity():
    ctx = _ctx_with_language("fr")
    rendered = render_system_prompt(ctx)
    lang_pos = rendered.find("User language: fr")
    identity_pos = rendered.find("You are RAYA")
    assert lang_pos != -1 and identity_pos != -1
    assert lang_pos < identity_pos, "Language constraint must appear before identity line"


def test_language_fact_appears_in_first_300_chars():
    ctx = _ctx_with_language("fr")
    rendered = render_system_prompt(ctx)
    lang_pos = rendered.find("User language:")
    assert lang_pos != -1
    assert lang_pos < 300, f"Language constraint too late in prompt (pos={lang_pos})"


def test_no_language_fact_when_not_detected():
    ctx = _ctx_with_language(None)
    rendered = render_system_prompt(ctx)
    assert "User language:" not in rendered


def test_language_instruction_says_exclusively():
    ctx = _ctx_with_language("fr")
    rendered = render_system_prompt(ctx)
    assert "exclusively" in rendered.lower() or "Respond exclusively" in rendered
