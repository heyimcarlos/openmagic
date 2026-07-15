"""Canonical persistence writes for renewal Approval Grants."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from openmagic_runtime.commands import Actor, Cause
from psycopg import Connection
from psycopg.types.json import Jsonb

from example_insurance.renewal_records import actor_record, cause_record


def record_approval_grant(
    connection: Connection[tuple[Any, ...]],
    *,
    approval_grant_id: UUID,
    decision_id: UUID,
    workflow_id: UUID,
    step_id: UUID,
    effect_fingerprint: str,
    actor: Actor,
    cause: Cause,
) -> None:
    connection.execute(
        "INSERT INTO example_insurance.approval_grants "
        "(approval_grant_id, decision_id, workflow_id, step_id, effect_fingerprint, "
        "actor, cause) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            approval_grant_id,
            decision_id,
            workflow_id,
            step_id,
            effect_fingerprint,
            Jsonb(actor_record(actor)),
            Jsonb(cause_record(cause)),
        ),
    )


def invalidate_unconsumed_grants(
    connection: Connection[tuple[Any, ...]], workflow_id: UUID
) -> None:
    connection.execute(
        "UPDATE example_insurance.approval_grants SET invalidated_at = clock_timestamp() "
        "WHERE workflow_id = %s AND consumed_at IS NULL AND invalidated_at IS NULL",
        (workflow_id,),
    )


def mark_grant_consumed(connection: Connection[tuple[Any, ...]], approval_grant_id: UUID) -> None:
    connection.execute(
        "UPDATE example_insurance.approval_grants SET consumed_at = clock_timestamp() "
        "WHERE approval_grant_id = %s",
        (approval_grant_id,),
    )


__all__ = [
    "invalidate_unconsumed_grants",
    "mark_grant_consumed",
    "record_approval_grant",
]
