"""Durable typed Agent Run provenance separate from kernel Attempts."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Any, cast
from uuid import UUID, uuid4

from psycopg import Connection

from openmagic_runtime._persistence.agent_run_records import (
    complete_agent_run,
    find_agent_run,
    finish_agent_run,
    insert_agent_run,
    read_running_input,
)
from openmagic_runtime.threads import ThreadAccess, ThreadContext

AgentScalar = str | int

_LOCAL_KEY = re.compile(r"^[a-z][a-z0-9_]*$")
_STABLE_KEY = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


@dataclass(frozen=True)
class AgentField:
    name: str
    value: AgentScalar

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not _LOCAL_KEY.fullmatch(self.name)
            or type(self.value) not in {str, int}
        ):
            raise ValueError("Agent field must have a name and an immutable scalar value")


@dataclass(frozen=True)
class AgentRecord:
    schema_key: str
    schema_version: int
    fields: tuple[AgentField, ...]

    def __post_init__(self) -> None:
        names = [field.name for field in self.fields]
        if (
            not isinstance(self.schema_key, str)
            or not _STABLE_KEY.fullmatch(self.schema_key)
            or type(self.schema_version) is not int
            or self.schema_version <= 0
            or type(self.fields) is not tuple
            or any(type(field) is not AgentField for field in self.fields)
        ):
            raise ValueError("Agent record schema identity is invalid")
        if len(names) != len(set(names)):
            raise ValueError("Agent record fields must be unique")

    def value(self, name: str) -> AgentScalar:
        try:
            return next(field.value for field in self.fields if field.name == name)
        except StopIteration:
            raise KeyError(f"Agent record has no field: {name}") from None


@dataclass(frozen=True)
class AgentConfiguration:
    agent_key: str
    agent_version: int
    instruction_key: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.agent_key, str)
            or not _STABLE_KEY.fullmatch(self.agent_key)
            or type(self.agent_version) is not int
            or self.agent_version <= 0
            or not isinstance(self.instruction_key, str)
            or not _STABLE_KEY.fullmatch(self.instruction_key)
        ):
            raise ValueError("Agent configuration identity is invalid")


@dataclass(frozen=True)
class AgentTask:
    task_type: str
    task_version: int
    input: AgentRecord

    def __post_init__(self) -> None:
        if (
            not isinstance(self.task_type, str)
            or not _STABLE_KEY.fullmatch(self.task_type)
            or type(self.task_version) is not int
            or self.task_version <= 0
            or type(self.input) is not AgentRecord
        ):
            raise ValueError("Agent task identity is invalid")


@dataclass(frozen=True)
class AgentDomainEvent:
    event_id: UUID
    event_type: str
    schema_version: int
    payload: AgentRecord

    def __post_init__(self) -> None:
        if (
            type(self.event_id) is not UUID
            or not isinstance(self.event_type, str)
            or not _STABLE_KEY.fullmatch(self.event_type)
            or type(self.schema_version) is not int
            or self.schema_version <= 0
            or type(self.payload) is not AgentRecord
        ):
            raise ValueError("Agent domain event identity is invalid")


@dataclass(frozen=True)
class AgentAudience:
    kind: str
    identifier: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.kind, str)
            or not _LOCAL_KEY.fullmatch(self.kind)
            or not isinstance(self.identifier, str)
            or not self.identifier
        ):
            raise ValueError("Agent audience must be explicit")


@dataclass(frozen=True)
class AgentRunInput:
    configuration: AgentConfiguration
    task: AgentTask
    thread_id: UUID
    context_through_sequence: int
    domain_event_context: tuple[AgentDomainEvent, ...]
    audience_context: AgentAudience
    locale: str

    def __post_init__(self) -> None:
        if (
            type(self.configuration) is not AgentConfiguration
            or type(self.task) is not AgentTask
            or type(self.thread_id) is not UUID
            or type(self.context_through_sequence) is not int
            or self.context_through_sequence < 0
            or type(self.domain_event_context) is not tuple
            or any(type(event) is not AgentDomainEvent for event in self.domain_event_context)
            or type(self.audience_context) is not AgentAudience
            or not isinstance(self.locale, str)
            or not self.locale
        ):
            raise ValueError("Agent Run Input is invalid")
        if len(self.domain_event_context) > 100:
            raise ValueError("Agent domain event context exceeds its bound")
        if not self.locale:
            raise ValueError("Agent locale must be explicit")


@dataclass(frozen=True)
class AgentExecutionInput:
    agent_run_id: UUID
    attempt_id: UUID
    run_input: AgentRunInput
    thread_context: ThreadContext

    def __post_init__(self) -> None:
        if (
            type(self.agent_run_id) is not UUID
            or type(self.attempt_id) is not UUID
            or type(self.run_input) is not AgentRunInput
            or type(self.thread_context) is not ThreadContext
        ):
            raise ValueError("Agent execution identity is invalid")
        if self.thread_context.thread_id != self.run_input.thread_id:
            raise ValueError("Agent execution Thread does not match its durable input")
        if self.thread_context.through_sequence != self.run_input.context_through_sequence:
            raise ValueError("Agent execution Thread cutoff does not match its durable input")


@dataclass(frozen=True)
class AgentRun:
    agent_run_id: UUID
    attempt_id: UUID
    agent_key: str
    thread_id: UUID
    context_through_sequence: int
    status: str


def _record_value(record: AgentRecord) -> dict[str, object]:
    return {
        "schema_key": record.schema_key,
        "schema_version": record.schema_version,
        "fields": [{"name": field.name, "value": field.value} for field in record.fields],
    }


def _input_value(input: AgentRunInput) -> dict[str, object]:
    return {
        "configuration": {
            "agent_key": input.configuration.agent_key,
            "agent_version": input.configuration.agent_version,
            "instruction_key": input.configuration.instruction_key,
        },
        "task": {
            "task_type": input.task.task_type,
            "task_version": input.task.task_version,
            "input": _record_value(input.task.input),
        },
        "thread_id": str(input.thread_id),
        "context_through_sequence": input.context_through_sequence,
        "domain_event_context": [
            {
                "event_id": str(event.event_id),
                "event_type": event.event_type,
                "schema_version": event.schema_version,
                "payload": _record_value(event.payload),
            }
            for event in input.domain_event_context
        ],
        "audience_context": {
            "kind": input.audience_context.kind,
            "identifier": input.audience_context.identifier,
        },
        "locale": input.locale,
    }


def _exact(value: object, fields: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise RuntimeError(f"Persisted Agent {path} does not match its closed contract")
    return cast(dict[str, Any], value)


def _array(value: object, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise RuntimeError(f"Persisted Agent {path} must be an array")
    return value


def _record_from_value(value: object, path: str) -> AgentRecord:
    record = _exact(value, {"schema_key", "schema_version", "fields"}, path)
    fields = tuple(
        AgentField(
            name=field["name"],
            value=field["value"],
        )
        for index, raw_field in enumerate(_array(record["fields"], f"{path}.fields"))
        for field in (_exact(raw_field, {"name", "value"}, f"{path}.fields[{index}]"),)
    )
    return AgentRecord(
        schema_key=record["schema_key"],
        schema_version=record["schema_version"],
        fields=fields,
    )


def _input_from_value(value: object) -> AgentRunInput:
    input = _exact(
        value,
        {
            "configuration",
            "task",
            "thread_id",
            "context_through_sequence",
            "domain_event_context",
            "audience_context",
            "locale",
        },
        "Run Input",
    )
    configuration = _exact(
        input["configuration"],
        {"agent_key", "agent_version", "instruction_key"},
        "configuration",
    )
    task = _exact(input["task"], {"task_type", "task_version", "input"}, "task")
    audience = _exact(input["audience_context"], {"kind", "identifier"}, "audience")
    events = tuple(
        AgentDomainEvent(
            event_id=UUID(event["event_id"]),
            event_type=event["event_type"],
            schema_version=event["schema_version"],
            payload=_record_from_value(event["payload"], f"domain_event_context[{index}].payload"),
        )
        for index, raw_event in enumerate(
            _array(input["domain_event_context"], "domain_event_context")
        )
        for event in (
            _exact(
                raw_event,
                {"event_id", "event_type", "schema_version", "payload"},
                f"domain_event_context[{index}]",
            ),
        )
    )
    return AgentRunInput(
        configuration=AgentConfiguration(
            agent_key=configuration["agent_key"],
            agent_version=configuration["agent_version"],
            instruction_key=configuration["instruction_key"],
        ),
        task=AgentTask(
            task_type=task["task_type"],
            task_version=task["task_version"],
            input=_record_from_value(task["input"], "task.input"),
        ),
        thread_id=UUID(input["thread_id"]),
        context_through_sequence=input["context_through_sequence"],
        domain_event_context=events,
        audience_context=AgentAudience(
            kind=audience["kind"],
            identifier=audience["identifier"],
        ),
        locale=input["locale"],
    )


class AgentRuns:
    def __init__(self, connection: Connection[tuple[Any, ...]]) -> None:
        self._connection = connection

    def start(self, *, attempt_id: UUID, input: AgentRunInput) -> AgentRun:
        agent_run_id = uuid4()
        insert_agent_run(
            self._connection,
            agent_run_id=agent_run_id,
            attempt_id=attempt_id,
            agent_key=input.configuration.agent_key,
            thread_id=input.thread_id,
            context_through_sequence=input.context_through_sequence,
            input_value=_input_value(input),
        )
        return AgentRun(
            agent_run_id=agent_run_id,
            attempt_id=attempt_id,
            agent_key=input.configuration.agent_key,
            thread_id=input.thread_id,
            context_through_sequence=input.context_through_sequence,
            status="running",
        )

    def execution_input_for_attempt(self, attempt_id: UUID) -> AgentExecutionInput:
        record = read_running_input(self._connection, attempt_id)
        if record is None:
            raise RuntimeError("Agent Attempt has no running durable Agent Run")
        agent_run_id, input_value = record
        run_input = _input_from_value(input_value)
        context = ThreadAccess(self._connection).context(
            run_input.thread_id,
            run_input.context_through_sequence,
        )
        return AgentExecutionInput(
            agent_run_id=agent_run_id,
            attempt_id=attempt_id,
            run_input=run_input,
            thread_context=context,
        )

    def complete_for_attempt(self, attempt_id: UUID, result: dict[str, Any]) -> AgentRun:
        run = self.find_by_attempt(attempt_id)
        if run is None:
            raise RuntimeError("Agent Attempt has no durable Agent Run")
        durable_result = complete_agent_run(
            self._connection,
            agent_run_id=run.agent_run_id,
            attempt_id=attempt_id,
            result=result,
        )
        if durable_result != result:
            raise RuntimeError("Agent Run completion conflicts with durable state")
        return replace(run, status="completed")

    def fail_for_attempt(self, attempt_id: UUID, failure: dict[str, Any]) -> None:
        finish_agent_run(
            self._connection,
            attempt_id=attempt_id,
            status="failed",
            result=failure,
        )

    def abandon_for_attempt(self, attempt_id: UUID) -> None:
        finish_agent_run(
            self._connection,
            attempt_id=attempt_id,
            status="abandoned",
            result={"class": "attempt_authority_expired"},
        )

    def find_by_attempt(self, attempt_id: UUID) -> AgentRun | None:
        record = find_agent_run(self._connection, attempt_id)
        if record is None:
            return None
        return AgentRun(
            agent_run_id=record.agent_run_id,
            attempt_id=attempt_id,
            agent_key=record.agent_key,
            thread_id=record.thread_id,
            context_through_sequence=record.context_through_sequence,
            status=record.status,
        )


__all__ = [
    "AgentAudience",
    "AgentConfiguration",
    "AgentDomainEvent",
    "AgentExecutionInput",
    "AgentField",
    "AgentRecord",
    "AgentRun",
    "AgentRunInput",
    "AgentRuns",
    "AgentScalar",
    "AgentTask",
]
