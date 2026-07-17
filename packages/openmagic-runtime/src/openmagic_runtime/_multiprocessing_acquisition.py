"""Multiprocessing acquisition and partial-start cleanup."""

from __future__ import annotations

import signal
from collections.abc import Iterable
from typing import Protocol

from openmagic_runtime._process_contracts import Closeable, ProcessCleanup
from openmagic_runtime._process_owner_state import OwnedProcessParts
from openmagic_runtime._process_tree import (
    GroupMemberEnumerator,
    is_session_leader,
    live_group_members,
    process_group_exists,
)


class MultiprocessingProcess(Protocol):
    @property
    def pid(self) -> int | None: ...

    def join(self, timeout: float | None = None) -> None: ...

    def is_alive(self) -> bool: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def close(self) -> None: ...


def multiprocessing_parts(
    process: MultiprocessingProcess,
    *,
    resources: Iterable[Closeable] = (),
    group_member_enumerator: GroupMemberEnumerator = live_group_members,
    require_session_leader: bool = False,
) -> OwnedProcessParts:

    process_id = process.pid
    if not isinstance(process_id, int):
        raise ValueError("Started multiprocessing child must expose its process identity")
    if require_session_leader and not is_session_leader(process_id):
        raise ValueError("Owned multiprocessing child must be an observed session leader")

    def wait_for_exit(timeout: float) -> bool:
        process.join(timeout=timeout)
        return not bool(process.is_alive())

    return OwnedProcessParts(
        process_id=process_id,
        wait_for_exit=wait_for_exit,
        signal_process=lambda sig: process.kill() if sig == signal.SIGKILL else process.terminate(),
        close_process=process.close,
        resources=tuple(resources),
        group_member_enumerator=group_member_enumerator,
    )


def adopt_multiprocessing_parts(
    process: MultiprocessingProcess,
    *,
    resources: Iterable[Closeable] = (),
) -> OwnedProcessParts:
    return multiprocessing_parts(
        process,
        resources=resources,
        require_session_leader=True,
    )


def cleanup_multiprocessing_start_parts(
    process: MultiprocessingProcess,
    *,
    resources: Iterable[Closeable] = (),
    timeout_seconds: float,
    group_member_enumerator: GroupMemberEnumerator = live_group_members,
) -> tuple[OwnedProcessParts | None, ProcessCleanup | None]:
    if timeout_seconds <= 0:
        raise ValueError("Process cleanup timeout must be positive")
    owned_resources = tuple(resources)
    process_id = process.pid
    if isinstance(process_id, int) and (
        is_session_leader(process_id)
        or process_group_exists(
            process_id,
            group_member_enumerator=group_member_enumerator,
        )
    ):
        return (
            multiprocessing_parts(
                process,
                resources=owned_resources,
                group_member_enumerator=group_member_enumerator,
            ),
            None,
        )
    return (
        None,
        _cleanup_unowned_multiprocessing(
            process,
            process_id=process_id,
            resources=owned_resources,
            timeout_seconds=timeout_seconds,
        ),
    )


def _cleanup_unowned_multiprocessing(
    process: MultiprocessingProcess,
    *,
    process_id: int | None,
    resources: tuple[Closeable, ...],
    timeout_seconds: float,
) -> ProcessCleanup:
    errors: list[BaseException] = []
    alive = isinstance(process_id, int)
    if alive:
        try:
            alive = bool(process.is_alive())
        except BaseException as error:
            errors.append(error)
    for action in (process.terminate, process.kill):
        if not alive:
            break
        try:
            action()
            process.join(timeout=timeout_seconds)
            alive = bool(process.is_alive())
        except BaseException as error:
            errors.append(error)
    if alive:
        errors.append(RuntimeError(f"partial child {process_id} survived cleanup"))
    for resource in resources:
        try:
            resource.close()
        except BaseException as error:
            errors.append(error)
    try:
        process.close()
    except BaseException as error:
        errors.append(error)
    return ProcessCleanup(reaped=not alive, errors=tuple(errors))


__all__ = [
    "MultiprocessingProcess",
    "adopt_multiprocessing_parts",
    "cleanup_multiprocessing_start_parts",
]
