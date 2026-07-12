"""Workflow-only Interaction Agent tools with injected Party authority."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, ValidationError

from server.services.conversation import get_conversation_log
from server.workflows import (
    DRAFT_RENEWAL_EMAIL_KIND,
    GMAIL_SEND_EMAIL_KIND,
    ProposeWorkflowJobsCommand,
    WorkflowCommandContext,
    WorkflowControlPlane,
    WorkflowError,
    WorkflowInspectionContext,
    WorkflowJobProposal,
    WorkflowRetrieval,
    WorkflowSearchRequest,
)

from .toolbox import InteractionToolContext, ToolResult


class _ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _PacketArguments(_ToolArguments):
    workflow_id: UUID


class _ProposalArguments(_ToolArguments):
    workflow_id: UUID
    sender_mailbox: EmailStr
    recipient_email: EmailStr


class _MessageArguments(_ToolArguments):
    message: str = Field(min_length=1, max_length=4000)


class _WaitArguments(_ToolArguments):
    reason: str = Field(min_length=1, max_length=500)


WORKFLOW_TOOL_SCHEMAS: tuple[dict[str, Any], ...] = (
    {
        "type": "function",
        "function": {
            "name": "search_workflows",
            "description": "Search authorized Workflow summaries with counts, facets, and pagination.",
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
            "name": "propose_renewal_email",
            "description": "Propose the typed Draft and dependent Send Jobs for a selected renewal Workflow.",
            "parameters": _ProposalArguments.model_json_schema(),
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
    ) -> None:
        self._retrieval = retrieval
        self._control_plane = control_plane

    @property
    def schemas(self) -> tuple[dict[str, Any], ...]:
        return WORKFLOW_TOOL_SCHEMAS

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
                packet = await self._retrieval.read_workflow_packet(
                    WorkflowInspectionContext(actor_party_id=context.actor_party_id),
                    request.workflow_id,
                )
                context.loaded_packet = packet
                return ToolResult(success=True, payload=packet.model_dump(mode="json"))
            if name == "propose_renewal_email":
                request = _ProposalArguments.model_validate(arguments)
                return await self._propose(request, context)
            if name == "send_message_to_user":
                request = _MessageArguments.model_validate(arguments)
                get_conversation_log().record_reply(request.message)
                return ToolResult(
                    success=True,
                    payload={"status": "delivered"},
                    user_message=request.message,
                    recorded_reply=True,
                )
            if name == "wait":
                request = _WaitArguments.model_validate(arguments)
                get_conversation_log().record_wait(request.reason)
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

    async def _propose(
        self,
        request: _ProposalArguments,
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
        policyholders = [
            participant
            for participant in packet.participants
            if "Policyholder" in participant.roles
        ]
        if len(policyholders) != 1:
            return ToolResult(success=False, payload={"code": "policyholder_not_resolved"})
        period = packet.workflow.input.get("renewal_period")
        if not isinstance(period, str):
            return ToolResult(success=False, payload={"code": "renewal_period_missing"})
        command = ProposeWorkflowJobsCommand(
            context=WorkflowCommandContext(
                actor_party_id=context.actor_party_id,
                organization_party_id=context.organization_party_id,
                cause_type="message",
                cause_id=context.cause_id,
            ),
            workflow_id=request.workflow_id,
            jobs=(
                WorkflowJobProposal(
                    key="draft",
                    kind=DRAFT_RENEWAL_EMAIL_KIND,
                    input={
                        "recipient_name": policyholders[0].name,
                        "renewal_period": period,
                    },
                ),
                WorkflowJobProposal(
                    key="send",
                    kind=GMAIL_SEND_EMAIL_KIND,
                    input={
                        "sender_mailbox": str(request.sender_mailbox),
                        "to": [str(request.recipient_email)],
                        "subject": {"job_output": "draft", "field": "subject"},
                        "body": {"job_output": "draft", "field": "body"},
                    },
                    depends_on=("draft",),
                ),
            ),
        )
        trace = await self._control_plane.propose_jobs(command)
        return ToolResult(
            success=True,
            payload={
                "workflow_id": str(trace.workflow.id),
                "job_ids": [str(job.id) for job in trace.jobs],
                "status": trace.workflow.status,
            },
        )
