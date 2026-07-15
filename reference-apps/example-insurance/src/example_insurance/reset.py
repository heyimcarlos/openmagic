"""Destructive synthetic deployment reset with a fail-closed legacy preflight."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import cast
from uuid import UUID

import psycopg
from psycopg import Connection, sql

from example_insurance.migrations import apply_migrations


class ResetPreflightBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class ResetAssessment:
    accepted: bool
    unexpected_records: tuple[tuple[str, int], ...]


_WORKFLOW_SCOPED = {
    "workflow_jobs": ("workflow_id",),
    "workflow_job_dependencies": ("workflow_id",),
    "workflow_job_runs": ("workflow_id",),
    "workflow_events": ("workflow_id",),
    "notifications": ("workflow_id",),
    "verification_challenges": ("workflow_id", "delivery_workflow_id"),
    "workflow_participants": ("workflow_id",),
    "workflow_participant_roles": ("workflow_id",),
}
_PARTY_SCOPED = {
    "parties": ("id",),
    "party_identifiers": ("party_id",),
    "organization_memberships": ("person_party_id", "organization_party_id"),
    "interaction_causes": ("actor_party_id",),
}
_LEGACY_TABLES = {
    "interaction_activity_receipts",
    "interaction_causes",
    "notifications",
    "organization_memberships",
    "parties",
    "party_identifiers",
    "verification_challenges",
    "workflow_events",
    "workflow_job_dependencies",
    "workflow_job_runs",
    "workflow_jobs",
    "workflow_participant_roles",
    "workflow_participants",
    "workflows",
}
_RESET_INFRASTRUCTURE_TABLES = {"alembic_version"}


def _table_exists(connection: Connection[tuple[object, ...]], table: str) -> bool:
    row = connection.execute("SELECT to_regclass(%s)", (f"public.{table}",)).fetchone()
    return row is not None and row[0] is not None


def _unexpected_count(
    connection: Connection[tuple[object, ...]],
    *,
    table: str,
    columns: tuple[str, ...],
    allowed: tuple[UUID, ...],
) -> int:
    if not allowed:
        row = connection.execute(
            sql.SQL("SELECT count(*) FROM public.{}").format(sql.Identifier(table))
        ).fetchone()
    else:
        conditions = sql.SQL(" OR ").join(
            sql.SQL("{} <> ALL(%s)").format(sql.Identifier(column)) for column in columns
        )
        row = connection.execute(
            sql.SQL("SELECT count(*) FROM public.{} WHERE ").format(sql.Identifier(table))
            + conditions,
            tuple([list(allowed)] * len(columns)),
        ).fetchone()
    return cast(int, row[0]) if row is not None else 0


def _public_tables(connection: Connection[tuple[object, ...]]) -> tuple[str, ...]:
    rows = connection.execute(
        "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public' ORDER BY tablename"
    ).fetchall()
    return tuple(str(row[0]) for row in rows)


def _unexpected_activity_receipts(
    connection: Connection[tuple[object, ...]],
    *,
    demo_workflow_ids: tuple[UUID, ...],
    demo_party_ids: tuple[UUID, ...],
) -> int:
    if not _table_exists(connection, "interaction_causes"):
        return _unexpected_count(
            connection,
            table="interaction_activity_receipts",
            columns=("id",),
            allowed=(),
        )
    row = connection.execute(
        "SELECT count(*) FROM public.interaction_activity_receipts AS receipt "
        "LEFT JOIN public.interaction_causes AS cause ON cause.id = receipt.cause_id "
        "WHERE NOT ("
        "COALESCE(receipt.workflow_id = ANY(%s), false) OR "
        "COALESCE(cause.actor_party_id = ANY(%s), false)"
        ")",
        (list(demo_workflow_ids), list(demo_party_ids)),
    ).fetchone()
    return cast(int, row[0]) if row is not None else 0


def assess_reset(
    database_url: str,
    *,
    demo_workflow_ids: tuple[UUID, ...] = (),
    demo_party_ids: tuple[UUID, ...] = (),
) -> ResetAssessment:
    """Reject every legacy record not covered by explicit synthetic identities."""

    with psycopg.connect(database_url) as connection, connection.transaction():
        connection.execute("SET TRANSACTION READ ONLY")
        return _assess_connection(
            connection,
            demo_workflow_ids=demo_workflow_ids,
            demo_party_ids=demo_party_ids,
        )


def _assess_connection(
    connection: Connection[tuple[object, ...]],
    *,
    demo_workflow_ids: tuple[UUID, ...],
    demo_party_ids: tuple[UUID, ...],
) -> ResetAssessment:
    unexpected: list[tuple[str, int]] = []
    if _table_exists(connection, "workflows"):
        count = _unexpected_count(
            connection,
            table="workflows",
            columns=("id",),
            allowed=demo_workflow_ids,
        )
        if count:
            unexpected.append(("workflows", count))
    for table, columns in _WORKFLOW_SCOPED.items():
        if _table_exists(connection, table):
            count = _unexpected_count(
                connection,
                table=table,
                columns=columns,
                allowed=demo_workflow_ids,
            )
            if count:
                unexpected.append((table, count))
    for table, columns in _PARTY_SCOPED.items():
        if _table_exists(connection, table):
            count = _unexpected_count(
                connection,
                table=table,
                columns=columns,
                allowed=demo_party_ids,
            )
            if count:
                unexpected.append((table, count))
    if _table_exists(connection, "interaction_activity_receipts"):
        count = _unexpected_activity_receipts(
            connection,
            demo_workflow_ids=demo_workflow_ids,
            demo_party_ids=demo_party_ids,
        )
        if count:
            unexpected.append(("interaction_activity_receipts", count))
    unknown_tables = set(_public_tables(connection)).difference(
        _LEGACY_TABLES | _RESET_INFRASTRUCTURE_TABLES
    )
    for table in sorted(unknown_tables):
        count = _unexpected_count(
            connection,
            table=table,
            columns=("id",),
            allowed=(),
        )
        if count:
            unexpected.append((table, count))
    return ResetAssessment(accepted=not unexpected, unexpected_records=tuple(sorted(unexpected)))


def reset_synthetic_deployment(
    database_url: str,
    *,
    demo_workflow_ids: tuple[UUID, ...] = (),
    demo_party_ids: tuple[UUID, ...] = (),
) -> None:
    with psycopg.connect(database_url) as connection, connection.transaction():
        public_tables = _public_tables(connection)
        if public_tables:
            targets = sql.SQL(", ").join(
                sql.SQL("public.{}").format(sql.Identifier(table)) for table in public_tables
            )
            connection.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(targets))
        assessment = _assess_connection(
            connection,
            demo_workflow_ids=demo_workflow_ids,
            demo_party_ids=demo_party_ids,
        )
        if not assessment.accepted:
            detail = ", ".join(f"{table}={count}" for table, count in assessment.unexpected_records)
            raise ResetPreflightBlocked(f"unexpected non-demo legacy records: {detail}")
        connection.execute("DROP SCHEMA IF EXISTS example_insurance CASCADE")
        connection.execute("DROP SCHEMA IF EXISTS openmagic_runtime CASCADE")
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")
    apply_migrations(database_url)


def main() -> None:
    parser = argparse.ArgumentParser(prog="example-insurance-reset")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--accept-destructive-reset", action="store_true")
    parser.add_argument("--demo-workflow-id", action="append", type=UUID, default=[])
    parser.add_argument("--demo-party-id", action="append", type=UUID, default=[])
    arguments = parser.parse_args()
    if not arguments.accept_destructive_reset:
        parser.error("--accept-destructive-reset is required")
    reset_synthetic_deployment(
        arguments.database_url,
        demo_workflow_ids=tuple(arguments.demo_workflow_id),
        demo_party_ids=tuple(arguments.demo_party_id),
    )


__all__ = [
    "ResetAssessment",
    "ResetPreflightBlocked",
    "assess_reset",
    "main",
    "reset_synthetic_deployment",
]
