"""contracts/clock.py (Chantier 12 §A, Temporal System) — source de vérité
= horloge OS réelle, jamais une valeur inventée. Ne teste PAS que "l'heure
actuelle" a une valeur particulière (dépendrait de la date réelle) — teste
la STRUCTURE et la cohérence interne (UTC vs local vs offset)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from raya.contracts.clock import DEFAULT_TIMEZONE, now_local, resolve_not_before
from raya.contracts import parse_iso


def test_default_timezone_is_europe_brussels():
    assert DEFAULT_TIMEZONE == "Europe/Brussels"


def test_now_local_returns_internally_consistent_fields():
    current = now_local()
    assert current.timezone == DEFAULT_TIMEZONE
    assert 1 <= current.day <= 31
    assert 1 <= current.month <= 12
    assert 0 <= current.hour <= 23
    assert current.date in current.iso_local
    assert current.weekday in ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def test_now_local_utc_and_local_represent_the_same_instant():
    current = now_local("Europe/Brussels")
    utc_dt = parse_iso(current.iso_utc)
    local_dt = datetime.fromisoformat(current.iso_local)
    assert abs((utc_dt - local_dt.astimezone(timezone.utc)).total_seconds()) < 1.0


def test_now_local_falls_back_to_utc_for_unknown_timezone():
    """Se dégrade honnêtement — jamais un crash pour une primitive aussi
    fondamentale que 'quelle heure est-il'."""
    current = now_local("Not/A_Real_Timezone")
    assert current.utc_offset in ("+00:00", "-00:00")


def test_resolve_not_before_with_delay_seconds_is_in_the_near_future():
    result = resolve_not_before(delay_seconds=60)
    target = parse_iso(result)
    now = datetime.now(timezone.utc)
    assert timedelta(seconds=55) < (target - now) < timedelta(seconds=65)


def test_resolve_not_before_with_none_returns_none():
    assert resolve_not_before() is None


def test_resolve_not_before_rejects_negative_delay():
    with pytest.raises(ValueError):
        resolve_not_before(delay_seconds=-5)


def test_resolve_not_before_rejects_both_delay_and_run_at():
    with pytest.raises(ValueError):
        resolve_not_before(delay_seconds=5, run_at="2026-01-01T10:00:00+01:00")


def test_resolve_not_before_rejects_run_at_without_timezone():
    with pytest.raises(ValueError):
        resolve_not_before(run_at="2026-01-01T10:00:00")  # jamais un fuseau inventé


def test_resolve_not_before_with_run_at_converts_to_utc():
    result = resolve_not_before(run_at="2026-06-15T22:00:00+02:00")
    parsed = parse_iso(result)
    assert parsed == datetime(2026, 6, 15, 20, 0, 0, tzinfo=timezone.utc)
