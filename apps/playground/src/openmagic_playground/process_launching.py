"""Explicit launcher seam for locally owned process groups."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol

from openmagic_runtime.processes import OwnedProcess


@dataclass(frozen=True)
class OwnedSubprocess:
    process: subprocess.Popen[bytes]
    owner: OwnedProcess


class ProcessLauncher(Protocol):
    """Launch and return an established session leader for caller-owned cleanup."""

    def launch(
        self,
        command: Sequence[str],
        *,
        working_directory: Path,
        environment: Mapping[str, str],
        output: BinaryIO,
    ) -> subprocess.Popen[bytes]: ...


class SubprocessLauncher:
    """Launch one new session whose complete process group is caller-owned."""

    def launch(
        self,
        command: Sequence[str],
        *,
        working_directory: Path,
        environment: Mapping[str, str],
        output: BinaryIO,
    ) -> subprocess.Popen[bytes]:
        return subprocess.Popen(
            tuple(command),
            cwd=working_directory,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )


def launch_owned_process(
    launcher: ProcessLauncher,
    command: Sequence[str],
    *,
    working_directory: Path,
    environment: Mapping[str, str],
    output: BinaryIO,
    cleanup_timeout_seconds: float,
) -> OwnedSubprocess:
    """Launch and acquire one complete subprocess group without an ownership gap."""

    process, owner = OwnedProcess.acquire_subprocess(
        lambda: launcher.launch(
            command,
            working_directory=working_directory,
            environment=environment,
            output=output,
        ),
        resources=(output,),
        timeout_seconds=cleanup_timeout_seconds,
    )
    return OwnedSubprocess(process=process, owner=owner)


__all__ = [
    "OwnedSubprocess",
    "ProcessLauncher",
    "SubprocessLauncher",
    "launch_owned_process",
]
