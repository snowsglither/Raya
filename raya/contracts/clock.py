"""Primitive temporelle RAYA-native (Chantier 12 §A) — source de vérité =
horloge OS réelle (`datetime.now`), jamais un timestamp inventé par le
modèle ni dérivé de la mémoire/du contexte. Vit dans `contracts/` (toujours
importable, RAYA_V2_REPOSITORY_STRUCTURE.md §20) car `tools/catalog/`,
`harness/` ET `cognition/` en ont tous besoin, et `contracts` est le seul
subsystem autorisé partout — éviter de dupliquer cette logique ou de créer
une dépendance ascendante interdite (ex: tools/ ne peut pas importer
raya.runtime).

Fournit une PRIMITIVE fiable (heure/date/jour/fuseau courants), jamais une
résolution NLP des expressions relatives ("demain", "dans 3 minutes", "à
22h") — cette interprétation reste la responsabilité de Cognition (le
modèle), qui s'appuie sur cette primitive plutôt que de deviner l'heure
actuelle (RAYA_V2 Chantier 12 §2 : "ne transforme pas toutes ces expressions
en logique spécifique au modèle, fournis une primitive temporelle fiable")."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover — stdlib manquant (jamais sur les runtimes ciblés)
    ZoneInfo = None  # type: ignore

from ._base import parse_iso

# Fuseau utilisateur par défaut (RAYA_V2 Chantier 12 §0) — configurable via
# RAYA_TIMEZONE (raya/runtime/config.py), jamais recalculé ni deviné ailleurs.
DEFAULT_TIMEZONE = "Europe/Brussels"

_WEEKDAYS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _resolve_tz(tz_name: str):
    """Se dégrade honnêtement vers UTC si le nom de fuseau est invalide/la
    base tzdata est indisponible — jamais un crash pour une primitive aussi
    fondamentale que 'quelle heure est-il'."""
    if ZoneInfo is None:
        return timezone.utc
    try:
        return ZoneInfo(tz_name)
    except Exception:
        return timezone.utc


def _format_utc_iso(dt: datetime) -> str:
    dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{dt.microsecond // 1000:03d}Z"


@dataclass
class LocalTime:
    """Décomposition structurée du temps courant — exploitable directement
    par Cognition pour interpréter une expression temporelle relative sans
    avoir à re-parser une chaîne ISO elle-même."""

    iso_utc: str
    iso_local: str
    date: str
    time: str
    weekday: str
    day: int
    month: int
    year: int
    hour: int
    minute: int
    second: int
    timezone: str
    utc_offset: str


def now_local(tz_name: str = DEFAULT_TIMEZONE) -> LocalTime:
    """Seule source de vérité temporelle de RAYA — horloge système réelle,
    jamais une valeur mémorisée ou fournie par le modèle."""
    tzinfo = _resolve_tz(tz_name)
    now_utc = datetime.now(timezone.utc)
    local = now_utc.astimezone(tzinfo)
    offset = local.strftime("%z") or "+0000"
    offset_fmt = f"{offset[:3]}:{offset[3:]}"
    return LocalTime(
        iso_utc=_format_utc_iso(now_utc),
        iso_local=local.isoformat(),
        date=local.strftime("%Y-%m-%d"),
        time=local.strftime("%H:%M:%S"),
        weekday=_WEEKDAYS_FR[local.weekday()],
        day=local.day,
        month=local.month,
        year=local.year,
        hour=local.hour,
        minute=local.minute,
        second=local.second,
        timezone=tz_name,
        utc_offset=offset_fmt,
    )


def local_time_to_dict(value: LocalTime) -> dict:
    return asdict(value)


def resolve_not_before(delay_seconds: float | None = None, run_at: str | None = None) -> str | None:
    """Retourne un timestamp UTC ISO8601 utilisable comme `Task.not_before`,
    ou `None` (exécution immédiate — comportement inchangé). N'invente JAMAIS
    un fuseau horaire : `run_at` sans offset explicite est rejeté (le modèle
    doit lire l'offset courant via `now_local()`/le Tool `system.time.now`
    avant de construire un horodatage absolu)."""
    if delay_seconds is not None and run_at is not None:
        raise ValueError("delay_seconds et run_at sont mutuellement exclusifs")
    if delay_seconds is not None:
        if delay_seconds < 0:
            raise ValueError("delay_seconds doit être positif ou nul")
        target = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
        return _format_utc_iso(target)
    if run_at is not None:
        parsed = parse_iso(run_at)
        if parsed.tzinfo is None:
            raise ValueError(
                "run_at doit inclure un fuseau horaire explicite (ex: +01:00 ou Z) — jamais une "
                "heure locale ambiguë. Utilise system.time.now pour connaître l'offset courant."
            )
        return _format_utc_iso(parsed)
    return None
