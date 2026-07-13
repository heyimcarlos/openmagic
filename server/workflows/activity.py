"""Durable, sanitized receipts for visible Interaction Agent activity."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

import sqlalchemy as sa

from .database import WorkflowDatabase
from .models import InteractionActivityReceiptRow, InteractionCauseRow


class InteractionActivityAction(StrEnum):
    SEARCH_WORKFLOWS = "search_workflows"
    READ_WORKFLOW_PACKET = "read_workflow_packet"
    PROPOSE_WORKFLOW = "propose_workflow"
    PROPOSE_WORKFLOW_WORK = "propose_workflow_work"
    REVISE_WORKFLOW_WORK = "revise_workflow_work"
    APPROVE_JOB = "approve_job"

    @classmethod
    def _missing_(cls, value: object) -> InteractionActivityAction | None:
        if value == "propose_renewal_email":
            return cls.PROPOSE_WORKFLOW_WORK
        return None


class InteractionActivityStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class InteractionActivityReceipt:
    id: UUID
    cause_id: str
    sequence: int
    action: InteractionActivityAction
    status: InteractionActivityStatus
    workflow_id: UUID | None
    created_at: datetime
    finished_at: datetime | None


class InteractionActivityStore:
    """Serialize receipts through their Interaction Cause and expose actor-scoped reads."""

    def __init__(self, database: WorkflowDatabase) -> None:
        self._database = database

    async def start(
        self,
        *,
        cause_id: str,
        action: InteractionActivityAction,
    ) -> InteractionActivityReceipt:
        action = InteractionActivityAction(action)
        async with self._database.transaction() as session:
            cause = await session.scalar(
                sa.select(InteractionCauseRow)
                .where(InteractionCauseRow.id == cause_id)
                .with_for_update()
            )
            if cause is None:
                raise LookupError("Interaction Cause does not exist")
            latest_sequence = await session.scalar(
                sa.select(sa.func.max(InteractionActivityReceiptRow.sequence)).where(
                    InteractionActivityReceiptRow.cause_id == cause_id
                )
            )
            row = InteractionActivityReceiptRow(
                cause_id=cause_id,
                sequence=(latest_sequence or 0) + 1,
                action_key=action.value,
                status=InteractionActivityStatus.RUNNING.value,
            )
            session.add(row)
            await session.flush()
            return self._receipt(row)

    async def finish(
        self,
        receipt_id: UUID,
        *,
        status: InteractionActivityStatus,
        workflow_id: UUID | None = None,
    ) -> InteractionActivityReceipt:
        status = InteractionActivityStatus(status)
        if status is InteractionActivityStatus.RUNNING:
            raise ValueError("a finished activity receipt requires a terminal status")
        async with self._database.transaction() as session:
            row = await session.scalar(
                sa.select(InteractionActivityReceiptRow)
                .where(InteractionActivityReceiptRow.id == receipt_id)
                .with_for_update()
            )
            if row is None:
                raise LookupError("Interaction activity receipt does not exist")
            if row.status != InteractionActivityStatus.RUNNING.value:
                if row.status == status.value and row.workflow_id == workflow_id:
                    return self._receipt(row)
                raise RuntimeError("Interaction activity receipt already finished")
            row.status = status.value
            row.workflow_id = workflow_id
            row.finished_at = datetime.now(UTC)
            await session.flush()
            return self._receipt(row)

    async def list_for_actor_causes(
        self,
        *,
        actor_party_id: UUID,
        cause_ids: list[str],
    ) -> tuple[InteractionActivityReceipt, ...]:
        """Read only receipts whose durable Cause belongs to the authenticated Party."""

        if not cause_ids:
            return ()
        async with self._database.read_transaction() as session:
            rows = (
                await session.scalars(
                    sa.select(InteractionActivityReceiptRow)
                    .join(
                        InteractionCauseRow,
                        InteractionCauseRow.id == InteractionActivityReceiptRow.cause_id,
                    )
                    .where(
                        InteractionCauseRow.actor_party_id == actor_party_id,
                        InteractionActivityReceiptRow.cause_id.in_(set(cause_ids)),
                    )
                    .order_by(
                        InteractionActivityReceiptRow.cause_id,
                        InteractionActivityReceiptRow.sequence,
                    )
                )
            ).all()
        return tuple(self._receipt(row) for row in rows)

    @staticmethod
    def _receipt(row: InteractionActivityReceiptRow) -> InteractionActivityReceipt:
        return InteractionActivityReceipt(
            id=row.id,
            cause_id=row.cause_id,
            sequence=row.sequence,
            action=InteractionActivityAction(row.action_key),
            status=InteractionActivityStatus(row.status),
            workflow_id=row.workflow_id,
            created_at=row.created_at,
            finished_at=row.finished_at,
        )


__all__ = [
    "InteractionActivityAction",
    "InteractionActivityReceipt",
    "InteractionActivityStatus",
    "InteractionActivityStore",
]
