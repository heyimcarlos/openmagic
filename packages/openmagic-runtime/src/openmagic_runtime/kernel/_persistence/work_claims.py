"""Claim and replay persistence for leased Attempt work."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row

from openmagic_runtime._canonical import canonical_digest
from openmagic_runtime._persistence.durable_values import (
    mapping_value,
    nonempty_string,
    nonnegative_integer_value,
    positive_integer_value,
    uuid_text_value,
    uuid_value,
)
from openmagic_runtime.kernel._persistence.trace import append_trace, read_trace_replay
from openmagic_runtime.kernel._persistence.work_authority import step_template
from openmagic_runtime.kernel._work_contracts import ClaimedAttempt, ClaimWork
from openmagic_runtime.kernel.definitions import StepTemplate


@dataclass(frozen=True)
class ClaimCandidateRecord:
    step_id: UUID
    template_key: str
    input: dict[str, Any]

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> ClaimCandidateRecord:
        return cls(
            step_id=uuid_value(record["step_id"]),
            template_key=nonempty_string(record["template_key"]),
            input=mapping_value(record["input"]),
        )


def decode_claimed_attempt_receipt(value: Mapping[str, Any]) -> ClaimedAttempt:
    return ClaimedAttempt(
        instance_id=uuid_text_value(value["instance_id"]),
        step_id=uuid_text_value(value["step_id"]),
        attempt_id=uuid_text_value(value["attempt_id"]),
        attempt_number=positive_integer_value(value["attempt_number"]),
        template_key=nonempty_string(value["template_key"]),
        executor_key=nonempty_string(value["executor_key"]),
        lease_seconds=positive_integer_value(value["lease_seconds"]),
        input=mapping_value(value["input"]),
    )


class AttemptClaimRecords:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def claim(self, request: ClaimWork) -> ClaimedAttempt | None:
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (str(request.claim_request_id),),
        )
        replay = read_trace_replay(
            self._connection,
            source_kind="claim",
            source_id=request.claim_request_id,
        )
        if replay is not None:
            if replay.input_digest != canonical_digest(request):
                raise ValueError("Attempt claim identity has conflicting input")
            return decode_claimed_attempt_receipt(replay.receipt)

        instance_id = self._lock_claimable_instance(request.executor_keys)
        if instance_id is None:
            return None
        selected = self._select_candidate(instance_id, request.executor_keys)
        if selected is None:
            return None
        candidate, template = selected
        attempt_number = self._next_attempt_number(candidate.step_id)
        if attempt_number > template.retry_policy.max_attempts:
            return None

        attempt_id = uuid4()
        self._connection.execute(
            "INSERT INTO openmagic_runtime.attempts "
            "(attempt_id, instance_id, step_id, attempt_number, state, worker_id, "
            "lease_expires_at, hard_deadline) VALUES "
            "(%s, %s, %s, %s, 'leased', %s, "
            "clock_timestamp() + (%s * interval '1 second'), "
            "clock_timestamp() + (%s * interval '1 second'))",
            (
                attempt_id,
                instance_id,
                candidate.step_id,
                attempt_number,
                request.worker_id,
                template.lease_seconds,
                template.maximum_attempt_seconds,
            ),
        )
        claim = ClaimedAttempt(
            instance_id=instance_id,
            step_id=candidate.step_id,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            template_key=candidate.template_key,
            executor_key=template.executor_key,
            lease_seconds=template.lease_seconds,
            input=dict(candidate.input),
        )
        payload = {
            "instance_id": str(claim.instance_id),
            "step_id": str(claim.step_id),
            "attempt_id": str(claim.attempt_id),
            "attempt_number": claim.attempt_number,
            "template_key": claim.template_key,
            "executor_key": claim.executor_key,
            "lease_seconds": claim.lease_seconds,
            "input": dict(claim.input),
        }
        append_trace(
            self._connection,
            instance_id=instance_id,
            event_type="attempt_leased",
            source_kind="claim",
            source_id=request.claim_request_id,
            input_value=request,
            receipt=lambda _: payload,
        )
        return claim

    def _lock_claimable_instance(self, executor_keys: tuple[str, ...]) -> UUID | None:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT i.instance_id FROM openmagic_runtime.instances AS i "
                "JOIN openmagic_runtime.workflow_definitions AS d "
                "ON d.definition_key = i.definition_key "
                "AND d.definition_version = i.definition_version "
                "WHERE i.state = 'open' AND EXISTS ("
                "SELECT 1 FROM openmagic_runtime.steps AS s "
                "JOIN LATERAL jsonb_array_elements(d.manifest->'step_templates') AS template "
                "ON template->>'key' = s.template_key "
                "WHERE s.instance_id = i.instance_id "
                "AND template->>'executor_key' = ANY(%s) "
                "AND s.state = 'pending' AND s.claimable_at <= clock_timestamp() "
                "AND NOT EXISTS (SELECT 1 FROM openmagic_runtime.attempts AS a "
                "WHERE a.step_id = s.step_id AND a.state = 'leased') "
                "AND NOT EXISTS (SELECT 1 FROM openmagic_runtime.step_dependencies AS dep "
                "JOIN openmagic_runtime.steps AS prerequisite "
                "ON prerequisite.step_id = dep.prerequisite_step_id "
                "WHERE dep.step_id = s.step_id AND prerequisite.state <> 'succeeded')) "
                "ORDER BY i.created_at, i.instance_id FOR UPDATE OF i SKIP LOCKED LIMIT 1",
                (list(executor_keys),),
            ).fetchone()
        return None if record is None else uuid_value(record["instance_id"])

    def _select_candidate(
        self, instance_id: UUID, executor_keys: tuple[str, ...]
    ) -> tuple[ClaimCandidateRecord, StepTemplate] | None:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            records = cursor.execute(
                "SELECT s.step_id, s.template_key, s.input FROM openmagic_runtime.steps AS s "
                "WHERE s.instance_id = %s AND s.state = 'pending' "
                "AND s.claimable_at <= clock_timestamp() "
                "AND NOT EXISTS (SELECT 1 FROM openmagic_runtime.attempts AS a "
                "WHERE a.step_id = s.step_id AND a.state = 'leased') "
                "AND NOT EXISTS (SELECT 1 FROM openmagic_runtime.step_dependencies AS dep "
                "JOIN openmagic_runtime.steps AS prerequisite "
                "ON prerequisite.step_id = dep.prerequisite_step_id "
                "WHERE dep.step_id = s.step_id AND prerequisite.state <> 'succeeded') "
                "ORDER BY s.claimable_at, s.step_id FOR UPDATE OF s SKIP LOCKED",
                (instance_id,),
            ).fetchall()
        for record in records:
            candidate = ClaimCandidateRecord.decode(record)
            template = step_template(self._connection, instance_id, candidate.template_key)
            if template.executor_key in executor_keys:
                return candidate, template
        return None

    def _next_attempt_number(self, step_id: UUID) -> int:
        with self._connection.cursor(row_factory=dict_row) as cursor:
            record = cursor.execute(
                "SELECT count(*) AS attempt_count FROM openmagic_runtime.attempts "
                "WHERE step_id = %s",
                (step_id,),
            ).fetchone()
        return 1 if record is None else nonnegative_integer_value(record["attempt_count"]) + 1


def claim_once_record(*, database_url: str, request: ClaimWork) -> ClaimedAttempt | None:
    try:
        with psycopg.connect(database_url) as connection, connection.transaction():
            return AttemptClaimRecords(connection).claim(request)
    except psycopg.errors.UniqueViolation as error:
        if error.diag.constraint_name == "one_leased_attempt_per_step":
            return None
        raise


__all__ = [
    "AttemptClaimRecords",
    "ClaimCandidateRecord",
    "claim_once_record",
    "decode_claimed_attempt_receipt",
]
