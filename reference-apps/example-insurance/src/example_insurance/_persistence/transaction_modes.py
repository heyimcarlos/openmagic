"""Canonical transaction modes for application persistence observations."""

from __future__ import annotations

from typing import Any

from psycopg import Connection


def set_read_only(connection: Connection[tuple[Any, ...]]) -> None:
    connection.execute("SET TRANSACTION READ ONLY")


def set_repeatable_read_only(connection: Connection[tuple[Any, ...]]) -> None:
    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")


__all__ = ["set_read_only", "set_repeatable_read_only"]
