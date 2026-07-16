"""Canonical durable Attempt authority reconstruction."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from openmagic_runtime._canonical import canonical_digest
from openmagic_runtime._persistence.durable_values import (
    boolean_value,
    integer_value,
    mapping_value,
    string_value,
    timestamp_value,
    uuid_value,
)
from openmagic_runtime.kernel._persistence.trace import append_trace, read_trace_replay
from openmagic_runtime.kernel._persistence.transition_records import read_instance_definition
from openmagic_runtime.kernel._record_decoding import attempt_state
from openmagic_runtime.kernel._work_contracts import (
    AttemptExecutionAuthority,
    ClaimedAttempt,
    RenewedAttemptLease,
    StaleAuthority,
)
from openmagic_runtime.kernel.definitions import StepTemplate, verified_definition
from openmagic_runtime.kernel.inspection_types import AttemptState


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
    state: AttemptState
    worker_id: str
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

    def __post_init__(self) -> None:
        completed_fields = self.observation is not None and self.observation_digest is not None
        if self.state == "completed" and not completed_fields:
            raise RuntimeError("Completed Attempt authority is missing its accepted result")
        if self.state != "completed" and (
            self.observation is not None or self.observation_digest is not None
        ):
            raise RuntimeError("Uncompleted Attempt authority contains an accepted result")

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> AttemptAuthorityRecord:
        observation = record["observation"]
        observation_digest = record["observation_digest"]
        return cls(
            state=attempt_state(record["state"]),
            worker_id=string_value(record["worker_id"]),
            lease_valid=boolean_value(record["lease_valid"]),
            deadline_valid=boolean_value(record["deadline_valid"]),
            observation=None if observation is None else mapping_value(observation),
            observation_digest=(
                None if observation_digest is None else string_value(observation_digest)
            ),
            instance_id=uuid_value(record["instance_id"]),
            step_id=uuid_value(record["step_id"]),
            attempt_number=integer_value(record["attempt_number"]),
            template_key=string_value(record["template_key"]),
            input=mapping_value(record["input"]),
            lease_expires_at=timestamp_value(record["lease_expires_at"]),
            checked_at=timestamp_value(record["checked_at"]),
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

    def completed_observation(self) -> dict[str, Any]:
        if self.state != "completed" or self.observation is None:
            raise RuntimeError("Attempt authority has no completed observation")
        return dict(self.observation)


def lock_attempt_authority(
    connection: Connection[tuple[Any, ...]], attempt_id: UUID
) -> AttemptAuthorityRecord:
    with connection.cursor(row_factory=dict_row) as cursor:
        identity = cursor.execute(
            "SELECT instance_id FROM openmagic_runtime.attempts WHERE attempt_id = %s",
            (attempt_id,),
        ).fetchone()
        if identity is None:
            raise StaleAuthority("Attempt authority does not exist")
        locked_instance = cursor.execute(
            "SELECT instance_id FROM openmagic_runtime.instances WHERE instance_id = %s FOR UPDATE",
            (identity["instance_id"],),
        ).fetchone()
        if locked_instance is None:
            raise StaleAuthority("Attempt authority does not exist")
        record = cursor.execute(
            "SELECT a.state, a.worker_id, "
            "a.lease_expires_at > clock_timestamp() AS lease_valid, "
            "a.hard_deadline > clock_timestamp() AS deadline_valid, "
            "a.observation, a.observation_digest, a.instance_id, a.step_id, "
            "a.attempt_number, s.template_key, s.input, a.lease_expires_at, "
            "clock_timestamp() AS checked_at "
            "FROM openmagic_runtime.attempts AS a "
            "JOIN openmagic_runtime.steps AS s ON s.step_id = a.step_id "
            "WHERE a.attempt_id = %s FOR UPDATE OF a, s",
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
                accepted_observation=authority.completed_observation(),
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


def renew_once_record(
    *, database_url: str, attempt: ClaimedAttempt, worker_id: str, renewal_id: UUID
) -> RenewedAttemptLease:
    with psycopg.connect(database_url) as connection, connection.transaction():
        return AttemptAuthorityRecords(connection).renew(
            attempt,
            worker_id=worker_id,
            renewal_id=renewal_id,
        )


__all__ = [
    "AttemptAuthorityRecord",
    "AttemptAuthorityRecords",
    "lock_attempt_authority",
    "renew_once_record",
    "step_template",
]
