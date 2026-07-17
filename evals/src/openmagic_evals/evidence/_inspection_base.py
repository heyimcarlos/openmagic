"""Shared configuration for concern-specific evidence inspectors."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import psycopg
from psycopg import Cursor
from psycopg.rows import dict_row


class InspectionDatabase:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    @contextmanager
    def read_snapshot(self) -> Iterator[Cursor[dict[str, Any]]]:
        """Own one typed repeatable-read, read-only PostgreSQL snapshot."""

        with (
            psycopg.connect(self._database_url) as connection,
            connection.transaction(),
            connection.cursor(row_factory=dict_row) as cursor,
        ):
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            yield cursor


def uuid_column(records: list[dict[str, Any]], column: str) -> tuple[UUID, ...]:
    return tuple(UUID(str(record[column])) for record in records)


__all__ = ["InspectionDatabase", "uuid_column"]
