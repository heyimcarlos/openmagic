"""Read-only runtime deployment evidence exposed to installed process roles."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection

from openmagic_runtime._canonical import canonical_bytes, canonical_digest
from openmagic_runtime._persistence.health_records import read_database_health
from openmagic_runtime.delivery import RuntimeDeliveryEvidence, deliveries_for_domain_event
from openmagic_runtime.kernel._evidence_records import (
    RuntimeAgentRunEvidence,
    RuntimeAttemptEvidence,
    RuntimeInstanceEvidence,
    RuntimeStepEvidence,
    RuntimeWaitEvidence,
    read_instance_evidence,
)


def content_fingerprint(value: object) -> str:
    """Return the runtime's canonical SHA-256 fingerprint for typed public evidence."""
    return canonical_digest(value)


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

    record = read_database_health(database_url)
    if not record.runtime_schema_ready:
        raise RuntimeError("OpenMagic Runtime schema is not installed")
    return RuntimeDatabaseHealth(
        status="ready",
        pid=os.getpid(),
        database=record.database,
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


class RuntimeEvidenceReader:
    """Transaction-scoped public projection over runtime-owned evidence."""

    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def instance(self, instance_id: UUID) -> RuntimeInstanceEvidence:
        return read_instance_evidence(self._connection, instance_id)

    def deliveries(self, domain_event_id: UUID) -> tuple[RuntimeDeliveryEvidence, ...]:
        return deliveries_for_domain_event(self._connection, domain_event_id)


__all__ = [
    "EvidenceRecord",
    "RuntimeAgentRunEvidence",
    "RuntimeAttemptEvidence",
    "RuntimeDatabaseHealth",
    "RuntimeDeliveryEvidence",
    "RuntimeEvidenceReader",
    "RuntimeInstanceEvidence",
    "RuntimeStepEvidence",
    "RuntimeWaitEvidence",
    "content_fingerprint",
    "inspect_runtime_database",
]
