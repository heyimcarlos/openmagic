"""One typed, relationally connected durable-chain inspector."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import sql

from openmagic_evals.evidence._inspection_base import InspectionDatabase
from openmagic_evals.evidence.core_models import InstanceDefinitionCorrelation


@dataclass(frozen=True)
class DurableChainObservation:
    command_ids: tuple[UUID, ...]
    workflow_ids: tuple[UUID, ...]
    instance_ids: tuple[UUID, ...]
    instance_definitions: tuple[InstanceDefinitionCorrelation, ...]
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


@dataclass(frozen=True)
class _ChainRoot:
    start_command_id: UUID
    renewal_instance_id: UUID
    renewal_thread_id: UUID
    protected_command_id: UUID
    approval_grant_id: UUID
    wait_id: UUID
    signal_id: UUID
    verification_workflow_id: UUID
    verification_instance_id: UUID
    destination_thread_id: UUID
    session_id: UUID
    logical_effect_id: UUID
    provider_request_id: str
    worker_id: str
    renewal_definition_key: str
    renewal_definition_version: int
    verification_definition_key: str
    verification_definition_version: int

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> _ChainRoot:
        return cls(
            start_command_id=UUID(str(record["start_command_id"])),
            renewal_instance_id=UUID(str(record["renewal_instance_id"])),
            renewal_thread_id=UUID(str(record["renewal_thread_id"])),
            protected_command_id=UUID(str(record["protected_command_id"])),
            approval_grant_id=UUID(str(record["approval_grant_id"])),
            wait_id=UUID(str(record["wait_id"])),
            signal_id=UUID(str(record["signal_id"])),
            verification_workflow_id=UUID(str(record["verification_workflow_id"])),
            verification_instance_id=UUID(str(record["verification_instance_id"])),
            destination_thread_id=UUID(str(record["destination_thread_id"])),
            session_id=UUID(str(record["session_id"])),
            logical_effect_id=UUID(str(record["logical_effect_id"])),
            provider_request_id=str(record["provider_request_id"]),
            worker_id=str(record["worker_id"]),
            renewal_definition_key=str(record["renewal_definition_key"]),
            renewal_definition_version=int(record["renewal_definition_version"]),
            verification_definition_key=str(record["verification_definition_key"]),
            verification_definition_version=int(record["verification_definition_version"]),
        )


def _uuid_column(records: list[dict[str, Any]], column: str) -> tuple[UUID, ...]:
    return tuple(UUID(str(record[column])) for record in records)


class DurableChainInspection(InspectionDatabase):
    def durable_chain(
        self,
        *,
        renewal_workflow_id: UUID,
        challenge_id: UUID,
        provider_request_id: str,
        worker_id: str,
    ) -> DurableChainObservation:
        """Prove one FK-backed renewal and verification chain in one snapshot."""

        with self.read_snapshot() as cursor:
            record = cursor.execute(
                "SELECT r.start_command_id, r.instance_id AS renewal_instance_id, "
                "r.thread_id AS renewal_thread_id, p.protected_command_id, "
                "g.approval_grant_id, d.wait_id, d.signal_id, "
                "c.delivery_workflow_id AS verification_workflow_id, "
                "c.delivery_instance_id AS verification_instance_id, "
                "c.destination_thread_id, s.session_id, effect.logical_effect_id, "
                "effect_evidence.provider_request_id, effect_attempt.worker_id, "
                "renewal_definition.definition_key AS renewal_definition_key, "
                "renewal_definition.definition_version AS renewal_definition_version, "
                "verification_definition.definition_key AS verification_definition_key, "
                "verification_definition.definition_version AS verification_definition_version "
                "FROM example_insurance.renewal_workflows AS r "
                "JOIN example_insurance.protected_commands AS p ON p.workflow_id = r.workflow_id "
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
                "JOIN openmagic_runtime.workflow_definitions AS renewal_definition "
                "ON renewal_definition.definition_key = renewal_instance.definition_key "
                "AND renewal_definition.definition_version = renewal_instance.definition_version "
                "JOIN openmagic_runtime.workflow_definitions AS verification_definition "
                "ON verification_definition.definition_key = verification_instance.definition_key "
                "AND verification_definition.definition_version = "
                "verification_instance.definition_version "
                "JOIN example_insurance.verification_sessions AS s "
                "ON s.challenge_id = c.challenge_id AND s.thread_id = c.thread_id "
                "AND s.identifier_thread_id = c.destination_thread_id "
                "WHERE r.workflow_id = %s AND effect_evidence.provider_request_id = %s "
                "AND effect_attempt.worker_id = %s",
                (challenge_id, renewal_workflow_id, provider_request_id, worker_id),
            ).fetchone()
            if record is None:
                raise AssertionError("canonical durable chain is not relationally connected")
            root = _ChainRoot.decode(record)
            instance_ids = (root.renewal_instance_id, root.verification_instance_id)
            step_ids = self._runtime_ids(cursor, "steps", "step_id", instance_ids)
            attempt_ids = self._runtime_ids(cursor, "attempts", "attempt_id", instance_ids)
            trace_event_ids = self._runtime_ids(
                cursor, "trace_events", "trace_event_id", instance_ids
            )
            message_records = cursor.execute(
                "SELECT message_id FROM openmagic_runtime.messages "
                "WHERE thread_id = ANY(%s) ORDER BY message_id",
                ([root.renewal_thread_id, root.destination_thread_id],),
            ).fetchall()
            event_records = cursor.execute(
                "SELECT event_id FROM example_insurance.domain_events "
                "WHERE workflow_id = %s ORDER BY event_id",
                (renewal_workflow_id,),
            ).fetchall()
            delivery_records = cursor.execute(
                "SELECT DISTINCT delivery_id FROM ("
                "SELECT delivery_id FROM openmagic_runtime.deliveries "
                "WHERE domain_event_id IN (SELECT event_id FROM example_insurance.domain_events "
                "WHERE workflow_id = %s) UNION ALL "
                "SELECT delivery_id FROM example_insurance.verification_workflows "
                "WHERE challenge_id = %s AND delivery_id IS NOT NULL"
                ") AS related ORDER BY delivery_id",
                (renewal_workflow_id, challenge_id),
            ).fetchall()
            delivery_ids = _uuid_column(delivery_records, "delivery_id")
            delivery_attempt_records = cursor.execute(
                "SELECT delivery_attempt_id FROM openmagic_runtime.delivery_attempts "
                "WHERE delivery_id = ANY(%s) ORDER BY delivery_attempt_id",
                (list(delivery_ids),),
            ).fetchall()
            agent_records = cursor.execute(
                "SELECT a.agent_run_id FROM openmagic_runtime.agent_runs AS a "
                "JOIN openmagic_runtime.attempts AS attempt USING (attempt_id) "
                "WHERE attempt.instance_id = ANY(%s) ORDER BY a.agent_run_id",
                (list(instance_ids),),
            ).fetchall()

        observation = DurableChainObservation(
            command_ids=(root.start_command_id, root.protected_command_id),
            workflow_ids=(renewal_workflow_id, root.verification_workflow_id),
            instance_ids=instance_ids,
            instance_definitions=(
                InstanceDefinitionCorrelation(
                    instance_id=root.renewal_instance_id,
                    definition_key=root.renewal_definition_key,
                    definition_version=root.renewal_definition_version,
                ),
                InstanceDefinitionCorrelation(
                    instance_id=root.verification_instance_id,
                    definition_key=root.verification_definition_key,
                    definition_version=root.verification_definition_version,
                ),
            ),
            step_ids=step_ids,
            attempt_ids=attempt_ids,
            wait_ids=(root.wait_id,),
            signal_ids=(root.signal_id,),
            trace_event_ids=trace_event_ids,
            thread_ids=(root.renewal_thread_id, root.destination_thread_id),
            message_ids=_uuid_column(message_records, "message_id"),
            agent_run_ids=_uuid_column(agent_records, "agent_run_id"),
            domain_event_ids=_uuid_column(event_records, "event_id"),
            delivery_ids=delivery_ids,
            delivery_attempt_ids=_uuid_column(delivery_attempt_records, "delivery_attempt_id"),
            approval_grant_ids=(root.approval_grant_id,),
            external_effect_ids=(root.logical_effect_id,),
            provider_request_ids=(root.provider_request_id,),
            worker_ids=(root.worker_id,),
            verification_challenge_ids=(challenge_id,),
            verification_session_ids=(root.session_id,),
            relationship_checks=(
                "command-receipt-to-renewal-workflow",
                "renewal-workflow-to-runtime-instance",
                "runtime-instance-to-registered-definition",
                "approval-decision-to-wait-and-signal",
                "protected-command-to-approval-grant",
                "approval-grant-to-external-effect",
                "external-effect-to-attempt-worker-and-provider-request",
                "challenge-to-protected-workflow",
                "challenge-to-verification-workflow-instance",
                "verification-session-to-challenge-and-threads",
            ),
        )
        self._validate(observation)
        return observation

    @staticmethod
    def _runtime_ids(
        cursor: Any,
        table: str,
        column: str,
        instance_ids: tuple[UUID, UUID],
    ) -> tuple[UUID, ...]:
        records = cursor.execute(
            sql.SQL(
                "SELECT {column} FROM openmagic_runtime.{table} "
                "WHERE instance_id = ANY(%s) ORDER BY {column}"
            ).format(column=sql.Identifier(column), table=sql.Identifier(table)),
            (list(instance_ids),),
        ).fetchall()
        return _uuid_column(records, column)

    @staticmethod
    def _validate(observation: DurableChainObservation) -> None:
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
        if {item.instance_id for item in observation.instance_definitions} != set(
            observation.instance_ids
        ):
            raise AssertionError("canonical durable chain omitted an Instance Definition identity")


__all__ = ["DurableChainInspection", "DurableChainObservation"]
