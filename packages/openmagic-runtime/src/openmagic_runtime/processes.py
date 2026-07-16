"""One bounded ownership policy for local child process trees and resources."""

from __future__ import annotations

import os
import signal
import subprocess as subprocess_module
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol


class Closeable(Protocol):
    def close(self) -> object: ...


class _MultiprocessingProcess(Protocol):
    @property
    def pid(self) -> int | None: ...

    def join(self, timeout: float | None = None) -> None: ...

    def is_alive(self) -> bool: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class ProcessCleanup:
    reaped: bool
    errors: tuple[BaseException, ...]

    def raise_errors(self, message: str) -> None:
        if self.errors:
            raise BaseExceptionGroup(message, list(self.errors))


def finish_owned_cleanup(
    cleanup: Callable[[], object],
    *,
    execution_error: BaseException | None,
    message: str,
) -> None:
    """Complete cleanup without replacing a previously observed failure."""

    try:
        cleanup()
    except BaseException as cleanup_error:
        if execution_error is None:
            raise
        raise BaseExceptionGroup(message, [execution_error, cleanup_error]) from execution_error


@contextmanager
def owned_cleanup_scope(
    cleanup: Callable[[], object],
    *,
    message: str,
) -> Iterator[None]:
    """Preserve an active execution failure while completing owned cleanup."""

    execution_error: BaseException | None = None
    try:
        yield
    except BaseException as error:
        execution_error = error
        raise
    finally:
        finish_owned_cleanup(
            cleanup,
            execution_error=execution_error,
            message=message,
        )


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


def _is_session_leader(process_id: int) -> bool:
    try:
        return os.getpgid(process_id) == process_id
    except ProcessLookupError:
        return False


