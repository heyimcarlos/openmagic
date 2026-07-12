"""Typed inputs and read projections for the Workflow Control Plane."""

from __future__ import annotations

from datetime import datetime
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
