"""Deployment health records owned by runtime persistence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import psycopg
from psycopg.rows import dict_row


@dataclass(frozen=True)
class DatabaseHealthRecord:
    database: str
    runtime_schema_ready: bool

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> DatabaseHealthRecord:
        return cls(
            database=str(record["database"]),
            runtime_schema_ready=bool(record["runtime_schema_ready"]),
        )


def read_database_health(database_url: str) -> DatabaseHealthRecord:
    with (
        psycopg.connect(database_url) as connection,
        connection.cursor(row_factory=dict_row) as cursor,
    ):
        record = cursor.execute(
            "SELECT current_database() AS database, "
            "to_regnamespace('openmagic_runtime') IS NOT NULL AS runtime_schema_ready"
        ).fetchone()
    if record is None:
        raise RuntimeError("PostgreSQL did not report the current database")
    return DatabaseHealthRecord.decode(record)


__all__ = ["DatabaseHealthRecord", "read_database_health"]
