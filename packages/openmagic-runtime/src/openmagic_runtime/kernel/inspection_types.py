"""Public transaction-scoped projections returned by kernel inspection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal
from uuid import UUID

InstanceState = Literal["open", "closed"]
StepState = Literal["pending", "succeeded", "failed", "cancelled"]
WaitState = Literal["unsatisfied", "satisfied", "cancelled"]
AttemptState = Literal["leased", "completed", "abandoned", "cancelled"]
AgentRunState = Literal["running", "completed", "failed", "abandoned"]


@dataclass(frozen=True)
class RuntimeInstance:
    instance_id: UUID
    state: InstanceState


@dataclass(frozen=True)
class RuntimeWait:
    wait_id: UUID
    instance_id: UUID
    template_key: str
    state: WaitState
    input: dict[str, Any]


@dataclass(frozen=True)
class RuntimeStep:
    step_id: UUID
    instance_id: UUID
    template_key: str
    state: StepState
    input: dict[str, Any]
    output_recorded: bool


@dataclass(frozen=True)
class RuntimeAttempt:
    attempt_id: UUID
    instance_id: UUID
    step_id: UUID
    attempt_number: int
    worker_id: str
    template_key: str
    step_input: dict[str, Any]


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
