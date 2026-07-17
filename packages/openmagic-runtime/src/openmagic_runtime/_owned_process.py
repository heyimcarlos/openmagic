"""One-shot lifecycle owner for a child process tree and its resources."""

from __future__ import annotations

import signal
import subprocess as subprocess_module
from collections.abc import Callable, Iterable, Mapping, Sequence
from pathlib import Path
from threading import Lock
from typing import BinaryIO

from openmagic_runtime._multiprocessing_acquisition import MultiprocessingProcess
from openmagic_runtime._process_contracts import Closeable, ProcessCleanup
from openmagic_runtime._process_owner_state import OwnedProcessParts, reap_owned_process
from openmagic_runtime._process_tree import GroupMemberEnumerator, live_group_members


class OwnedProcess:
    """Own one session leader, its complete process group, and closeable resources."""

    def __init__(
        self,
        *,
        process_id: int,
        wait_for_exit: Callable[[float], bool],
        signal_process: Callable[[signal.Signals], object] | None = None,
        close_process: Callable[[], object] | None = None,
        resources: Iterable[Closeable] = (),
        group_member_enumerator: GroupMemberEnumerator = live_group_members,
    ) -> None:
        self._parts = OwnedProcessParts(
            process_id=process_id,
            wait_for_exit=wait_for_exit,
            signal_process=signal_process,
            close_process=close_process,
            resources=tuple(resources),
            group_member_enumerator=group_member_enumerator,
        )
        self._cleanup_lock = Lock()
        self._cleanup_result: ProcessCleanup | None = None

    @classmethod
    def _from_parts(cls, parts: OwnedProcessParts) -> OwnedProcess:
        return cls(
            process_id=parts.process_id,
            wait_for_exit=parts.wait_for_exit,
            signal_process=parts.signal_process,
            close_process=parts.close_process,
            resources=parts.resources,
            group_member_enumerator=parts.group_member_enumerator,
        )

    @property
    def process_id(self) -> int:
        return self._parts.process_id

    @classmethod
    def subprocess(
        cls,
        process: subprocess_module.Popen[bytes],
        *,
        resources: Iterable[Closeable] = (),
    ) -> OwnedProcess:
        from openmagic_runtime._subprocess_acquisition import adopt_subprocess_parts

        return cls._from_parts(adopt_subprocess_parts(process, resources=resources))

    @classmethod
    def launch_subprocess(
        cls,
        command: Sequence[str],
        *,
        working_directory: Path,
        environment: Mapping[str, str],
        stdin: int | BinaryIO | None = subprocess_module.DEVNULL,
        stdout: int | BinaryIO | None = None,
        stderr: int | BinaryIO | None = None,
        resources: Iterable[Closeable] = (),
        timeout_seconds: float,
    ) -> tuple[subprocess_module.Popen[bytes], OwnedProcess]:
        from openmagic_runtime._subprocess_acquisition import launch_subprocess_parts

        process, parts = launch_subprocess_parts(
            command,
            working_directory=working_directory,
            environment=environment,
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            resources=resources,
            timeout_seconds=timeout_seconds,
        )
        return process, cls._from_parts(parts)

    @classmethod
    def multiprocessing(
        cls,
        process: MultiprocessingProcess,
        *,
        resources: Iterable[Closeable] = (),
    ) -> OwnedProcess:
        from openmagic_runtime._multiprocessing_acquisition import adopt_multiprocessing_parts

        return cls._from_parts(adopt_multiprocessing_parts(process, resources=resources))

    @classmethod
    def cleanup_multiprocessing_start(
        cls,
        process: MultiprocessingProcess,
        *,
        resources: Iterable[Closeable] = (),
        timeout_seconds: float,
        group_member_enumerator: GroupMemberEnumerator = live_group_members,
    ) -> ProcessCleanup:
        from openmagic_runtime._multiprocessing_acquisition import (
            cleanup_multiprocessing_start_parts,
        )

        parts, direct_cleanup = cleanup_multiprocessing_start_parts(
            process,
            resources=resources,
            timeout_seconds=timeout_seconds,
            group_member_enumerator=group_member_enumerator,
        )
        if direct_cleanup is not None:
            return direct_cleanup
        if parts is None:
            raise AssertionError("multiprocessing cleanup omitted its owned state")
        return reap_owned_process(parts, timeout_seconds=timeout_seconds)

    def reap(self, *, timeout_seconds: float, forced_loss: bool = False) -> ProcessCleanup:
        if timeout_seconds <= 0:
            raise ValueError("Process cleanup timeout must be positive")
        with self._cleanup_lock:
            if self._cleanup_result is None:
                self._cleanup_result = reap_owned_process(
                    self._parts,
                    timeout_seconds=timeout_seconds,
                    forced_loss=forced_loss,
                )
            return self._cleanup_result


__all__ = ["OwnedProcess"]
