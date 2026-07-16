"""Immutable command configuration for locally owned process groups."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from openmagic_runtime.processes import OwnedProcess


@dataclass(frozen=True)
class OwnedSubprocess:
    process: subprocess.Popen[bytes]
    owner: OwnedProcess


@dataclass(frozen=True)
class ProcessCommand:
    """A validated immutable command that cannot execute during configuration."""

    arguments: tuple[str, ...]

    def __post_init__(self) -> None:
        if type(self.arguments) is not tuple or not self.arguments:
            raise ValueError("Process command arguments must be a non-empty tuple")
        if any(type(argument) is not str or not argument for argument in self.arguments):
            raise ValueError("Process command arguments must be non-empty strings")


def launch_owned_process(
    command: Sequence[str],
    *,
    command_override: ProcessCommand | None = None,
    working_directory: Path,
    environment: Mapping[str, str],
    output: BinaryIO,
    cleanup_timeout_seconds: float,
) -> OwnedSubprocess:
    """Launch and acquire one complete subprocess group without an ownership gap."""

    if command_override is not None and type(command_override) is not ProcessCommand:
        raise TypeError("Process command override must be immutable ProcessCommand data")
    resolved_command = (
        command_override.arguments if command_override is not None else tuple(command)
    )
    process, owner = OwnedProcess.launch_subprocess(
        resolved_command,
        working_directory=working_directory,
        environment=environment,
        stdout=output,
        stderr=subprocess.STDOUT,
        resources=(output,),
        timeout_seconds=cleanup_timeout_seconds,
    )
    return OwnedSubprocess(process=process, owner=owner)


__all__ = [
    "OwnedSubprocess",
    "ProcessCommand",
    "launch_owned_process",
]
