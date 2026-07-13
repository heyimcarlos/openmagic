"""Workflow-only Interaction Agent tools with injected Party authority."""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from server.services.conversation import get_conversation_log
from server.workflows import (
    ApproveWorkflowJobCommand,
    AuthorizeProtectedOperationCommand,
    ProposeWorkflowArguments,
    ProposeWorkflowCommand,
    ProposeWorkflowWorkArguments,
    ProposeWorkflowWorkCommand,
    ProtectedOperation,
    RecordInteractionCauseCommand,
    ReviseAndApproveWorkflowEmailCommand,
    RevisedEmailContent,
    ReviseWorkflowWorkArguments,
    ReviseWorkflowWorkCommand,
    StepUpVerification,
    WorkflowCommandContext,
    WorkflowControlPlane,
    WorkflowError,
    WorkflowInspectionContext,
    WorkflowRetrieval,
    WorkflowSearchRequest,
)

from .toolbox import InteractionToolContext, ToolResult


class _ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _PacketArguments(_ToolArguments):
    workflow_id: UUID


class _ApprovalArguments(_ToolArguments):
    job_id: UUID
    expected_draft_revision_id: UUID


class _ReviseAndApproveArguments(_ToolArguments):
    workflow_id: UUID
    job_id: UUID
    expected_draft_revision_id: UUID
    email: RevisedEmailContent


class _MessageArguments(_ToolArguments):
    message: str = Field(min_length=1, max_length=4000)


class _WaitArguments(_ToolArguments):
    reason: str = Field(min_length=1, max_length=500)


WORKFLOW_TOOL_SCHEMAS: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "function": {
            "name": "search_workflows",
            "description": (
                "Search authorized Workflow summaries with counts, facets, and pagination. "
                "For V0, workflow_kind is renewal_outreach.v1 or is omitted."
            ),
            "parameters": WorkflowSearchRequest.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_workflow_packet",
            "description": "Read bounded operational context for one resolved Workflow.",
            "parameters": _PacketArguments.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_workflow",
            "description": (
                "Create one registered Workflow and its trusted initial Job graph from "
                "an authorized source Workflow Packet."
            ),
            "parameters": ProposeWorkflowArguments.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_workflow_work",
            "description": (
                "Propose one typed business operation for a selected Workflow. "
                "The application owns its Job graph and execution policy."
            ),
            "parameters": ProposeWorkflowWorkArguments.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "revise_workflow_work",
            "description": (
                "Replace safely cancelable work with one immutable registered revision. "
                "The revision still requires explicit approval."
            ),
            "parameters": ReviseWorkflowWorkArguments.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "approve_job",
            "description": "Submit explicit Party approval for one exact presented Send Job.",
            "parameters": _ApprovalArguments.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_message_to_user",
            "description": "Send one user-visible response.",
            "parameters": _MessageArguments.model_json_schema(),
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": "Wait silently when no additional response is needed.",
            "parameters": _WaitArguments.model_json_schema(),
        },
    },
)


