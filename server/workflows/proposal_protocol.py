"""Replay-safe Workflow graph persistence and stable proposal receipts."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import datetime
from uuid import UUID, uuid4

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import (
    WorkflowCommandContext,
    WorkflowTrace,
    WorkflowTraceEvent,
    WorkflowTraceJob,
    WorkflowTraceWorkflow,
)
from .errors import WorkflowLifecycleError
from .models import (
    WorkflowEventRow,
    WorkflowJobDependencyRow,
    WorkflowJobRow,
    WorkflowRow,
)
from .registry import ValidatedWorkflowProposal, WorkflowKindRegistry


class WorkflowProposalProtocol:
    """Own one Cause-bound Job graph and its immutable acceptance receipt."""

    def __init__(self, registry: WorkflowKindRegistry) -> None:
        self._registry = registry

    def job_graph_digest(self, proposal: ValidatedWorkflowProposal) -> str:
        return self._digest(self._normalized_jobs(proposal))

    def workflow_digest(self, proposal: ValidatedWorkflowProposal) -> str:
        return self._digest(
            {
                "kind": proposal.kind,
                "objective": proposal.objective,
                "input": proposal.input,
                "jobs": self._normalized_jobs(proposal),
            }
        )

    async def event_for_cause(
        self,
        session: AsyncSession,
        context: WorkflowCommandContext,
    ) -> WorkflowEventRow | None:
        return await session.scalar(
            sa.select(WorkflowEventRow).where(
                WorkflowEventRow.event_type == "workflow_jobs_proposed",
                WorkflowEventRow.cause_type == context.cause_type,
                WorkflowEventRow.cause_id == context.cause_id,
            )
        )

    @staticmethod
    def replays(
        event: WorkflowEventRow,
        context: WorkflowCommandContext,
        proposal_digest: str,
        organization_party_id: UUID,
    ) -> bool:
        return (
            event.actor_id == str(context.actor_party_id)
            and event.data.get("organization_party_id") == str(organization_party_id)
            and event.data.get("proposal_digest") == proposal_digest
        )

    async def append_graph(
        self,
        session: AsyncSession,
        workflow: WorkflowRow,
        validated: ValidatedWorkflowProposal,
        context: WorkflowCommandContext,
        *,
        proposal_digest: str,
    ) -> WorkflowEventRow:
        job_ids = {job.key: uuid4() for job in validated.jobs}
        job_inputs = {
            job.key: self._registry.materialize_job_input(job, job_ids) for job in validated.jobs
        }
        job_rows = {
            job.key: WorkflowJobRow(
                id=job_ids[job.key],
                workflow_id=workflow.id,
                kind=job.kind,
                status=(
                    "waiting" if job.depends_on or job.contract.requires_approval else "queued"
                ),
                attempts=0,
                max_attempts=job.contract.max_attempts,
                input=job_inputs[job.key],
            )
            for job in validated.jobs
        }
        session.add_all(job_rows.values())
        await session.flush()
        session.add_all(
            [
                WorkflowJobDependencyRow(
                    workflow_id=workflow.id,
                    job_id=job_ids[job.key],
                    depends_on_job_id=job_ids[dependency],
                )
                for job in validated.jobs
                for dependency in job.depends_on
            ]
        )
        event = WorkflowEventRow(
            workflow_id=workflow.id,
            event_type="workflow_jobs_proposed",
            actor_type="party",
            actor_id=str(context.actor_party_id),
            cause_type=context.cause_type,
            cause_id=context.cause_id,
            data={
                "job_ids": [str(job_ids[job.key]) for job in validated.jobs],
                "job_receipts": [
                    {
                        "job_id": str(job_ids[job.key]),
                        "available_at": job_rows[job.key].available_at.isoformat(),
                    }
                    for job in validated.jobs
                ],
                "organization_party_id": str(workflow.organization_party_id),
                "proposal_digest": proposal_digest,
            },
        )
        session.add(event)
        await session.flush()
        return event

    async def read_receipt(
        self,
        session: AsyncSession,
        workflow: WorkflowRow,
        event: WorkflowEventRow,
    ) -> WorkflowTrace:
        try:
            job_ids = tuple(UUID(value) for value in event.data["job_ids"])
            available_at_by_job = {
                UUID(item["job_id"]): datetime.fromisoformat(item["available_at"])
                for item in event.data["job_receipts"]
            }
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkflowLifecycleError("Workflow proposal receipt is invalid") from exc
        if set(job_ids) != set(available_at_by_job):
            raise WorkflowLifecycleError("Workflow proposal receipt schedule is incomplete")
        jobs = (
            await session.scalars(
                sa.select(WorkflowJobRow)
                .where(
                    WorkflowJobRow.workflow_id == workflow.id,
                    WorkflowJobRow.id.in_(job_ids),
                )
                .order_by(WorkflowJobRow.created_at, WorkflowJobRow.id)
            )
        ).all()
        if len(jobs) != len(job_ids):
            raise WorkflowLifecycleError("Workflow proposal receipt is incomplete")
        dependencies = (
            await session.scalars(
                sa.select(WorkflowJobDependencyRow).where(
                    WorkflowJobDependencyRow.workflow_id == workflow.id,
                    WorkflowJobDependencyRow.job_id.in_(job_ids),
                )
            )
        ).all()
        dependencies_by_job: dict[UUID, list[UUID]] = defaultdict(list)
        for dependency in dependencies:
            dependencies_by_job[dependency.job_id].append(dependency.depends_on_job_id)
        for dependency_ids in dependencies_by_job.values():
            dependency_ids.sort()
        return WorkflowTrace(
            workflow=WorkflowTraceWorkflow(
                id=workflow.id,
                kind=workflow.kind,
                objective=workflow.objective,
                status="active",
                input=workflow.input,
                corrects_workflow_id=workflow.corrects_workflow_id,
                created_at=workflow.created_at,
            ),
            jobs=tuple(
                self._receipt_job(
                    job,
                    dependencies_by_job[job.id],
                    available_at_by_job[job.id],
                )
                for job in jobs
            ),
            runs=(),
            events=(self._receipt_event(event),),
            notifications=(),
        )

    def _receipt_job(
        self,
        job: WorkflowJobRow,
        dependency_ids: list[UUID],
        available_at: datetime,
    ) -> WorkflowTraceJob:
        requires_approval = self._registry.requires_approval(job.kind)
        waiting_reasons = tuple(f"dependency:{item}" for item in dependency_ids)
        if not waiting_reasons and requires_approval:
            waiting_reasons = ("exact_approval",)
        return WorkflowTraceJob(
            id=job.id,
            workflow_id=job.workflow_id,
            kind=job.kind,
            status="waiting" if dependency_ids or requires_approval else "queued",
            attempts=0,
            max_attempts=job.max_attempts,
            available_at=available_at,
            input=job.input,
            output=None,
            revises_job_id=job.revises_job_id,
            depends_on_job_ids=tuple(dependency_ids),
            waiting_reasons=waiting_reasons,
            created_at=job.created_at,
        )

    @staticmethod
    def _receipt_event(event: WorkflowEventRow) -> WorkflowTraceEvent:
        return WorkflowTraceEvent(
            id=event.id,
            workflow_id=event.workflow_id,
            job_id=event.job_id,
            run_id=event.run_id,
            approval_grant_id=event.approval_grant_id,
            event_type=event.event_type,
            actor_type=event.actor_type,
            actor_id=event.actor_id,
            cause_type=event.cause_type,
            cause_id=event.cause_id,
            data=event.data,
            occurred_at=event.occurred_at,
        )

    @staticmethod
    def _normalized_jobs(proposal: ValidatedWorkflowProposal) -> list[dict[str, object]]:
        return [
            {
                "key": job.key,
                "kind": job.kind,
                "input": job.proposed_input.model_dump(mode="json"),
                "depends_on": job.depends_on,
            }
            for job in proposal.jobs
        ]

    @staticmethod
    def _digest(value: object) -> str:
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


__all__ = ["WorkflowProposalProtocol"]
