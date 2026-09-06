"""Support commun aux contrats RAYA V2 (RAYA_V2_CONTRACTS.md §0).

Aucune logique métier ici : uniquement génération d'ID, horodatage, et
sérialisation/désérialisation générique pour dataclasses. contracts/ ne doit
dépendre d'aucun autre subsystem RAYA (RAYA_V2_TECHNICAL_ARCHITECTURE.md §1).
"""

from __future__ import annotations

import dataclasses
import enum
import json
import os
import time
import typing
from datetime import datetime, timezone

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def _ulid() -> str:
    """26 caractères base32 Crockford, lexicographiquement triables par temps de création."""
    ms = int(time.time() * 1000)
    raw = ms.to_bytes(6, "big") + os.urandom(10)  # 128 bits
    value = int.from_bytes(raw, "big")
    chars = [""] * 26
    for i in range(25, -1, -1):
        chars[i] = _CROCKFORD[value & 0x1F]
        value >>= 5
    return "".join(chars)


def new_id(prefix: str) -> str:
    """ID préfixé par type, ex: evt_..., task_..., tc_... (RAYA_V2_CONTRACTS.md §0)."""
    return f"{prefix}_{_ulid()}"


def utc_now_iso() -> str:
    """ISO 8601 UTC, précision milliseconde, ex: 2026-09-04T14:32:10.123Z."""
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


def parse_iso(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def to_dict(obj: typing.Any) -> typing.Any:
    """Sérialisation générique : dataclass -> dict, Enum -> .value, récursif."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {f.name: to_dict(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, enum.Enum):
        return obj.value
    if isinstance(obj, (list, tuple)):
        return [to_dict(v) for v in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


def _unwrap_optional(tp: typing.Any) -> tuple[typing.Any, bool]:
    origin = typing.get_origin(tp)
    if origin is typing.Union or (origin is not None and getattr(origin, "__name__", "") == "UnionType"):
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        if len(args) == 1:
            return args[0], True
    return tp, False


def _coerce(raw: typing.Any, tp: typing.Any) -> typing.Any:
    tp, _optional = _unwrap_optional(tp)
    if raw is None:
        return None
    origin = typing.get_origin(tp)
    if origin is list:
        (elem_t,) = typing.get_args(tp)
        return [_coerce(v, elem_t) for v in raw]
    if isinstance(tp, type) and issubclass(tp, enum.Enum):
        return tp(raw)
    if dataclasses.is_dataclass(tp):
        return from_dict(tp, raw)
    return raw


def from_dict(cls: type, data: dict | None) -> typing.Any:
    """Reconstruction générique depuis un dict JSON-compatible."""
    if data is None:
        return None
    hints = typing.get_type_hints(cls)
    kwargs = {}
    for f in dataclasses.fields(cls):
        tp = hints.get(f.name, f.type)
        kwargs[f.name] = _coerce(data.get(f.name), tp)
    return cls(**kwargs)


def to_json(obj: typing.Any) -> str:
    return json.dumps(to_dict(obj), ensure_ascii=False)


def from_json(cls: type, raw: str) -> typing.Any:
    return from_dict(cls, json.loads(raw))
