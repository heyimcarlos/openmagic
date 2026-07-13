"""Typed inputs and read projections for the Workflow Control Plane."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


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


class PrepareRenewalEmailOperation(WorkflowContract):
    """Prepare one reviewable renewal email without exposing its Job graph."""

    type: Literal["prepare_renewal_email"]


class ProposeWorkflowWorkArguments(WorkflowContract):
    """Closed business operation accepted from the Interaction Agent."""

    workflow_id: UUID
    operation: PrepareRenewalEmailOperation


class ProposeWorkflowWorkCommand(WorkflowContract):
    """Compile and propose typed work for one existing Workflow."""

    context: WorkflowCommandContext
    workflow_id: UUID
    operation: PrepareRenewalEmailOperation


class RenewalOutreachInput(WorkflowContract):
    """Typed business input for the V0 renewal Workflow Kind."""

    renewal_period: str = Field(min_length=1, max_length=32)


class ProposeWorkflowArguments(WorkflowContract):
    """Create one registered V0 Workflow variant from an authorized source packet."""

    source_workflow_id: UUID
    corrects_workflow_id: UUID | None = None
    workflow_kind: str = Field(min_length=1, max_length=255)
    objective: str = Field(min_length=1, max_length=500)
    input: RenewalOutreachInput
    operation: PrepareRenewalEmailOperation


class ProposeWorkflowCommand(WorkflowContract):
    """Compile and atomically create one registered V0 Workflow variant."""

    context: WorkflowCommandContext
    source_workflow_id: UUID
    corrects_workflow_id: UUID | None = None
    workflow_kind: str = Field(min_length=1, max_length=255)
    objective: str = Field(min_length=1, max_length=500)
    input: RenewalOutreachInput
    operation: PrepareRenewalEmailOperation


class ApproveWorkflowJobCommand(WorkflowContract):
    """Authorize one exact presented External Effect through an implicit Party."""

    context: WorkflowCommandContext
    job_id: UUID
    expected_draft_revision_id: UUID


class RevisedEmailContent(WorkflowContract):
    """Complete editable portion of one exact email effect."""

    to: tuple[EmailStr, ...] = Field(min_length=1)
    cc: tuple[EmailStr, ...] = ()
    bcc: tuple[EmailStr, ...] = ()
    subject: str = Field(min_length=1, max_length=998)
    body: str = Field(min_length=1, max_length=100_000)


class ReviseWorkflowEmailCommand(WorkflowContract):
    """Replace one safely cancelable email with a reviewable revision."""

    context: WorkflowCommandContext
    workflow_id: UUID
    job_id: UUID
    expected_draft_revision_id: UUID
    email: RevisedEmailContent


class ReviseAndApproveWorkflowEmailCommand(ReviseWorkflowEmailCommand):
    """Replace safely cancelable email work and approve the exact revision."""


class WorkflowEmailRevision(WorkflowContract):
    workflow_id: UUID
    draft_job_id: UUID
    send_job_id: UUID


class ReviseEmailOperation(WorkflowContract):
    type: Literal["revise_email"]
    job_id: UUID
    expected_draft_revision_id: UUID
    email: RevisedEmailContent


class ReviseWorkflowWorkArguments(WorkflowContract):
    workflow_id: UUID
    operation: ReviseEmailOperation


class ReviseWorkflowWorkCommand(WorkflowContract):
    context: WorkflowCommandContext
    workflow_id: UUID
    operation: ReviseEmailOperation


class RecordInteractionCauseCommand(WorkflowContract):
    """Persist a trusted reference to a human interaction before interpretation."""

    context: WorkflowCommandContext
    content: str = Field(min_length=1, max_length=10_000)


class CancelWorkflowCommand(WorkflowContract):
    """Atomically cancel one safely cancelable Workflow aggregate."""

    context: WorkflowCommandContext
    workflow_id: UUID


class CancelWorkflowResult(WorkflowContract):
    workflow_id: UUID
    outcome: Literal["cancelled", "too_late"]


class RevokeWorkflowAuthorityCommand(WorkflowContract):
    """Revoke one Broker authority fact and invalidate unconsumed approvals."""

    context: WorkflowCommandContext
    workflow_id: UUID
    subject_party_id: UUID
    party_identifier_id: UUID | None = None
    reason: Literal[
        "approver_identity_revoked",
        "broker_role_revoked",
        "organization_membership_revoked",
    ]

    @model_validator(mode="after")
    def validate_identifier_target(self) -> RevokeWorkflowAuthorityCommand:
        targets_identifier = self.reason == "approver_identity_revoked"
        if targets_identifier != (self.party_identifier_id is not None):
            raise ValueError("party_identifier_id is required only for approver_identity_revoked")
        return self


class AuthorityRevocationResult(WorkflowContract):
    workflow_id: UUID
    reason: str
    invalidated_grants: int


class ApprovalGrant(WorkflowContract):
    """Immutable evidence returned for a recorded or replayed exact approval."""

    approval_grant_id: UUID
    workflow_id: UUID
    job_id: UUID
    approving_party_id: UUID
    draft_job_id: UUID
    effect_fingerprint: str
    cause_type: Literal["message", "ui_action"]
    cause_id: str
    granted_at: datetime


class ClaimWorkflowJobCommand(WorkflowContract):
    """Claim at most one eligible Job through the Worker-only boundary."""

    worker_id: str = Field(min_length=1, max_length=255)
    application_build: str = Field(min_length=1, max_length=255)
    lease_duration: timedelta = Field(gt=timedelta(0), le=timedelta(minutes=30))
    executor_keys: tuple[str, ...] = Field(min_length=1)


class BeginExternalEffectDispatchCommand(WorkflowContract):
    """Consume one Run's dispatch allowance before its provider call."""

    run_id: UUID


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
    kinds: tuple[str, ...] = ()


