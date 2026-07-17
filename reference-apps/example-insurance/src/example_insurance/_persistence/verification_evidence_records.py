"""Transaction-scoped verification evidence assembled from canonical owners."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast
from uuid import UUID

from openmagic_runtime.evidence import (
    RuntimeDeliveryEvidence,
    RuntimeEvidenceReader,
    RuntimeInstanceEvidence,
)
from psycopg import Connection
from psycopg.rows import dict_row

from example_insurance._persistence.durable_values import uuid_value


def _event_source(value: object) -> tuple[Literal["command", "attempt"], UUID]:
    if not isinstance(value, Mapping) or set(value) != {"kind", "identifier"}:
        raise RuntimeError("Verification Domain Event has malformed lineage")
    lineage = cast(Mapping[str, object], value)
    kind = lineage["kind"]
    if kind == "command":
        source_kind: Literal["command", "attempt"] = "command"
    elif kind == "attempt":
        source_kind = "attempt"
    else:
        raise RuntimeError("Verification Domain Event has an unsupported lineage kind")
    identifier = lineage["identifier"]
    if not isinstance(identifier, str):
        raise RuntimeError("Verification Domain Event has malformed lineage identity")
    try:
        return source_kind, UUID(identifier)
    except ValueError as error:
        raise RuntimeError("Verification Domain Event has malformed lineage identity") from error


@dataclass(frozen=True)
class ApplicationEventEvidence:
    event_id: UUID
    source_kind: Literal["command", "attempt"]
    source_id: UUID

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> ApplicationEventEvidence:
        source_kind, source_id = _event_source(record["cause"])
        return cls(
            event_id=uuid_value(record["event_id"]),
            source_kind=source_kind,
            source_id=source_id,
        )


@dataclass(frozen=True)
class VerificationApplicationEvidence:
    challenge_id: UUID
    protected_command_id: UUID
    protected_workflow_id: UUID
    protected_thread_id: UUID
    approval_grant_id: UUID
    identifier_thread_id: UUID
    verification_workflow_id: UUID
    verification_instance_id: UUID
    challenge_event: ApplicationEventEvidence
    challenge_delivery_id: UUID
    session_id: UUID
    submit_command_id: UUID
    authorized_delivery_id: UUID
    authorized_event: ApplicationEventEvidence
    verification_runtime: RuntimeInstanceEvidence
    challenge_delivery: RuntimeDeliveryEvidence
    authorized_delivery: RuntimeDeliveryEvidence


def load_verification_application_evidence(
    connection: Connection[tuple[Any, ...]], challenge_id: UUID
) -> VerificationApplicationEvidence:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT c.challenge_id, c.protected_command_id, c.protected_workflow_id, "
            "c.thread_id AS protected_thread_id, "
            "p.approval_grant_id, "
            "c.destination_thread_id AS identifier_thread_id, "
            "w.workflow_id AS verification_workflow_id, "
            "w.instance_id AS verification_instance_id, "
            "e.event_id AS challenge_event_id, e.cause AS challenge_event_cause, "
            "w.delivery_id AS challenge_delivery_id, s.session_id, s.submit_command_id, "
            "p.authorized_delivery_id FROM example_insurance.verification_challenges AS c "
            "JOIN example_insurance.verification_workflows AS w "
            "ON w.challenge_id = c.challenge_id "
            "JOIN example_insurance.verification_events AS e "
            "ON e.event_id = w.delivery_event_id AND e.workflow_id = w.workflow_id "
            "JOIN example_insurance.verification_sessions AS s "
            "ON s.challenge_id = c.challenge_id "
            "JOIN example_insurance.protected_commands AS p "
            "ON p.protected_command_id = c.protected_command_id "
            "WHERE c.challenge_id = %s AND c.state = 'accepted' "
            "AND p.state = 'authorized'",
            (challenge_id,),
        ).fetchone()
    if record is None:
        raise KeyError(f"Accepted Verification Challenge not found: {challenge_id}")
    reader = RuntimeEvidenceReader(connection)
    verification_instance_id = uuid_value(record["verification_instance_id"])
    challenge_delivery = reader.delivery(uuid_value(record["challenge_delivery_id"]))
    authorized_delivery = reader.delivery(uuid_value(record["authorized_delivery_id"]))
    with connection.cursor(row_factory=dict_row) as cursor:
        authorized_event_record = cursor.execute(
            "SELECT event_id, cause FROM example_insurance.domain_events WHERE event_id = %s",
            (authorized_delivery.domain_event_id,),
        ).fetchone()
    if authorized_event_record is None:
        raise RuntimeError("Authorized Delivery is missing its application Domain Event")
    challenge_event = ApplicationEventEvidence.decode(
        {
            "event_id": record["challenge_event_id"],
            "cause": record["challenge_event_cause"],
        }
    )
    if challenge_delivery.domain_event_id != challenge_event.event_id:
        raise RuntimeError("Challenge Delivery is unrelated to its application Domain Event")
    return VerificationApplicationEvidence(
        challenge_id=uuid_value(record["challenge_id"]),
        protected_command_id=uuid_value(record["protected_command_id"]),
        protected_workflow_id=uuid_value(record["protected_workflow_id"]),
        protected_thread_id=uuid_value(record["protected_thread_id"]),
        approval_grant_id=uuid_value(record["approval_grant_id"]),
        identifier_thread_id=uuid_value(record["identifier_thread_id"]),
        verification_workflow_id=uuid_value(record["verification_workflow_id"]),
        verification_instance_id=verification_instance_id,
        challenge_event=challenge_event,
        challenge_delivery_id=challenge_delivery.delivery_id,
        session_id=uuid_value(record["session_id"]),
        submit_command_id=uuid_value(record["submit_command_id"]),
        authorized_delivery_id=authorized_delivery.delivery_id,
        authorized_event=ApplicationEventEvidence.decode(authorized_event_record),
        verification_runtime=reader.instance(verification_instance_id),
        challenge_delivery=challenge_delivery,
        authorized_delivery=authorized_delivery,
    )


__all__ = [
    "ApplicationEventEvidence",
    "VerificationApplicationEvidence",
    "load_verification_application_evidence",
]
