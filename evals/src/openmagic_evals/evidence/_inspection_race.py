"""Typed PostgreSQL observations for deterministic race evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, LiteralString
from uuid import UUID

from openmagic_evals.evidence._inspection_base import InspectionDatabase


@dataclass(frozen=True)
class TransactionState:
    command_receipts: int
    workflows: int
    instances: int
    domain_events: int
    steps: int
    trace_events: int

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> TransactionState:
        return cls(
            command_receipts=int(record["command_receipts"]),
            workflows=int(record["workflows"]),
            instances=int(record["instances"]),
            domain_events=int(record["domain_events"]),
            steps=int(record["steps"]),
            trace_events=int(record["trace_events"]),
        )


class RaceInspection(InspectionDatabase):
    def step_running_attempts(self, step_id: UUID) -> int:
        return self._count(
            "SELECT count(*) AS count FROM openmagic_runtime.attempts "
            "WHERE step_id = %s AND state = 'leased'",
            step_id,
        )

    def delivery_running_attempts(self, delivery_id: UUID) -> int:
        return self._count(
            "SELECT count(*) AS count FROM openmagic_runtime.delivery_attempts "
            "WHERE delivery_id = %s AND state = 'running'",
            delivery_id,
        )

    def accepted_signals(self, wait_id: UUID) -> int:
        return self._count(
            "SELECT count(*) AS count FROM openmagic_runtime.signals WHERE wait_id = %s",
            wait_id,
        )

    def completed_attempts(self, attempt_id: UUID) -> int:
        return self._count(
            "SELECT count(*) AS count FROM openmagic_runtime.attempts "
            "WHERE attempt_id = %s AND state = 'completed'",
            attempt_id,
        )

    def materialized_steps(self, instance_id: UUID, template_key: str) -> int:
        with self.read_snapshot() as cursor:
            record = cursor.execute(
                "SELECT count(*) AS count FROM openmagic_runtime.steps "
                "WHERE instance_id = %s AND template_key = %s",
                (instance_id, template_key),
            ).fetchone()
        return 0 if record is None else int(record["count"])

    def command_receipts(self, command_id: UUID) -> int:
        return self._count(
            "SELECT count(*) AS count FROM openmagic_runtime.command_receipts "
            "WHERE command_id = %s",
            command_id,
        )

    def verification_sessions(self, challenge_id: UUID) -> int:
        return self._count(
            "SELECT count(*) AS count FROM example_insurance.verification_sessions "
            "WHERE challenge_id = %s",
            challenge_id,
        )

    def transaction_state(self, command_id: UUID, workflow_id: UUID) -> TransactionState:
        with self.read_snapshot() as cursor:
            record = cursor.execute(
                "SELECT "
                "(SELECT count(*) FROM openmagic_runtime.command_receipts "
                " WHERE command_id = %s) AS command_receipts, "
                "(SELECT count(*) FROM example_insurance.renewal_workflows "
                " WHERE workflow_id = %s) AS workflows, "
                "(SELECT count(*) FROM openmagic_runtime.instances "
                " WHERE input ->> 'workflow_id' = %s) AS instances, "
                "(SELECT count(*) FROM example_insurance.domain_events "
                " WHERE workflow_id = %s) AS domain_events, "
                "(SELECT count(*) FROM openmagic_runtime.steps AS s "
                " JOIN openmagic_runtime.instances AS i USING (instance_id) "
                " WHERE i.input ->> 'workflow_id' = %s) AS steps, "
                "(SELECT count(*) FROM openmagic_runtime.trace_events AS t "
                " JOIN openmagic_runtime.instances AS i USING (instance_id) "
                " WHERE i.input ->> 'workflow_id' = %s) AS trace_events",
                (
                    command_id,
                    workflow_id,
                    str(workflow_id),
                    workflow_id,
                    str(workflow_id),
                    str(workflow_id),
                ),
            ).fetchone()
        if record is None:
            raise RuntimeError("PostgreSQL did not return transaction state")
        return TransactionState.decode(record)

    def _count(self, statement: LiteralString, identity: UUID) -> int:
        with self.read_snapshot() as cursor:
            record = cursor.execute(statement, (identity,)).fetchone()
        return 0 if record is None else int(record["count"])


__all__ = ["RaceInspection", "TransactionState"]
