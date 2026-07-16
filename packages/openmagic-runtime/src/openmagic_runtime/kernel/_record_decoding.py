"""Private decoding of PostgreSQL records into kernel projections."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from openmagic_runtime.kernel.inspection_types import (
    AgentRunState,
    AttemptState,
    InstanceState,
    RuntimeAttempt,
    RuntimeInstance,
    RuntimeStep,
    RuntimeWait,
    StepState,
    WaitState,
)


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


def decode_runtime_instance(record: Mapping[str, Any]) -> RuntimeInstance:
    return RuntimeInstance(
        instance_id=UUID(str(record["instance_id"])),
        state=instance_state(record["state"]),
    )


def decode_runtime_wait(record: Mapping[str, Any]) -> RuntimeWait:
    return RuntimeWait(
        wait_id=UUID(str(record["wait_id"])),
        instance_id=UUID(str(record["instance_id"])),
        template_key=str(record["template_key"]),
        state=wait_state(record["state"]),
        input=dict(record["input"]),
    )


def decode_runtime_step(record: Mapping[str, Any]) -> RuntimeStep:
    return RuntimeStep(
        step_id=UUID(str(record["step_id"])),
        instance_id=UUID(str(record["instance_id"])),
        template_key=str(record["template_key"]),
        state=step_state(record["state"]),
        input=dict(record["input"]),
        output_recorded=bool(record["output_recorded"]),
    )


def decode_runtime_attempt(record: Mapping[str, Any]) -> RuntimeAttempt:
    return RuntimeAttempt(
        attempt_id=UUID(str(record["attempt_id"])),
        instance_id=UUID(str(record["instance_id"])),
        step_id=UUID(str(record["step_id"])),
        attempt_number=int(record["attempt_number"]),
        worker_id=str(record["worker_id"]),
        template_key=str(record["template_key"]),
        step_input=dict(record["step_input"]),
    )


__all__: list[str] = []
