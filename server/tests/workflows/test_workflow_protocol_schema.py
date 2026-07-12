from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from server.tests.workflows.factories import create_command
from server.workflows import WorkflowControlPlane, WorkflowDatabase

PROTOCOL_TABLES = {
    "workflows",
    "workflow_jobs",
    "workflow_job_dependencies",
    "workflow_job_runs",
    "workflow_events",
    "notifications",
}

EXPECTED_INDEXES = {
    "workflow_jobs": {
        "ix_workflow_jobs_claim",
        "uq_workflow_jobs_revises_job_id",
    },
    "workflow_job_dependencies": {"ix_workflow_job_dependencies_reverse"},
    "workflow_job_runs": {
        "ix_workflow_job_runs_lease",
        "uq_workflow_job_runs_running_job",
    },
    "workflow_events": {
        "ix_workflow_events_job",
        "ix_workflow_events_run",
        "ix_workflow_events_timeline",
        "uq_workflow_events_approval_cause",
        "uq_workflow_events_approval_invalidation",
        "uq_workflow_events_dispatch_job",
    },
    "notifications": {"ix_notifications_claim", "ix_notifications_lease"},
}


async def reflected_schema(database_url: str) -> tuple[set[str], dict[str, set[str]]]:
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        tables, indexes = await connection.run_sync(
            lambda sync_connection: (
                set(sa.inspect(sync_connection).get_table_names()),
                {
                    table: {
                        index["name"]
                        for index in sa.inspect(sync_connection).get_indexes(table)
                        if index["name"] is not None
                    }
                    for table in EXPECTED_INDEXES
                },
            )
        )
    await engine.dispose()
    return tables, indexes


async def test_migration_creates_protocol_tables_and_critical_indexes(
    migrated_postgres_url: str,
):
    tables, indexes = await reflected_schema(migrated_postgres_url)

    assert tables >= PROTOCOL_TABLES
    for table, expected_names in EXPECTED_INDEXES.items():
        assert expected_names <= indexes[table]


