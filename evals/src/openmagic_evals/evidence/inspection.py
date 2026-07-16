"""Typed read-only PostgreSQL observations owned by the private evidence package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import LiteralString
from uuid import UUID

import psycopg


@dataclass(frozen=True)
class QueueState:
    pending_steps: int
    pending_deliveries: int


@dataclass(frozen=True)
class AttemptAuthority:
    instance_id: UUID
    step_id: UUID
    attempt_id: UUID
    worker_id: str


@dataclass(frozen=True)
class DeliveryAuthority:
    delivery_id: UUID
    delivery_attempt_id: UUID
    thread_id: UUID
    worker_id: str


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


@dataclass(frozen=True)
class TransactionState:
    command_receipts: int
    workflows: int
    instances: int
    domain_events: int
    steps: int
    trace_events: int


class EvidenceInspection:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url

    def queue_state(self) -> QueueState:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = connection.execute(
                "SELECT "
                "(SELECT count(*) FROM openmagic_runtime.steps WHERE state = 'pending'), "
                "(SELECT count(*) FROM openmagic_runtime.deliveries WHERE status = 'pending')"
            ).fetchone()
        if row is None:
            raise RuntimeError("PostgreSQL did not return queue observations")
        return QueueState(pending_steps=int(row[0]), pending_deliveries=int(row[1]))

    def active_attempt(self, worker_id: str) -> AttemptAuthority | None:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = connection.execute(
                "SELECT instance_id, step_id, attempt_id, worker_id "
                "FROM openmagic_runtime.attempts "
                "WHERE worker_id = %s AND state = 'leased' "
                "AND lease_expires_at > clock_timestamp() "
                "ORDER BY created_at DESC LIMIT 1",
                (worker_id,),
            ).fetchone()
        if row is None:
            return None
        return AttemptAuthority(
            instance_id=UUID(str(row[0])),
            step_id=UUID(str(row[1])),
            attempt_id=UUID(str(row[2])),
            worker_id=str(row[3]),
        )

    def active_delivery(self, worker_id: str) -> DeliveryAuthority | None:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = connection.execute(
                "SELECT a.delivery_id, a.delivery_attempt_id, d.thread_id, a.worker_id "
                "FROM openmagic_runtime.delivery_attempts AS a "
                "JOIN openmagic_runtime.deliveries AS d ON d.delivery_id = a.delivery_id "
                "WHERE a.worker_id = %s AND a.state = 'running' "
                "AND a.lease_expires_at > clock_timestamp() "
                "ORDER BY a.created_at DESC LIMIT 1",
                (worker_id,),
            ).fetchone()
        if row is None:
            return None
        return DeliveryAuthority(
            delivery_id=UUID(str(row[0])),
            delivery_attempt_id=UUID(str(row[1])),
            thread_id=UUID(str(row[2])),
            worker_id=str(row[3]),
        )

    def query_is_lock_waiting(self, fragment: str) -> bool:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION READ ONLY")
            row = connection.execute(
                "SELECT 1 FROM pg_stat_activity WHERE datname = current_database() "
                "AND state = 'active' AND query LIKE %s AND wait_event_type = 'Lock'",
                (f"%{fragment}%",),
            ).fetchone()
        return row is not None

    def step_running_attempts(self, step_id: UUID) -> int:
        return self._count(
            "SELECT count(*) FROM openmagic_runtime.attempts "
            "WHERE step_id = %s AND state = 'leased'",
            step_id,
        )

    def delivery_running_attempts(self, delivery_id: UUID) -> int:
        return self._count(
            "SELECT count(*) FROM openmagic_runtime.delivery_attempts "
            "WHERE delivery_id = %s AND state = 'running'",
            delivery_id,
        )

    def accepted_signals(self, wait_id: UUID) -> int:
        return self._count(
            "SELECT count(*) FROM openmagic_runtime.signals WHERE wait_id = %s",
            wait_id,
        )

    def completed_attempts(self, attempt_id: UUID) -> int:
        return self._count(
            "SELECT count(*) FROM openmagic_runtime.attempts "
            "WHERE attempt_id = %s AND state = 'completed'",
            attempt_id,
        )

    def materialized_steps(self, instance_id: UUID, template_key: str) -> int:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = connection.execute(
                "SELECT count(*) FROM openmagic_runtime.steps "
                "WHERE instance_id = %s AND template_key = %s",
                (instance_id, template_key),
            ).fetchone()
        return int(row[0]) if row is not None else 0

    def command_receipts(self, command_id: UUID) -> int:
        return self._count(
            "SELECT count(*) FROM openmagic_runtime.command_receipts WHERE command_id = %s",
            command_id,
        )

    def verification_sessions(self, challenge_id: UUID) -> int:
        return self._count(
            "SELECT count(*) FROM example_insurance.verification_sessions WHERE challenge_id = %s",
            challenge_id,
        )

    def transaction_state(self, command_id: UUID, workflow_id: UUID) -> TransactionState:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = connection.execute(
                "SELECT "
                "(SELECT count(*) FROM openmagic_runtime.command_receipts "
                " WHERE command_id = %s), "
                "(SELECT count(*) FROM example_insurance.renewal_workflows "
                " WHERE workflow_id = %s), "
                "(SELECT count(*) FROM openmagic_runtime.instances "
                " WHERE input ->> 'workflow_id' = %s), "
                "(SELECT count(*) FROM example_insurance.domain_events "
                " WHERE workflow_id = %s), "
                "(SELECT count(*) FROM openmagic_runtime.steps AS s "
                " JOIN openmagic_runtime.instances AS i USING (instance_id) "
                " WHERE i.input ->> 'workflow_id' = %s), "
                "(SELECT count(*) FROM openmagic_runtime.trace_events AS t "
                " JOIN openmagic_runtime.instances AS i USING (instance_id) "
                " WHERE i.input ->> 'workflow_id' = %s)",
                (
                    command_id,
                    workflow_id,
                    str(workflow_id),
                    workflow_id,
                    str(workflow_id),
                    str(workflow_id),
                ),
            ).fetchone()
        if row is None:
            raise RuntimeError("PostgreSQL did not return transaction state")
        return TransactionState(*(int(value) for value in row))

    def agent_safety(self, thread_id: UUID, instance_id: UUID) -> AgentSafetyObservation:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            source_rows = connection.execute(
                "SELECT source_kind FROM openmagic_runtime.messages WHERE thread_id = %s",
                (thread_id,),
            ).fetchall()
            delivery_attempt_rows = connection.execute(
                "SELECT a.delivery_attempt_id FROM openmagic_runtime.delivery_attempts AS a "
                "JOIN openmagic_runtime.deliveries AS d ON d.delivery_id = a.delivery_id "
                "WHERE d.thread_id = %s ORDER BY a.created_at, a.delivery_attempt_id",
                (thread_id,),
            ).fetchall()
            command_row = connection.execute(
                "SELECT count(*) FROM openmagic_runtime.command_receipts"
            ).fetchone()
            delivery_rows = connection.execute(
                "SELECT d.thread_id FROM openmagic_runtime.deliveries AS d "
                "JOIN example_insurance.domain_events AS e ON e.event_id = d.domain_event_id "
                "JOIN example_insurance.renewal_workflows AS w ON w.workflow_id = e.workflow_id "
                "WHERE w.instance_id = %s ORDER BY d.delivery_id",
                (instance_id,),
            ).fetchall()
            retry_row = connection.execute(
                "SELECT count(*) FROM openmagic_runtime.trace_events "
                "WHERE instance_id = %s AND event_type = 'step_retry_authorized'",
                (instance_id,),
            ).fetchone()
        return AgentSafetyObservation(
            message_source_kinds=tuple(str(row[0]) for row in source_rows),
            delivery_attempt_ids=tuple(UUID(str(row[0])) for row in delivery_attempt_rows),
            command_count=int(command_row[0]) if command_row is not None else 0,
            delivery_thread_ids=tuple(UUID(str(row[0])) for row in delivery_rows),
            retry_authorization_count=int(retry_row[0]) if retry_row is not None else 0,
        )

    def renewal_demo_ids(self, instance_id: UUID) -> tuple[tuple[UUID, ...], tuple[UUID, ...]]:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            trace_rows = connection.execute(
                "SELECT trace_event_id FROM openmagic_runtime.trace_events "
                "WHERE instance_id = %s ORDER BY sequence",
                (instance_id,),
            ).fetchall()
            delivery_rows = connection.execute(
                "SELECT a.delivery_attempt_id FROM openmagic_runtime.delivery_attempts AS a "
                "JOIN openmagic_runtime.deliveries AS d ON d.delivery_id = a.delivery_id "
                "JOIN example_insurance.domain_events AS e ON e.event_id = d.domain_event_id "
                "JOIN example_insurance.renewal_workflows AS w ON w.workflow_id = e.workflow_id "
                "WHERE w.instance_id = %s ORDER BY a.created_at, a.delivery_attempt_id",
                (instance_id,),
            ).fetchall()
        return (
            tuple(UUID(str(row[0])) for row in trace_rows),
            tuple(UUID(str(row[0])) for row in delivery_rows),
        )

    def verification_demo(self, challenge_id: UUID) -> VerificationDemoObservation | None:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = connection.execute(
                "SELECT c.delivery_instance_id, c.delivery_workflow_id, s.session_id "
                "FROM example_insurance.verification_challenges AS c "
                "JOIN example_insurance.verification_sessions AS s USING (challenge_id) "
                "WHERE c.challenge_id = %s",
                (challenge_id,),
            ).fetchone()
            attempt_rows = (
                connection.execute(
                    "SELECT step_id, attempt_id FROM openmagic_runtime.attempts "
                    "WHERE instance_id = %s ORDER BY created_at, attempt_id",
                    (row[0],),
                ).fetchall()
                if row is not None
                else ()
            )
        if row is None:
            return None
        return VerificationDemoObservation(
            instance_id=UUID(str(row[0])),
            workflow_id=UUID(str(row[1])),
            session_id=UUID(str(row[2])),
            step_attempt_ids=tuple(
                (UUID(str(attempt[0])), UUID(str(attempt[1]))) for attempt in attempt_rows
            ),
        )

    def _count(self, statement: LiteralString, identity: UUID) -> int:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = connection.execute(statement, (identity,)).fetchone()
        return int(row[0]) if row is not None else 0


__all__ = [
    "AgentSafetyObservation",
    "AttemptAuthority",
    "DeliveryAuthority",
    "EvidenceInspection",
    "QueueState",
    "TransactionState",
    "VerificationDemoObservation",
]
