"""Bounded model-facing contracts for Workflow search and packet retrieval."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from .contracts import WorkflowContract


class WorkflowInspectionContext(WorkflowContract):
    """Trusted Party context injected by the application boundary."""

    actor_party_id: UUID


class WorkflowSearchRequest(WorkflowContract):
    query: str = Field(default="", max_length=500)
    workflow_kind: str | None = Field(default=None, max_length=255)
    status: Literal["active", "completed", "cancelled"] | None = None
    participant: str | None = Field(default=None, min_length=1, max_length=200)
    organization: str | None = Field(default=None, min_length=1, max_length=200)
    renewal_period: str | None = Field(default=None, pattern=r"^[0-9]{4}$")
    cursor: str | None = Field(default=None, min_length=1, max_length=2000)
    limit: int = Field(default=5, ge=1, le=10)

    @model_validator(mode="after")
    def require_retrieval_signal(self) -> WorkflowSearchRequest:
        if not self.query.strip() and not any(
            (
                self.workflow_kind,
                self.status,
                self.participant,
                self.organization,
                self.renewal_period,
            )
        ):
            raise ValueError("search requires a query or structured filter")
        return self


class WorkflowParticipantSummary(WorkflowContract):
    party_id: UUID
    name: str
    roles: tuple[str, ...]


class WorkflowSearchResult(WorkflowContract):
    workflow_id: UUID
    objective: str
    workflow_kind: str
    status: str
    organization: str
    participants: tuple[WorkflowParticipantSummary, ...]
    renewal_period: str | None
    created_at: datetime
    match_reasons: tuple[str, ...]


class WorkflowFacetEntry(WorkflowContract):
    value: str
    count: int


class WorkflowFacet(WorkflowContract):
    entries: tuple[WorkflowFacetEntry, ...]
    has_more: bool


class WorkflowSearchFacets(WorkflowContract):
    status: WorkflowFacet
    workflow_kind: WorkflowFacet
    organization: WorkflowFacet
    renewal_period: WorkflowFacet


class WorkflowSearchPage(WorkflowContract):
    results: tuple[WorkflowSearchResult, ...]
    total_matches: int
    has_more: bool
    next_cursor: str | None
    applied_filters: dict[str, str]
    facets: WorkflowSearchFacets
    generated_at: datetime


class WorkflowPacketWorkflow(WorkflowContract):
    workflow_id: UUID
    workflow_kind: str
    objective: str
    status: str
    input: dict[str, Any]
    organization: str
    corrects_workflow_id: UUID | None
    created_at: datetime


class WorkflowPacketRun(WorkflowContract):
    run_id: UUID
    status: str
    outcome: str | None
    error_summary: str | None
    started_at: datetime
    finished_at: datetime | None


class WorkflowPacketApproval(WorkflowContract):
    approval_grant_id: UUID
    approving_party_id: UUID
    draft_job_id: UUID | None
    cause_type: str
    cause_id: str
    granted_at: datetime
    outcome: Literal["usable", "invalidated", "consumed"]


class WorkflowPacketDispatch(WorkflowContract):
    started_at: datetime
    run_id: UUID | None


class WorkflowPacketJob(WorkflowContract):
    job_id: UUID
    kind: str
    status: str
    input: dict[str, Any]
    resolved_input: dict[str, Any] | None
    output: dict[str, Any] | None
    revises_job_id: UUID | None
    depends_on_job_ids: tuple[UUID, ...]
    attempts: int
    max_attempts: int
    available_at: datetime
    waiting_reasons: tuple[str, ...]
    latest_run: WorkflowPacketRun | None
    approval: WorkflowPacketApproval | None
    dispatch: WorkflowPacketDispatch | None


class WorkflowPacketEvent(WorkflowContract):
    event_id: UUID
    job_id: UUID | None
    run_id: UUID | None
    event_type: str
    actor_type: str
    actor_id: str
    cause_type: str
    cause_id: str
    occurred_at: datetime
    summary: str


class WorkflowPacketEventWindow(WorkflowContract):
    returned: int
    total: int
    has_earlier: bool


class WorkflowPacket(WorkflowContract):
    packet_version: Literal["v1"] = "v1"
    generated_at: datetime
    workflow: WorkflowPacketWorkflow
    participants: tuple[WorkflowParticipantSummary, ...]
    jobs: tuple[WorkflowPacketJob, ...]
    recent_events: tuple[WorkflowPacketEvent, ...]
    event_window: WorkflowPacketEventWindow
