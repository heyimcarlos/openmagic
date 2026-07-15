"""Durable Agent Run provenance separate from kernel Attempts."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
from uuid import UUID, uuid4

from psycopg import Connection
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class AgentRunInput:
    agent_key: str
    agent_version: int
    task_type: str
    task_version: int
    thread_id: UUID
    context_through_sequence: int
    domain_event_context: tuple[dict[str, Any], ...]
    audience_context: dict[str, Any]
    locale: str
    task_input: dict[str, Any]

    def __post_init__(self) -> None:
        if self.agent_version <= 0 or self.task_version <= 0:
            raise ValueError("Agent and task versions must be positive")
        if self.context_through_sequence < 0:
            raise ValueError("Agent context cutoff cannot be negative")
        if len(self.domain_event_context) > 100:
            raise ValueError("Agent domain event context exceeds its bound")


@dataclass(frozen=True)
class AgentRun:
    agent_run_id: UUID
    attempt_id: UUID
    agent_key: str
    thread_id: UUID
    context_through_sequence: int
    status: str


class AgentRuns:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def start(
        self,
        *,
        attempt_id: UUID,
        input: AgentRunInput,
    ) -> AgentRun:
        agent_run_id = uuid4()
        self._connection.execute(
            "INSERT INTO openmagic_runtime.agent_runs "
            "(agent_run_id, attempt_id, agent_key, thread_id, context_through_sequence, input, status) "
            "VALUES (%s, %s, %s, %s, %s, %s, 'running')",
            (
                agent_run_id,
                attempt_id,
                input.agent_key,
                input.thread_id,
                input.context_through_sequence,
                Jsonb(
                    {
                        "agent_key": input.agent_key,
                        "agent_version": input.agent_version,
                        "task_type": input.task_type,
                        "task_version": input.task_version,
                        "thread_id": str(input.thread_id),
                        "context_through_sequence": input.context_through_sequence,
                        "domain_event_context": list(input.domain_event_context),
                        "audience_context": input.audience_context,
                        "locale": input.locale,
                        "task_input": input.task_input,
                    }
                ),
            ),
        )
        return AgentRun(
            agent_run_id=agent_run_id,
            attempt_id=attempt_id,
            agent_key=input.agent_key,
            thread_id=input.thread_id,
            context_through_sequence=input.context_through_sequence,
            status="running",
        )

    def complete_for_attempt(self, attempt_id: UUID, result: dict[str, Any]) -> AgentRun:
        run = self.find_by_attempt(attempt_id)
        if run is None:
            raise RuntimeError("Agent Attempt has no durable Agent Run")
        updated = self._connection.execute(
            "UPDATE openmagic_runtime.agent_runs SET status = 'completed', result = %s, "
            "completed_at = clock_timestamp() WHERE agent_run_id = %s AND attempt_id = %s "
            "AND status = 'running' RETURNING agent_run_id",
            (Jsonb(result), run.agent_run_id, attempt_id),
        ).fetchone()
        if updated is None:
            existing = self._connection.execute(
                "SELECT result FROM openmagic_runtime.agent_runs "
                "WHERE attempt_id = %s AND status = 'completed'",
                (attempt_id,),
            ).fetchone()
            if existing is None or dict(existing[0]) != result:
                raise RuntimeError("Agent Run completion conflicts with durable state")
        return replace(run, status="completed")

    def fail_for_attempt(self, attempt_id: UUID, failure: dict[str, Any]) -> None:
        self._connection.execute(
            "UPDATE openmagic_runtime.agent_runs SET status = 'failed', result = %s, "
            "completed_at = clock_timestamp() WHERE attempt_id = %s AND status = 'running'",
            (Jsonb(failure), attempt_id),
        )

    def abandon_for_attempt(self, attempt_id: UUID) -> None:
        self._connection.execute(
            "UPDATE openmagic_runtime.agent_runs SET status = 'abandoned', "
            "result = %s, completed_at = clock_timestamp() "
            "WHERE attempt_id = %s AND status = 'running'",
            (Jsonb({"class": "attempt_authority_expired"}), attempt_id),
        )

    def find_by_attempt(self, attempt_id: UUID) -> AgentRun | None:
        row = self._connection.execute(
            "SELECT agent_run_id, agent_key, thread_id, context_through_sequence, status "
            "FROM openmagic_runtime.agent_runs WHERE attempt_id = %s",
            (attempt_id,),
        ).fetchone()
        if row is None:
            return None
        return AgentRun(
            agent_run_id=UUID(str(row[0])),
            attempt_id=attempt_id,
            agent_key=str(row[1]),
            thread_id=UUID(str(row[2])),
            context_through_sequence=int(row[3]),
            status=str(row[4]),
        )


__all__ = ["AgentRun", "AgentRunInput", "AgentRuns"]