class WorkflowInteractionToolbox:
    """Translate typed model intent into retrieval and Control Plane calls."""

    def __init__(
        self,
        *,
        retrieval: WorkflowRetrieval,
        control_plane: WorkflowControlPlane,
        verification: StepUpVerification | None = None,
    ) -> None:
        self._retrieval = retrieval
        self._control_plane = control_plane
        self._verification = verification

    @property
    def schemas(self) -> tuple[dict[str, Any], ...]:
        return WORKFLOW_TOOL_SCHEMAS

    async def record_interaction_cause(
        self,
        context: InteractionToolContext,
        content: str,
    ) -> None:
        """Persist the trusted human message before the model interprets it."""

        await self._control_plane.record_interaction_cause(
            RecordInteractionCauseCommand(
                context=WorkflowCommandContext(
                    actor_party_id=context.actor_party_id,
                    organization_party_id=context.organization_party_id,
                    cause_type=context.cause_type,
                    cause_id=context.cause_id,
                ),
                content=content,
            )
        )

    async def invoke(
        self,
        name: str,
        arguments: dict[str, Any],
        context: InteractionToolContext,
    ) -> ToolResult:
        try:
            if name == "search_workflows":
                request = WorkflowSearchRequest.model_validate(arguments)
                page = await self._retrieval.search_workflows(
                    WorkflowInspectionContext(actor_party_id=context.actor_party_id),
                    request,
                )
                context.resolved_workflow_id = (
                    page.results[0].workflow_id if page.total_matches == 1 else None
                )
                return ToolResult(success=True, payload=page.model_dump(mode="json"))
            if name == "read_workflow_packet":
                request = _PacketArguments.model_validate(arguments)
                allowed_workflow_id = context.trusted_workflow_id or context.resolved_workflow_id
                if request.workflow_id != allowed_workflow_id:
                    return ToolResult(
                        success=False,
                        payload={"code": "workflow_resolution_required"},
                    )
                if (
                    context.loaded_packet is not None
                    and context.loaded_packet.workflow.workflow_id != request.workflow_id
                ):
                    return ToolResult(
                        success=False,
                        payload={"code": "workflow_packet_already_selected"},
                    )
                verification = await self._gate_verification(
                    context,
                    workflow_id=request.workflow_id,
                    purpose="sensitive_read",
                    operation=ProtectedOperation(
                        name="read_workflow_packet",
                        arguments={"workflow_id": str(request.workflow_id)},
                    ),
                )
                if verification is not None:
                    return verification
                packet = await self._retrieval.read_workflow_packet(
                    WorkflowInspectionContext(actor_party_id=context.actor_party_id),
                    request.workflow_id,
                )
                context.loaded_packet = packet
                return ToolResult(success=True, payload=packet.model_dump(mode="json"))
            if name == "propose_workflow_work":
                request = ProposeWorkflowWorkArguments.model_validate(arguments)
                if context.loaded_packet is None:
                    if (
                        context.verification_challenge_id is None
                        or context.trusted_workflow_id != request.workflow_id
                    ):
                        return ToolResult(
                            success=False,
                            payload={"code": "workflow_packet_required"},
                        )
                    verification = await self._gate_verification(
                        context,
                        workflow_id=request.workflow_id,
                        purpose="sensitive_write",
                        operation=ProtectedOperation(
                            name="propose_workflow_work",
                            arguments=request.model_dump(mode="json"),
                        ),
                    )
                    if verification is not None:
                        return verification
                    context.loaded_packet = await self._retrieval.read_workflow_packet(
                        WorkflowInspectionContext(actor_party_id=context.actor_party_id),
                        request.workflow_id,
                    )
                else:
                    resolved_workflow_id = (
                        context.trusted_workflow_id or context.resolved_workflow_id
                    )
                    if (
                        context.loaded_packet.workflow.workflow_id != request.workflow_id
                        or request.workflow_id != resolved_workflow_id
                    ):
                        return ToolResult(
                            success=False,
                            payload={"code": "workflow_packet_required"},
                        )
                    verification = await self._gate_verification(
                        context,
                        workflow_id=request.workflow_id,
                        purpose="sensitive_write",
                        operation=ProtectedOperation(
                            name="propose_workflow_work",
                            arguments=request.model_dump(mode="json"),
                        ),
                    )
                    if verification is not None:
                        return verification
                return await self._propose(request, context)
            if name == "propose_workflow":
                request = ProposeWorkflowArguments.model_validate(arguments)
                packet = context.loaded_packet
                resolved_workflow_id = context.trusted_workflow_id or context.resolved_workflow_id
                has_selected_packet = (
                    packet is not None
                    and packet.workflow.workflow_id == request.source_workflow_id
                    and resolved_workflow_id == request.source_workflow_id
                )
                is_verified_resume = (
                    context.verification_challenge_id is not None
                    and context.trusted_workflow_id == request.source_workflow_id
                )
                if not has_selected_packet and not is_verified_resume:
                    return ToolResult(
                        success=False,
                        payload={"code": "workflow_packet_required"},
                    )
                verification = await self._gate_verification(
                    context,
                    workflow_id=request.source_workflow_id,
                    purpose="sensitive_write",
                    operation=ProtectedOperation(
                        name="propose_workflow",
                        arguments=request.model_dump(mode="json"),
                    ),
                )
                if verification is not None:
                    return verification
                trace = await self._control_plane.propose_workflow(
                    ProposeWorkflowCommand(
                        context=WorkflowCommandContext(
                            actor_party_id=context.actor_party_id,
                            organization_party_id=context.organization_party_id,
                            cause_type=context.cause_type,
                            cause_id=context.cause_id,
                        ),
                        **request.model_dump(),
                    )
                )
                return ToolResult(
                    success=True,
                    payload={
                        "workflow_id": str(trace.workflow.id),
                        "job_ids": [str(job.id) for job in trace.jobs],
                        "status": trace.workflow.status,
                    },
                )
            if name == "approve_job":
                request = _ApprovalArguments.model_validate(arguments)
                packet = context.loaded_packet
                if packet is None:
                    workflow_id = context.trusted_workflow_id
                    if context.verification_challenge_id is None or workflow_id is None:
                        return ToolResult(
                            success=False,
                            payload={"code": "workflow_packet_required"},
                        )
                    verification = await self._gate_verification(
                        context,
                        workflow_id=workflow_id,
                        purpose="sensitive_write",
                        operation=ProtectedOperation(
                            name="approve_job",
                            arguments=request.model_dump(mode="json"),
                        ),
                    )
                    if verification is not None:
                        return verification
                    packet = await self._retrieval.read_workflow_packet(
                        WorkflowInspectionContext(actor_party_id=context.actor_party_id),
                        workflow_id,
                    )
                    context.loaded_packet = packet
                job = next(
                    (
                        item
                        for item in packet.jobs
                        if item.job_id == request.job_id
                        and item.status == "waiting"
                        and request.expected_draft_revision_id in item.depends_on_job_ids
                    ),
                    None,
                )
                if job is None:
                    return ToolResult(success=False, payload={"code": "stale_approval_target"})
                verification = await self._gate_verification(
                    context,
                    workflow_id=packet.workflow.workflow_id,
                    purpose="sensitive_write",
                    operation=ProtectedOperation(
                        name="approve_job",
                        arguments=request.model_dump(mode="json"),
                    ),
                )
                if verification is not None:
                    return verification
                grant = await self._control_plane.approve_job(
                    ApproveWorkflowJobCommand(
                        context=WorkflowCommandContext(
                            actor_party_id=context.actor_party_id,
                            organization_party_id=context.organization_party_id,
                            cause_type=context.cause_type,
                            cause_id=context.cause_id,
                        ),
                        job_id=request.job_id,
                        expected_draft_revision_id=request.expected_draft_revision_id,
                    )
                )
                return ToolResult(
                    success=True,
                    payload={
                        "approval_grant_id": str(grant.approval_grant_id),
                        "job_id": str(grant.job_id),
                        "status": "queued",
                    },
                )
            if name == "revise_workflow_work":
                request = ReviseWorkflowWorkArguments.model_validate(arguments)
                packet = context.loaded_packet
                resolved_workflow_id = context.trusted_workflow_id or context.resolved_workflow_id
                has_selected_packet = (
                    packet is not None
                    and packet.workflow.workflow_id == request.workflow_id
                    and resolved_workflow_id == request.workflow_id
                )
                is_verified_resume = (
                    context.verification_challenge_id is not None
                    and context.trusted_workflow_id == request.workflow_id
                )
                if not has_selected_packet and not is_verified_resume:
                    return ToolResult(
                        success=False,
                        payload={"code": "workflow_packet_required"},
                    )
                verification = await self._gate_verification(
                    context,
                    workflow_id=request.workflow_id,
                    purpose="sensitive_write",
                    operation=ProtectedOperation(
                        name="revise_workflow_work",
                        arguments=request.model_dump(mode="json"),
                    ),
                )
                if verification is not None:
                    return verification
                revision = await self._control_plane.revise_work(
                    ReviseWorkflowWorkCommand(
                        context=WorkflowCommandContext(
                            actor_party_id=context.actor_party_id,
                            organization_party_id=context.organization_party_id,
                            cause_type=context.cause_type,
                            cause_id=context.cause_id,
                        ),
                        workflow_id=request.workflow_id,
                        operation=request.operation,
                    )
                )
                return ToolResult(
                    success=True,
                    payload={
                        "workflow_id": str(revision.workflow_id),
                        "draft_revision_id": str(revision.draft_job_id),
                        "job_id": str(revision.send_job_id),
                        "status": "waiting_for_approval",
                    },
                )
            if name == "revise_and_approve_email":
                request = _ReviseAndApproveArguments.model_validate(arguments)
                workflow_id = context.trusted_workflow_id or request.workflow_id
                if workflow_id != request.workflow_id:
                    return ToolResult(
                        success=False,
                        payload={"code": "workflow_packet_required"},
                    )
                verification = await self._gate_verification(
                    context,
                    workflow_id=request.workflow_id,
                    purpose="sensitive_write",
                    operation=ProtectedOperation(
                        name="revise_and_approve_email",
                        arguments=request.model_dump(mode="json"),
                    ),
                )
                if verification is not None:
                    return verification
                grant = await self._control_plane.revise_and_approve_email(
                    ReviseAndApproveWorkflowEmailCommand(
                        context=WorkflowCommandContext(
                            actor_party_id=context.actor_party_id,
                            organization_party_id=context.organization_party_id,
                            cause_type=context.cause_type,
                            cause_id=context.cause_id,
                        ),
                        workflow_id=request.workflow_id,
                        job_id=request.job_id,
                        expected_draft_revision_id=request.expected_draft_revision_id,
                        email=request.email,
                    )
                )
                return ToolResult(
                    success=True,
                    payload={
                        "approval_grant_id": str(grant.approval_grant_id),
                        "draft_revision_id": str(grant.draft_job_id),
                        "job_id": str(grant.job_id),
                        "status": "queued",
                    },
                )
            if name == "send_message_to_user":
                request = _MessageArguments.model_validate(arguments)
                conversation = context.conversation or get_conversation_log()
                if context.delivery_id is not None:
                    conversation.record_reply_once(
                        context.delivery_id,
                        request.message,
                        cause_id=context.cause_id,
                    )
                else:
                    conversation.record_reply(request.message, cause_id=context.cause_id)
                return ToolResult(
                    success=True,
                    payload={"status": "delivered"},
                    user_message=request.message,
                    recorded_reply=True,
                )
            if name == "wait":
                request = _WaitArguments.model_validate(arguments)
                conversation = context.conversation or get_conversation_log()
                conversation.record_wait(request.reason)
                return ToolResult(
                    success=True,
                    payload={"status": "waiting"},
                    recorded_reply=True,
                )
            return ToolResult(success=False, payload={"code": "unknown_tool"})
        except ValidationError as exc:
            return ToolResult(
                success=False,
                payload={"code": "invalid_arguments", "errors": exc.errors(include_input=False)},
            )
        except WorkflowError as exc:
            return ToolResult(
                success=False,
                payload={"code": type(exc).__name__, "message": str(exc)},
            )

    async def _gate_verification(
        self,
        context: InteractionToolContext,
        *,
        workflow_id: UUID,
        purpose: Literal["sensitive_read", "sensitive_write"],
        operation: ProtectedOperation,
    ) -> ToolResult | None:
        if self._verification is None:
            return None
        if context.interaction_id is None:
            return ToolResult(
                success=False,
                payload={"code": "verification_context_required"},
            )
        if context.verification_challenge_id is not None:
            decision = await self._verification.validate_verified_resume(
                challenge_id=context.verification_challenge_id,
                actor_party_id=context.actor_party_id,
                interaction_id=context.interaction_id,
                workflow_id=workflow_id,
                operation=operation,
            )
        else:
            decision = await self._verification.authorize_or_challenge(
                AuthorizeProtectedOperationCommand(
                    actor_party_id=context.actor_party_id,
                    interaction_id=context.interaction_id,
                    workflow_id=workflow_id,
                    purpose=purpose,
                    cause_id=context.cause_id,
                    cause_type=context.cause_type,
                    operation=operation,
                )
            )
        if decision.status == "verification_unavailable":
            return ToolResult(
                success=False,
                payload={"code": "verification_unavailable"},
            )
        if decision.status == "verification_required":
            return ToolResult(
                success=False,
                payload={
                    "code": "verification_required",
                    "challenge_id": str(decision.challenge_id),
                    "purpose": purpose,
                    "delivery_method": "email_code",
                    "delivery_status": "queued",
                    "destination": decision.masked_destination,
                    "expires_at": (
                        decision.expires_at.isoformat() if decision.expires_at is not None else None
                    ),
                },
            )
        if decision.status == "verification_in_progress":
            return ToolResult(
                success=False,
                payload={
                    "code": "verification_in_progress",
                    "challenge_id": str(decision.challenge_id),
                    "destination": decision.masked_destination,
                    "expires_at": (
                        decision.expires_at.isoformat() if decision.expires_at is not None else None
                    ),
                },
            )
        return None

    async def _propose(
        self,
        request: ProposeWorkflowWorkArguments,
        context: InteractionToolContext,
    ) -> ToolResult:
        packet = context.loaded_packet
        resolved_workflow_id = context.trusted_workflow_id or context.resolved_workflow_id
        if (
            packet is None
            or packet.workflow.workflow_id != request.workflow_id
            or request.workflow_id != resolved_workflow_id
        ):
            return ToolResult(success=False, payload={"code": "workflow_packet_required"})
        command = ProposeWorkflowWorkCommand(
            context=WorkflowCommandContext(
                actor_party_id=context.actor_party_id,
                organization_party_id=context.organization_party_id,
                cause_type=context.cause_type,
                cause_id=context.cause_id,
            ),
            workflow_id=request.workflow_id,
            operation=request.operation,
        )
        trace = await self._control_plane.propose_work(command)
        return ToolResult(
            success=True,
            payload={
                "workflow_id": str(trace.workflow.id),
                "job_ids": [str(job.id) for job in trace.jobs],
                "status": trace.workflow.status,
            },
        )