async def test_dependency_cannot_cross_workflow(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    first = await control_plane.create_workflow(create_command())
    second = await control_plane.create_workflow(create_command())
    first_send = next(job for job in first.jobs if job.status == "waiting")
    second_draft = next(job for job in second.jobs if job.status == "queued")

    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        with pytest.raises(IntegrityError):
            await connection.execute(
                sa.text(
                    "INSERT INTO workflow_job_dependencies "
                    "(workflow_id, job_id, depends_on_job_id) "
                    "VALUES (:workflow_id, :job_id, :depends_on_job_id)"
                ),
                {
                    "workflow_id": first.workflow.id,
                    "job_id": first_send.id,
                    "depends_on_job_id": second_draft.id,
                },
            )
        await transaction.rollback()
    await engine.dispose()


async def test_database_rejects_self_dependency(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    trace = await control_plane.create_workflow(create_command())
    draft = next(job for job in trace.jobs if job.status == "queued")

    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        with pytest.raises(IntegrityError):
            await connection.execute(
                sa.text(
                    "INSERT INTO workflow_job_dependencies "
                    "(workflow_id, job_id, depends_on_job_id) "
                    "VALUES (:workflow_id, :job_id, :job_id)"
                ),
                {"workflow_id": trace.workflow.id, "job_id": draft.id},
            )
        await transaction.rollback()
    await engine.dispose()


async def test_database_rejects_second_running_run_for_job(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    trace = await control_plane.create_workflow(create_command())
    draft = next(job for job in trace.jobs if job.status == "queued")
    lease_expires_at = datetime.now(UTC) + timedelta(minutes=1)

    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO workflow_job_runs "
                "(id, workflow_id, job_id, status, worker_id, lease_expires_at, "
                "application_build) VALUES "
                "(:id, :workflow_id, :job_id, 'running', 'worker-1', "
                ":lease_expires_at, 'test')"
            ),
            {
                "id": uuid4(),
                "workflow_id": trace.workflow.id,
                "job_id": draft.id,
                "lease_expires_at": lease_expires_at,
            },
        )
        await connection.commit()

        transaction = await connection.begin()
        with pytest.raises(IntegrityError):
            await connection.execute(
                sa.text(
                    "INSERT INTO workflow_job_runs "
                    "(id, workflow_id, job_id, status, worker_id, lease_expires_at, "
                    "application_build) VALUES "
                    "(:id, :workflow_id, :job_id, 'running', 'worker-2', "
                    ":lease_expires_at, 'test')"
                ),
                {
                    "id": uuid4(),
                    "workflow_id": trace.workflow.id,
                    "job_id": draft.id,
                    "lease_expires_at": lease_expires_at,
                },
            )
        await transaction.rollback()
    await engine.dispose()


async def test_database_rejects_output_before_job_succeeds(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    trace = await control_plane.create_workflow(create_command())
    draft = next(job for job in trace.jobs if job.status == "queued")

    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        with pytest.raises(IntegrityError):
            await connection.execute(
                sa.text(
                    "UPDATE workflow_jobs SET output = CAST(:output AS jsonb) WHERE id = :job_id"
                ),
                {"output": json.dumps({"subject": "too early"}), "job_id": draft.id},
            )
        await transaction.rollback()
    await engine.dispose()


async def test_database_rejects_attempts_above_persisted_budget(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    trace = await control_plane.create_workflow(create_command())
    draft = next(job for job in trace.jobs if job.status == "queued")

    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        with pytest.raises(IntegrityError):
            await connection.execute(
                sa.text("UPDATE workflow_jobs SET attempts = max_attempts + 1 WHERE id = :job_id"),
                {"job_id": draft.id},
            )
        await transaction.rollback()
    await engine.dispose()


async def test_approval_grant_reference_cannot_cross_workflow(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    first = await control_plane.create_workflow(create_command())
    second = await control_plane.create_workflow(create_command())
    first_send = next(job for job in first.jobs if job.status == "waiting")
    second_send = next(job for job in second.jobs if job.status == "waiting")
    approval_grant_id = uuid4()

    engine = create_async_engine(migrated_postgres_url)
    async with engine.connect() as connection:
        await connection.execute(
            sa.text(
                "INSERT INTO workflow_events "
                "(id, workflow_id, job_id, event_type, actor_type, actor_id, "
                "cause_type, cause_id) VALUES "
                "(:id, :workflow_id, :job_id, 'approval_granted', 'party', "
                "'broker', 'message', 'approval-message')"
            ),
            {
                "id": approval_grant_id,
                "workflow_id": first.workflow.id,
                "job_id": first_send.id,
            },
        )
        await connection.commit()

        transaction = await connection.begin()
        with pytest.raises(IntegrityError):
            await connection.execute(
                sa.text(
                    "INSERT INTO workflow_events "
                    "(id, workflow_id, job_id, event_type, actor_type, actor_id, "
                    "cause_type, cause_id, approval_grant_id) VALUES "
                    "(:id, :workflow_id, :job_id, 'approval_invalidated', 'system', "
                    "'control-plane', 'job', :cause_id, :approval_grant_id)"
                ),
                {
                    "id": uuid4(),
                    "workflow_id": second.workflow.id,
                    "job_id": second_send.id,
                    "cause_id": str(second_send.id),
                    "approval_grant_id": approval_grant_id,
                },
            )
        await transaction.rollback()
    await engine.dispose()


async def test_workflow_reads_use_one_repeatable_snapshot(
    control_plane: WorkflowControlPlane,
    migrated_postgres_url: str,
):
    database = WorkflowDatabase(migrated_postgres_url)
    async with database.read_transaction() as session:
        before = await session.scalar(sa.text("SELECT count(*) FROM workflows"))
        await control_plane.create_workflow(create_command())
        after_concurrent_commit = await session.scalar(sa.text("SELECT count(*) FROM workflows"))

    assert before == 0
    assert after_concurrent_commit == before
    await database.dispose()
