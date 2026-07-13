"""Local-only load controls and read projection for the Workflow demo."""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import datetime
from functools import lru_cache
from statistics import median
from typing import Literal, cast
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from server.config import Settings
from server.workflows import (
    CLAIM_INTAKE_REVIEW_KIND,
    DRAFT_RENEWAL_EMAIL_KIND,
    GMAIL_SEND_EMAIL_KIND,
    POLICY_COVERAGE_REVIEW_KIND,
    RENEWAL_OUTREACH_KIND,
    CreateWorkflowCommand,
    RecordInteractionCauseCommand,
    StaticWorkflowAuthority,
    WorkflowCommandContext,
    WorkflowControlPlane,
    WorkflowDatabase,
    WorkflowOperationalEvent,
    WorkflowOperationalJob,
    WorkflowOperationsProjection,
    default_workflow_registry,
)

from .backpressure_workload import (
    MIXED_WORKFLOW_SCENARIOS,
    DemoWorkflowScenario,
    DemoWorkflowSelection,
    build_demo_workflow_proposal,
)

_CAUSE_PREFIX = "demo-backpressure:"

JobStatus = Literal["waiting", "queued", "running", "succeeded", "failed", "cancelled"]
RunStatus = Literal["running", "succeeded", "failed", "cancelled", "abandoned"]
NotificationStatus = Literal["queued", "delivering", "delivered", "failed"]


class DemoModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BackpressureWorkerView(DemoModel):
    configured_job_concurrency: int
    configured_notification_concurrency: int
    job_worker_ids: tuple[str, ...]
    max_job_worker_capacity: int
    process_model: Literal["in_process_async_workers"] = "in_process_async_workers"
    claim_policy: str = "one eligible Job per Worker per tick"
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


class BackpressureScope(DemoModel):
    visible_workflows: int
    total_workflows: int
    workflow_limit: int
    truncated: bool


class BackpressureLatencyView(DemoModel):
    queue_claim_p50_ms: int | None
    execution_p50_ms: int | None
    notification_delivery_p50_ms: int | None
    end_to_end_p50_ms: int | None


class BackpressureJobView(DemoModel):
    id: UUID
    workflow_id: UUID
    kind: str
    label: str
    task_summary: str
    status: JobStatus
    attempts: int
    max_attempts: int
    revision: int
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
    interaction_runtime_instance_id: UUID | None = None
    created_at: datetime
    delivered_at: datetime | None


class BackpressureApprovalView(DemoModel):
    workflow_id: UUID
    job_id: UUID
    draft_revision_id: UUID
    revision: int
    sender: str
    to: tuple[str, ...]
    cc: tuple[str, ...] = ()
    bcc: tuple[str, ...] = ()
    subject: str
    body: str


class BackpressureActivityView(DemoModel):
    id: str
    type: str
    source: Literal["workflow_event", "notification"]
    workflow_id: UUID
    job_id: UUID | None = None
    run_id: UUID | None = None
    boundary: str | None = None
    occurred_at: datetime


class BackpressureSnapshot(DemoModel):
    captured_at: datetime
    worker: BackpressureWorkerView
    scope: BackpressureScope
    latency: BackpressureLatencyView
    counts: BackpressureCounts
    jobs: tuple[BackpressureJobView, ...]
    runs: tuple[BackpressureRunView, ...]
    notifications: tuple[BackpressureNotificationView, ...]
    approval_requests: tuple[BackpressureApprovalView, ...]
    activity: tuple[BackpressureActivityView, ...]


