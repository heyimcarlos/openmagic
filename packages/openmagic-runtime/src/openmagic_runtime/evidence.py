"""Read-only runtime deployment evidence exposed to installed process roles."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass

import psycopg


@dataclass(frozen=True)
class RuntimeDatabaseHealth:
    status: str
    pid: int
    database: str
    runtime_schema_ready: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def inspect_runtime_database(database_url: str) -> RuntimeDatabaseHealth:
    """Read runtime-owned deployment identity without retaining a session."""

    with psycopg.connect(database_url) as connection:
        database = connection.execute("SELECT current_database()").fetchone()
        runtime_schema = connection.execute(
            "SELECT to_regnamespace('openmagic_runtime') IS NOT NULL"
        ).fetchone()
    if database is None:
        raise RuntimeError("PostgreSQL did not report the current database")
    if runtime_schema is None or not runtime_schema[0]:
        raise RuntimeError("OpenMagic Runtime schema is not installed")
    return RuntimeDatabaseHealth(
        status="ready",
        pid=os.getpid(),
        database=str(database[0]),
        runtime_schema_ready=True,
    )


__all__ = ["RuntimeDatabaseHealth", "inspect_runtime_database"]
