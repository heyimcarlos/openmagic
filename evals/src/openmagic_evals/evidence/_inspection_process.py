"""Typed PostgreSQL observations for fresh-process evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from openmagic_evals.evidence._inspection_base import InspectionDatabase
from openmagic_evals.evidence.core_models import InstanceDefinitionCorrelation


@dataclass(frozen=True)
class QueueState:
    pending_steps: int
    pending_deliveries: int

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> QueueState:
        return cls(int(record["pending_steps"]), int(record["pending_deliveries"]))


@dataclass(frozen=True)
class AttemptAuthority:
    instance_id: UUID
    instance_definition: InstanceDefinitionCorrelation
    step_id: UUID
    attempt_id: UUID
    worker_id: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> AttemptAuthority:
        instance_id = UUID(str(record["instance_id"]))
        return cls(
            instance_id,
            InstanceDefinitionCorrelation(
                instance_id=instance_id,
                definition_key=str(record["definition_key"]),
                definition_version=int(record["definition_version"]),
            ),
            UUID(str(record["step_id"])),
            UUID(str(record["attempt_id"])),
            str(record["worker_id"]),
        )


@dataclass(frozen=True)
class DeliveryAuthority:
    delivery_id: UUID
    delivery_attempt_id: UUID
    thread_id: UUID
    worker_id: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> DeliveryAuthority:
        return cls(
            UUID(str(record["delivery_id"])),
            UUID(str(record["delivery_attempt_id"])),
            UUID(str(record["thread_id"])),
            str(record["worker_id"]),
        )


class ProcessInspection(InspectionDatabase):
    def queue_state(self) -> QueueState:
        with self.read_snapshot() as cursor:
            record = cursor.execute(
                "SELECT "
                "(SELECT count(*) FROM openmagic_runtime.steps WHERE state = 'pending') "
                "AS pending_steps, "
                "(SELECT count(*) FROM openmagic_runtime.deliveries WHERE status = 'pending') "
                "AS pending_deliveries"
            ).fetchone()
        if record is None:
            raise RuntimeError("PostgreSQL did not return queue observations")
        return QueueState.decode(record)

    def active_attempt(self, worker_id: str) -> AttemptAuthority | None:
        with self.read_snapshot() as cursor:
            record = cursor.execute(
                "SELECT a.instance_id, i.definition_key, i.definition_version, "
                "a.step_id, a.attempt_id, a.worker_id "
                "FROM openmagic_runtime.attempts AS a "
                "JOIN openmagic_runtime.instances AS i USING (instance_id) "
                "WHERE a.worker_id = %s AND a.state = 'leased' "
                "AND lease_expires_at > clock_timestamp() "
                "ORDER BY a.created_at DESC LIMIT 1",
                (worker_id,),
            ).fetchone()
        return None if record is None else AttemptAuthority.decode(record)

    def active_delivery(self, worker_id: str) -> DeliveryAuthority | None:
        with self.read_snapshot() as cursor:
            record = cursor.execute(
                "SELECT a.delivery_id, a.delivery_attempt_id, d.thread_id, a.worker_id "
                "FROM openmagic_runtime.delivery_attempts AS a "
                "JOIN openmagic_runtime.deliveries AS d ON d.delivery_id = a.delivery_id "
                "WHERE a.worker_id = %s AND a.state = 'running' "
                "AND a.lease_expires_at > clock_timestamp() "
                "ORDER BY a.created_at DESC LIMIT 1",
                (worker_id,),
            ).fetchone()
        return None if record is None else DeliveryAuthority.decode(record)

    def query_is_lock_waiting(self, fragment: str) -> bool:
        with self.read_snapshot() as cursor:
            record = cursor.execute(
                "SELECT 1 FROM pg_stat_activity WHERE datname = current_database() "
                "AND state = 'active' AND query LIKE %s AND wait_event_type = 'Lock'",
                (f"%{fragment}%",),
            ).fetchone()
        return record is not None


__all__ = [
    "AttemptAuthority",
    "DeliveryAuthority",
    "ProcessInspection",
    "QueueState",
]