class BackpressureDemoService:
    """Create safe renewal load through the real Control Plane and project its state."""

    def __init__(
        self,
        settings: Settings,
        *,
        worker_ids: Callable[[], tuple[str, ...]] | None = None,
        max_worker_capacity: Callable[[], int] | None = None,
    ) -> None:
        if not settings.database_url:
            raise ValueError("Workflow database configuration is incomplete")
        if not settings.workflow_broker_party_id or not settings.workflow_organization_party_id:
            raise ValueError("Workflow demo identity configuration is incomplete")
        self._settings = settings
        self._worker_ids = worker_ids or (lambda: ())
        self._max_worker_capacity = max_worker_capacity or (lambda: 0)
        self._broker_party_id = UUID(settings.workflow_broker_party_id)
        self._organization_party_id = UUID(settings.workflow_organization_party_id)
        self._database = WorkflowDatabase(settings.database_url)
        self._projection = WorkflowOperationsProjection(self._database)
        self._control_plane = WorkflowControlPlane(
            database=self._database,
            registry=default_workflow_registry(),
            authority=StaticWorkflowAuthority(
                grants={
                    (self._broker_party_id, self._organization_party_id, workflow_kind)
                    for workflow_kind in (
                        RENEWAL_OUTREACH_KIND,
                        CLAIM_INTAKE_REVIEW_KIND,
                        POLICY_COVERAGE_REVIEW_KIND,
                    )
                }
            ),
        )

    async def enqueue_workflows(
        self,
        workflow_count: int,
        scenario: DemoWorkflowSelection = "mixed",
    ) -> BackpressureSnapshot:
        """Create varied, complete Workflow graphs through Control Plane commands."""

        if workflow_count < 1 or workflow_count > 50:
            raise ValueError("workflow_count must be from 1 through 50")
        if scenario not in {"mixed", "renewal", "claim", "policy"}:
            raise ValueError("scenario is not recognized")
        start = uuid4().int % len(MIXED_WORKFLOW_SCENARIOS)
        scenarios: tuple[DemoWorkflowScenario, ...] = tuple(
            MIXED_WORKFLOW_SCENARIOS[
                (start + index) % len(MIXED_WORKFLOW_SCENARIOS)
            ]
            if scenario == "mixed"
            else scenario
            for index in range(workflow_count)
        )
        await asyncio.gather(
            *(
                self._create_workflow(index, workflow_scenario)
                for index, workflow_scenario in enumerate(scenarios, start=1)
            )
        )
        return await self.snapshot()

    async def _create_workflow(
        self,
        index: int,
        scenario: DemoWorkflowScenario,
    ) -> None:
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
                content=f"Queue {scenario} backpressure demo Workflow {index}",
            )
        )
        await self._control_plane.create_workflow(
            CreateWorkflowCommand(
                context=context,
                proposal=build_demo_workflow_proposal(
                    scenario,
                    index=index,
                    request_id=request_id,
                    broker_email=self._settings.demo_broker_email,
                    policyholder_email=self._settings.demo_policyholder_email,
                ),
            )
        )

    async def snapshot(self) -> BackpressureSnapshot:
        projected = await self._projection.project(cause_prefix=_CAUSE_PREFIX)
        now = projected.captured_at
        jobs = projected.jobs
        runs = projected.job_runs
        notifications = projected.notifications
        job_counts = Counter(dict(projected.totals.job_status_counts))
        run_counts = Counter(dict(projected.totals.run_status_counts))
        notification_counts = Counter(dict(projected.totals.notification_status_counts))
        jobs_by_id = {job.id: job for job in jobs}
        revision_by_job_id = self._job_revisions(jobs)
        approved_job_ids: set[UUID] = {
            event.job_id
            for event in projected.events
            if event.event_type == "approval_granted" and event.job_id is not None
        }
        presentation_by_job = {
            event.job_id: event
            for event in projected.events
            if event.event_type == "approval_presentation_committed"
            and event.job_id is not None
        }
        interaction_runtime_by_notification: dict[UUID, UUID] = {}
        for event in presentation_by_job.values():
            runtime_id = event.data.get("interaction_runtime_instance_id")
            if event.cause_type != "notification" or not isinstance(runtime_id, str):
                continue
            try:
                interaction_runtime_by_notification[UUID(event.cause_id)] = UUID(runtime_id)
            except ValueError:
                continue
        approval_requests = self._approval_requests(
            jobs,
            presentation_by_job,
            approved_job_ids,
        )
        first_job_at_by_workflow: dict[UUID, datetime] = {}
        for job in jobs:
            first_job_at_by_workflow[job.workflow_id] = min(
                job.created_at,
                first_job_at_by_workflow.get(job.workflow_id, job.created_at),
            )
        oldest_queued_seconds = (
            max(0, int((now - projected.totals.oldest_queued_at).total_seconds()))
            if projected.totals.oldest_queued_at is not None
            else 0
        )
        activity = [
            BackpressureActivityView(
                id=str(event.id),
                type=event.event_type,
                source="workflow_event",
                workflow_id=event.workflow_id,
                job_id=event.job_id,
                run_id=event.run_id,
                boundary=self._activity_boundary(event),
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
        worker_ids = self._worker_ids()
        return BackpressureSnapshot(
            captured_at=now,
            worker=BackpressureWorkerView(
                configured_job_concurrency=len(worker_ids),
                configured_notification_concurrency=1,
                job_worker_ids=worker_ids,
                max_job_worker_capacity=self._max_worker_capacity(),
            ),
            scope=BackpressureScope(
                visible_workflows=projected.workflow_count,
                total_workflows=projected.total_workflow_count,
                workflow_limit=projected.workflow_limit,
                truncated=projected.total_workflow_count > projected.workflow_count,
            ),
            latency=BackpressureLatencyView(
                queue_claim_p50_ms=self._p50(
                    self._elapsed_ms(run.created_at, jobs_by_id[run.job_id].created_at)
                    for run in runs
                    if run.job_id in jobs_by_id
                ),
                execution_p50_ms=self._p50(
                    self._elapsed_ms(run.finished_at, run.created_at)
                    for run in runs
                    if run.finished_at is not None
                ),
                notification_delivery_p50_ms=self._p50(
                    self._elapsed_ms(item.delivered_at, item.created_at)
                    for item in notifications
                    if item.delivered_at is not None
                ),
                end_to_end_p50_ms=self._p50(
                    self._elapsed_ms(
                        item.delivered_at,
                        first_job_at_by_workflow[item.workflow_id],
                    )
                    for item in notifications
                    if item.delivered_at is not None
                    and item.workflow_id in first_job_at_by_workflow
                ),
            ),
            counts=BackpressureCounts(
                workflows=projected.total_workflow_count,
                jobs=sum(job_counts.values()),
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
                completed_last_minute=projected.totals.completed_last_minute,
                oldest_queued_seconds=oldest_queued_seconds,
            ),
            jobs=tuple(
                BackpressureJobView(
                    id=job.id,
                    workflow_id=job.workflow_id,
                    kind=job.kind,
                    label=self._job_label(job),
                    task_summary=self._task_summary(job),
                    status=cast(JobStatus, job.status),
                    attempts=job.attempts,
                    max_attempts=job.max_attempts,
                    revision=revision_by_job_id[job.id],
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
                    interaction_runtime_instance_id=(
                        interaction_runtime_by_notification.get(item.id)
                    ),
                    created_at=item.created_at,
                    delivered_at=item.delivered_at,
                )
                for item in notifications
            ),
            approval_requests=approval_requests,
            activity=tuple(activity[:80]),
        )

    async def dispose(self) -> None:
        await self._database.dispose()

    @staticmethod
    def _job_revisions(
        jobs: tuple[WorkflowOperationalJob, ...],
    ) -> dict[UUID, int]:
        jobs_by_id = {job.id: job for job in jobs}
        revisions: dict[UUID, int] = {}

        def resolve(job: WorkflowOperationalJob, seen: frozenset[UUID] = frozenset()) -> int:
            if job.id in revisions:
                return revisions[job.id]
            if job.id in seen or job.revises_job_id is None:
                revisions[job.id] = 1
                return 1
            parent = jobs_by_id.get(job.revises_job_id)
            if parent is None or parent.kind != job.kind:
                revisions[job.id] = 1
                return 1
            revisions[job.id] = resolve(parent, seen | {job.id}) + 1
            return revisions[job.id]

        for job in jobs:
            resolve(job)
        return revisions

    @staticmethod
    def _activity_boundary(event: WorkflowOperationalEvent) -> str | None:
        if event.event_type == "workflow_work_revised":
            return (
                "revise_and_approve_email"
                if event.data.get("approved") is True
                else "revise_workflow_work"
            )
        if event.event_type == "workflow_jobs_proposed":
            if "source_workflow_id" in event.data:
                return "propose_workflow"
            if event.cause_id.startswith(_CAUSE_PREFIX):
                return "create_workflow"
            return "propose_workflow_work"
        if event.event_type == "approval_granted":
            return "approve_job"
        return None

    @staticmethod
    def _job_label(job: WorkflowOperationalJob) -> str:
        if job.kind == DRAFT_RENEWAL_EMAIL_KIND:
            return "Draft renewal email"
        if job.kind == GMAIL_SEND_EMAIL_KIND:
            return "Send approved email"
        task_type = job.input.get("task_type")
        return {
            "extract_claim_facts": "Extract claim facts",
            "triage_claim": "Assess claim routing",
            "review_policy_coverage": "Review policy coverage",
        }.get(str(task_type), "Execute insurance task")

    @staticmethod
    def _task_summary(job: WorkflowOperationalJob) -> str:
        if job.kind == DRAFT_RENEWAL_EMAIL_KIND:
            recipient = job.input.get("recipient_name")
            period = job.input.get("renewal_period")
            if isinstance(recipient, str) and isinstance(period, str):
                return f"Draft the {period} renewal for {recipient}"
            return "Draft one renewal email from bounded Workflow input"
        if job.kind == GMAIL_SEND_EMAIL_KIND:
            return "Wait for exact approval, then send the frozen Draft Revision"
        subject = job.input.get("subject")
        task_type = job.input.get("task_type")
        action = {
            "extract_claim_facts": "Extract reported facts for",
            "triage_claim": "Assess the next review queue for",
            "review_policy_coverage": "Review open coverage questions for",
        }.get(str(task_type), "Complete bounded work for")
        return f"{action} {subject}" if isinstance(subject, str) else action

    @staticmethod
    def _approval_requests(
        jobs: tuple[WorkflowOperationalJob, ...],
        presentation_by_job: dict[UUID, WorkflowOperationalEvent],
        approved_job_ids: set[UUID],
    ) -> tuple[BackpressureApprovalView, ...]:
        jobs_by_id = {job.id: job for job in jobs}
        drafts_by_workflow: dict[UUID, list[WorkflowOperationalJob]] = {}
        for job in jobs:
            if job.kind == DRAFT_RENEWAL_EMAIL_KIND:
                drafts_by_workflow.setdefault(job.workflow_id, []).append(job)
        for drafts in drafts_by_workflow.values():
            drafts.sort(key=lambda item: (item.created_at, item.id))

        collected: list[tuple[datetime, BackpressureApprovalView]] = []
        for send in jobs:
            presentation = presentation_by_job.get(send.id)
            if (
                send.kind != GMAIL_SEND_EMAIL_KIND
                or send.status != "waiting"
                or send.id in approved_job_ids
                or presentation is None
            ):
                continue
            draft_value = presentation.data.get("draft_job_id")
            try:
                draft_id = UUID(str(draft_value))
            except (TypeError, ValueError):
                continue
            draft = jobs_by_id.get(draft_id)
            if draft is None or draft.output is None or draft.status != "succeeded":
                continue
            sender = send.input.get("sender_mailbox")
            to = send.input.get("to")
            cc = send.input.get("cc", ())
            bcc = send.input.get("bcc", ())
            subject = BackpressureDemoService._resolved_input_value(send, draft, "subject")
            body = BackpressureDemoService._resolved_input_value(send, draft, "body")
            if (
                not isinstance(sender, str)
                or not isinstance(to, list | tuple)
                or not to
                or not all(isinstance(address, str) for address in to)
                or not isinstance(cc, list | tuple)
                or not all(isinstance(address, str) for address in cc)
                or not isinstance(bcc, list | tuple)
                or not all(isinstance(address, str) for address in bcc)
                or not isinstance(subject, str)
                or not subject
                or not isinstance(body, str)
                or not body
            ):
                continue
            drafts = drafts_by_workflow.get(send.workflow_id, [])
            try:
                revision = drafts.index(draft) + 1
            except ValueError:
                continue
            collected.append(
                (
                    presentation.occurred_at,
                    BackpressureApprovalView(
                        workflow_id=send.workflow_id,
                        job_id=send.id,
                        draft_revision_id=draft.id,
                        revision=revision,
                        sender=sender,
                        to=tuple(cast(str, address) for address in to),
                        cc=tuple(cast(str, address) for address in cc),
                        bcc=tuple(cast(str, address) for address in bcc),
                        subject=subject,
                        body=body,
                    ),
                )
            )
        collected.sort(key=lambda item: item[0], reverse=True)
        return tuple(item for _, item in collected[:5])

    @staticmethod
    def _resolved_input_value(
        send: WorkflowOperationalJob,
        draft: WorkflowOperationalJob,
        field: str,
    ) -> object:
        if draft.output is None:
            return None
        value = send.input.get(field)
        if not isinstance(value, dict) or set(value) != {"job_output", "field"}:
            return value
        if str(value.get("job_output")) != str(draft.id):
            return None
        source_field = value.get("field")
        return draft.output.get(source_field) if isinstance(source_field, str) else None

    @staticmethod
    def _p50(values: Iterable[int]) -> int | None:
        collected = tuple(values)
        return int(median(collected)) if collected else None

    @staticmethod
    def _elapsed_ms(later: datetime, earlier: datetime) -> int:
        return max(0, int((later - earlier).total_seconds() * 1000))


_services: set[BackpressureDemoService] = set()


@lru_cache(maxsize=4)
def get_backpressure_demo_service(
    database_url: str,
    broker_party_id: str,
    organization_party_id: str,
    broker_email: str,
    policyholder_email: str,
) -> BackpressureDemoService:
    """Return one cached demo service for a complete Workflow configuration."""

    from server.services.workflow_runtime import get_workflow_runtime_service

    runtime = get_workflow_runtime_service()
    service = BackpressureDemoService(
        Settings(
            database_url=database_url,
            workflow_broker_party_id=broker_party_id,
            workflow_organization_party_id=organization_party_id,
            demo_broker_email=broker_email,
            demo_policyholder_email=policyholder_email,
            interaction_mode="workflow",
        ),
        worker_ids=lambda: runtime.job_worker_ids,
        max_worker_capacity=lambda: runtime.max_job_worker_capacity,
    )
    _services.add(service)
    return service


async def dispose_backpressure_demo_services() -> None:
    """Dispose every cached demo database engine during application shutdown."""

    for service in tuple(_services):
        await service.dispose()
    _services.clear()
    get_backpressure_demo_service.cache_clear()


__all__ = [
    "BackpressureDemoService",
    "BackpressureSnapshot",
    "dispose_backpressure_demo_services",
    "get_backpressure_demo_service",
]
