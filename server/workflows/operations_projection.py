"""Bounded operational reads for engineering views of Workflow activity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa

from .database import WorkflowDatabase
from .models import (
    NotificationRow,
    WorkflowEventRow,
    WorkflowJobRow,
    WorkflowJobRunRow,
    WorkflowRow,
)


@dataclass(frozen=True)
class WorkflowOperationalJob:
    id: UUID
    workflow_id: UUID
    kind: str
    input: dict[str, object]
    status: str
    attempts: int
    max_attempts: int
    created_at: datetime


@dataclass(frozen=True)
class WorkflowOperationalJobRun:
    id: UUID
    job_id: UUID
    status: str
    worker_id: str
    runtime_instance_id: UUID | None
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class WorkflowOperationalNotification:
    id: UUID
    workflow_id: UUID
    kind: str
    status: str
    attempts: int
    claimed_by: str | None
    delivered_by: str | None
    created_at: datetime
    delivered_at: datetime | None


@dataclass(frozen=True)
class WorkflowOperationalEvent:
    id: UUID
    event_type: str
    workflow_id: UUID
    job_id: UUID | None
    run_id: UUID | None
    occurred_at: datetime


@dataclass(frozen=True)
class WorkflowOperationalSnapshot:
    captured_at: datetime
    workflow_count: int
    jobs: tuple[WorkflowOperationalJob, ...]
    job_runs: tuple[WorkflowOperationalJobRun, ...]
    notifications: tuple[WorkflowOperationalNotification, ...]
    events: tuple[WorkflowOperationalEvent, ...]


class WorkflowOperationsProjection:
    """Read a bounded recent slice without exposing Workflow persistence rows."""

    def __init__(self, database: WorkflowDatabase) -> None:
        self._database = database

    async def project(
        self,
        *,
        cause_prefix: str,
        workflow_limit: int = 50,
        event_limit: int = 200,
    ) -> WorkflowOperationalSnapshot:
        workflow_ids = (
            sa.select(WorkflowEventRow.workflow_id)
            .where(
                WorkflowEventRow.event_type == "workflow_jobs_proposed",
                WorkflowEventRow.cause_id.startswith(cause_prefix),
            )
            .order_by(WorkflowEventRow.occurred_at.desc(), WorkflowEventRow.id.desc())
            .limit(workflow_limit)
            .scalar_subquery()
        )
        async with self._database.read_transaction() as session:
            workflows = (
                await session.scalars(
                    sa.select(WorkflowRow.id).where(WorkflowRow.id.in_(workflow_ids))
                )
            ).all()
            jobs = (
                await session.scalars(
                    sa.select(WorkflowJobRow)
                    .where(WorkflowJobRow.workflow_id.in_(workflow_ids))
                    .order_by(WorkflowJobRow.created_at.desc(), WorkflowJobRow.id.desc())
                )
            ).all()
            job_runs = (
                await session.scalars(
                    sa.select(WorkflowJobRunRow)
                    .where(WorkflowJobRunRow.workflow_id.in_(workflow_ids))
                    .order_by(
                        WorkflowJobRunRow.created_at.desc(),
                        WorkflowJobRunRow.id.desc(),
                    )
                )
            ).all()
            notifications = (
                await session.scalars(
                    sa.select(NotificationRow)
                    .where(NotificationRow.workflow_id.in_(workflow_ids))
                    .order_by(NotificationRow.created_at.desc(), NotificationRow.id.desc())
                )
            ).all()
            events = (
                await session.scalars(
                    sa.select(WorkflowEventRow)
                    .where(WorkflowEventRow.workflow_id.in_(workflow_ids))
                    .order_by(
                        WorkflowEventRow.occurred_at.desc(),
                        WorkflowEventRow.id.desc(),
                    )
                    .limit(event_limit)
                )
            ).all()
        return WorkflowOperationalSnapshot(
            captured_at=datetime.now(UTC),
            workflow_count=len(workflows),
            jobs=tuple(
                WorkflowOperationalJob(
                    id=job.id,
                    workflow_id=job.workflow_id,
                    kind=job.kind,
                    input=dict(job.input),
                    status=job.status,
                    attempts=job.attempts,
                    max_attempts=job.max_attempts,
                    created_at=job.created_at,
                )
                for job in jobs
            ),
            job_runs=tuple(
                WorkflowOperationalJobRun(
                    id=job_run.id,
                    job_id=job_run.job_id,
                    status=job_run.status,
                    worker_id=job_run.worker_id,
                    runtime_instance_id=job_run.runtime_instance_id,
                    created_at=job_run.created_at,
                    finished_at=job_run.finished_at,
                )
                for job_run in job_runs
            ),
            notifications=tuple(
                WorkflowOperationalNotification(
                    id=notification.id,
                    workflow_id=notification.workflow_id,
                    kind=notification.kind,
                    status=notification.status,
                    attempts=notification.attempts,
                    claimed_by=notification.claimed_by,
                    delivered_by=notification.delivered_by,
                    created_at=notification.created_at,
                    delivered_at=notification.delivered_at,
                )
                for notification in notifications
            ),
            events=tuple(
                WorkflowOperationalEvent(
                    id=event.id,
                    event_type=event.event_type,
                    workflow_id=event.workflow_id,
                    job_id=event.job_id,
                    run_id=event.run_id,
                    occurred_at=event.occurred_at,
                )
                for event in events
            ),
        )


__all__ = [
    "WorkflowOperationalEvent",
    "WorkflowOperationalJob",
    "WorkflowOperationalJobRun",
    "WorkflowOperationalNotification",
    "WorkflowOperationalSnapshot",
    "WorkflowOperationsProjection",
]
