"""chunk_message (RAYA V2 Phase 9, consigne §14) — découpage Telegram-safe."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raya.interfaces.telegram.chunker import TELEGRAM_MESSAGE_LIMIT, chunk_message  # noqa: E402


def test_short_text_is_a_single_chunk():
    assert chunk_message("bonjour") == ["bonjour"]


def test_empty_text_returns_single_empty_chunk():
    assert chunk_message("") == [""]


def test_text_exactly_at_limit_is_unchanged():
    text = "a" * TELEGRAM_MESSAGE_LIMIT
    assert chunk_message(text) == [text]


def test_long_plain_text_splits_into_multiple_chunks_each_within_limit():
    text = "\n\n".join(f"paragraphe {i} " + "x" * 200 for i in range(60))
    chunks = chunk_message(text)
    assert len(chunks) > 1
    assert all(len(c) <= TELEGRAM_MESSAGE_LIMIT for c in chunks)


def test_reassembling_chunks_preserves_all_original_lines():
    lines = [f"ligne {i}" for i in range(2000)]
    text = "\n".join(lines)
    chunks = chunk_message(text)
    reassembled = "\n".join(chunks).split("\n")
    assert reassembled == lines


def test_never_splits_inside_a_code_fence():
    body = "\n".join(f"code_line_{i}" for i in range(400))
    text = f"avant\n```\n{body}\n```\naprès"
    chunks = chunk_message(text)
    for chunk in chunks:
        # un chunk qui contient une ligne ``` d'ouverture doit aussi en contenir une de fermeture (ou l'inverse)
        fence_count = sum(1 for line in chunk.split("\n") if line.strip().startswith("```"))
        assert fence_count % 2 == 0, f"chunk avec un fence non refermé : {chunk[:80]!r}"


def test_single_word_longer_than_limit_is_hard_split():
    huge_word = "x" * (TELEGRAM_MESSAGE_LIMIT + 500)
    chunks = chunk_message(huge_word)
    assert len(chunks) >= 2
    assert all(len(c) <= TELEGRAM_MESSAGE_LIMIT for c in chunks)
    assert "".join(chunks) == huge_word
