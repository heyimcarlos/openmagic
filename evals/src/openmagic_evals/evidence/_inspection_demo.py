"""Typed PostgreSQL observations for Agent safety and public demonstrations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from openmagic_evals.evidence._inspection_base import InspectionDatabase


@dataclass(frozen=True)
class AgentSafetyObservation:
    message_source_kinds: tuple[str, ...]
    delivery_attempt_ids: tuple[UUID, ...]
    command_count: int
    delivery_thread_ids: tuple[UUID, ...]
    retry_authorization_count: int


@dataclass(frozen=True)
class VerificationDemoObservation:
    instance_id: UUID
    workflow_id: UUID
    session_id: UUID
    step_attempt_ids: tuple[tuple[UUID, UUID], ...]

    @classmethod
    def decode(
        cls,
        record: Mapping[str, Any],
        attempts: tuple[Mapping[str, Any], ...],
    ) -> VerificationDemoObservation:
        return cls(
            instance_id=UUID(str(record["delivery_instance_id"])),
            workflow_id=UUID(str(record["delivery_workflow_id"])),
            session_id=UUID(str(record["session_id"])),
            step_attempt_ids=tuple(
                (UUID(str(attempt["step_id"])), UUID(str(attempt["attempt_id"])))
                for attempt in attempts
            ),
        )


def _uuid_column(records: list[dict[str, Any]], column: str) -> tuple[UUID, ...]:
    return tuple(UUID(str(record[column])) for record in records)


class DemoInspection(InspectionDatabase):
    def agent_safety(self, thread_id: UUID, instance_id: UUID) -> AgentSafetyObservation:
        with self.read_snapshot() as cursor:
            source_records = cursor.execute(
                "SELECT source_kind FROM openmagic_runtime.messages WHERE thread_id = %s",
                (thread_id,),
            ).fetchall()
            delivery_attempt_records = cursor.execute(
                "SELECT a.delivery_attempt_id FROM openmagic_runtime.delivery_attempts AS a "
                "JOIN openmagic_runtime.deliveries AS d ON d.delivery_id = a.delivery_id "
                "WHERE d.thread_id = %s ORDER BY a.created_at, a.delivery_attempt_id",
                (thread_id,),
            ).fetchall()
            command_record = cursor.execute(
                "SELECT count(*) AS command_count FROM openmagic_runtime.command_receipts"
            ).fetchone()
            delivery_records = cursor.execute(
                "SELECT d.thread_id FROM openmagic_runtime.deliveries AS d "
                "JOIN example_insurance.domain_events AS e ON e.event_id = d.domain_event_id "
                "JOIN example_insurance.renewal_workflows AS w ON w.workflow_id = e.workflow_id "
                "WHERE w.instance_id = %s ORDER BY d.delivery_id",
                (instance_id,),
            ).fetchall()
            retry_record = cursor.execute(
                "SELECT count(*) AS retry_count FROM openmagic_runtime.trace_events "
                "WHERE instance_id = %s AND event_type = 'step_retry_authorized'",
                (instance_id,),
            ).fetchone()
        return AgentSafetyObservation(
            message_source_kinds=tuple(str(record["source_kind"]) for record in source_records),
            delivery_attempt_ids=_uuid_column(delivery_attempt_records, "delivery_attempt_id"),
            command_count=0 if command_record is None else int(command_record["command_count"]),
            delivery_thread_ids=_uuid_column(delivery_records, "thread_id"),
            retry_authorization_count=0
            if retry_record is None
            else int(retry_record["retry_count"]),
        )

    def renewal_demo_ids(self, instance_id: UUID) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
        with self.read_snapshot() as cursor:
            trace_records = cursor.execute(
                "SELECT trace_event_id FROM openmagic_runtime.trace_events "
                "WHERE instance_id = %s ORDER BY sequence",
                (instance_id,),
            ).fetchall()
            delivery_records = cursor.execute(
                "SELECT a.delivery_attempt_id FROM openmagic_runtime.delivery_attempts AS a "
                "JOIN openmagic_runtime.deliveries AS d ON d.delivery_id = a.delivery_id "
                "JOIN example_insurance.domain_events AS e ON e.event_id = d.domain_event_id "
                "JOIN example_insurance.renewal_workflows AS w ON w.workflow_id = e.workflow_id "
                "WHERE w.instance_id = %s ORDER BY a.created_at, a.delivery_attempt_id",
                (instance_id,),
            ).fetchall()
        return (
            _uuid_column(trace_records, "trace_event_id"),
            _uuid_column(delivery_records, "delivery_attempt_id"),
        )

    def verification_demo(self, challenge_id: UUID) -> VerificationDemoObservation | None:
        with self.read_snapshot() as cursor:
            record = cursor.execute(
                "SELECT c.delivery_instance_id, c.delivery_workflow_id, s.session_id "
                "FROM example_insurance.verification_challenges AS c "
                "JOIN example_insurance.verification_sessions AS s USING (challenge_id) "
                "WHERE c.challenge_id = %s",
                (challenge_id,),
            ).fetchone()
            attempts = (
                cursor.execute(
                    "SELECT step_id, attempt_id FROM openmagic_runtime.attempts "
                    "WHERE instance_id = %s ORDER BY created_at, attempt_id",
                    (record["delivery_instance_id"],),
                ).fetchall()
                if record is not None
                else []
            )
        return (
            None if record is None else VerificationDemoObservation.decode(record, tuple(attempts))
        )


__all__ = [
    "AgentSafetyObservation",
    "DemoInspection",
    "VerificationDemoObservation",
]
