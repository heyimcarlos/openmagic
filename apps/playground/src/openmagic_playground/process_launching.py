"""Explicit launcher seam for locally owned process groups."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import BinaryIO, Protocol


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


def finish_owned_context(
    cleanup: Callable[[], object],
    *,
    execution_error: BaseException | None,
    message: str,
) -> None:
    """Run complete owner cleanup without replacing an active body failure."""

    try:
        cleanup()
    except BaseException as cleanup_error:
        if execution_error is None:
            raise
        raise BaseExceptionGroup(message, [execution_error, cleanup_error]) from execution_error


__all__ = ["ProcessLauncher", "SubprocessLauncher", "finish_owned_context"]