NotificationDeliveryStatus = Literal["queued", "delivering", "delivered", "failed"]


class NotificationDeliveryPacket(WorkflowContract):
    """Stable identifiers passed to a fresh Interaction Agent runtime."""

    notification_id: UUID
    workflow_event_id: UUID
    workflow_id: UUID
    kind: str
    delivery_attempt: int
    status: NotificationDeliveryStatus


class _WorkflowPacketOperationArguments(WorkflowContract):
    workflow_id: UUID


class _ApproveJobOperationArguments(WorkflowContract):
    job_id: UUID
    expected_draft_revision_id: UUID


class _ReviseAndApproveEmailOperationArguments(WorkflowContract):
    workflow_id: UUID
    job_id: UUID
    expected_draft_revision_id: UUID
    email: RevisedEmailContent


_PROTECTED_OPERATION_SCHEMAS: dict[str, type[WorkflowContract]] = {
    "read_workflow_packet": _WorkflowPacketOperationArguments,
    "propose_workflow": ProposeWorkflowArguments,
    "propose_workflow_work": ProposeWorkflowWorkArguments,
    "revise_workflow_work": ReviseWorkflowWorkArguments,
    "approve_job": _ApproveJobOperationArguments,
    "revise_and_approve_email": _ReviseAndApproveEmailOperationArguments,
}


