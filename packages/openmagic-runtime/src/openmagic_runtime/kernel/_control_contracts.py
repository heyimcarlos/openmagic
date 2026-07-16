"""Dependency-neutral contracts for kernel control transitions."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True)
class StartInstance:
    command_id: UUID
    definition_key: str
    definition_version: int
    instance_input: dict[str, Any]
    route_input: dict[str, Any]


@dataclass(frozen=True)
class StartInstanceReceipt:
    instance_id: UUID
    definition_key: str
    definition_version: int
    steps: dict[str, UUID]
    waits: dict[str, UUID]
    trace_event_id: UUID
    trace_sequence: int


__all__ = ["StartInstance", "StartInstanceReceipt"]
