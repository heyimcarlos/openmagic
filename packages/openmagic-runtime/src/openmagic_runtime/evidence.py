"""Read-only runtime deployment evidence exposed to installed process roles."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

import psycopg
from psycopg import Connection

from openmagic_runtime._canonical import canonical_bytes


@dataclass(frozen=True)
class RuntimeDatabaseHealth:
    status: str
    pid: int
    database: str
    runtime_schema_ready: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def inspect_runtime_database(database_url: str) -> RuntimeDatabaseHealth:
    """Read runtime-owned deployment identity without retaining a session."""

    with psycopg.connect(database_url) as connection:
        database = connection.execute("SELECT current_database()").fetchone()
        runtime_schema = connection.execute(
            "SELECT to_regnamespace('openmagic_runtime') IS NOT NULL"
        ).fetchone()
    if database is None:
        raise RuntimeError("PostgreSQL did not report the current database")
    if runtime_schema is None or not runtime_schema[0]:
        raise RuntimeError("OpenMagic Runtime schema is not installed")
    return RuntimeDatabaseHealth(
        status="ready",
        pid=os.getpid(),
        database=str(database[0]),
        runtime_schema_ready=True,
    )


@dataclass(frozen=True)
class EvidenceRecord:
    schema_version: str
    scenario: str
    correlations: dict[str, Any]
    outcomes: dict[str, Any]
    invariant_violations: tuple[str, ...]
    redacted: bool

    def to_json(self) -> str:
        return canonical_bytes(self).decode("utf-8")


@dataclass(frozen=True)
class RuntimeStepEvidence:
    step_id: UUID
    template_key: str
    state: str


@dataclass(frozen=True)
class RuntimeWaitEvidence:
    wait_id: UUID
    template_key: str
    state: str


@dataclass(frozen=True)
class RuntimeInstanceEvidence:
    steps: tuple[RuntimeStepEvidence, ...]
    attempts: tuple[tuple[UUID, str], ...]
    agent_runs: tuple[tuple[UUID, UUID, str], ...]
    waits: tuple[RuntimeWaitEvidence, ...]


@dataclass(frozen=True)
class RuntimeDeliveryEvidence:
    delivery_id: UUID
    status: str
    delivered_message_id: UUID | None
    attempt_states: tuple[str, ...]


class RuntimeEvidenceReader:
    """Transaction-scoped public projection over runtime-owned evidence."""

    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def instance(self, instance_id: UUID) -> RuntimeInstanceEvidence:
        steps = self._connection.execute(
            "SELECT step_id, template_key, state FROM openmagic_runtime.steps "
            "WHERE instance_id = %s ORDER BY created_at, step_id",
            (instance_id,),
        ).fetchall()
        attempts = self._connection.execute(
            "SELECT attempt_id, state FROM openmagic_runtime.attempts "
            "WHERE instance_id = %s ORDER BY created_at, attempt_id",
            (instance_id,),
        ).fetchall()
        waits = self._connection.execute(
            "SELECT wait_id, template_key, state FROM openmagic_runtime.waits "
            "WHERE instance_id = %s ORDER BY created_at, wait_id",
            (instance_id,),
        ).fetchall()
        agent_runs = self._connection.execute(
            "SELECT r.agent_run_id, r.attempt_id, r.status "
            "FROM openmagic_runtime.agent_runs AS r "
            "JOIN openmagic_runtime.attempts AS a ON a.attempt_id = r.attempt_id "
            "WHERE a.instance_id = %s ORDER BY r.created_at, r.agent_run_id",
            (instance_id,),
        ).fetchall()
        return RuntimeInstanceEvidence(
            steps=tuple(
                RuntimeStepEvidence(UUID(str(row[0])), str(row[1]), str(row[2])) for row in steps
            ),
            attempts=tuple((UUID(str(row[0])), str(row[1])) for row in attempts),
            agent_runs=tuple(
                (UUID(str(row[0])), UUID(str(row[1])), str(row[2])) for row in agent_runs
            ),
            waits=tuple(
                RuntimeWaitEvidence(UUID(str(row[0])), str(row[1]), str(row[2])) for row in waits
            ),
        )

    def deliveries(self, domain_event_id: UUID) -> tuple[RuntimeDeliveryEvidence, ...]:
        deliveries = self._connection.execute(
            "SELECT delivery_id, status, delivered_message_id "
            "FROM openmagic_runtime.deliveries WHERE domain_event_id = %s "
            "ORDER BY created_at, delivery_id",
            (domain_event_id,),
        ).fetchall()
        return tuple(
            RuntimeDeliveryEvidence(
                delivery_id=UUID(str(delivery[0])),
                status=str(delivery[1]),
                delivered_message_id=(UUID(str(delivery[2])) if delivery[2] is not None else None),
                attempt_states=tuple(
                    str(row[0])
                    for row in self._connection.execute(
                        "SELECT state FROM openmagic_runtime.delivery_attempts "
                        "WHERE delivery_id = %s ORDER BY created_at, delivery_attempt_id",
                        (delivery[0],),
                    ).fetchall()
                ),
            )
            for delivery in deliveries
        )


__all__ = [
    "EvidenceRecord",
    "RuntimeDatabaseHealth",
    "RuntimeDeliveryEvidence",
    "RuntimeEvidenceReader",
    "RuntimeInstanceEvidence",
    "RuntimeStepEvidence",
    "RuntimeWaitEvidence",
    "inspect_runtime_database",
]
