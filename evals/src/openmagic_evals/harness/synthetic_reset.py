"""Private destructive reset for explicitly marked synthetic databases."""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from example_insurance.migrations import apply_migrations
from psycopg import Connection, sql


class ResetPreflightBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class ResetAssessment:
    accepted: bool
    blocking_conditions: tuple[str, ...]


_SYNTHETIC_DATABASE_PREFIXES = ("openmagic_playground_", "openmagic_test_")
_SYNTHETIC_MARKER = "openmagic:synthetic:issue-71.v1"
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


def _database_marker(connection: Connection[tuple[object, ...]]) -> str | None:
    row = connection.execute(
        "SELECT description FROM pg_shdescription AS d "
        "JOIN pg_database AS db ON db.oid = d.objoid "
        "WHERE db.datname = current_database() AND d.classoid = 'pg_database'::regclass"
    ).fetchone()
    return str(row[0]) if row is not None else None


def _assess_connection(connection: Connection[tuple[object, ...]]) -> ResetAssessment:
    conditions: list[str] = []
    database = _database_name(connection)
    if not database.startswith(_SYNTHETIC_DATABASE_PREFIXES):
        conditions.append("database name is not explicitly synthetic")
    if set(_user_schemas(connection)).difference(_OWNED_SCHEMAS):
        conditions.append("database contains an unowned schema")
    if _public_tables(connection):
        conditions.append("public schema contains application tables")
    if _database_marker(connection) != _SYNTHETIC_MARKER:
        conditions.append("deployment is not durably marked synthetic")
    return ResetAssessment(accepted=not conditions, blocking_conditions=tuple(conditions))


def assess_reset(database_url: str) -> ResetAssessment:
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
        database = _database_name(connection)
        if not database.startswith(_SYNTHETIC_DATABASE_PREFIXES):
            raise ResetPreflightBlocked("database name is not explicitly synthetic")
        tables = tuple(
            (schema, table)
            for schema, table in _owned_tables(connection)
            if table != "migration_history"
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
            sql.SQL("COMMENT ON DATABASE {} IS {}").format(
                sql.Identifier(database), sql.Literal(_SYNTHETIC_MARKER)
            )
        )


def reset_synthetic_deployment(database_url: str) -> None:
    """Drop and rebuild only a preflight-approved synthetic deployment."""

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(current_database(), 0))")
        assessment = _assess_connection(connection)
        if not assessment.accepted:
            raise ResetPreflightBlocked("; ".join(assessment.blocking_conditions))
        tables = _owned_tables(connection)
        if tables:
            targets = sql.SQL(", ").join(
                sql.SQL("{}.{}").format(sql.Identifier(schema), sql.Identifier(table))
                for schema, table in tables
            )
            connection.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(targets))
        connection.execute("DROP SCHEMA IF EXISTS example_insurance CASCADE")
        connection.execute("DROP SCHEMA IF EXISTS openmagic_runtime CASCADE")
    apply_migrations(database_url)


__all__ = [
    "ResetAssessment",
    "ResetPreflightBlocked",
    "assess_reset",
    "mark_synthetic_deployment",
    "reset_synthetic_deployment",
]
