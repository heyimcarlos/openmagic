"""Destructive reset restricted to explicitly named synthetic databases."""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import psycopg
from psycopg import Connection, sql

from example_insurance.migrations import apply_migrations


class ResetPreflightBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class ResetAssessment:
    accepted: bool
    blocking_conditions: tuple[str, ...]


_SYNTHETIC_DATABASE_PREFIXES = ("openmagic_playground_", "openmagic_test_")
_OWNED_SCHEMAS = ("example_insurance", "openmagic_runtime")


def _database_name(connection: Connection[tuple[object, ...]]) -> str:
    row = connection.execute("SELECT current_database()").fetchone()
    if row is None:
        raise RuntimeError("PostgreSQL did not report the current database")
    return str(row[0])


def _user_schemas(connection: Connection[tuple[object, ...]]) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT nspname FROM pg_namespace WHERE nspname NOT IN "
        "('information_schema', 'pg_catalog', 'pg_toast', 'public') "
        "AND nspname NOT LIKE 'pg_temp_%' AND nspname NOT LIKE 'pg_toast_temp_%' "
        "ORDER BY nspname"
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _public_tables(connection: Connection[tuple[object, ...]]) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public' ORDER BY tablename"
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _assess_connection(connection: Connection[tuple[object, ...]]) -> ResetAssessment:
    conditions: list[str] = []
    database = _database_name(connection)
    if not database.startswith(_SYNTHETIC_DATABASE_PREFIXES):
        conditions.append("database name is not explicitly synthetic")
    unknown_schemas = set(_user_schemas(connection)).difference(_OWNED_SCHEMAS)
    if unknown_schemas:
        conditions.append("database contains an unowned schema")
    if _public_tables(connection):
        conditions.append("public schema contains application tables")
    purpose = connection.execute(
        "SELECT deployment_purpose FROM example_insurance.deployment_metadata WHERE singleton"
    ).fetchone()
    if purpose != ("synthetic",):
        conditions.append("deployment is not durably marked synthetic")
    return ResetAssessment(accepted=not conditions, blocking_conditions=tuple(conditions))


def assess_reset(database_url: str) -> ResetAssessment:
    """Confirm that reset is limited to the named synthetic deployment shape."""

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        return _assess_connection(connection)


def _owned_tables(connection: Connection[tuple[object, ...]]) -> tuple[tuple[str, str], ...]:
    rows = connection.execute(
        "SELECT schemaname, tablename FROM pg_catalog.pg_tables "
        "WHERE schemaname = ANY(%s) ORDER BY schemaname, tablename",
        (list(_OWNED_SCHEMAS),),
    ).fetchall()
    return tuple((str(row[0]), str(row[1])) for row in rows)


def mark_synthetic_deployment(database_url: str) -> None:
    """Mark one empty, explicitly named deployment as synthetic and resettable."""

    with psycopg.connect(database_url) as connection, connection.transaction():
        if not _database_name(connection).startswith(_SYNTHETIC_DATABASE_PREFIXES):
            raise ResetPreflightBlocked("database name is not explicitly synthetic")
        tables = tuple(
            (schema, table)
            for schema, table in _owned_tables(connection)
            if table not in {"deployment_metadata", "migration_history"}
        )
        for schema, table in tables:
            row = connection.execute(
                sql.SQL("SELECT 1 FROM {}.{} LIMIT 1").format(
                    sql.Identifier(schema), sql.Identifier(table)
                )
            ).fetchone()
            if row is not None:
                raise ResetPreflightBlocked("synthetic marker requires an empty deployment")
        connection.execute(
            "UPDATE example_insurance.deployment_metadata "
            "SET deployment_purpose = 'synthetic' WHERE singleton"
        )


def reset_synthetic_deployment(database_url: str) -> None:
    """Drop and rebuild only a preflight-approved synthetic deployment."""

    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute("SELECT pg_advisory_lock(hashtextextended(current_database(), 0))")
        try:
            with connection.transaction():
                assessment = _assess_connection(connection)
                if not assessment.accepted:
                    raise ResetPreflightBlocked("; ".join(assessment.blocking_conditions))
                tables = _owned_tables(connection)
                if tables:
                    targets = sql.SQL(", ").join(
                        sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))
                        for schema, table in tables
                    )
                    connection.execute(
                        sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(targets)
                    )
                connection.execute("DROP SCHEMA IF EXISTS example_insurance CASCADE")
                connection.execute("DROP SCHEMA IF EXISTS openmagic_runtime CASCADE")
            apply_migrations(database_url)
            mark_synthetic_deployment(database_url)
        finally:
            connection.execute("SELECT pg_advisory_unlock(hashtextextended(current_database(), 0))")


def main() -> None:
    parser = argparse.ArgumentParser(prog="example-insurance-reset")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--accept-destructive-reset", action="store_true")
    arguments = parser.parse_args()
    if not arguments.accept_destructive_reset:
        parser.error("--accept-destructive-reset is required")
    reset_synthetic_deployment(arguments.database_url)


__all__ = [
    "ResetAssessment",
    "ResetPreflightBlocked",
    "assess_reset",
    "main",
    "mark_synthetic_deployment",
    "reset_synthetic_deployment",
]
