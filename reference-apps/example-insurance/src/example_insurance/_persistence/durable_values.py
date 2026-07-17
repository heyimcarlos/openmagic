"""Strict scalar decoding for application-owned durable records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast
from uuid import UUID


def invalid_durable_value() -> RuntimeError:
    return RuntimeError("Application evidence contains an invalid durable representation")


def uuid_value(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise invalid_durable_value()
    return value


def nonempty_string(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise invalid_durable_value()
    return value


def positive_integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise invalid_durable_value()
    return value


def boolean_value(value: object) -> bool:
    if not isinstance(value, bool):
        raise invalid_durable_value()
    return value


def object_value(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise invalid_durable_value()
    return dict(cast(Mapping[str, Any], value))


__all__ = [
    "boolean_value",
    "invalid_durable_value",
    "nonempty_string",
    "object_value",
    "positive_integer",
    "uuid_value",
]
