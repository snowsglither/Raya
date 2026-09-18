"""Transport d'images via OllamaCloudAdapter — _encode_image_ref + _messages_to_ollama."""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path

import pytest

from raya.contracts import ContentPart, Message
from raya.models.providers.ollama_cloud import _encode_image_ref, _messages_to_ollama


# ─── _encode_image_ref ────────────────────────────────────────────────────────

def test_encode_image_ref_returns_valid_base64(tmp_path):
    img_file = tmp_path / "test.png"
    img_file.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    result = _encode_image_ref(str(img_file))
    assert result is not None
    decoded = base64.b64decode(result)
    assert decoded[:8] == b"\x89PNG\r\n\x1a\n"


def test_encode_image_ref_returns_none_for_missing_file(tmp_path):
    result = _encode_image_ref(str(tmp_path / "nonexistent.png"))
    assert result is None


def test_encode_image_ref_returns_no_data_uri_prefix(tmp_path):
    """Ollama attend du base64 pur, sans 'data:image/png;base64,' prefix."""
    img_file = tmp_path / "img.png"
    img_file.write_bytes(b"\x89PNG\r\n" + b"\x00" * 10)
    result = _encode_image_ref(str(img_file))
    assert result is not None
    assert not result.startswith("data:")


def test_encode_image_ref_returns_ascii(tmp_path):
    img_file = tmp_path / "img.png"
    img_file.write_bytes(b"\x89PNG\r\n" + b"\x00" * 10)
    result = _encode_image_ref(str(img_file))
    assert result is not None
    result.encode("ascii")  # ne doit pas lever UnicodeEncodeError


# ─── _messages_to_ollama ─────────────────────────────────────────────────────

def test_messages_to_ollama_text_only():
    messages = [Message(role="user", content=[ContentPart(type="text", value="Hello")])]
    result = _messages_to_ollama(messages)
    assert result[0]["content"] == "Hello"
    assert "images" not in result[0]


def test_messages_to_ollama_with_image_ref(tmp_path):
    img = tmp_path / "screenshot.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 20)
    messages = [Message(role="user", content=[
        ContentPart(type="image_ref", value=str(img)),
        ContentPart(type="text", value="What do you see?"),
    ])]
    result = _messages_to_ollama(messages)
    assert "images" in result[0]
    assert len(result[0]["images"]) == 1
    # Doit être du base64 pur, décodable
    base64.b64decode(result[0]["images"][0])


def test_messages_to_ollama_image_ref_missing_file_skipped(tmp_path):
    messages = [Message(role="user", content=[
        ContentPart(type="image_ref", value=str(tmp_path / "ghost.png")),
        ContentPart(type="text", value="test"),
    ])]
    result = _messages_to_ollama(messages)
    # Fichier manquant → images absent ou liste vide (dégradation gracieuse)
    images = result[0].get("images", [])
    assert len(images) == 0


def test_messages_to_ollama_multiple_images(tmp_path):
    imgs = []
    for i in range(3):
        p = tmp_path / f"img{i}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes([i]) * 10)
        imgs.append(p)
    messages = [Message(role="user", content=[
        ContentPart(type="image_ref", value=str(imgs[0])),
        ContentPart(type="image_ref", value=str(imgs[1])),
        ContentPart(type="image_ref", value=str(imgs[2])),
        ContentPart(type="text", value="Describe all"),
    ])]
    result = _messages_to_ollama(messages)
    assert "images" in result[0]
    assert len(result[0]["images"]) == 3


def test_messages_to_ollama_text_and_image_coexist(tmp_path):
    img = tmp_path / "screen.png"
    img.write_bytes(b"\x89PNG\r\n" + b"\x00" * 10)
    messages = [Message(role="user", content=[
        ContentPart(type="image_ref", value=str(img)),
        ContentPart(type="text", value="Describe the screen"),
    ])]
    result = _messages_to_ollama(messages)
    assert result[0]["content"] == "Describe the screen"
    assert "images" in result[0]
    assert len(result[0]["images"]) == 1


def test_messages_to_ollama_no_regression_for_plain_text():
    """Aucune régression pour les messages sans image — comportement exact d'avant."""
    messages = [
        Message(role="user", content=[ContentPart(type="text", value="Question?")]),
        Message(role="assistant", content=[ContentPart(type="text", value="Answer.")]),
    ]
    result = _messages_to_ollama(messages)
    assert result[0] == {"role": "user", "content": "Question?"}
    assert result[1]["content"] == "Answer."
    assert "images" not in result[0]
    assert "images" not in result[1]