class ProtectedOperation(WorkflowContract):
    """One recognized, schema-validated operation waiting behind verification."""

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    arguments: dict[str, Any]

    @model_validator(mode="before")
    @classmethod
    def validate_operation_contract(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        if value.get("name") == "propose_renewal_email":
            legacy_arguments = value.get("arguments")
            if isinstance(legacy_arguments, dict):
                value = {
                    **value,
                    "name": "propose_workflow_work",
                    "arguments": {
                        **legacy_arguments,
                        "operation": {"type": "prepare_renewal_email"},
                    },
                }
        schema = _PROTECTED_OPERATION_SCHEMAS.get(value.get("name"))
        if schema is None:
            raise ValueError("protected operation is not recognized")
        validated = schema.model_validate(value.get("arguments"))
        return {**value, "arguments": validated.model_dump(mode="json")}


class AuthorizeProtectedOperationCommand(WorkflowContract):
    """Ask the application gate to authorize or challenge one protected operation."""

    actor_party_id: UUID
    interaction_id: str = Field(min_length=1, max_length=255)
    workflow_id: UUID
    purpose: Literal["sensitive_read", "sensitive_write"]
    cause_id: str = Field(min_length=1, max_length=255)
    cause_type: Literal["message", "ui_action"] = "message"
    operation: ProtectedOperation


class VerificationDecision(WorkflowContract):
    """Deterministic identity-proof decision returned to a protected operation."""

    status: Literal[
        "session_valid",
        "verification_required",
        "verification_in_progress",
        "verification_unavailable",
    ]
    challenge_id: UUID | None = None
    masked_destination: str | None = None
    expires_at: datetime | None = None
    verification_session_expires_at: datetime | None = None


class SubmitVerificationCodeCommand(WorkflowContract):
    """Submit one six-digit proof through the originating interaction."""

    actor_party_id: UUID
    interaction_id: str = Field(min_length=1, max_length=255)
    cause_id: str = Field(min_length=1, max_length=255)
    code: str = Field(pattern=r"^\d{6}$")


class VerificationCodeResult(WorkflowContract):
    """Result of consuming a verification code, including resumable typed intent."""

    status: Literal[
        "verified",
        "invalid_code",
        "attempts_exhausted",
        "expired",
        "no_active_challenge",
        "verification_unavailable",
    ]
    challenge_id: UUID | None = None
    workflow_id: UUID | None = None
    purpose: Literal["sensitive_read", "sensitive_write"] | None = None
    request_cause_id: str | None = None
    operation: ProtectedOperation | None = None
    verification_session_expires_at: datetime | None = None


class VerificationEmailDelivery(WorkflowContract):
    """Trusted email delivery material, never returned to the model or browser."""

    challenge_id: UUID
    job_id: UUID
    run_id: UUID
    destination: str
    code: str = Field(pattern=r"^\d{6}$")
    expires_at: datetime


class VerificationResumeDelivery(WorkflowContract):
    """Exact verified continuation resolved through a leased Notification."""

    challenge_id: UUID
    actor_party_id: UUID
    interaction_id: str
    workflow_id: UUID
    request_cause_id: str
    request_cause_type: Literal["message", "ui_action"]
    operation: ProtectedOperation


class VerificationDeliveryAttention(WorkflowContract):
    """Current-state recovery decision for a terminal or uncertain code delivery."""

    interaction_id: str
    message: str | None


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
    """Trusted presentation target resolved from durable Workflow state."""

    destination_party_id: UUID
    draft_job_id: UUID
    send_job_id: UUID
    effect_fingerprint: str
    effect: dict[str, Any]


class NotificationAudienceContext(WorkflowContract):
    """Trusted destination and kind for one claimed Notification."""

    destination_party_id: UUID
    kind: Literal["approval_required", "send_confirmed", "work_completed"]


class NotificationStatusContext(WorkflowContract):
    """Deterministic user-facing status authorized at delivery time."""

    destination_party_id: UUID
    message: str


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
    worker_id: str
    application_build: str
    runtime_instance_id: UUID | None
    lease_expires_at: datetime
    result: dict[str, Any] | None
    finished_at: datetime | None


class WorkflowTraceEvent(WorkflowContract):
    id: UUID
    workflow_id: UUID
    job_id: UUID | None
    run_id: UUID | None
    approval_grant_id: UUID | None
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
    attempts: int
    max_attempts: int
    available_at: datetime
    claimed_by: str | None
    lease_expires_at: datetime | None
    delivered_at: datetime | None
    delivered_by: str | None
    last_error: str | None


class WorkflowTrace(WorkflowContract):
    """Development evidence for one persisted Workflow aggregate."""

    workflow: WorkflowTraceWorkflow
    jobs: tuple[WorkflowTraceJob, ...]
    runs: tuple[WorkflowTraceRun, ...]
    events: tuple[WorkflowTraceEvent, ...]
    notifications: tuple[WorkflowTraceNotification, ...]
