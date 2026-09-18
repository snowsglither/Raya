"""Regression tests — URL evidence propagation after browser.click.

Verifies that:
- _LAST_CLICKED_OBSERVATION declares a current_url spec (evidence_field="url")
- The spec has the expected TTL and domain/key values
- _CURRENT_URL_OBSERVATION (from navigate) also exposes url
- Two specs are present in _LAST_CLICKED_OBSERVATION (target + url)
"""
from __future__ import annotations

from raya.tools.catalog.browser import _CURRENT_URL_OBSERVATION, _LAST_CLICKED_OBSERVATION


def test_last_clicked_observation_has_url_spec():
    fields = [s.evidence_field for s in _LAST_CLICKED_OBSERVATION]
    assert "url" in fields, "_LAST_CLICKED_OBSERVATION must expose 'url' evidence field"


def test_last_clicked_observation_url_key_and_domain():
    url_spec = next((s for s in _LAST_CLICKED_OBSERVATION if s.evidence_field == "url"), None)
    assert url_spec is not None
    assert url_spec.domain == "browser"
    assert url_spec.key == "current_url"


def test_last_clicked_observation_url_freshness_ttl():
    url_spec = next(s for s in _LAST_CLICKED_OBSERVATION if s.evidence_field == "url")
    assert url_spec.freshness_ttl_s == 60


def test_last_clicked_observation_has_clicked_target_spec():
    fields = [s.evidence_field for s in _LAST_CLICKED_OBSERVATION]
    assert "clicked_target" in fields


def test_navigate_observation_also_exposes_url():
    fields = [s.evidence_field for s in _CURRENT_URL_OBSERVATION]
    assert "url" in fields, "_CURRENT_URL_OBSERVATION must expose 'url' evidence field"
