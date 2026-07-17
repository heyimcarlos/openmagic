"""Atomic subprocess acquisition for the shared owned-process lifecycle."""

from __future__ import annotations

import subprocess as subprocess_module
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import BinaryIO

from openmagic_runtime._process_contracts import Closeable
from openmagic_runtime._process_owner_state import OwnedProcessParts, reap_owned_process
from openmagic_runtime._process_tree import is_session_leader


def subprocess_parts(
    process: subprocess_module.Popen[bytes],
    *,
    resources: Iterable[Closeable] = (),
) -> OwnedProcessParts:

    def wait_for_exit(timeout: float) -> bool:
        try:
            process.wait(timeout=timeout)
        except subprocess_module.TimeoutExpired:
            return False
        return process.poll() is not None

    process_resources: list[Closeable] = list(resources)
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None and all(stream is not item for item in process_resources):
            process_resources.append(stream)
    return OwnedProcessParts(
        process_id=process.pid,
        wait_for_exit=wait_for_exit,
        signal_process=process.send_signal,
        resources=tuple(process_resources),
    )


def adopt_subprocess_parts(
    process: subprocess_module.Popen[bytes],
    *,
    resources: Iterable[Closeable] = (),
) -> OwnedProcessParts:
    if not is_session_leader(process.pid):
        raise ValueError("Owned subprocess must be an observed session leader")
    return subprocess_parts(process, resources=resources)


def launch_subprocess_parts(
    command: Sequence[str],
    *,
    working_directory: Path,
    environment: Mapping[str, str],
    stdin: int | BinaryIO | None = subprocess_module.DEVNULL,
    stdout: int | BinaryIO | None = None,
    stderr: int | BinaryIO | None = None,
    resources: Iterable[Closeable] = (),
    timeout_seconds: float,
) -> tuple[subprocess_module.Popen[bytes], OwnedProcessParts]:
    if timeout_seconds <= 0:
        raise ValueError("Process acquisition timeout must be positive")
    owned_resources = tuple(resources)
    process: subprocess_module.Popen[bytes] | None = None
    parts: OwnedProcessParts | None = None
    try:
        process = subprocess_module.Popen(
            tuple(command),
            cwd=working_directory,
            env=dict(environment),
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        parts = subprocess_parts(process, resources=owned_resources)
        if process.poll() is None and not is_session_leader(process.pid):
            raise ValueError("Owned subprocess must be an observed session leader")
    except BaseException as acquisition_error:
        cleanup_errors = _cleanup_partial_subprocess(
            process,
            parts,
            resources=owned_resources,
            timeout_seconds=timeout_seconds,
        )
        if cleanup_errors:
            raise BaseExceptionGroup(
                "subprocess acquisition and cleanup failed",
                [acquisition_error, *cleanup_errors],
            ) from acquisition_error
        raise
    if process is None or parts is None:
        raise AssertionError("subprocess acquisition omitted its owned process")
    return process, parts


def _cleanup_partial_subprocess(
    process: subprocess_module.Popen[bytes] | None,
    parts: OwnedProcessParts | None,
    *,
    resources: tuple[Closeable, ...],
    timeout_seconds: float,
) -> list[BaseException]:
    errors: list[BaseException] = []
    if process is not None:
        try:
            partial_parts = parts or subprocess_parts(process, resources=resources)
            errors.extend(reap_owned_process(partial_parts, timeout_seconds=timeout_seconds).errors)
        except BaseException as error:
            errors.append(error)
        return errors
    for resource in resources:
        try:
            resource.close()
        except BaseException as error:
            errors.append(error)
    return errors


__all__ = ["adopt_subprocess_parts", "launch_subprocess_parts"]
