"""Canonical durable Attempt authority reconstruction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import Connection
from psycopg.rows import dict_row

from openmagic_runtime._canonical import canonical_digest
from openmagic_runtime.kernel._persistence.trace import append_trace, read_trace_replay
from openmagic_runtime.kernel._persistence.transition_records import read_instance_definition
from openmagic_runtime.kernel._work_contracts import (
    AttemptExecutionAuthority,
    ClaimedAttempt,
    RenewedAttemptLease,
    StaleAuthority,
)
from openmagic_runtime.kernel.definitions import StepTemplate, verified_definition


def step_template(
    connection: Connection[tuple[Any, ...]], instance_id: UUID, template_key: str
) -> StepTemplate:
    record = read_instance_definition(connection, instance_id)
    if record is None:
        raise RuntimeError("Pinned Workflow Definition is unavailable")
    definition = verified_definition(record.manifest, record.manifest_digest)
    return next(item for item in definition.step_templates if item.key == template_key)


@dataclass(frozen=True)
class AttemptAuthorityRecord:
    state: str
    worker_id: str | None
    lease_valid: bool
    deadline_valid: bool
    observation: dict[str, Any] | None
    observation_digest: str | None
    instance_id: UUID
    step_id: UUID
    attempt_number: int
    template_key: str
    input: dict[str, Any]
    lease_expires_at: datetime
    checked_at: datetime

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> AttemptAuthorityRecord:
        observation = record["observation"]
        observation_digest = record["observation_digest"]
        worker_id = record["worker_id"]
        return cls(
            state=str(record["state"]),
            worker_id=None if worker_id is None else str(worker_id),
            lease_valid=bool(record["lease_valid"]),
            deadline_valid=bool(record["deadline_valid"]),
            observation=None if observation is None else dict(observation),
            observation_digest=None if observation_digest is None else str(observation_digest),
            instance_id=UUID(str(record["instance_id"])),
            step_id=UUID(str(record["step_id"])),
            attempt_number=int(record["attempt_number"]),
            template_key=str(record["template_key"]),
            input=dict(record["input"]),
            lease_expires_at=record["lease_expires_at"],
            checked_at=record["checked_at"],
        )

    def claim(
        self,
        *,
        attempt_id: UUID,
        template: StepTemplate,
    ) -> ClaimedAttempt:
        return ClaimedAttempt(
            instance_id=self.instance_id,
            step_id=self.step_id,
            attempt_id=attempt_id,
            attempt_number=self.attempt_number,
            template_key=self.template_key,
            executor_key=template.executor_key,
            lease_seconds=template.lease_seconds,
            input=dict(self.input),
        )

    def require_matching_claim(
        self,
        claim: ClaimedAttempt,
        *,
        template: StepTemplate,
    ) -> None:
        if claim != self.claim(attempt_id=claim.attempt_id, template=template):
            raise StaleAuthority("Worker claim does not match durable Attempt authority")

    def require_live_lease(self, worker_id: str) -> None:
        if (
            self.state != "leased"
            or self.worker_id != worker_id
            or not self.lease_valid
            or not self.deadline_valid
        ):
            raise StaleAuthority(
                "Attempt authority is stale",
                checked_at=self.checked_at,
                lease_expires_at=self.lease_expires_at,
            )


def lock_attempt_authority(
    connection: Connection[tuple[Any, ...]], attempt_id: UUID
) -> AttemptAuthorityRecord:
    with connection.cursor(row_factory=dict_row) as cursor:
        record = cursor.execute(
            "SELECT a.state, a.worker_id, "
            "a.lease_expires_at > clock_timestamp() AS lease_valid, "
            "a.hard_deadline > clock_timestamp() AS deadline_valid, "
            "a.observation, a.observation_digest, a.instance_id, a.step_id, "
            "a.attempt_number, s.template_key, s.input, a.lease_expires_at, "
            "clock_timestamp() AS checked_at "
            "FROM openmagic_runtime.attempts AS a "
            "JOIN openmagic_runtime.steps AS s ON s.step_id = a.step_id "
            "JOIN openmagic_runtime.instances AS i ON i.instance_id = a.instance_id "
            "WHERE a.attempt_id = %s FOR UPDATE OF i, a, s",
            (attempt_id,),
        ).fetchone()
    if record is None:
        raise StaleAuthority("Attempt authority does not exist")
    return AttemptAuthorityRecord.decode(record)


class AttemptAuthorityRecords:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def execution_authority(
        self,
        attempt: ClaimedAttempt,
        *,
        worker_id: str,
    ) -> AttemptExecutionAuthority:
        authority = lock_attempt_authority(self._connection, attempt.attempt_id)
        template = step_template(
            self._connection,
            authority.instance_id,
            authority.template_key,
        )
        authority.require_matching_claim(attempt, template=template)
        if authority.state == "completed":
            return AttemptExecutionAuthority(
                claim=authority.claim(attempt_id=attempt.attempt_id, template=template),
                directive="replay",
                accepted_observation=None
                if authority.observation is None
                else dict(authority.observation),
            )
        authority.require_live_lease(worker_id)
        return AttemptExecutionAuthority(
            claim=authority.claim(attempt_id=attempt.attempt_id, template=template),
            directive="execute",
            accepted_observation=None,
        )

    def renew(
        self,
        attempt: ClaimedAttempt,
        *,
        worker_id: str,
        renewal_id: UUID,
    ) -> RenewedAttemptLease:
        renewal_input = {"attempt": attempt, "worker_id": worker_id}
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (str(renewal_id),),
        )
        replay = read_trace_replay(
            self._connection,
            source_kind="attempt_lease_renewal",
            source_id=renewal_id,
        )
        if replay is not None:
            if replay.input_digest != canonical_digest(renewal_input):
                raise ValueError("Attempt lease renewal identity has conflicting input")
            receipt = replay.receipt
            return RenewedAttemptLease(
                attempt_id=UUID(str(receipt["attempt_id"])),
                lease_expires_at=datetime.fromisoformat(str(receipt["lease_expires_at"])),
                hard_deadline=datetime.fromisoformat(str(receipt["hard_deadline"])),
            )

        authority = lock_attempt_authority(self._connection, attempt.attempt_id)
        template = step_template(
            self._connection,
            authority.instance_id,
            authority.template_key,
        )
        authority.require_matching_claim(attempt, template=template)
        authority.require_live_lease(worker_id)
        with self._connection.cursor(row_factory=dict_row) as cursor:
            renewed = cursor.execute(
                "UPDATE openmagic_runtime.attempts SET lease_expires_at = "
                "LEAST(clock_timestamp() + (%s * interval '1 second'), hard_deadline) "
                "WHERE attempt_id = %s RETURNING lease_expires_at, hard_deadline",
                (template.lease_seconds, attempt.attempt_id),
            ).fetchone()
        if renewed is None:
            raise StaleAuthority("Attempt authority could not be renewed")
        result = RenewedAttemptLease(
            attempt_id=attempt.attempt_id,
            lease_expires_at=renewed["lease_expires_at"],
            hard_deadline=renewed["hard_deadline"],
        )
        append_trace(
            self._connection,
            instance_id=attempt.instance_id,
            event_type="attempt_lease_renewed",
            source_kind="attempt_lease_renewal",
            source_id=renewal_id,
            input_value=renewal_input,
            receipt=lambda _: {
                "attempt_id": str(result.attempt_id),
                "lease_expires_at": result.lease_expires_at.isoformat(),
                "hard_deadline": result.hard_deadline.isoformat(),
            },
        )
        return result


__all__ = [
    "AttemptAuthorityRecord",
    "AttemptAuthorityRecords",
    "lock_attempt_authority",
    "step_template",
]
