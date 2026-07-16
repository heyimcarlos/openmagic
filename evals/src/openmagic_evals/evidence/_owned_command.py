"""Private bounded command capture with complete subprocess-tree ownership."""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from openmagic_runtime.processes import OwnedProcess, finish_owned_cleanup


@dataclass(frozen=True)
class OwnedCommandResult:
    returncode: int
    stdout: str
    stderr: str


def capture_owned_command(
    command: Sequence[str],
    *,
    working_directory: Path,
    environment: Mapping[str, str],
    timeout_seconds: float,
) -> OwnedCommandResult:
    """Capture one command and reap its whole session under every exit path."""

    cleanup_timeout_seconds = min(timeout_seconds, 1.0)
    process, owner = OwnedProcess.launch_subprocess(
        command,
        working_directory=working_directory,
        environment=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout_seconds=cleanup_timeout_seconds,
    )

    def reap() -> None:
        owner.reap(timeout_seconds=cleanup_timeout_seconds).raise_errors(
            "owned command cleanup failed"
        )

    try:
        stdout, stderr = process.communicate(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        timeout_error = TimeoutError(
            f"owned command exceeded its {timeout_seconds:g} second timeout"
        )
        finish_owned_cleanup(
            reap,
            execution_error=timeout_error,
            message="owned command execution and cleanup failed",
        )
        raise timeout_error from error
    except BaseException as execution_error:
        finish_owned_cleanup(
            reap,
            execution_error=execution_error,
            message="owned command execution and cleanup failed",
        )
        raise
    else:
        finish_owned_cleanup(
            reap,
            execution_error=None,
            message="owned command cleanup failed",
        )
    returncode = process.returncode
    if returncode is None:
        raise AssertionError("communicated command omitted its exit status")
    return OwnedCommandResult(
        returncode=returncode,
        stdout=stdout.decode("utf-8"),
        stderr=stderr.decode("utf-8"),
    )


__all__ = ["OwnedCommandResult", "capture_owned_command"]
