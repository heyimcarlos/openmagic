"""Private typed reads used only by kernel transitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row

from openmagic_runtime.kernel.records import AttemptState, StepState, attempt_state, step_state


@dataclass(frozen=True)
class RuntimeDefinitionRecord:
    manifest: dict[str, Any]
    manifest_digest: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeDefinitionRecord:
        return cls(
            manifest=dict(record["manifest"]),
            manifest_digest=str(record["manifest_digest"]),
        )


@dataclass(frozen=True)
class RuntimeDispositionSource:
    instance_id: UUID
    step_id: UUID
    attempt_number: int
    state: AttemptState
    observation_digest: str | None
    template_key: str

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeDispositionSource:
        observation_digest = record["observation_digest"]
        return cls(
            instance_id=UUID(str(record["instance_id"])),
            step_id=UUID(str(record["step_id"])),
            attempt_number=int(record["attempt_number"]),
            state=attempt_state(record["state"]),
            observation_digest=(
                str(observation_digest) if observation_digest is not None else None
            ),
            template_key=str(record["template_key"]),
        )


@dataclass(frozen=True)
class RuntimeDeferredStep:
    step_id: UUID
    instance_id: UUID
    template_key: str
    state: StepState
    deferred_attempt_id: UUID | None

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeDeferredStep:
        deferred_attempt_id = record["deferred_attempt_id"]
        return cls(
            step_id=UUID(str(record["step_id"])),
            instance_id=UUID(str(record["instance_id"])),
            template_key=str(record["template_key"]),
            state=step_state(record["state"]),
            deferred_attempt_id=(
                UUID(str(deferred_attempt_id)) if deferred_attempt_id is not None else None
            ),
        )


def read_instance_definition(
    connection: Connection[tuple[Any, ...]], instance_id: UUID
) -> RuntimeDefinitionRecord | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT d.manifest, d.manifest_digest FROM openmagic_runtime.instances AS i "
            "JOIN openmagic_runtime.workflow_definitions AS d ON "
            "d.definition_key = i.definition_key "
            "AND d.definition_version = i.definition_version WHERE i.instance_id = %s",
            (instance_id,),
        ).fetchone()
    return RuntimeDefinitionRecord.decode(record) if record is not None else None


def lock_disposition_source(
    connection: Connection[tuple[Any, ...]], attempt_id: UUID
) -> RuntimeDispositionSource | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT a.instance_id, a.step_id, a.attempt_number, a.state, "
            "a.observation_digest, s.template_key FROM openmagic_runtime.attempts AS a "
            "JOIN openmagic_runtime.steps AS s ON s.step_id = a.step_id "
            "WHERE a.attempt_id = %s FOR UPDATE OF a, s",
            (attempt_id,),
        ).fetchone()
    return RuntimeDispositionSource.decode(record) if record is not None else None


def lock_deferred_step(
    connection: Connection[tuple[Any, ...]], *, instance_id: UUID, step_id: UUID
) -> RuntimeDeferredStep | None:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT step_id, instance_id, template_key, state, deferred_attempt_id "
            "FROM openmagic_runtime.steps WHERE step_id = %s AND instance_id = %s "
            "FOR UPDATE",
            (step_id, instance_id),
        ).fetchone()
    return RuntimeDeferredStep.decode(record) if record is not None else None


def attempt_count_for_step(connection: Connection[tuple[Any, ...]], step_id: UUID) -> int:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT count(*) AS attempt_count FROM openmagic_runtime.attempts WHERE step_id = %s",
            (step_id,),
        ).fetchone()
    if record is None:
        raise RuntimeError("Attempt count is unavailable")
    return int(record["attempt_count"])
