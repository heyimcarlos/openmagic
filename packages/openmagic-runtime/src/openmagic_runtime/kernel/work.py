"""Leased Attempt claiming and fenced result acceptance."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from psycopg import Connection
from psycopg.types.json import Jsonb

from openmagic_runtime._canonical import canonical_digest
from openmagic_runtime.kernel._trace import append_trace
from openmagic_runtime.kernel.definitions import validate_payload, verified_definition


class StaleAuthority(RuntimeError):
    """Raised when a Worker submits a result after its lease authority ended."""


class AttemptResultConflict(RuntimeError):
    """Raised when an Attempt identity is reused with a different observation."""


@dataclass(frozen=True)
class ClaimWork:
    claim_request_id: UUID
    worker_id: str
    executor_keys: tuple[str, ...]


@dataclass(frozen=True)
class ClaimedAttempt:
    instance_id: UUID
    step_id: UUID
    attempt_id: UUID
    attempt_number: int
    template_key: str
    executor_key: str
    lease_seconds: int
    input: dict[str, Any]


@dataclass(frozen=True)
class RenewedAttemptLease:
    attempt_id: UUID
    lease_expires_at: datetime
    hard_deadline: datetime


@dataclass(frozen=True)
class AttemptExecutionAuthority:
    claim: ClaimedAttempt
    directive: Literal["execute", "replay"]
    accepted_observation: dict[str, Any] | None


@dataclass
class DispositionRequired:
    instance_id: UUID
    step_id: UUID
    attempt_id: UUID
    attempt_number: int
    template_key: str
    observation: dict[str, Any]
    basis_state: Literal["completed", "abandoned"] = "completed"
    consumed: bool = False
    replayed: bool = False


def _template(connection: Connection[tuple[Any, ...]], instance_id: UUID, key: str) -> Any:
    row = connection.execute(
        "SELECT d.manifest, d.manifest_digest FROM openmagic_runtime.instances AS i "
        "JOIN openmagic_runtime.workflow_definitions AS d "
        "ON d.definition_key = i.definition_key AND d.definition_version = i.definition_version "
        "WHERE i.instance_id = %s",
        (instance_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError("Pinned Workflow Definition is unavailable")
    definition = verified_definition(dict(row[0]), str(row[1]))
    return next(item for item in definition.step_templates if item.key == key)


class KernelWork:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def claim(self, request: ClaimWork) -> ClaimedAttempt | None:
        self._connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            (str(request.claim_request_id),),
        )
        replay = self._connection.execute(
            "SELECT receipt, input_digest FROM openmagic_runtime.trace_events "
            "WHERE source_kind = 'claim' AND source_id = %s",
            (request.claim_request_id,),
        ).fetchone()
        if replay is not None:
            if str(replay[1]) != canonical_digest(request):
                raise ValueError("Attempt claim identity has conflicting input")
            value = dict(replay[0])
            return ClaimedAttempt(
                instance_id=UUID(value["instance_id"]),
                step_id=UUID(value["step_id"]),
                attempt_id=UUID(value["attempt_id"]),
                attempt_number=value["attempt_number"],
                template_key=value["template_key"],
                executor_key=value["executor_key"],
                lease_seconds=int(value["lease_seconds"]),
                input=dict(value["input"]),
            )
        instance = self._connection.execute(
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
            (list(request.executor_keys),),
        ).fetchone()
        if instance is None:
            return None
        instance_id = UUID(str(instance[0]))
        candidates = self._connection.execute(
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
        selected: tuple[Any, ...] | None = None
        selected_template: Any = None
        for candidate in candidates:
            template = _template(self._connection, instance_id, str(candidate[1]))
            if template.executor_key in request.executor_keys:
                selected = candidate
                selected_template = template
                break
        if selected is None:
            return None
        step_id = UUID(str(selected[0]))
        previous = self._connection.execute(
            "SELECT count(*) FROM openmagic_runtime.attempts WHERE step_id = %s",
            (step_id,),
        ).fetchone()
        attempt_number = int(previous[0]) + 1 if previous is not None else 1
        if attempt_number > selected_template.retry_policy.max_attempts:
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
                step_id,
                attempt_number,
                request.worker_id,
                selected_template.lease_seconds,
                selected_template.maximum_attempt_seconds,
            ),
        )
        payload = {
            "instance_id": str(instance_id),
            "step_id": str(step_id),
            "attempt_id": str(attempt_id),
            "attempt_number": attempt_number,
            "template_key": str(selected[1]),
            "executor_key": selected_template.executor_key,
            "lease_seconds": selected_template.lease_seconds,
            "input": dict(selected[2]),
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
        return ClaimedAttempt(
            instance_id=instance_id,
            step_id=step_id,
            attempt_id=attempt_id,
            attempt_number=attempt_number,
            template_key=str(selected[1]),
            executor_key=selected_template.executor_key,
            lease_seconds=selected_template.lease_seconds,
            input=dict(selected[2]),
        )

    def execution_authority(
        self,
        attempt: ClaimedAttempt,
        *,
        worker_id: str,
    ) -> AttemptExecutionAuthority:
        durable_instance = self._connection.execute(
            "SELECT instance_id FROM openmagic_runtime.attempts WHERE attempt_id = %s",
            (attempt.attempt_id,),
        ).fetchone()
        if durable_instance is None:
            raise StaleAuthority("Attempt authority does not exist")
        self._connection.execute(
            "SELECT instance_id FROM openmagic_runtime.instances WHERE instance_id = %s FOR UPDATE",
            (durable_instance[0],),
        ).fetchone()
        row = self._connection.execute(
            "SELECT a.state, a.worker_id, a.lease_expires_at > clock_timestamp(), "
            "a.hard_deadline > clock_timestamp(), a.observation, a.instance_id, a.step_id, "
            "a.attempt_number, s.template_key, s.input FROM openmagic_runtime.attempts AS a "
            "JOIN openmagic_runtime.steps AS s ON s.step_id = a.step_id "
            "WHERE a.attempt_id = %s FOR UPDATE OF a, s",
            (attempt.attempt_id,),
        ).fetchone()
        if row is None:
            raise StaleAuthority("Attempt authority does not exist")
        template = _template(self._connection, UUID(str(row[5])), str(row[8]))
        durable_claim = ClaimedAttempt(
            instance_id=UUID(str(row[5])),
            step_id=UUID(str(row[6])),
            attempt_id=attempt.attempt_id,
            attempt_number=int(row[7]),
            template_key=str(row[8]),
            executor_key=template.executor_key,
            lease_seconds=template.lease_seconds,
            input=dict(row[9]),
        )
        if attempt != durable_claim:
            raise StaleAuthority("Worker claim does not match durable Attempt authority")
        if row[0] == "completed":
            return AttemptExecutionAuthority(
                claim=durable_claim,
                directive="replay",
                accepted_observation=dict(row[4]),
            )
        if row[0] != "leased" or row[1] != worker_id or not row[2] or not row[3]:
            raise StaleAuthority("Attempt authority is stale")
        return AttemptExecutionAuthority(
            claim=durable_claim,
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
        replay = self._connection.execute(
            "SELECT receipt, input_digest FROM openmagic_runtime.trace_events "
            "WHERE source_kind = 'attempt_lease_renewal' AND source_id = %s",
            (renewal_id,),
        ).fetchone()
        if replay is not None:
            if str(replay[1]) != canonical_digest(renewal_input):
                raise ValueError("Attempt lease renewal identity has conflicting input")
            receipt = dict(replay[0])
            return RenewedAttemptLease(
                attempt_id=UUID(receipt["attempt_id"]),
                lease_expires_at=datetime.fromisoformat(receipt["lease_expires_at"]),
                hard_deadline=datetime.fromisoformat(receipt["hard_deadline"]),
            )
        durable_instance = self._connection.execute(
            "SELECT instance_id FROM openmagic_runtime.attempts WHERE attempt_id = %s",
            (attempt.attempt_id,),
        ).fetchone()
        if durable_instance is None:
            raise StaleAuthority("Attempt authority does not exist")
        self._connection.execute(
            "SELECT instance_id FROM openmagic_runtime.instances WHERE instance_id = %s FOR UPDATE",
            (durable_instance[0],),
        ).fetchone()
        row = self._connection.execute(
            "SELECT a.state, a.worker_id, a.lease_expires_at > clock_timestamp(), "
            "a.hard_deadline > clock_timestamp(), a.instance_id, a.step_id, "
            "a.attempt_number, s.template_key, s.input FROM openmagic_runtime.attempts AS a "
            "JOIN openmagic_runtime.steps AS s ON s.step_id = a.step_id "
            "WHERE a.attempt_id = %s FOR UPDATE OF a, s",
            (attempt.attempt_id,),
        ).fetchone()
        if row is None:
            raise StaleAuthority("Attempt authority does not exist")
        template = _template(self._connection, UUID(str(row[4])), str(row[7]))
        durable_claim = ClaimedAttempt(
            instance_id=UUID(str(row[4])),
            step_id=UUID(str(row[5])),
            attempt_id=attempt.attempt_id,
            attempt_number=int(row[6]),
            template_key=str(row[7]),
            executor_key=template.executor_key,
            lease_seconds=template.lease_seconds,
            input=dict(row[8]),
        )
        if attempt != durable_claim:
            raise StaleAuthority("Worker claim does not match durable Attempt authority")
        if row[0] != "leased" or row[1] != worker_id or not row[2] or not row[3]:
            raise StaleAuthority("Attempt authority is stale")
        renewed = self._connection.execute(
            "UPDATE openmagic_runtime.attempts SET lease_expires_at = "
            "LEAST(clock_timestamp() + (%s * interval '1 second'), hard_deadline) "
            "WHERE attempt_id = %s RETURNING lease_expires_at, hard_deadline",
            (template.lease_seconds, attempt.attempt_id),
        ).fetchone()
        if renewed is None:
            raise StaleAuthority("Attempt authority could not be renewed")
        result = RenewedAttemptLease(
            attempt_id=attempt.attempt_id,
            lease_expires_at=renewed[0],
            hard_deadline=renewed[1],
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

    def recover_expired(self, instance_id: UUID | None = None) -> DispositionRequired | None:
        instance = self._connection.execute(
            "SELECT i.instance_id FROM openmagic_runtime.instances AS i WHERE i.state = 'open' "
            "AND (%s::uuid IS NULL OR i.instance_id = %s) "
            "AND EXISTS (SELECT 1 FROM openmagic_runtime.attempts AS a "
            "WHERE a.instance_id = i.instance_id AND a.state = 'leased' "
            "AND (a.lease_expires_at <= clock_timestamp() "
            "OR a.hard_deadline <= clock_timestamp())) "
            "ORDER BY i.created_at, i.instance_id FOR UPDATE SKIP LOCKED LIMIT 1",
            (instance_id, instance_id),
        ).fetchone()
        if instance is None:
            return None
        instance_id = UUID(str(instance[0]))
        attempt = self._connection.execute(
            "SELECT a.attempt_id, a.step_id, a.attempt_number, s.template_key "
            "FROM openmagic_runtime.attempts AS a "
            "JOIN openmagic_runtime.steps AS s ON s.step_id = a.step_id "
            "WHERE a.instance_id = %s AND a.state = 'leased' "
            "AND (a.lease_expires_at <= clock_timestamp() "
            "OR a.hard_deadline <= clock_timestamp()) "
            "ORDER BY a.created_at, a.attempt_id FOR UPDATE OF a LIMIT 1",
            (instance_id,),
        ).fetchone()
        if attempt is None:
            return None
        attempt_id = UUID(str(attempt[0]))
        step_id = UUID(str(attempt[1]))
        self._connection.execute(
            "UPDATE openmagic_runtime.attempts SET state = 'abandoned', "
            "completed_at = clock_timestamp() WHERE attempt_id = %s",
            (attempt_id,),
        )
        append_trace(
            self._connection,
            instance_id=instance_id,
            event_type="attempt_abandoned",
            source_kind="attempt_abandonment",
            source_id=attempt_id,
            input_value={"attempt_id": str(attempt_id)},
            receipt=lambda _: {"attempt_id": str(attempt_id), "step_id": str(step_id)},
        )
        return DispositionRequired(
            instance_id=instance_id,
            step_id=step_id,
            attempt_id=attempt_id,
            attempt_number=int(attempt[2]),
            template_key=str(attempt[3]),
            observation={"expiry_cause": "lease_or_hard_deadline"},
            basis_state="abandoned",
        )

    def accept_result(
        self,
        attempt: ClaimedAttempt,
        *,
        worker_id: str,
        observation: dict[str, Any],
    ) -> DispositionRequired:
        durable_instance = self._connection.execute(
            "SELECT instance_id FROM openmagic_runtime.attempts WHERE attempt_id = %s",
            (attempt.attempt_id,),
        ).fetchone()
        if durable_instance is None:
            raise RuntimeError("Attempt not found")
        self._connection.execute(
            "SELECT instance_id FROM openmagic_runtime.instances WHERE instance_id = %s FOR UPDATE",
            (durable_instance[0],),
        ).fetchone()
        existing = self._connection.execute(
            "SELECT a.state, a.worker_id, a.lease_expires_at > clock_timestamp(), "
            "a.hard_deadline > clock_timestamp(), a.observation, a.observation_digest, "
            "a.instance_id, a.step_id, a.attempt_number, s.template_key, s.input "
            "FROM openmagic_runtime.attempts AS a "
            "JOIN openmagic_runtime.steps AS s ON s.step_id = a.step_id "
            "WHERE a.attempt_id = %s FOR UPDATE OF a, s",
            (attempt.attempt_id,),
        ).fetchone()
        if existing is None:
            raise RuntimeError("Attempt not found")
        template = _template(self._connection, UUID(str(existing[6])), str(existing[9]))
        if (
            attempt.instance_id != UUID(str(existing[6]))
            or attempt.step_id != UUID(str(existing[7]))
            or attempt.attempt_number != int(existing[8])
            or attempt.template_key != str(existing[9])
            or attempt.executor_key != template.executor_key
            or attempt.input != dict(existing[10])
        ):
            raise StaleAuthority("Worker claim does not match durable Attempt authority")
        digest = canonical_digest(observation)
        if existing[0] == "completed":
            if existing[5] != digest:
                raise AttemptResultConflict("Attempt result conflicts with its accepted result")
            return DispositionRequired(
                instance_id=attempt.instance_id,
                step_id=attempt.step_id,
                attempt_id=attempt.attempt_id,
                attempt_number=attempt.attempt_number,
                template_key=attempt.template_key,
                observation=dict(existing[4]),
                basis_state="completed",
                consumed=True,
                replayed=True,
            )
        if (
            existing[0] != "leased"
            or existing[1] != worker_id
            or not existing[2]
            or not existing[3]
        ):
            raise StaleAuthority("Attempt authority is stale")
        validate_payload(observation, template.observation_contract)
        self._connection.execute(
            "UPDATE openmagic_runtime.attempts SET state = 'completed', observation = %s, "
            "observation_digest = %s, completed_at = clock_timestamp() WHERE attempt_id = %s",
            (Jsonb(observation), digest, attempt.attempt_id),
        )
        append_trace(
            self._connection,
            instance_id=attempt.instance_id,
            event_type="attempt_completed",
            source_kind="attempt_result",
            source_id=attempt.attempt_id,
            input_value=observation,
            receipt=lambda _: {"attempt_id": str(attempt.attempt_id)},
        )
        return DispositionRequired(
            instance_id=attempt.instance_id,
            step_id=attempt.step_id,
            attempt_id=attempt.attempt_id,
            attempt_number=attempt.attempt_number,
            template_key=attempt.template_key,
            observation=observation,
            basis_state="completed",
        )


def claim_once(*, database_url: str, request: ClaimWork) -> ClaimedAttempt | None:
    try:
        with psycopg.connect(database_url) as connection, connection.transaction():
            return KernelWork(connection).claim(request)
    except psycopg.errors.UniqueViolation as error:
        if error.diag.constraint_name == "one_leased_attempt_per_step":
            return None
        raise


def renew_once(
    *, database_url: str, attempt: ClaimedAttempt, worker_id: str, renewal_id: UUID
) -> RenewedAttemptLease:
    with psycopg.connect(database_url) as connection, connection.transaction():
        return KernelWork(connection).renew(
            attempt,
            worker_id=worker_id,
            renewal_id=renewal_id,
        )


__all__ = [
    "AttemptExecutionAuthority",
    "AttemptResultConflict",
    "ClaimWork",
    "ClaimedAttempt",
    "DispositionRequired",
    "KernelWork",
    "RenewedAttemptLease",
    "StaleAuthority",
    "claim_once",
    "renew_once",
]
