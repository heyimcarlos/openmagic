"""Resolve simulated inbound SMS identities without accepting caller-supplied roles."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa

from .database import WorkflowDatabase
from .identity_models import PartyIdentifierRow, PartyRow


@dataclass(frozen=True)
class ResolvedSmsParty:
    party_id: UUID
    display_name: str
    phone: str


def normalize_sms_phone(value: str) -> str:
    """Canonicalize a simulator phone number into a minimal E.164 representation."""

    digits = re.sub(r"\D", "", value)
    if not 8 <= len(digits) <= 15:
        raise ValueError("SMS sender phone must contain 8 to 15 digits")
    return f"+{digits}"


async def resolve_sms_party(database: WorkflowDatabase, phone: str) -> ResolvedSmsParty:
    """Resolve channel continuity or create one stable Provisional Party."""

    normalized = normalize_sms_phone(phone)
    async with database.transaction() as session:
        lock_key = int.from_bytes(
            hashlib.sha256(normalized.encode()).digest()[:8],
            "big",
            signed=True,
        )
        await session.execute(sa.select(sa.func.pg_advisory_xact_lock(lock_key)))
        row = (
            await session.execute(
                sa.select(PartyIdentifierRow, PartyRow)
                .join(PartyRow, PartyRow.id == PartyIdentifierRow.party_id)
                .where(
                    PartyIdentifierRow.kind == "phone",
                    PartyIdentifierRow.value == normalized,
                    PartyIdentifierRow.revoked_at.is_(None),
                )
                .with_for_update()
            )
        ).one_or_none()
        if row is None:
            party = PartyRow(
                id=uuid4(),
                kind="person",
                display_name=f"Caller {normalized[-4:]}",
            )
            session.add(party)
            await session.flush()
            session.add(
                PartyIdentifierRow(
                    id=uuid4(),
                    party_id=party.id,
                    kind="phone",
                    value=normalized,
                    verified_at=None,
                )
            )
            return ResolvedSmsParty(
                party_id=party.id,
                display_name=party.display_name,
                phone=normalized,
            )

        identifier, party = row
        return ResolvedSmsParty(
            party_id=party.id,
            display_name=party.display_name,
            phone=identifier.value,
        )


async def find_sms_party(
    database: WorkflowDatabase,
    phone: str,
) -> ResolvedSmsParty | None:
    """Read an existing SMS Party without creating channel identity state."""

    normalized = normalize_sms_phone(phone)
    async with database.read_transaction() as session:
        row = (
            await session.execute(
                sa.select(PartyIdentifierRow, PartyRow)
                .join(PartyRow, PartyRow.id == PartyIdentifierRow.party_id)
                .where(
                    PartyIdentifierRow.kind == "phone",
                    PartyIdentifierRow.value == normalized,
                    PartyIdentifierRow.revoked_at.is_(None),
                )
            )
        ).one_or_none()
    if row is None:
        return None
    identifier, party = row
    return ResolvedSmsParty(
        party_id=party.id,
        display_name=party.display_name,
        phone=identifier.value,
    )


def sms_interaction_id(phone: str) -> str:
    """Derive one server-owned SMS interaction identity from the sender phone."""

    normalized = normalize_sms_phone(phone)
    digest = hashlib.sha256(normalized.encode()).hexdigest()
    return f"sms:{digest[:32]}"


__all__ = [
    "ResolvedSmsParty",
    "find_sms_party",
    "normalize_sms_phone",
    "resolve_sms_party",
    "sms_interaction_id",
]
