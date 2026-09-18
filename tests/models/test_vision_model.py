"""raya.models.vision — observe_image, grounding, graceful degradation."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from raya.contracts import (
    Confidence,
    ContentPart,
    FinishReason,
    ModelCapability,
    ModelResponse,
    VisualObservation,
    ViewportInfo,
    new_id,
)
from raya.models.vision import _parse_grounding, observe_image


# ─── _parse_grounding ─────────────────────────────────────────────────────────

def test_parse_grounding_valid_format():
    text = 'FOUND: bbox=[0.1,0.2,0.8,0.9] label="Play button" confidence=0.95\nSome description.'
    vp = ViewportInfo(image_width=1920, image_height=1080)
    target = _parse_grounding(text, "obs1", vp)
    assert target is not None
    assert target.label == "Play button"
    assert abs(target.confidence - 0.95) < 0.01
    assert target.bbox.x_min == pytest.approx(0.1)
    assert target.bbox.x_max == pytest.approx(0.8)


def test_parse_grounding_not_found_returns_none():
    text = 'NOT_FOUND: reason="element not visible"'
    target = _parse_grounding(text, "obs2", None)
    assert target is None


def test_parse_grounding_malformed_returns_none():
    target = _parse_grounding("garbage text with no structure", "obs3", None)
    assert target is None


def test_parse_grounding_degenerate_bbox_returns_none():
    text = 'FOUND: bbox=[0.5,0.2,0.5,0.9] label="x" confidence=0.8'
    target = _parse_grounding(text, "obs4", None)
    assert target is None


def test_parse_grounding_out_of_range_coords_returns_none():
    text = 'FOUND: bbox=[-0.1,0.2,0.8,0.9] label="x" confidence=0.8'
    target = _parse_grounding(text, "obs5", None)
    assert target is None


def test_parse_grounding_confidence_clamped_to_1():
    text = 'FOUND: bbox=[0.1,0.1,0.9,0.9] label="x" confidence=1.5'
    target = _parse_grounding(text, "obs6", None)
    if target is not None:
        assert target.confidence <= 1.0


# ─── Chantier 20E — robustesse parser (formats réels observés en production) ──

def test_parse_grounding_20e_t1_bbox_no_spaces():
    """T1 — bbox sans espaces (format nominal)."""
    text = 'FOUND: bbox=[0.1,0.2,0.3,0.4] label="test" confidence=0.9'
    target = _parse_grounding(text, "t1", None)
    assert target is not None
    assert target.label == "test"
    assert target.bbox.x_min == pytest.approx(0.1)
    assert target.bbox.y_max == pytest.approx(0.4)


def test_parse_grounding_20e_t2_bbox_with_spaces():
    """T2 — bbox avec espaces après virgules (observé sur Amazon)."""
    text = 'FOUND: bbox=[0.1, 0.2, 0.3, 0.4] label="test" confidence=0.9'
    target = _parse_grounding(text, "t2", None)
    assert target is not None
    assert target.label == "test"
    assert target.bbox.x_min == pytest.approx(0.1)
    assert target.bbox.y_max == pytest.approx(0.4)


def test_parse_grounding_20e_t3_label_internal_quotes():
    """T3 — label avec guillemets internes + bbox avec espaces."""
    text = 'FOUND: bbox=[0.76, 0.46, 0.93, 0.51] label="bouton "Ajouter au panier"" confidence=0.99'
    target = _parse_grounding(text, "t3", None)
    assert target is not None
    assert "panier" in target.label
    assert target.confidence == pytest.approx(0.99)
    assert target.bbox.x_min == pytest.approx(0.76)
    assert target.bbox.x_max == pytest.approx(0.93)


def test_parse_grounding_20e_t4_real_amazon_output():
    """T4 — raw output exact de Gemma4:31b observé en audit 20D (bbox sans espaces)."""
    text = (
        'FOUND: bbox=[0.76,0.46,0.93,0.51] label="bouton "Ajouter au panier"" confidence=0.99\n\n'
        "The image is a screenshot of an Amazon France product page for an Xbox Series S console."
    )
    target = _parse_grounding(text, "t4", None)
    assert target is not None
    assert target.confidence == pytest.approx(0.99)
    assert target.bbox.x_min == pytest.approx(0.76)
    assert target.bbox.y_min == pytest.approx(0.46)
    assert target.bbox.x_max == pytest.approx(0.93)
    assert target.bbox.y_max == pytest.approx(0.51)


def test_parse_grounding_20e_t5_real_product_title():
    """T5 — raw output exact du titre produit observé en audit 20D (bbox avec espaces)."""
    text = (
        'FOUND: bbox=[0.35, 0.19, 0.51, 0.24] label="titre du produit Xbox Series S" confidence=0.9\n'
        "This image is a screenshot of an Amazon product page."
    )
    target = _parse_grounding(text, "t5", None)
    assert target is not None
    assert "Xbox" in target.label
    assert target.bbox.x_min == pytest.approx(0.35)
    assert target.bbox.y_min == pytest.approx(0.19)


def test_parse_grounding_20e_t6_invalid_degenerate_bbox():
    """T6 — bbox dégénérée (x_min > x_max) — doit être rejetée."""
    text = 'FOUND: bbox=[0.9,0.1,0.1,0.9] label="test" confidence=0.8'
    target = _parse_grounding(text, "t6", None)
    assert target is None


def test_parse_grounding_20e_t7_wrong_coord_count():
    """T7 — nombre de coordonnées incorrect — doit être rejeté."""
    text = 'FOUND: bbox=[0.1,0.2,0.3] label="test" confidence=0.8'
    target = _parse_grounding(text, "t7", None)
    assert target is None


def test_parse_grounding_20e_t8_invalid_confidence():
    """T8 — confidence non numérique — doit être rejeté."""
    text = 'FOUND: bbox=[0.1,0.2,0.8,0.9] label="test" confidence=high'
    target = _parse_grounding(text, "t8", None)
    assert target is None


def test_parse_grounding_without_viewport_uses_default():
    text = 'FOUND: bbox=[0.0,0.0,1.0,1.0] label="full" confidence=0.7'
    target = _parse_grounding(text, "obs7", None)
    assert target is not None


# ─── observe_image ─────────────────────────────────────────────────────────────

def _make_image_file(tmp_path: Path) -> Path:
    """Crée un fichier PNG minimal (1×1 pixel) pour les tests."""
    try:
        from PIL import Image
        img = Image.new("RGB", (10, 10), color=(128, 128, 128))
        p = tmp_path / "test.png"
        img.save(str(p))
        return p
    except ImportError:
        p = tmp_path / "test.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        return p


def _fake_registry(response_text: str, capability=ModelCapability.VISION):
    from raya.contracts import ModelDescriptor
    from raya.models import ModelRegistry
    from tests.support.fake_provider import FakeScriptedProvider

    resp = ModelResponse(
        request_id="req1",
        content=[ContentPart(type="text", value=response_text)],
        finish_reason=FinishReason.COMPLETED,
        provider_used="fake-vision",
    )
    provider = FakeScriptedProvider([resp], capabilities=[capability])
    reg = ModelRegistry()
    reg.register(provider)
    return reg


def test_observe_image_returns_none_for_missing_file(tmp_path):
    reg = _fake_registry("some description")
    result = observe_image(reg, tmp_path / "nonexistent.png")
    assert result is None


def test_observe_image_returns_observation_on_success(tmp_path):
    p = _make_image_file(tmp_path)
    reg = _fake_registry("A desktop with a browser window open.")
    obs = observe_image(reg, p, source="perception:screen")
    assert obs is not None
    assert isinstance(obs, VisualObservation)
    assert obs.confidence == Confidence.INFERRED
    assert obs.description == "A desktop with a browser window open."


def test_observe_image_source_must_start_with_perception(tmp_path):
    p = _make_image_file(tmp_path)
    reg = _fake_registry("test")
    with pytest.raises(ValueError):
        observe_image(reg, p, source="llm:direct")


def test_observe_image_grounding_found(tmp_path):
    p = _make_image_file(tmp_path)
    response_text = 'FOUND: bbox=[0.4,0.3,0.6,0.5] label="Play button" confidence=0.88\nVideo player visible.'
    reg = _fake_registry(response_text)
    vp = ViewportInfo(image_width=1280, image_height=720)
    obs = observe_image(reg, p, find_target="Play button", viewport=vp, source="perception:browser")
    assert obs is not None
    assert obs.target is not None
    assert obs.target.label == "Play button"
    sx, sy = obs.target.screen_coordinates()
    assert 0 < sx < 1280
    assert 0 < sy < 720


def test_observe_image_grounding_not_found(tmp_path):
    p = _make_image_file(tmp_path)
    response_text = 'NOT_FOUND: reason="button not visible"\nBlank page.'
    reg = _fake_registry(response_text)
    obs = observe_image(reg, p, find_target="Play button", source="perception:screen")
    assert obs is not None
    assert obs.target is None


def test_observe_image_model_error_returns_empty_description(tmp_path):
    from raya.contracts import ErrorInfo, ModelDescriptor
    from raya.models import ModelRegistry
    from tests.support.fake_provider import FakeScriptedProvider

    p = _make_image_file(tmp_path)
    err_resp = ModelResponse(
        request_id="req2",
        content=[],
        finish_reason=FinishReason.ERROR,
        error=ErrorInfo(code="TIMEOUT", message="request timed out", retryable=True),
        provider_used="fake-vision",
    )
    provider = FakeScriptedProvider([err_resp], capabilities=[ModelCapability.VISION])
    reg = ModelRegistry()
    reg.register(provider)
    obs = observe_image(reg, p, source="perception:screen")
    assert obs is not None
    assert obs.description == ""
    assert "TIMEOUT" in obs.raw_model_output


def test_observe_image_semantic_entities_extracted(tmp_path):
    p = _make_image_file(tmp_path)
    reg = _fake_registry("The browser window shows YouTube. A video player is visible with a button.")
    obs = observe_image(reg, p, source="perception:screen")
    assert obs is not None
    entities_lower = [e.lower() for e in obs.semantic_entities]
    assert "browser" in entities_lower or "youtube" in [e for e in obs.semantic_entities]
