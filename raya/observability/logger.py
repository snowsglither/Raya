"""Logs structurés (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1.16).

Passif par construction : ne retourne rien qui puisse influencer un appelant,
n'est jamais dans le chemin critique (RAYA_V2_ARCHITECTURAL_INVARIANTS.md).
"""

from __future__ import annotations

import logging
import sys

_LOGGER = logging.getLogger("raya")
if not _LOGGER.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] raya: %(message)s"))
    _LOGGER.addHandler(_handler)
    _LOGGER.setLevel(logging.INFO)


def log(level: str, message: str, *, correlation_id: str | None = None, **fields: object) -> None:
    extra = " ".join(f"{k}={v!r}" for k, v in fields.items())
    corr = f" correlation_id={correlation_id}" if correlation_id else ""
    _LOGGER.log(getattr(logging, level.upper(), logging.INFO), f"{message}{corr} {extra}".rstrip())
