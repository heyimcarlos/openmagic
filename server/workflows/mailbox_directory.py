"""Trusted Party mailbox lookup behind the public Workflow boundary."""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, EmailStr

from .database import WorkflowDatabase
from .identity_models import PartyIdentifierRow


class VerifiedMailbox(BaseModel):
    """One current verified mailbox identifier owned by a Party."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: UUID
    address: EmailStr


async def resolve_verified_mailbox(
    database: WorkflowDatabase,
    party_id: UUID,
) -> VerifiedMailbox | None:
    """Return one unambiguous current mailbox, otherwise fail closed."""

    async with database.read_transaction() as session:
        identifiers = (
            await session.scalars(
                sa.select(PartyIdentifierRow).where(
                    PartyIdentifierRow.party_id == party_id,
                    PartyIdentifierRow.kind == "email",
                    PartyIdentifierRow.verified_at.is_not(None),
                    PartyIdentifierRow.revoked_at.is_(None),
                )
            )
        ).all()
    if len(identifiers) != 1:
        return None
    identifier = identifiers[0]
    return VerifiedMailbox(id=identifier.id, address=identifier.value)


__all__ = ["VerifiedMailbox", "resolve_verified_mailbox"]
