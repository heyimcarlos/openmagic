from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any
from uuid import UUID


def _json_value(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        normalized = value.astimezone(UTC)
        return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_value(value: Any) -> Any:
    return _json_value(value)


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()
