"""Canonical V0 Gmail effect resolution and fingerprinting."""

from __future__ import annotations

import hashlib
import json
from typing import Literal
from uuid import UUID

import sqlalchemy as sa
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from .errors import WorkflowLifecycleError
from .identity_models import PartyIdentifierRow
from .models import WorkflowJobRow


class EmailSendEffectV1(BaseModel):
    """One exact plain-text email effect approved and dispatched as a unit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: Literal["send_email"] = "send_email"
    sender_mailbox_id: UUID
    expected_sender_address: EmailStr
    to: tuple[EmailStr, ...]
    cc: tuple[EmailStr, ...] = ()
    bcc: tuple[EmailStr, ...] = ()
    subject: str
    body: str
    body_format: Literal["plain_text"] = "plain_text"


class EmailSendExecutionContextV1(BaseModel):
    """Stable OpenMagic correlation supplied to one adapter invocation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    job_id: UUID
    run_id: UUID
    effect_fingerprint: str


class EmailSendDispatchV1(BaseModel):
    """Immutable request authorized by a committed dispatch boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    workflow_id: UUID
    approval_grant_id: UUID
    effect: EmailSendEffectV1
    context: EmailSendExecutionContextV1
    effect_fingerprint: str


async def resolve_email_effect(
    session: AsyncSession,
    workflow_id: UUID,
    send: WorkflowJobRow,
    *,
    sender_mailbox_id: UUID | None = None,
    require_current_sender: bool = True,
) -> EmailSendEffectV1:
    """Resolve immutable Job references and the current stable sender identifier."""

    sender_address = send.input.get("sender_mailbox")
    if not isinstance(sender_address, str):
        raise WorkflowLifecycleError("Send Job sender mailbox is invalid")
    sender_predicates = [
        PartyIdentifierRow.kind == "email",
        PartyIdentifierRow.value == sender_address,
        PartyIdentifierRow.verified_at.is_not(None),
    ]
    if sender_mailbox_id is not None:
        sender_predicates.append(PartyIdentifierRow.id == sender_mailbox_id)
    if require_current_sender:
        sender_predicates.append(PartyIdentifierRow.revoked_at.is_(None))
    sender_ids = (
        await session.scalars(sa.select(PartyIdentifierRow.id).where(*sender_predicates))
    ).all()
    if len(sender_ids) != 1:
        raise WorkflowLifecycleError("Send Job sender mailbox is not currently verified")

    resolved = dict(send.input)
    for field, value in send.input.items():
        if not isinstance(value, dict) or set(value) != {"job_output", "field"}:
            continue
        try:
            source_id = UUID(str(value["job_output"]))
            source_field = str(value["field"])
        except (KeyError, TypeError, ValueError) as exc:
            raise WorkflowLifecycleError("Send Job input reference is invalid") from exc
        source = await session.scalar(
            sa.select(WorkflowJobRow).where(
                WorkflowJobRow.workflow_id == workflow_id,
                WorkflowJobRow.id == source_id,
            )
        )
        if source is None or source.output is None or source_field not in source.output:
            raise WorkflowLifecycleError("Send Job input reference is unresolved")
        resolved[field] = source.output[source_field]

    return EmailSendEffectV1(
        sender_mailbox_id=sender_ids[0],
        expected_sender_address=sender_address,
        to=resolved.get("to", ()),
        cc=resolved.get("cc", ()),
        bcc=resolved.get("bcc", ()),
        subject=resolved.get("subject", ""),
        body=resolved.get("body", ""),
    )


def fingerprint_email_effect(effect: EmailSendEffectV1) -> str:
    """Hash the normalized complete Effect-Defining Input."""

    encoded = json.dumps(
        effect.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "EmailSendDispatchV1",
    "EmailSendEffectV1",
    "EmailSendExecutionContextV1",
    "fingerprint_email_effect",
    "resolve_email_effect",
]
