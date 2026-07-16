"""Accepted Attempt result persistence."""

from __future__ import annotations

from typing import Any

from psycopg import Connection
from psycopg.types.json import Jsonb

from openmagic_runtime._canonical import canonical_digest
from openmagic_runtime.kernel._persistence.trace import append_trace
from openmagic_runtime.kernel._persistence.work_authority import (
    lock_attempt_authority,
    step_template,
)
from openmagic_runtime.kernel._work_contracts import (
    AttemptResultConflict,
    ClaimedAttempt,
    DispositionRequired,
)
from openmagic_runtime.kernel.definitions import validate_payload


class AttemptResultRecords:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def accept_result(
        self,
        attempt: ClaimedAttempt,
        *,
        worker_id: str,
        observation: dict[str, Any],
    ) -> DispositionRequired:
        authority = lock_attempt_authority(self._connection, attempt.attempt_id)
        template = step_template(
            self._connection,
            authority.instance_id,
            authority.template_key,
        )
        authority.require_matching_claim(attempt, template=template)
        digest = canonical_digest(observation)
        if authority.state == "completed":
            if authority.observation_digest != digest or authority.observation is None:
                raise AttemptResultConflict("Attempt result conflicts with its accepted result")
            return DispositionRequired(
                instance_id=attempt.instance_id,
                step_id=attempt.step_id,
                attempt_id=attempt.attempt_id,
                attempt_number=attempt.attempt_number,
                template_key=attempt.template_key,
                observation=dict(authority.observation),
                basis_state="completed",
                consumed=True,
                replayed=True,
            )
        authority.require_live_lease(worker_id)
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


__all__ = ["AttemptResultRecords"]
