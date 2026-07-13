"""Deterministic, authorization-scoped chat telemetry projection."""

from __future__ import annotations

from collections import defaultdict
from typing import Literal
from uuid import UUID

from server.models import (
    ChatAgentActivity,
    ChatApprovalRequest,
    ChatCockpitEvent,
    ChatCockpitJob,
    ChatCockpitWorkflow,
    ChatTurnTelemetry,
    ChatWorkflowCheckpoint,
    ChatWorkflowCockpit,
    ChatWorkflowJobStage,
    ChatWorkflowStage,
    ChatWorkflowTelemetry,
    WorkflowCheckpointStatus,
)

from ...workflows import (
    DRAFT_RENEWAL_EMAIL_KIND,
    GMAIL_SEND_EMAIL_KIND,
    RENEWAL_OUTREACH_KIND,
    VERIFICATION_EMAIL_DELIVERY_WORKFLOW_KIND,
    VERIFICATION_EMAIL_JOB_KIND,
    InteractionActivityAction,
    InteractionActivityReceipt,
    InteractionActivityStatus,
    InteractionActivityStore,
    WorkflowInspectionContext,
    WorkflowKindRegistry,
    WorkflowPacket,
    WorkflowPacketJob,
    WorkflowRetrieval,
)

_ACTIVITY_LABELS = {
    InteractionActivityAction.SEARCH_WORKFLOWS: "Searched authorized Workflows",
    InteractionActivityAction.READ_WORKFLOW_PACKET: "Read bounded Workflow context",
    InteractionActivityAction.PROPOSE_WORKFLOW_WORK: "Proposed business work",
    InteractionActivityAction.APPROVE_JOB: "Submitted exact Job approval",
}

_JOB_LABELS = {
    DRAFT_RENEWAL_EMAIL_KIND: "Draft renewal email",
    GMAIL_SEND_EMAIL_KIND: "Send approved email",
    VERIFICATION_EMAIL_JOB_KIND: "Send verification code",
}


