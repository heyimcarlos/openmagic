"""Bounded operational reads for engineering views of Workflow activity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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
    output: dict[str, object] | None
    status: str
    attempts: int
    max_attempts: int
    revises_job_id: UUID | None
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
    cause_type: str
    cause_id: str
    data: dict[str, object]
    occurred_at: datetime


@dataclass(frozen=True)
class WorkflowOperationalTotals:
    job_status_counts: tuple[tuple[str, int], ...]
    run_status_counts: tuple[tuple[str, int], ...]
    notification_status_counts: tuple[tuple[str, int], ...]
    completed_last_minute: int
    oldest_queued_at: datetime | None


@dataclass(frozen=True)
class WorkflowOperationalSnapshot:
    captured_at: datetime
    workflow_count: int
    total_workflow_count: int
    workflow_limit: int
    totals: WorkflowOperationalTotals
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
        captured_at = datetime.now(UTC)
        all_workflow_ids = (
            sa.select(WorkflowEventRow.workflow_id)
            .where(
                WorkflowEventRow.event_type == "workflow_jobs_proposed",
                WorkflowEventRow.cause_id.startswith(cause_prefix),
            )
            .scalar_subquery()
        )
        async with self._database.read_transaction() as session:
            total_workflow_count = await session.scalar(
                sa.select(sa.func.count(sa.distinct(WorkflowEventRow.workflow_id))).where(
                    WorkflowEventRow.event_type == "workflow_jobs_proposed",
                    WorkflowEventRow.cause_id.startswith(cause_prefix),
                )
            )
            selected_workflow_ids: list[UUID] = []

            async def add_priority(
                query: sa.Select[tuple[UUID]],
                *,
                candidate_limit: int | None = None,
            ) -> None:
                remaining = workflow_limit - len(selected_workflow_ids)
                if remaining <= 0:
                    return
                candidates = (
                    await session.scalars(query.limit(candidate_limit or workflow_limit))
                ).all()
                for workflow_id in candidates:
                    if workflow_id not in selected_workflow_ids:
                        selected_workflow_ids.append(workflow_id)
                    if len(selected_workflow_ids) >= workflow_limit:
                        return

            await add_priority(
                sa.select(WorkflowJobRow.workflow_id)
                .where(
                    WorkflowJobRow.workflow_id.in_(all_workflow_ids),
                    WorkflowJobRow.status == "running",
                )
                .group_by(WorkflowJobRow.workflow_id)
                .order_by(sa.func.min(WorkflowJobRow.created_at), WorkflowJobRow.workflow_id)
            )
            await add_priority(
                sa.select(NotificationRow.workflow_id)
                .where(
                    NotificationRow.workflow_id.in_(all_workflow_ids),
                    NotificationRow.status == "delivering",
                )
                .group_by(NotificationRow.workflow_id)
                .order_by(sa.func.min(NotificationRow.created_at), NotificationRow.workflow_id)
            )
            await add_priority(
                sa.select(WorkflowJobRunRow.workflow_id)
                .where(
                    WorkflowJobRunRow.workflow_id.in_(all_workflow_ids),
                    WorkflowJobRunRow.finished_at >= captured_at - timedelta(seconds=10),
                )
                .group_by(WorkflowJobRunRow.workflow_id)
                .order_by(
                    sa.func.max(WorkflowJobRunRow.finished_at).desc(),
                    WorkflowJobRunRow.workflow_id,
                )
            )
            await add_priority(
                sa.select(NotificationRow.workflow_id)
                .where(
                    NotificationRow.workflow_id.in_(all_workflow_ids),
                    NotificationRow.delivered_at >= captured_at - timedelta(seconds=10),
                )
                .group_by(NotificationRow.workflow_id)
                .order_by(
                    sa.func.max(NotificationRow.delivered_at).desc(),
                    NotificationRow.workflow_id,
                )
            )
            await add_priority(
                sa.select(WorkflowEventRow.workflow_id)
                .where(
                    WorkflowEventRow.workflow_id.in_(all_workflow_ids),
                    WorkflowEventRow.event_type == "approval_presentation_committed",
                )
                .order_by(WorkflowEventRow.occurred_at.desc(), WorkflowEventRow.id.desc()),
                candidate_limit=5,
            )
            await add_priority(
                sa.select(WorkflowJobRow.workflow_id)
                .where(
                    WorkflowJobRow.workflow_id.in_(all_workflow_ids),
                    WorkflowJobRow.status == "queued",
                )
                .group_by(WorkflowJobRow.workflow_id)
                .order_by(sa.func.min(WorkflowJobRow.created_at), WorkflowJobRow.workflow_id)
            )
            await add_priority(
                sa.select(NotificationRow.workflow_id)
                .where(
                    NotificationRow.workflow_id.in_(all_workflow_ids),
                    NotificationRow.status == "queued",
                )
                .group_by(NotificationRow.workflow_id)
                .order_by(sa.func.min(NotificationRow.created_at), NotificationRow.workflow_id)
            )
            await add_priority(
                sa.select(NotificationRow.workflow_id)
                .where(
                    NotificationRow.workflow_id.in_(all_workflow_ids),
                    NotificationRow.status == "delivered",
                )
                .group_by(NotificationRow.workflow_id)
                .order_by(
                    sa.func.max(NotificationRow.delivered_at).desc(),
                    NotificationRow.workflow_id,
                )
            )
            await add_priority(
                sa.select(WorkflowEventRow.workflow_id)
                .where(
                    WorkflowEventRow.event_type == "workflow_jobs_proposed",
                    WorkflowEventRow.cause_id.startswith(cause_prefix),
                )
                .order_by(WorkflowEventRow.occurred_at.desc(), WorkflowEventRow.id.desc())
            )

            job_status_counts = tuple(
                (status, count)
                for status, count in (
                    await session.execute(
                        sa.select(WorkflowJobRow.status, sa.func.count())
                        .where(WorkflowJobRow.workflow_id.in_(all_workflow_ids))
                        .group_by(WorkflowJobRow.status)
                        .order_by(WorkflowJobRow.status)
                    )
                ).all()
            )
            run_status_counts = tuple(
                (status, count)
                for status, count in (
                    await session.execute(
                        sa.select(WorkflowJobRunRow.status, sa.func.count())
                        .where(WorkflowJobRunRow.workflow_id.in_(all_workflow_ids))
                        .group_by(WorkflowJobRunRow.status)
                        .order_by(WorkflowJobRunRow.status)
                    )
                ).all()
            )
            notification_status_counts = tuple(
                (status, count)
                for status, count in (
                    await session.execute(
                        sa.select(NotificationRow.status, sa.func.count())
                        .where(NotificationRow.workflow_id.in_(all_workflow_ids))
                        .group_by(NotificationRow.status)
                        .order_by(NotificationRow.status)
                    )
                ).all()
            )
            completed_last_minute = await session.scalar(
                sa.select(sa.func.count())
                .select_from(WorkflowJobRunRow)
                .where(
                    WorkflowJobRunRow.workflow_id.in_(all_workflow_ids),
                    WorkflowJobRunRow.status == "succeeded",
                    WorkflowJobRunRow.finished_at >= captured_at - timedelta(minutes=1),
                )
            )
            oldest_queued_at = await session.scalar(
                sa.select(sa.func.min(WorkflowJobRow.created_at)).where(
                    WorkflowJobRow.workflow_id.in_(all_workflow_ids),
                    WorkflowJobRow.status == "queued",
                )
            )
            workflows = (
                await session.scalars(
                    sa.select(WorkflowRow.id).where(WorkflowRow.id.in_(selected_workflow_ids))
                )
            ).all()
            jobs = (
                await session.scalars(
                    sa.select(WorkflowJobRow)
                    .where(WorkflowJobRow.workflow_id.in_(selected_workflow_ids))
                    .order_by(WorkflowJobRow.created_at.desc(), WorkflowJobRow.id.desc())
                )
            ).all()
            job_runs = (
                await session.scalars(
                    sa.select(WorkflowJobRunRow)
                    .where(WorkflowJobRunRow.workflow_id.in_(selected_workflow_ids))
                    .order_by(
                        WorkflowJobRunRow.created_at.desc(),
                        WorkflowJobRunRow.id.desc(),
                    )
                )
            ).all()
            notifications = (
                await session.scalars(
                    sa.select(NotificationRow)
                    .where(NotificationRow.workflow_id.in_(selected_workflow_ids))
                    .order_by(NotificationRow.created_at.desc(), NotificationRow.id.desc())
                )
            ).all()
            events = (
                await session.scalars(
                    sa.select(WorkflowEventRow)
                    .where(WorkflowEventRow.workflow_id.in_(selected_workflow_ids))
                    .order_by(
                        WorkflowEventRow.occurred_at.desc(),
                        WorkflowEventRow.id.desc(),
                    )
                    .limit(event_limit)
                )
            ).all()
        return WorkflowOperationalSnapshot(
            captured_at=captured_at,
            workflow_count=len(workflows),
            total_workflow_count=total_workflow_count or 0,
            workflow_limit=workflow_limit,
            totals=WorkflowOperationalTotals(
                job_status_counts=job_status_counts,
                run_status_counts=run_status_counts,
                notification_status_counts=notification_status_counts,
                completed_last_minute=completed_last_minute or 0,
                oldest_queued_at=oldest_queued_at,
            ),
            jobs=tuple(
                WorkflowOperationalJob(
                    id=job.id,
                    workflow_id=job.workflow_id,
                    kind=job.kind,
                    input=dict(job.input),
                    output=dict(job.output) if job.output is not None else None,
                    status=job.status,
                    attempts=job.attempts,
                    max_attempts=job.max_attempts,
                    revises_job_id=job.revises_job_id,
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
                    cause_type=event.cause_type,
                    cause_id=event.cause_id,
                    data=dict(event.data),
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
    "WorkflowOperationalTotals",
    "WorkflowOperationsProjection",
]
