"""Canonical persistence helpers for renewal Domain Events."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from openmagic_runtime.commands import Actor, Cause
from psycopg import Connection
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class CommandEventLineage:
    actor: Actor
    command_id: UUID

    @property
    def cause(self) -> Cause:
        return Cause("command", str(self.command_id))


def actor_record(actor: Actor) -> dict[str, str]:
    return {"kind": actor.kind, "identifier": actor.identifier}


def cause_record(cause: Cause) -> dict[str, str]:
    return {"kind": cause.kind, "identifier": cause.identifier}


def record_event(
    connection: Connection[tuple[Any, ...]],
    *,
    event_type: str,
    workflow_id: UUID,
    actor: Actor,
    cause: Cause,
    payload: dict[str, Any],
) -> UUID:
    event_id = uuid4()
    connection.execute(
        "INSERT INTO example_insurance.domain_events "
        "(event_id, event_type, schema_version, workflow_id, actor, cause, payload) "
        "VALUES (%s, %s, 1, %s, %s, %s, %s)",
        (
            event_id,
            event_type,
            workflow_id,
            Jsonb(actor_record(actor)),
            Jsonb(cause_record(cause)),
            Jsonb(payload),
        ),
    )
    return event_id


__all__ = [
    "CommandEventLineage",
    "actor_record",
    "cause_record",
    "record_event",
]