def _process_group_exists(
    process_group_id: int,
    *,
    group_member_enumerator: Callable[[int], tuple[int, ...] | None],
) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    members = group_member_enumerator(process_group_id)
    return True if members is None else bool(members)


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
        group_member_enumerator: Callable[[int], tuple[int, ...] | None] = _live_group_members,
    ) -> None:
        if process_id <= 0:
            raise ValueError("Owned process identity must be positive")
        self.process_id = process_id
        self._wait_for_exit = wait_for_exit
        self._signal_process = signal_process
        self._close_process = close_process
        self._resources = tuple(resources)
        self._group_member_enumerator = group_member_enumerator

    @classmethod
    def _subprocess_owner(
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

        process_resources: list[Closeable] = list(resources)
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and all(stream is not item for item in process_resources):
                process_resources.append(stream)
        return cls(
            process_id=process.pid,
            wait_for_exit=wait_for_exit,
            signal_process=process.send_signal,
            resources=process_resources,
        )

    @classmethod
    def subprocess(
        cls,
        process: subprocess_module.Popen[bytes],
        *,
        resources: Iterable[Closeable] = (),
    ) -> OwnedProcess:
        """Adopt a caller-verified live session leader."""

        if not _is_session_leader(process.pid):
            raise ValueError("Owned subprocess must be an observed session leader")
        return cls._subprocess_owner(process, resources=resources)

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
        """Create and own one new session without exposing an unowned launch seam."""

        if timeout_seconds <= 0:
            raise ValueError("Process acquisition timeout must be positive")
        owned_resources = tuple(resources)
        process: subprocess_module.Popen[bytes] | None = None
        owner: OwnedProcess | None = None
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
            owner = cls._subprocess_owner(process, resources=owned_resources)
            if process.poll() is None and not _is_session_leader(process.pid):
                raise ValueError("Owned subprocess must be an observed session leader")
        except BaseException as acquisition_error:
            cleanup_errors: list[BaseException] = []
            if process is not None:
                try:
                    partial_owner = owner or cls._subprocess_owner(
                        process,
                        resources=owned_resources,
                    )
                    cleanup_errors.extend(
                        partial_owner.reap(timeout_seconds=timeout_seconds).errors
                    )
                except BaseException as error:
                    cleanup_errors.append(error)
            else:
                for resource in owned_resources:
                    try:
                        resource.close()
                    except BaseException as error:
                        cleanup_errors.append(error)
            if cleanup_errors:
                raise BaseExceptionGroup(
                    "subprocess acquisition and cleanup failed",
                    [acquisition_error, *cleanup_errors],
                ) from acquisition_error
            raise
        if process is None or owner is None:
            raise AssertionError("subprocess acquisition omitted its owned process")
        return process, owner

    @classmethod
    def _multiprocessing_owner(
        cls,
        process: _MultiprocessingProcess,
        *,
        resources: Iterable[Closeable] = (),
        group_member_enumerator: Callable[[int], tuple[int, ...] | None] = _live_group_members,
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
            signal_process=lambda sig: (
                process.kill() if sig == signal.SIGKILL else process.terminate()
            ),
            close_process=process.close,
            resources=resources,
            group_member_enumerator=group_member_enumerator,
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
        if not _is_session_leader(process_id):
            raise ValueError("Owned multiprocessing child must be an observed session leader")
        return cls._multiprocessing_owner(process, resources=resources)

    @classmethod
    def cleanup_multiprocessing_start(
        cls,
        process: _MultiprocessingProcess,
        *,
        resources: Iterable[Closeable] = (),
        timeout_seconds: float,
        group_member_enumerator: Callable[[int], tuple[int, ...] | None] = _live_group_members,
    ) -> ProcessCleanup:
        """Clean a partially acquired child without assuming session ownership."""
        if timeout_seconds <= 0:
            raise ValueError("Process cleanup timeout must be positive")
        owned_resources = tuple(resources)
        process_id = getattr(process, "pid", None)
        if isinstance(process_id, int) and (
            _is_session_leader(process_id)
            or _process_group_exists(
                process_id,
                group_member_enumerator=group_member_enumerator,
            )
        ):
            return cls._multiprocessing_owner(
                process,
                resources=owned_resources,
                group_member_enumerator=group_member_enumerator,
            ).reap(timeout_seconds=timeout_seconds)

        errors: list[BaseException] = []
        leader_exited = not isinstance(process_id, int)
        if isinstance(process_id, int):
            try:
                alive = bool(process.is_alive())
            except BaseException as error:
                errors.append(error)
                alive = True
            if alive:
                try:
                    process.terminate()
                except BaseException as error:
                    errors.append(error)
                try:
                    process.join(timeout=timeout_seconds)
                except BaseException as error:
                    errors.append(error)
                try:
                    alive = bool(process.is_alive())
                except BaseException as error:
                    errors.append(error)
                    alive = True
            if alive:
                try:
                    process.kill()
                except BaseException as error:
                    errors.append(error)
                try:
                    process.join(timeout=timeout_seconds)
                except BaseException as error:
                    errors.append(error)
                try:
                    alive = bool(process.is_alive())
                except BaseException as error:
                    errors.append(error)
                    alive = True
            leader_exited = not alive
            if not leader_exited:
                errors.append(RuntimeError(f"partial child {process_id} survived cleanup"))
        for resource in owned_resources:
            try:
                resource.close()
            except BaseException as error:
                errors.append(error)
        try:
            process.close()
        except BaseException as error:
            errors.append(error)
        return ProcessCleanup(reaped=leader_exited, errors=tuple(errors))

    def reap(self, *, timeout_seconds: float, forced_loss: bool = False) -> ProcessCleanup:
        if timeout_seconds <= 0:
            raise ValueError("Process cleanup timeout must be positive")
        errors: list[BaseException] = []

        def enumerate_group_members(process_group_id: int) -> tuple[int, ...] | None:
            try:
                return self._group_member_enumerator(process_group_id)
            except BaseException as error:
                errors.append(error)
                return None

        def group_exists() -> bool:
            try:
                return _process_group_exists(
                    self.process_id,
                    group_member_enumerator=enumerate_group_members,
                )
            except BaseException as error:
                errors.append(error)
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
            owned_group_observed = group_exists()
            if owned_group_observed:
                initial_signal = signal.SIGKILL if forced_loss else signal.SIGTERM
                signal_group(initial_signal)
            elif self._signal_process is not None:
                try:
                    self._signal_process(signal.SIGKILL if forced_loss else signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except BaseException as error:
                    errors.append(error)
            try:
                leader_exited = self._wait_for_exit(timeout_seconds)
            except BaseException as error:
                errors.append(error)
                leader_exited = False
            if owned_group_observed:
                disappeared = wait_for_group()
                if not disappeared and not forced_loss:
                    signal_group(signal.SIGKILL)
                    if not leader_exited:
                        try:
                            leader_exited = self._wait_for_exit(timeout_seconds)
                        except BaseException as error:
                            errors.append(error)
                    disappeared = wait_for_group()
                if not disappeared:
                    errors.append(
                        RuntimeError(f"owned process group {self.process_id} survived cleanup")
                    )
            elif not leader_exited and not forced_loss:
                if self._signal_process is not None:
                    try:
                        self._signal_process(signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except BaseException as error:
                        errors.append(error)
                try:
                    leader_exited = self._wait_for_exit(timeout_seconds)
                except BaseException as error:
                    errors.append(error)
                    leader_exited = False
            if not leader_exited:
                errors.append(
                    RuntimeError(f"process-group leader {self.process_id} survived cleanup")
                )
            members = enumerate_group_members(self.process_id)
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
        group_disappeared = not group_exists()
        return ProcessCleanup(
            reaped=leader_exited and group_disappeared,
            errors=tuple(errors),
        )


__all__ = [
    "Closeable",
    "OwnedProcess",
    "ProcessCleanup",
    "finish_owned_cleanup",
    "owned_cleanup_scope",
]
