"""Typed read-only PostgreSQL observations owned by the private evidence package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import LiteralString
from uuid import UUID

import psycopg
from psycopg import sql


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


@dataclass(frozen=True)
class DurableChainObservation:
    command_ids: tuple[UUID, ...]
    workflow_ids: tuple[UUID, ...]
    instance_ids: tuple[UUID, ...]
    step_ids: tuple[UUID, ...]
    attempt_ids: tuple[UUID, ...]
    wait_ids: tuple[UUID, ...]
    signal_ids: tuple[UUID, ...]
    trace_event_ids: tuple[UUID, ...]
    thread_ids: tuple[UUID, ...]
    message_ids: tuple[UUID, ...]
    agent_run_ids: tuple[UUID, ...]
    domain_event_ids: tuple[UUID, ...]
    delivery_ids: tuple[UUID, ...]
    delivery_attempt_ids: tuple[UUID, ...]
    approval_grant_ids: tuple[UUID, ...]
    external_effect_ids: tuple[UUID, ...]
    provider_request_ids: tuple[str, ...]
    worker_ids: tuple[str, ...]
    verification_challenge_ids: tuple[UUID, ...]
    verification_session_ids: tuple[UUID, ...]
    relationship_checks: tuple[str, ...]


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

    def durable_chain(
        self,
        *,
        renewal_workflow_id: UUID,
        challenge_id: UUID,
        provider_request_id: str,
        worker_id: str,
    ) -> DurableChainObservation:
        """Prove one FK-backed renewal and verification chain in one snapshot."""

        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = connection.execute(
                "SELECT r.start_command_id, r.instance_id, r.thread_id, "
                "p.protected_command_id, g.approval_grant_id, d.wait_id, d.signal_id, "
                "c.delivery_workflow_id, c.delivery_instance_id, c.destination_thread_id, "
                "s.session_id, effect.logical_effect_id, effect_evidence.provider_request_id, "
                "effect_attempt.worker_id "
                "FROM example_insurance.renewal_workflows AS r "
                "JOIN example_insurance.protected_commands AS p "
                "ON p.workflow_id = r.workflow_id "
                "JOIN openmagic_runtime.command_receipts AS start_receipt "
                "ON start_receipt.command_id = r.start_command_id "
                "JOIN openmagic_runtime.command_receipts AS protected_receipt "
                "ON protected_receipt.command_id = p.protected_command_id "
                "JOIN example_insurance.approval_grants AS g "
                "ON g.approval_grant_id = p.approval_grant_id AND g.workflow_id = r.workflow_id "
                "JOIN example_insurance.renewal_decisions AS d "
                "ON d.decision_id = g.decision_id AND d.workflow_id = r.workflow_id "
                "JOIN openmagic_runtime.waits AS w "
                "ON w.wait_id = d.wait_id AND w.instance_id = r.instance_id "
                "JOIN openmagic_runtime.signals AS signal "
                "ON signal.signal_id = d.signal_id AND signal.wait_id = w.wait_id "
                "JOIN example_insurance.external_effects AS effect "
                "ON effect.workflow_id = r.workflow_id "
                "AND effect.approval_grant_id = g.approval_grant_id "
                "JOIN openmagic_runtime.attempts AS effect_attempt "
                "ON effect_attempt.attempt_id = effect.dispatch_attempt_id "
                "JOIN example_insurance.external_effect_evidence AS effect_evidence "
                "ON effect_evidence.logical_effect_id = effect.logical_effect_id "
                "AND effect_evidence.attempt_id = effect_attempt.attempt_id "
                "AND effect_evidence.classification = 'applied' "
                "JOIN example_insurance.verification_challenges AS c "
                "ON c.challenge_id = %s AND c.protected_command_id = p.protected_command_id "
                "AND c.protected_workflow_id = r.workflow_id "
                "JOIN example_insurance.verification_workflows AS v "
                "ON v.workflow_id = c.delivery_workflow_id AND v.challenge_id = c.challenge_id "
                "AND v.instance_id = c.delivery_instance_id "
                "AND v.protected_workflow_id = r.workflow_id "
                "JOIN openmagic_runtime.instances AS renewal_instance "
                "ON renewal_instance.instance_id = r.instance_id "
                "JOIN openmagic_runtime.instances AS verification_instance "
                "ON verification_instance.instance_id = v.instance_id "
                "JOIN example_insurance.verification_sessions AS s "
                "ON s.challenge_id = c.challenge_id AND s.thread_id = c.thread_id "
                "AND s.identifier_thread_id = c.destination_thread_id "
                "WHERE r.workflow_id = %s AND effect_evidence.provider_request_id = %s "
                "AND effect_attempt.worker_id = %s",
                (challenge_id, renewal_workflow_id, provider_request_id, worker_id),
            ).fetchone()
            if row is None:
                raise AssertionError("canonical durable chain is not relationally connected")
            renewal_instance_id = UUID(str(row[1]))
            verification_instance_id = UUID(str(row[8]))
            instance_ids = (renewal_instance_id, verification_instance_id)

            def runtime_ids(table: str, column: str) -> tuple[UUID, ...]:
                rows = connection.execute(
                    sql.SQL(
                        "SELECT {column} FROM openmagic_runtime.{table} "
                        "WHERE instance_id = ANY(%s) ORDER BY {column}"
                    ).format(
                        column=sql.Identifier(column),
                        table=sql.Identifier(table),
                    ),
                    (list(instance_ids),),
                ).fetchall()
                return tuple(UUID(str(item[0])) for item in rows)

            message_rows = connection.execute(
                "SELECT m.message_id FROM openmagic_runtime.messages AS m "
                "WHERE m.thread_id = ANY(%s) ORDER BY m.message_id",
                ([UUID(str(row[2])), UUID(str(row[9]))],),
            ).fetchall()
            event_rows = connection.execute(
                "SELECT event_id FROM example_insurance.domain_events "
                "WHERE workflow_id = %s ORDER BY event_id",
                (renewal_workflow_id,),
            ).fetchall()
            delivery_rows = connection.execute(
                "SELECT DISTINCT delivery_id FROM ("
                "SELECT delivery_id FROM openmagic_runtime.deliveries "
                "WHERE domain_event_id IN ("
                "SELECT event_id FROM example_insurance.domain_events WHERE workflow_id = %s"
                ") UNION ALL SELECT delivery_id FROM example_insurance.verification_workflows "
                "WHERE challenge_id = %s AND delivery_id IS NOT NULL"
                ") AS related ORDER BY delivery_id",
                (renewal_workflow_id, challenge_id),
            ).fetchall()
            delivery_ids = tuple(UUID(str(item[0])) for item in delivery_rows)
            delivery_attempt_rows = connection.execute(
                "SELECT delivery_attempt_id FROM openmagic_runtime.delivery_attempts "
                "WHERE delivery_id = ANY(%s) ORDER BY delivery_attempt_id",
                (list(delivery_ids),),
            ).fetchall()
            agent_rows = connection.execute(
                "SELECT a.agent_run_id FROM openmagic_runtime.agent_runs AS a "
                "JOIN openmagic_runtime.attempts AS attempt USING (attempt_id) "
                "WHERE attempt.instance_id = ANY(%s) ORDER BY a.agent_run_id",
                (list(instance_ids),),
            ).fetchall()
            step_ids = runtime_ids("steps", "step_id")
            attempt_ids = runtime_ids("attempts", "attempt_id")
            trace_event_ids = runtime_ids("trace_events", "trace_event_id")

        observation = DurableChainObservation(
            command_ids=(UUID(str(row[0])), UUID(str(row[3]))),
            workflow_ids=(renewal_workflow_id, UUID(str(row[7]))),
            instance_ids=instance_ids,
            step_ids=step_ids,
            attempt_ids=attempt_ids,
            wait_ids=(UUID(str(row[5])),),
            signal_ids=(UUID(str(row[6])),),
            trace_event_ids=trace_event_ids,
            thread_ids=(UUID(str(row[2])), UUID(str(row[9]))),
            message_ids=tuple(UUID(str(item[0])) for item in message_rows),
            agent_run_ids=tuple(UUID(str(item[0])) for item in agent_rows),
            domain_event_ids=tuple(UUID(str(item[0])) for item in event_rows),
            delivery_ids=delivery_ids,
            delivery_attempt_ids=tuple(UUID(str(item[0])) for item in delivery_attempt_rows),
            approval_grant_ids=(UUID(str(row[4])),),
            external_effect_ids=(UUID(str(row[11])),),
            provider_request_ids=(str(row[12]),),
            worker_ids=(str(row[13]),),
            verification_challenge_ids=(challenge_id,),
            verification_session_ids=(UUID(str(row[10])),),
            relationship_checks=(
                "command-receipt-to-renewal-workflow",
                "renewal-workflow-to-runtime-instance",
                "approval-decision-to-wait-and-signal",
                "protected-command-to-approval-grant",
                "approval-grant-to-external-effect",
                "external-effect-to-attempt-worker-and-provider-request",
                "challenge-to-protected-workflow",
                "challenge-to-verification-workflow-instance",
                "verification-session-to-challenge-and-threads",
            ),
        )
        if not all(
            (
                observation.step_ids,
                observation.attempt_ids,
                observation.trace_event_ids,
                observation.message_ids,
                observation.agent_run_ids,
                observation.domain_event_ids,
                observation.delivery_ids,
                observation.delivery_attempt_ids,
            )
        ):
            raise AssertionError("canonical durable chain omitted a durable child identity")
        return observation

    def _count(self, statement: LiteralString, identity: UUID) -> int:
        with psycopg.connect(self._database_url) as connection, connection.transaction():
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            row = connection.execute(statement, (identity,)).fetchone()
        return int(row[0]) if row is not None else 0


__all__ = [
    "AgentSafetyObservation",
    "AttemptAuthority",
    "DeliveryAuthority",
    "DurableChainObservation",
    "EvidenceInspection",
    "QueueState",
    "TransactionState",
    "VerificationDemoObservation",
]
