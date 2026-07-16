"""One bounded ownership policy for local child process trees and resources."""

from __future__ import annotations

import os
import signal
import subprocess as subprocess_module
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class Closeable(Protocol):
    def close(self) -> object: ...


class _MultiprocessingProcess(Protocol):
    pid: int | None

    def join(self, timeout: float | None = None) -> None: ...

    def is_alive(self) -> bool: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ProcessCleanup:
    reaped: bool
    errors: tuple[BaseException, ...]

    def raise_errors(self, message: str) -> None:
        if self.errors:
            raise BaseExceptionGroup(message, list(self.errors))


def _live_group_members(process_group_id: int) -> tuple[int, ...] | None:
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    members: list[int] = []
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            _, separator, suffix = (entry / "stat").read_text(encoding="utf-8").rpartition(")")
            fields = suffix.split()
            state = fields[0]
            member_group = int(fields[2])
        except (FileNotFoundError, PermissionError, ProcessLookupError, ValueError):
            continue
        if separator and member_group == process_group_id and state not in {"X", "Z"}:
            members.append(int(entry.name))
    return tuple(sorted(members))


class OwnedProcess:
    """Own one session leader, its complete process group, and closeable resources."""

    def __init__(
        self,
        *,
        process_id: int,
        wait_for_exit: Callable[[float], bool],
        close_process: Callable[[], object] | None = None,
        resources: Iterable[Closeable] = (),
    ) -> None:
        if process_id <= 0:
            raise ValueError("Owned process identity must be positive")
        self.process_id = process_id
        self._wait_for_exit = wait_for_exit
        self._close_process = close_process
        self._resources = tuple(resources)

    @classmethod
    def subprocess(
        cls,
        process: subprocess_module.Popen[bytes],
        *,
        resources: Iterable[Closeable] = (),
    ) -> OwnedProcess:
        def wait_for_exit(timeout: float) -> bool:
            try:
                process.wait(timeout=timeout)
            except subprocess_module.TimeoutExpired:
                return False
            return process.poll() is not None

        return cls(
            process_id=process.pid,
            wait_for_exit=wait_for_exit,
            resources=resources,
        )

    @classmethod
    def multiprocessing(
        cls,
        process: _MultiprocessingProcess,
        *,
        resources: Iterable[Closeable] = (),
    ) -> OwnedProcess:
        process_id = getattr(process, "pid", None)
        if not isinstance(process_id, int):
            raise ValueError("Started multiprocessing child must expose its process identity")

        def wait_for_exit(timeout: float) -> bool:
            process.join(timeout=timeout)
            return not bool(process.is_alive())

        return cls(
            process_id=process_id,
            wait_for_exit=wait_for_exit,
            close_process=process.close,
            resources=resources,
        )

    def reap(self, *, timeout_seconds: float, forced_loss: bool = False) -> ProcessCleanup:
        if timeout_seconds <= 0:
            raise ValueError("Process cleanup timeout must be positive")
        errors: list[BaseException] = []

        def group_exists() -> bool:
            members = _live_group_members(self.process_id)
            if members is not None:
                return bool(members)
            try:
                os.killpg(self.process_id, 0)
            except ProcessLookupError:
                return False
            except BaseException as error:
                errors.append(error)
                return True
            return True

        def signal_group(sig: signal.Signals) -> None:
            try:
                os.killpg(self.process_id, sig)
            except ProcessLookupError:
                return
            except BaseException as error:
                errors.append(error)

        def wait_for_group() -> bool:
            deadline = time.monotonic() + timeout_seconds
            while time.monotonic() < deadline:
                if not group_exists():
                    return True
                time.sleep(0.01)
            return not group_exists()

        try:
            if group_exists():
                signal_group(signal.SIGKILL if forced_loss else signal.SIGTERM)
                disappeared = wait_for_group()
                if not disappeared and not forced_loss:
                    signal_group(signal.SIGKILL)
                    disappeared = wait_for_group()
                if not disappeared:
                    errors.append(
                        RuntimeError(f"owned process group {self.process_id} survived cleanup")
                    )
            try:
                leader_exited = self._wait_for_exit(timeout_seconds)
            except BaseException as error:
                errors.append(error)
                leader_exited = False
            if not leader_exited:
                errors.append(
                    RuntimeError(f"process-group leader {self.process_id} survived cleanup")
                )
            members = _live_group_members(self.process_id)
            if members:
                errors.append(
                    RuntimeError(
                        f"owned process group {self.process_id} retained live members {members!r}"
                    )
                )
        finally:
            for resource in self._resources:
                try:
                    resource.close()
                except BaseException as error:
                    errors.append(error)
            if self._close_process is not None:
                try:
                    self._close_process()
                except BaseException as error:
                    errors.append(error)
        return ProcessCleanup(reaped=not group_exists(), errors=tuple(errors))


__all__ = ["Closeable", "OwnedProcess", "ProcessCleanup"]
