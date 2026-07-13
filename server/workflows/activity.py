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
class InteractionActivityPresentation:
    """Bounded, application-authored result context safe for chat display."""

    summary: str
    items: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.summary.strip() or len(self.summary) > 255:
            raise ValueError("activity summary must contain at most 255 characters")
        if len(self.items) > 8:
            raise ValueError("activity presentation may contain at most eight items")
        if any(not item.strip() or len(item) > 255 for item in self.items):
            raise ValueError("activity items must contain at most 255 characters")


@dataclass(frozen=True)
class InteractionActivityReceipt:
    id: UUID
    cause_id: str
    sequence: int
    action: InteractionActivityAction
    status: InteractionActivityStatus
    workflow_id: UUID | None
    input_summary: str | None
    presentation: InteractionActivityPresentation | None
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
        input_summary: str | None = None,
    ) -> InteractionActivityReceipt:
        action = InteractionActivityAction(action)
        if input_summary is not None and (not input_summary.strip() or len(input_summary) > 500):
            raise ValueError("activity input summary must contain at most 500 characters")
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
                input_summary=input_summary,
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
        presentation: InteractionActivityPresentation | None = None,
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
                serialized_presentation = (
                    {
                        "summary": presentation.summary,
                        "items": list(presentation.items),
                    }
                    if presentation is not None
                    else None
                )
                if (
                    row.status == status.value
                    and row.workflow_id == workflow_id
                    and row.presentation == serialized_presentation
                ):
                    return self._receipt(row)
                raise RuntimeError("Interaction activity receipt already finished")
            row.status = status.value
            row.workflow_id = workflow_id
            row.presentation = (
                {
                    "summary": presentation.summary,
                    "items": list(presentation.items),
                }
                if presentation is not None
                else None
            )
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
        presentation = None
        if isinstance(row.presentation, dict):
            summary = row.presentation.get("summary")
            items = row.presentation.get("items", [])
            if (
                isinstance(summary, str)
                and isinstance(items, list)
                and all(isinstance(item, str) for item in items)
            ):
                presentation = InteractionActivityPresentation(
                    summary=summary,
                    items=tuple(items),
                )
        return InteractionActivityReceipt(
            id=row.id,
            cause_id=row.cause_id,
            sequence=row.sequence,
            action=InteractionActivityAction(row.action_key),
            status=InteractionActivityStatus(row.status),
            workflow_id=row.workflow_id,
            input_summary=row.input_summary,
            presentation=presentation,
            created_at=row.created_at,
            finished_at=row.finished_at,
        )


__all__ = [
    "InteractionActivityAction",
    "InteractionActivityPresentation",
    "InteractionActivityReceipt",
    "InteractionActivityStatus",
    "InteractionActivityStore",
]
