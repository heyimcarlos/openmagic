"""Typed inputs and read projections for the Workflow Control Plane."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WorkflowContract(BaseModel):
    """Reject unknown fields and keep accepted command data immutable."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class WorkflowCommandContext(WorkflowContract):
    """Trusted identity and Cause data supplied by the application boundary."""

    actor_party_id: UUID
    organization_party_id: UUID
    cause_type: Literal["message", "ui_action"]
    cause_id: str = Field(min_length=1, max_length=255)


class WorkflowJobProposal(WorkflowContract):
    """One model-proposable Job definition without execution configuration."""

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    kind: str = Field(min_length=1, max_length=255)
    input: dict[str, Any]
    depends_on: tuple[str, ...] = ()


class WorkflowProposal(WorkflowContract):
    """A complete typed Workflow graph proposed in one command."""

    kind: str = Field(min_length=1, max_length=255)
    objective: str = Field(min_length=1, max_length=500)
    input: dict[str, Any]
    jobs: tuple[WorkflowJobProposal, ...] = Field(min_length=1)


class CreateWorkflowCommand(WorkflowContract):
    """Create one new Workflow after deterministic validation and authority."""

    context: WorkflowCommandContext
    proposal: WorkflowProposal


class ProposeWorkflowJobsCommand(WorkflowContract):
    """Append one complete typed Job graph to an existing Workflow."""

    context: WorkflowCommandContext
    workflow_id: UUID
    jobs: tuple[WorkflowJobProposal, ...] = Field(min_length=1)


class ClaimWorkflowJobCommand(WorkflowContract):
    """Claim at most one eligible Job through the Worker-only boundary."""

    worker_id: str = Field(min_length=1, max_length=255)
    application_build: str = Field(min_length=1, max_length=255)
    lease_duration: timedelta = Field(gt=timedelta(0), le=timedelta(minutes=30))
    executor_keys: tuple[str, ...] = Field(min_length=1)


class RunResult(WorkflowContract):
    """One immutable execution-attempt conclusion."""

    outcome: Literal["succeeded", "failed", "uncertain"]
    data: dict[str, Any] | None = None
    evidence: tuple[dict[str, Any], ...] = ()
    error: dict[str, Any] | None = None


class ReportRunResultCommand(WorkflowContract):
    """Commit one typed Run Result while the Run still has authority."""

    run_id: UUID
    result: RunResult


class WorkflowExecutionPacket(WorkflowContract):
    """Bounded Run context selected entirely by trusted application contracts."""

    workflow_id: UUID
    job_id: UUID
    run_id: UUID
    job_kind: str
    execution_strategy: str
    executor_key: str
    input: dict[str, Any]
    runtime_instance_id: UUID | None
    lease_expires_at: datetime


class CommittedRunResult(WorkflowContract):
    """Stable acknowledgement for an accepted or replayed result command."""

    workflow_id: UUID
    job_id: UUID
    run_id: UUID
    run_status: str
    job_status: str
    result: RunResult


class ClaimNotificationCommand(WorkflowContract):
    """Claim one due Notification for delivery."""

    worker_id: str = Field(min_length=1, max_length=255)
    lease_duration: timedelta = Field(gt=timedelta(0), le=timedelta(minutes=30))


class NotificationDeliveryPacket(WorkflowContract):
    """Stable identifiers passed to a fresh Interaction Agent runtime."""

    notification_id: UUID
    workflow_event_id: UUID
    workflow_id: UUID
    delivery_attempt: int


class AcknowledgeNotificationCommand(WorkflowContract):
    """Mark one claimed Notification delivered through the Worker boundary."""

    notification_id: UUID
    worker_id: str = Field(min_length=1, max_length=255)
    delivery_attempt: int = Field(gt=0)


class ReportNotificationFailureCommand(WorkflowContract):
    """Release one failed delivery attempt through its current lease."""

    notification_id: UUID
    worker_id: str = Field(min_length=1, max_length=255)
    delivery_attempt: int = Field(gt=0)
    error_code: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")


class NotificationPresentationContext(WorkflowContract):
    """Trusted destination resolved from stable Notification identifiers."""

    destination_party_id: UUID


class WorkflowTraceWorkflow(WorkflowContract):
    id: UUID
    kind: str
    objective: str
    status: str
    input: dict[str, Any]
    corrects_workflow_id: UUID | None
    created_at: datetime


class WorkflowTraceJob(WorkflowContract):
    id: UUID
    workflow_id: UUID
    kind: str
    status: str
    attempts: int
    max_attempts: int
    available_at: datetime
    input: dict[str, Any]
    output: dict[str, Any] | None
    revises_job_id: UUID | None
    depends_on_job_ids: tuple[UUID, ...]
    waiting_reasons: tuple[str, ...]
    created_at: datetime


class WorkflowTraceRun(WorkflowContract):
    id: UUID
    job_id: UUID
    status: str
    runtime_instance_id: UUID | None


class WorkflowTraceEvent(WorkflowContract):
    id: UUID
    workflow_id: UUID
    job_id: UUID | None
    run_id: UUID | None
    event_type: str
    actor_type: str
    actor_id: str
    cause_type: str
    cause_id: str
    data: dict[str, Any]
    occurred_at: datetime


class WorkflowTraceNotification(WorkflowContract):
    id: UUID
    workflow_event_id: UUID
    kind: str
    status: str


class WorkflowTrace(WorkflowContract):
    """Development evidence for one persisted Workflow aggregate."""

    workflow: WorkflowTraceWorkflow
    jobs: tuple[WorkflowTraceJob, ...]
    runs: tuple[WorkflowTraceRun, ...]
    events: tuple[WorkflowTraceEvent, ...]
    notifications: tuple[WorkflowTraceNotification, ...]
