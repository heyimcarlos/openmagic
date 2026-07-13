"""Durable authentication and serialization for human Interaction Causes."""

from __future__ import annotations

import hashlib

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from .contracts import RecordInteractionCauseCommand, WorkflowCommandContext
from .database import WorkflowDatabase
from .errors import WorkflowLifecycleError
from .models import InteractionCauseRow


class WorkflowInteractionCauseProtocol:
    """Record one authenticated Cause and lock it before derived mutation."""

    def __init__(self, database: WorkflowDatabase) -> None:
        self._database = database

    async def record(self, command: RecordInteractionCauseCommand) -> None:
        content_digest = hashlib.sha256(command.content.encode()).hexdigest()
        async with self._database.transaction() as session:
            inserted = await session.scalar(
                pg_insert(InteractionCauseRow)
                .values(
                    id=command.context.cause_id,
                    cause_type=command.context.cause_type,
                    actor_party_id=command.context.actor_party_id,
                    content_digest=content_digest,
                )
                .on_conflict_do_nothing(index_elements=(InteractionCauseRow.id,))
                .returning(InteractionCauseRow.id)
            )
            if inserted is not None:
                return
            existing = await self.require(session, command.context)
            if existing.content_digest != content_digest:
                raise WorkflowLifecycleError("Interaction Cause identity conflicts")

    @staticmethod
    async def require(
        session: AsyncSession,
        context: WorkflowCommandContext,
    ) -> InteractionCauseRow:
        cause = await session.scalar(
            sa.select(InteractionCauseRow)
            .where(InteractionCauseRow.id == context.cause_id)
            .with_for_update()
        )
        if (
            cause is None
            or cause.cause_type != context.cause_type
            or cause.actor_party_id != context.actor_party_id
        ):
            raise WorkflowLifecycleError("Interaction Cause is not authenticated")
        return cause


__all__ = ["WorkflowInteractionCauseProtocol"]
