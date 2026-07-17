"""Fail-closed scalar decoding shared by runtime persistence owners."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any, TypeGuard
from uuid import UUID


def invalid_durable_value() -> RuntimeError:
    return RuntimeError("Runtime persistence has an invalid durable representation")


def uuid_value(value: object) -> UUID:
    if not isinstance(value, UUID):
        raise invalid_durable_value()
    return value


def integer_value(value: object) -> int:
    if type(value) is not int:
        raise invalid_durable_value()
    return value


def nonnegative_integer_value(value: object) -> int:
    decoded = integer_value(value)
    if decoded < 0:
        raise invalid_durable_value()
    return decoded


def positive_integer_value(value: object) -> int:
    decoded = integer_value(value)
    if decoded <= 0:
        raise invalid_durable_value()
    return decoded


def boolean_value(value: object) -> bool:
    if type(value) is not bool:
        raise invalid_durable_value()
    return value


def string_value(value: object) -> str:
    if not isinstance(value, str):
        raise invalid_durable_value()
    return value


def nonempty_string(value: object) -> str:
    decoded = string_value(value)
    if not decoded:
        raise invalid_durable_value()
    return decoded


def uuid_text_value(value: object) -> UUID:
    decoded_text = string_value(value)
    try:
        decoded = UUID(decoded_text)
    except ValueError as error:
        raise invalid_durable_value() from error
    if str(decoded) != decoded_text:
        raise invalid_durable_value()
    return decoded


def timestamp_value(value: object) -> datetime:
    if not isinstance(value, datetime):
        raise invalid_durable_value()
    return value


def _string_keyed_mapping(value: object) -> TypeGuard[Mapping[str, Any]]:
    return isinstance(value, Mapping) and all(isinstance(key, str) for key in value)


def mapping_value(value: object) -> dict[str, Any]:
    if not _string_keyed_mapping(value):
        raise invalid_durable_value()
    return dict(value)


def nonempty_mapping(value: object) -> dict[str, Any]:
    decoded = mapping_value(value)
    if not decoded:
        raise invalid_durable_value()
    return decoded


def integer_items(value: object) -> tuple[int, ...]:
    if not isinstance(value, list):
        raise invalid_durable_value()
    return tuple(integer_value(item) for item in value)


def string_items(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise invalid_durable_value()
    return tuple(nonempty_string(item) for item in value)


__all__: list[str] = []
