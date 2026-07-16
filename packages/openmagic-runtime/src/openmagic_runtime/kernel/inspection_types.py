"""Public transaction-scoped projections returned by kernel inspection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

InstanceState = Literal["open", "closed"]
StepState = Literal["pending", "succeeded", "failed", "cancelled"]
WaitState = Literal["unsatisfied", "satisfied", "cancelled"]
AttemptState = Literal["leased", "completed", "abandoned", "cancelled"]
AgentRunState = Literal["running", "completed", "failed", "abandoned"]


def instance_state(value: object) -> InstanceState:
    if value == "open":
        return "open"
    if value == "closed":
        return "closed"
    raise RuntimeError("Instance has an invalid state")


def step_state(value: object) -> StepState:
    if value == "pending":
        return "pending"
    if value == "succeeded":
        return "succeeded"
    if value == "failed":
        return "failed"
    if value == "cancelled":
        return "cancelled"
    raise RuntimeError("Step has an invalid state")


def wait_state(value: object) -> WaitState:
    if value == "unsatisfied":
        return "unsatisfied"
    if value == "satisfied":
        return "satisfied"
    if value == "cancelled":
        return "cancelled"
    raise RuntimeError("Wait has an invalid state")


def attempt_state(value: object) -> AttemptState:
    if value == "leased":
        return "leased"
    if value == "completed":
        return "completed"
    if value == "abandoned":
        return "abandoned"
    if value == "cancelled":
        return "cancelled"
    raise RuntimeError("Attempt has an invalid state")


def agent_run_state(value: object) -> AgentRunState:
    if value == "running":
        return "running"
    if value == "completed":
        return "completed"
    if value == "failed":
        return "failed"
    if value == "abandoned":
        return "abandoned"
    raise RuntimeError("Agent Run has an invalid state")


@dataclass(frozen=True)
class RuntimeInstance:
    instance_id: UUID
    state: InstanceState

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeInstance:
        return cls(
            instance_id=UUID(str(record["instance_id"])), state=instance_state(record["state"])
        )


@dataclass(frozen=True)
class RuntimeWait:
    wait_id: UUID
    instance_id: UUID
    template_key: str
    state: WaitState
    input: dict[str, Any]

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeWait:
        return cls(
            wait_id=UUID(str(record["wait_id"])),
            instance_id=UUID(str(record["instance_id"])),
            template_key=str(record["template_key"]),
            state=wait_state(record["state"]),
            input=dict(record["input"]),
        )


@dataclass(frozen=True)
class RuntimeStep:
    step_id: UUID
    instance_id: UUID
    template_key: str
    state: StepState
    input: dict[str, Any]
    output_recorded: bool

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeStep:
        return cls(
            step_id=UUID(str(record["step_id"])),
            instance_id=UUID(str(record["instance_id"])),
            template_key=str(record["template_key"]),
            state=step_state(record["state"]),
            input=dict(record["input"]),
            output_recorded=bool(record["output_recorded"]),
        )


@dataclass(frozen=True)
class RuntimeAttempt:
    attempt_id: UUID
    instance_id: UUID
    step_id: UUID
    attempt_number: int
    worker_id: str
    template_key: str
    step_input: dict[str, Any]

    @classmethod
    def decode(cls, record: Mapping[str, Any]) -> RuntimeAttempt:
        return cls(
            attempt_id=UUID(str(record["attempt_id"])),
            instance_id=UUID(str(record["instance_id"])),
            step_id=UUID(str(record["step_id"])),
            attempt_number=int(record["attempt_number"]),
            worker_id=str(record["worker_id"]),
            template_key=str(record["template_key"]),
            step_input=dict(record["step_input"]),
        )


@dataclass(frozen=True)
class ActivatedOccurrences:
    steps: dict[str, UUID]
    waits: dict[str, UUID]


__all__ = [
    "ActivatedOccurrences",
    "AgentRunState",
    "AttemptState",
    "InstanceState",
    "RuntimeAttempt",
    "RuntimeInstance",
    "RuntimeStep",
    "RuntimeWait",
    "StepState",
    "WaitState",
]
