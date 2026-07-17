"""Portable process-group observation and terminate-then-kill policy."""

from __future__ import annotations

import os
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from openmagic_runtime._process_contracts import ProcessCleanup

GroupMemberEnumerator = Callable[[int], tuple[int, ...] | None]


def live_group_members(process_group_id: int) -> tuple[int, ...] | None:
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


def is_session_leader(process_id: int) -> bool:
    try:
        return os.getpgid(process_id) == process_id
    except ProcessLookupError:
        return False


def process_group_exists(
    process_group_id: int,
    *,
    group_member_enumerator: GroupMemberEnumerator,
) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    members = group_member_enumerator(process_group_id)
    return True if members is None else bool(members)


@dataclass(frozen=True)
class ProcessTree:
    process_group_id: int
    wait_for_leader: Callable[[float], bool]
    signal_leader: Callable[[signal.Signals], object] | None
    group_member_enumerator: GroupMemberEnumerator = live_group_members

    def reap(self, *, timeout_seconds: float, forced_loss: bool) -> ProcessCleanup:
        errors: list[BaseException] = []

        def enumerate_members() -> tuple[int, ...] | None:
            try:
                return self.group_member_enumerator(self.process_group_id)
            except BaseException as error:
                errors.append(error)
                return None

        def group_exists() -> bool:
            try:
                return process_group_exists(
                    self.process_group_id,
                    group_member_enumerator=lambda _: enumerate_members(),
                )
            except BaseException as error:
                errors.append(error)
                return True

        def signal_group(sig: signal.Signals) -> None:
            try:
                os.killpg(self.process_group_id, sig)
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

        owned_group_observed = group_exists()
        initial_signal = signal.SIGKILL if forced_loss else signal.SIGTERM
        if owned_group_observed:
            signal_group(initial_signal)
        elif self.signal_leader is not None:
            try:
                self.signal_leader(initial_signal)
            except ProcessLookupError:
                pass
            except BaseException as error:
                errors.append(error)
        try:
            leader_exited = self.wait_for_leader(timeout_seconds)
        except BaseException as error:
            errors.append(error)
            leader_exited = False
        if owned_group_observed:
            disappeared = wait_for_group()
            if not disappeared and not forced_loss:
                signal_group(signal.SIGKILL)
                if not leader_exited:
                    try:
                        leader_exited = self.wait_for_leader(timeout_seconds)
                    except BaseException as error:
                        errors.append(error)
                disappeared = wait_for_group()
            if not disappeared:
                errors.append(
                    RuntimeError(f"owned process group {self.process_group_id} survived cleanup")
                )
        elif not leader_exited and not forced_loss:
            if self.signal_leader is not None:
                try:
                    self.signal_leader(signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except BaseException as error:
                    errors.append(error)
            try:
                leader_exited = self.wait_for_leader(timeout_seconds)
            except BaseException as error:
                errors.append(error)
                leader_exited = False
        if not leader_exited:
            errors.append(
                RuntimeError(f"process-group leader {self.process_group_id} survived cleanup")
            )
        members = enumerate_members()
        if members:
            errors.append(
                RuntimeError(
                    f"owned process group {self.process_group_id} retained live members {members!r}"
                )
            )
        group_disappeared = not group_exists()
        return ProcessCleanup(
            reaped=leader_exited and group_disappeared,
            errors=tuple(errors),
        )


__all__ = [
    "GroupMemberEnumerator",
    "ProcessTree",
    "is_session_leader",
    "live_group_members",
    "process_group_exists",
]
