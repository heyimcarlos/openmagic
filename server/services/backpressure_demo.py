"""Local-only load controls and read projection for the Workflow demo."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timedelta
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from server.config import Settings
from server.workflows import (
    DRAFT_RENEWAL_EMAIL_KIND,
    GMAIL_SEND_EMAIL_KIND,
    RENEWAL_OUTREACH_KIND,
    CreateWorkflowCommand,
    RecordInteractionCauseCommand,
    StaticWorkflowAuthority,
    WorkflowCommandContext,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowJobProposal,
    WorkflowOperationalJob,
    WorkflowOperationsProjection,
    WorkflowProposal,
    default_workflow_registry,
)

_CAUSE_PREFIX = "demo-backpressure:"

JobStatus = Literal["waiting", "queued", "running", "succeeded", "failed", "cancelled"]
RunStatus = Literal["running", "succeeded", "failed", "cancelled", "abandoned"]
NotificationStatus = Literal["queued", "delivering", "delivered", "failed"]


class DemoModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BackpressureWorkerView(DemoModel):
    configured_job_concurrency: int = 1
    configured_notification_concurrency: int = 1
    claim_policy: str = "one eligible Job per tick"
    liveness: Literal["not_persisted"] = "not_persisted"


class BackpressureCounts(DemoModel):
    workflows: int
    jobs: int
    waiting: int
    queued: int
    running: int
    succeeded: int
    failed: int
    cancelled: int
    runs_running: int
    runs_succeeded: int
    runs_failed: int
    notifications_queued: int
    notifications_delivering: int
    notifications_delivered: int
    notifications_failed: int
    completed_last_minute: int
    oldest_queued_seconds: int


class BackpressureJobView(DemoModel):
    id: UUID
    workflow_id: UUID
    kind: str
    label: str
    task_summary: str
    status: JobStatus
    attempts: int
    max_attempts: int
    created_at: datetime


class BackpressureRunView(DemoModel):
    id: UUID
    job_id: UUID
    status: RunStatus
    worker_id: str
    runtime_instance_id: UUID | None
    created_at: datetime
    finished_at: datetime | None


class BackpressureNotificationView(DemoModel):
    id: UUID
    workflow_id: UUID
    kind: str
    status: NotificationStatus
    attempts: int
    claimed_by: str | None
    delivered_by: str | None
    created_at: datetime
    delivered_at: datetime | None


class BackpressureActivityView(DemoModel):
    id: str
    type: str
    source: Literal["workflow_event", "notification"]
    workflow_id: UUID
    job_id: UUID | None = None
    run_id: UUID | None = None
    occurred_at: datetime


class BackpressureSnapshot(DemoModel):
    captured_at: datetime
    worker: BackpressureWorkerView = Field(default_factory=BackpressureWorkerView)
    counts: BackpressureCounts
    jobs: tuple[BackpressureJobView, ...]
    runs: tuple[BackpressureRunView, ...]
    notifications: tuple[BackpressureNotificationView, ...]
    activity: tuple[BackpressureActivityView, ...]


class BackpressureDemoService:
    """Create safe renewal load through the real Control Plane and project its state."""

    def __init__(self, settings: Settings) -> None:
        if not settings.database_url:
            raise ValueError("Workflow database configuration is incomplete")
        if not settings.workflow_broker_party_id or not settings.workflow_organization_party_id:
            raise ValueError("Workflow demo identity configuration is incomplete")
        self._settings = settings
        self._broker_party_id = UUID(settings.workflow_broker_party_id)
        self._organization_party_id = UUID(settings.workflow_organization_party_id)
        self._database = WorkflowDatabase(settings.database_url)
        self._projection = WorkflowOperationsProjection(self._database)
        self._control_plane = WorkflowControlPlane(
            database=self._database,
            registry=default_workflow_registry(),
            authority=StaticWorkflowAuthority(
                grants={
                    (
                        self._broker_party_id,
                        self._organization_party_id,
                        RENEWAL_OUTREACH_KIND,
                    )
                }
            ),
        )

    async def enqueue_jobs(self, job_count: int) -> BackpressureSnapshot:
        """Create complete two-Job renewal graphs through atomic Control Plane commands."""

        if job_count < 2 or job_count > 40 or job_count % 2 != 0:
            raise ValueError("job_count must be an even number from 2 through 40")
        await asyncio.gather(
            *(self._create_workflow(index) for index in range(1, job_count // 2 + 1))
        )
        return await self.snapshot()

    async def _create_workflow(self, index: int) -> None:
        request_id = uuid4()
        cause_id = f"{_CAUSE_PREFIX}{request_id}"
        context = WorkflowCommandContext(
            actor_party_id=self._broker_party_id,
            organization_party_id=self._organization_party_id,
            cause_type="ui_action",
            cause_id=cause_id,
        )
        await self._control_plane.record_interaction_cause(
            RecordInteractionCauseCommand(
                context=context,
                content=f"Queue backpressure demo Workflow {index}",
            )
        )
        await self._control_plane.create_workflow(
            CreateWorkflowCommand(
                context=context,
                proposal=WorkflowProposal(
                    kind=RENEWAL_OUTREACH_KIND,
                    objective=f"Backpressure demo renewal {request_id.hex[:8]}",
                    input={"renewal_period": "2026"},
                    jobs=(
                        WorkflowJobProposal(
                            key="draft",
                            kind=DRAFT_RENEWAL_EMAIL_KIND,
                            input={
                                "recipient_name": f"Demo Policyholder {index}",
                                "renewal_period": "2026",
                            },
                        ),
                        WorkflowJobProposal(
                            key="send",
                            kind=GMAIL_SEND_EMAIL_KIND,
                            input={
                                "sender_mailbox": self._settings.demo_broker_email,
                                "to": [self._settings.demo_policyholder_email],
                                "subject": {"job_output": "draft", "field": "subject"},
                                "body": {"job_output": "draft", "field": "body"},
                            },
                            depends_on=("draft",),
                        ),
                    ),
                ),
            )
        )

    async def snapshot(self) -> BackpressureSnapshot:
        projected = await self._projection.project(cause_prefix=_CAUSE_PREFIX)
        now = projected.captured_at
        jobs = projected.jobs
        runs = projected.job_runs
        notifications = projected.notifications
        job_counts = Counter(job.status for job in jobs)
        run_counts = Counter(run.status for run in runs)
        notification_counts = Counter(item.status for item in notifications)
        queued_at = [job.created_at for job in jobs if job.status == "queued"]
        oldest_queued_seconds = (
            max(0, int((now - min(queued_at)).total_seconds())) if queued_at else 0
        )
        activity = [
            BackpressureActivityView(
                id=str(event.id),
                type=event.event_type,
                source="workflow_event",
                workflow_id=event.workflow_id,
                job_id=event.job_id,
                run_id=event.run_id,
                occurred_at=event.occurred_at,
            )
            for event in projected.events
        ]
        activity.extend(
            BackpressureActivityView(
                id=f"notification:{item.id}:{item.status}",
                type=f"notification_{item.status}",
                source="notification",
                workflow_id=item.workflow_id,
                occurred_at=item.delivered_at or item.created_at,
            )
            for item in notifications
        )
        activity.sort(
            key=lambda item: (item.occurred_at, item.source == "notification", item.id),
            reverse=True,
        )
        return BackpressureSnapshot(
            captured_at=now,
            counts=BackpressureCounts(
                workflows=projected.workflow_count,
                jobs=len(jobs),
                waiting=job_counts["waiting"],
                queued=job_counts["queued"],
                running=job_counts["running"],
                succeeded=job_counts["succeeded"],
                failed=job_counts["failed"],
                cancelled=job_counts["cancelled"],
                runs_running=run_counts["running"],
                runs_succeeded=run_counts["succeeded"],
                runs_failed=run_counts["failed"],
                notifications_queued=notification_counts["queued"],
                notifications_delivering=notification_counts["delivering"],
                notifications_delivered=notification_counts["delivered"],
                notifications_failed=notification_counts["failed"],
                completed_last_minute=sum(
                    1
                    for run in runs
                    if run.status == "succeeded"
                    and run.finished_at is not None
                    and run.finished_at >= now - timedelta(minutes=1)
                ),
                oldest_queued_seconds=oldest_queued_seconds,
            ),
            jobs=tuple(
                BackpressureJobView(
                    id=job.id,
                    workflow_id=job.workflow_id,
                    kind=job.kind,
                    label=(
                        "Draft renewal email"
                        if job.kind == DRAFT_RENEWAL_EMAIL_KIND
                        else "Send approved email"
                    ),
                    task_summary=self._task_summary(job),
                    status=cast(JobStatus, job.status),
                    attempts=job.attempts,
                    max_attempts=job.max_attempts,
                    created_at=job.created_at,
                )
                for job in jobs
            ),
            runs=tuple(
                BackpressureRunView(
                    id=run.id,
                    job_id=run.job_id,
                    status=cast(RunStatus, run.status),
                    worker_id=run.worker_id,
                    runtime_instance_id=run.runtime_instance_id,
                    created_at=run.created_at,
                    finished_at=run.finished_at,
                )
                for run in runs
            ),
            notifications=tuple(
                BackpressureNotificationView(
                    id=item.id,
                    workflow_id=item.workflow_id,
                    kind=item.kind,
                    status=cast(NotificationStatus, item.status),
                    attempts=item.attempts,
                    claimed_by=item.claimed_by,
                    delivered_by=item.delivered_by,
                    created_at=item.created_at,
                    delivered_at=item.delivered_at,
                )
                for item in notifications
            ),
            activity=tuple(activity[:80]),
        )

    async def dispose(self) -> None:
        await self._database.dispose()

    @staticmethod
    def _task_summary(job: WorkflowOperationalJob) -> str:
        if job.kind == DRAFT_RENEWAL_EMAIL_KIND:
            recipient = job.input.get("recipient_name")
            period = job.input.get("renewal_period")
            if isinstance(recipient, str) and isinstance(period, str):
                return f"Draft the {period} renewal for {recipient}"
            return "Draft one renewal email from bounded Workflow input"
        return "Wait for exact approval, then send the frozen Draft Revision"


__all__ = [
    "BackpressureDemoService",
    "BackpressureSnapshot",
]