class WorkflowTelemetryProjector:
    """Combine sanitized interaction receipts with current authorized Workflow packets."""

    def __init__(
        self,
        *,
        retrieval: WorkflowRetrieval,
        activity_store: InteractionActivityStore,
        registry: WorkflowKindRegistry,
    ) -> None:
        self._retrieval = retrieval
        self._activity_store = activity_store
        self._registry = registry

    async def project(
        self,
        *,
        actor_party_id: UUID,
        cause_ids: list[str],
    ) -> dict[str, ChatTurnTelemetry]:
        ordered_causes = list(dict.fromkeys(cause_ids))
        if not ordered_causes:
            return {}
        context = WorkflowInspectionContext(actor_party_id=actor_party_id)
        receipts = await self._activity_store.list_for_actor_causes(
            actor_party_id=actor_party_id,
            cause_ids=ordered_causes,
        )
        event_workflows = await self._retrieval.authorized_workflow_ids_for_causes(
            context,
            ordered_causes,
        )
        receipts_by_cause: dict[str, list[InteractionActivityReceipt]] = defaultdict(list)
        for receipt in receipts:
            receipts_by_cause[receipt.cause_id].append(receipt)

        workflows_by_cause: dict[str, list[UUID]] = {}
        packet_ids: list[UUID] = []
        for cause_id in ordered_causes:
            workflow_ids: list[UUID] = []
            for receipt in receipts_by_cause[cause_id]:
                workflow_id = self._receipt_workflow_id(receipt)
                if workflow_id is not None and workflow_id not in workflow_ids:
                    workflow_ids.append(workflow_id)
            for workflow_id in event_workflows.get(cause_id, ()):
                if workflow_id not in workflow_ids:
                    workflow_ids.append(workflow_id)
            workflows_by_cause[cause_id] = workflow_ids
            for workflow_id in workflow_ids:
                if workflow_id not in packet_ids:
                    packet_ids.append(workflow_id)

        packets = await self._retrieval.read_workflow_packets(context, packet_ids)
        packets_by_id = {packet.workflow.workflow_id: packet for packet in packets}
        projected: dict[str, ChatTurnTelemetry] = {}
        for cause_id in ordered_causes:
            activity = [self._activity(receipt) for receipt in receipts_by_cause[cause_id]]
            visible_packets = [
                packets_by_id[workflow_id]
                for workflow_id in workflows_by_cause[cause_id]
                if workflow_id in packets_by_id
                and packets_by_id[workflow_id].workflow.workflow_kind
                != VERIFICATION_EMAIL_DELIVERY_WORKFLOW_KIND
            ]
            workflows = [self._workflow(packet) for packet in visible_packets]
            if not activity and not workflows:
                continue
            projected[cause_id] = ChatTurnTelemetry(
                activity_summary=self._activity_summary(activity, workflows),
                activity=activity,
                workflows=workflows,
                approval_request=(
                    next(
                        (
                            request
                            for packet in visible_packets
                            if (request := self._approval_request(packet)) is not None
                        ),
                        None,
                    )
                    if cause_id.startswith("notification:")
                    else None
                ),
                cockpit=self._cockpit(visible_packets[0]) if visible_packets else None,
            )
        return projected

    @staticmethod
    def _activity(receipt: InteractionActivityReceipt) -> ChatAgentActivity:
        return ChatAgentActivity(
            id=str(receipt.id),
            label=_ACTIVITY_LABELS[receipt.action],
            status=receipt.status.value,
        )

    @staticmethod
    def _receipt_workflow_id(receipt: InteractionActivityReceipt) -> UUID | None:
        if (
            receipt.status is not InteractionActivityStatus.SUCCEEDED
            or receipt.action is InteractionActivityAction.SEARCH_WORKFLOWS
        ):
            return None
        return receipt.workflow_id

    def _workflow(self, packet: WorkflowPacket) -> ChatWorkflowTelemetry:
        stages: list[ChatWorkflowStage] = []
        for job in self._topological_jobs(packet.jobs):
            if self._registry.requires_approval(job.kind):
                stages.append(
                    ChatWorkflowCheckpoint(
                        id=f"approval:{job.job_id}",
                        kind="checkpoint",
                        label="Exact approval",
                        status=self._approval_status(job),
                    )
                )
            stages.append(
                ChatWorkflowJobStage(
                    id=str(job.job_id),
                    kind="job",
                    label=_JOB_LABELS.get(job.kind, "Workflow Job"),
                    status=job.status,
                )
            )
        return ChatWorkflowTelemetry(
            id=str(packet.workflow.workflow_id),
            title=self._workflow_title(packet),
            status_label=self._workflow_status_label(packet),
            stages=stages,
        )

    def _approval_request(self, packet: WorkflowPacket) -> ChatApprovalRequest | None:
        jobs_by_id = {job.job_id: job for job in packet.jobs}
        draft_ids = [job.job_id for job in packet.jobs if job.kind == DRAFT_RENEWAL_EMAIL_KIND]
        presented_job_ids = {
            event.job_id
            for event in packet.recent_events
            if event.event_type == "approval_presentation_committed" and event.job_id is not None
        }
        for job in self._topological_jobs(packet.jobs):
            if (
                job.kind != GMAIL_SEND_EMAIL_KIND
                or job.status != "waiting"
                or job.job_id not in presented_job_ids
                or job.resolved_input is None
                or any(reason.kind == "dependency" for reason in job.waiting_reasons)
                or not any(
                    reason.kind in {"exact_approval", "approval_invalidated"}
                    for reason in job.waiting_reasons
                )
            ):
                continue
            draft_id = next(
                (
                    dependency_id
                    for dependency_id in job.depends_on_job_ids
                    if jobs_by_id.get(dependency_id) is not None
                    and jobs_by_id[dependency_id].kind == DRAFT_RENEWAL_EMAIL_KIND
                    and jobs_by_id[dependency_id].status == "succeeded"
                ),
                None,
            )
            if draft_id is None:
                continue
            resolved = job.resolved_input
            sender = resolved.get("sender_mailbox")
            to = resolved.get("to")
            subject = resolved.get("subject")
            body = resolved.get("body")
            if (
                not isinstance(sender, str)
                or not isinstance(to, list | tuple)
                or not to
                or not all(isinstance(address, str) for address in to)
                or not isinstance(subject, str)
                or not subject
                or not isinstance(body, str)
                or not body
            ):
                continue
            cc = resolved.get("cc", ())
            bcc = resolved.get("bcc", ())
            if not isinstance(cc, list | tuple) or not isinstance(bcc, list | tuple):
                continue
            return ChatApprovalRequest(
                workflow_id=str(packet.workflow.workflow_id),
                job_id=str(job.job_id),
                draft_revision_id=str(draft_id),
                revision=draft_ids.index(draft_id) + 1,
                sender=sender,
                to=[str(address) for address in to],
                cc=[str(address) for address in cc],
                bcc=[str(address) for address in bcc],
                subject=subject,
                body=body,
            )
        return None

    def _cockpit(self, packet: WorkflowPacket) -> ChatWorkflowCockpit:
        jobs_by_id = {job.job_id: job for job in packet.jobs}
        return ChatWorkflowCockpit(
            workflow=ChatCockpitWorkflow(
                id=str(packet.workflow.workflow_id),
                kind=packet.workflow.workflow_kind,
                objective=packet.workflow.objective,
                organization=packet.workflow.organization,
                status=packet.workflow.status,
            ),
            jobs=[
                ChatCockpitJob(
                    id=str(job.job_id),
                    kind=job.kind,
                    title=_JOB_LABELS.get(job.kind, job.kind),
                    detail=self._job_detail(job),
                    status=job.status,
                    depends_on=[str(dependency_id) for dependency_id in job.depends_on_job_ids],
                )
                for job in self._topological_jobs(packet.jobs)
            ],
            events=[
                ChatCockpitEvent(
                    id=str(event.event_id),
                    occurred_at=event.occurred_at.isoformat(),
                    type=event.event_type,
                    aggregate=self._event_aggregate(event.job_id, event.run_id, jobs_by_id),
                    detail=event.summary,
                    tone=self._event_tone(event.event_type),
                )
                for event in packet.recent_events
            ],
            has_earlier_events=packet.event_window.has_earlier,
        )

    @staticmethod
    def _job_detail(job: WorkflowPacketJob) -> str:
        if job.status == "waiting" and job.waiting_reasons:
            labels = {
                "dependency": "Waiting for prerequisite",
                "exact_approval": "Waiting for exact approval",
                "approval_invalidated": "Approval must be renewed",
                "uncertain_external_effect": "External outcome uncertain",
            }
            return ", ".join(labels[reason.kind] for reason in job.waiting_reasons)
        attempt = f"Attempt {job.attempts} of {job.max_attempts}"
        if job.latest_run is not None:
            return f"{attempt}, latest Run {str(job.latest_run.run_id)[:8]}"
        return attempt

    @staticmethod
    def _event_aggregate(
        job_id: UUID | None,
        run_id: UUID | None,
        jobs_by_id: dict[UUID, WorkflowPacketJob],
    ) -> str:
        if job_id is not None and job_id in jobs_by_id:
            job = jobs_by_id[job_id]
            label = _JOB_LABELS.get(job.kind, "Job")
            return f"{label} Run" if run_id is not None else label
        return "Workflow"

    @staticmethod
    def _event_tone(event_type: str) -> Literal["progress", "success", "terminal"]:
        if event_type in {"workflow_completed", "workflow_cancelled"}:
            return "terminal"
        if event_type in {
            "draft_ready",
            "approval_granted",
            "email_send_succeeded",
            "workflow_jobs_proposed",
        }:
            return "success"
        return "progress"

    @staticmethod
    def _workflow_title(packet: WorkflowPacket) -> str:
        if packet.workflow.workflow_kind == RENEWAL_OUTREACH_KIND:
            policyholder = next(
                (
                    participant.name
                    for participant in packet.participants
                    if "Policyholder" in participant.roles
                ),
                None,
            )
            if policyholder is not None:
                return f"{policyholder} renewal outreach"
        return packet.workflow.objective[:255]

    @staticmethod
    def _workflow_status_label(packet: WorkflowPacket) -> str:
        if packet.workflow.status == "completed":
            return "Completed"
        if packet.workflow.status == "cancelled":
            return "Cancelled"
        if any(
            job.latest_run is not None and job.latest_run.outcome == "uncertain"
            for job in packet.jobs
        ):
            return "Outcome uncertain"
        if any(job.status == "failed" for job in packet.jobs):
            return "Needs attention"
        if any(
            job.status == "running" and job.kind == DRAFT_RENEWAL_EMAIL_KIND for job in packet.jobs
        ):
            return "Drafting email"
        if any(job.status == "running" for job in packet.jobs):
            return "In progress"
        if any(
            job.status == "waiting"
            and any(
                reason.kind in {"exact_approval", "approval_invalidated"}
                for reason in job.waiting_reasons
            )
            for job in packet.jobs
        ):
            return "Waiting for approval"
        if any(job.status == "queued" for job in packet.jobs):
            return "In progress"
        return "Waiting on prerequisite"

    @staticmethod
    def _approval_status(job: WorkflowPacketJob) -> WorkflowCheckpointStatus:
        if any(reason.kind == "dependency" for reason in job.waiting_reasons):
            return "unavailable"
        if job.approval is not None and job.approval.outcome in {"usable", "consumed"}:
            return "satisfied"
        return "waiting"

    @staticmethod
    def _topological_jobs(jobs: tuple[WorkflowPacketJob, ...]) -> tuple[WorkflowPacketJob, ...]:
        if len(jobs) < 2:
            return jobs
        jobs_by_id = {job.job_id: job for job in jobs}
        original_index = {job.job_id: index for index, job in enumerate(jobs)}
        dependents: dict[UUID, list[UUID]] = defaultdict(list)
        indegree: dict[UUID, int] = {job.job_id: 0 for job in jobs}
        for job in jobs:
            for dependency_id in job.depends_on_job_ids:
                if dependency_id not in jobs_by_id:
                    continue
                indegree[job.job_id] += 1
                dependents[dependency_id].append(job.job_id)
        ready = sorted(
            (job_id for job_id, count in indegree.items() if count == 0),
            key=original_index.__getitem__,
        )
        ordered: list[WorkflowPacketJob] = []
        while ready:
            job_id = ready.pop(0)
            ordered.append(jobs_by_id[job_id])
            for dependent_id in sorted(
                dependents[job_id],
                key=original_index.__getitem__,
            ):
                indegree[dependent_id] -= 1
                if indegree[dependent_id] == 0:
                    ready.append(dependent_id)
                    ready.sort(key=original_index.__getitem__)
        return tuple(ordered) if len(ordered) == len(jobs) else jobs

    @staticmethod
    def _activity_summary(
        activity: list[ChatAgentActivity],
        workflows: list[ChatWorkflowTelemetry],
    ) -> str:
        workflow_count = len(workflows)
        if workflow_count:
            noun = "Workflow" if workflow_count == 1 else "Workflows"
            if activity:
                if any(item.status == "failed" for item in activity):
                    return f"Found {workflow_count} {noun}, some actions failed"
                return f"Found context for {workflow_count} {noun}"
            return f"Updated {workflow_count} {noun}"
        action_count = len(activity)
        noun = "action" if action_count == 1 else "actions"
        if any(item.status == "failed" for item in activity):
            verb = "needs" if action_count == 1 else "need"
            return f"Agent {noun} {verb} attention"
        if any(item.status == "running" for item in activity):
            return f"{action_count} Agent {noun} in progress"
        return f"Completed {action_count} Agent {noun}"


__all__ = ["WorkflowTelemetryProjector"]
