"""Canonical persistence helpers for renewal events and effect evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID, uuid4

from openmagic_runtime.commands import Actor, Cause
from psycopg import Connection
from psycopg.types.json import Jsonb

from example_insurance.renewal_effect_policy import EffectObservation

EffectEvidenceSource = Literal[
    "provider_response",
    "provider_lookup",
    "worker_loss_after_fence",
]


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


def record_effect_evidence(
    connection: Connection[tuple[Any, ...]],
    *,
    logical_effect_id: UUID,
    attempt_id: UUID,
    classification: EffectObservation,
    source: EffectEvidenceSource,
    provider_request_id: str | None,
) -> None:
    connection.execute(
        "INSERT INTO example_insurance.external_effect_evidence "
        "(evidence_id, logical_effect_id, attempt_id, classification, source, "
        "provider_request_id, details) VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (
            uuid4(),
            logical_effect_id,
            attempt_id,
            classification,
            source,
            provider_request_id,
            Jsonb({"classification": classification, "source": source}),
        ),
    )


__all__ = [
    "CommandEventLineage",
    "EffectEvidenceSource",
    "actor_record",
    "cause_record",
    "record_effect_evidence",
    "record_event",
]
