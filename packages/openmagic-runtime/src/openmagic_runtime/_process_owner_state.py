"""Immutable process ownership state and the single cleanup policy."""

from __future__ import annotations

import signal
from collections.abc import Callable
from dataclasses import dataclass

from openmagic_runtime._process_contracts import Closeable, ProcessCleanup
from openmagic_runtime._process_tree import GroupMemberEnumerator, ProcessTree, live_group_members


@dataclass(frozen=True)
class OwnedProcessParts:
    process_id: int
    wait_for_exit: Callable[[float], bool]
    signal_process: Callable[[signal.Signals], object] | None = None
    close_process: Callable[[], object] | None = None
    resources: tuple[Closeable, ...] = ()
    group_member_enumerator: GroupMemberEnumerator = live_group_members

    def __post_init__(self) -> None:
        if self.process_id <= 0:
            raise ValueError("Owned process identity must be positive")


def reap_owned_process(
    parts: OwnedProcessParts,
    *,
    timeout_seconds: float,
    forced_loss: bool = False,
) -> ProcessCleanup:
    if timeout_seconds <= 0:
        raise ValueError("Process cleanup timeout must be positive")
    cleanup = ProcessCleanup(reaped=False, errors=())
    errors: list[BaseException] = []
    try:
        cleanup = ProcessTree(
            process_group_id=parts.process_id,
            wait_for_leader=parts.wait_for_exit,
            signal_leader=parts.signal_process,
            group_member_enumerator=parts.group_member_enumerator,
        ).reap(timeout_seconds=timeout_seconds, forced_loss=forced_loss)
        errors.extend(cleanup.errors)
    except BaseException as error:
        errors.append(error)
    finally:
        for resource in parts.resources:
            try:
                resource.close()
            except BaseException as error:
                errors.append(error)
        if parts.close_process is not None:
            try:
                parts.close_process()
            except BaseException as error:
                errors.append(error)
    return ProcessCleanup(reaped=cleanup.reaped, errors=tuple(errors))


__all__ = ["OwnedProcessParts", "reap_owned_process"]
