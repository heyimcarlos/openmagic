"""Sanitized provenance for the exact playground PostgreSQL deployment."""

from __future__ import annotations

from typing import Any

import psycopg
from openmagic_runtime.evidence import content_fingerprint
from psycopg import sql

from openmagic_playground.deployment import POSTGRES_IMAGE


def _migration_head(connection: psycopg.Connection[tuple[Any, ...]], table: str) -> str | None:
    exists = connection.execute("SELECT to_regclass(%s)", (table,)).fetchone()
    if exists is None or exists[0] is None:
        return None
    schema, relation = table.split(".", maxsplit=1)
    row = connection.execute(
        sql.SQL("SELECT version FROM {}.{} ORDER BY version DESC LIMIT 1").format(
            sql.Identifier(schema), sql.Identifier(relation)
        )
    ).fetchone()
    return None if row is None else str(row[0])


def observe_postgres(database_url: str) -> dict[str, object]:
    """Return exact deployment provenance without raw connection identity."""

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        row = connection.execute(
            "SELECT current_setting('server_version'), "
            "current_setting('transaction_isolation'), "
            "current_setting('synchronous_commit'), "
            "current_setting('TimeZone'), "
            "current_setting('max_connections'), "
            "current_database(), (pg_control_system()).system_identifier::text"
        ).fetchone()
        migrations = {
            "example_insurance": _migration_head(connection, "example_insurance.migration_history"),
            "openmagic_runtime": _migration_head(connection, "openmagic_runtime.migration_history"),
        }
    if row is None:
        raise RuntimeError("PostgreSQL did not return playground provenance")
    configuration = {
        "max_connections": str(row[4]),
        "synchronous_commit": str(row[2]),
        "timezone": str(row[3]),
        "transaction_isolation": str(row[1]),
    }
    return {
        "deployment_id": "sha256:"
        + content_fingerprint({"database": str(row[5]), "system_identifier": str(row[6])}),
        "postgres_version": str(row[0]),
        "postgres_image": POSTGRES_IMAGE,
        "postgres_configuration": configuration,
        "postgres_configuration_digest": "sha256:" + content_fingerprint(configuration),
        "migration_heads": migrations,
    }


__all__ = ["observe_postgres"]
