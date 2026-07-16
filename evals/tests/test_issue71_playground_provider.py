from __future__ import annotations

import io
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest
from openmagic_playground.synthetic_provider import SyntheticEmailProvider


class _TerminateFailureThenKill:
    pid = 710072

    def __init__(self) -> None:
        self.alive = True
        self.killed = False

    def poll(self) -> int | None:
        return None if self.alive else -9

    @staticmethod
    def terminate() -> None:
        raise OSError("synthetic terminate failure")

    def kill(self) -> None:
        self.killed = True
        self.alive = False

    def wait(self, timeout: float) -> int:
        if self.alive:
            raise subprocess.TimeoutExpired("synthetic-provider", timeout)
        return -9


class _PollFailureThenKill:
    pid = 710073

    def __init__(self) -> None:
        self.alive = True
        self.killed = False
        self.poll_calls = 0

    def poll(self) -> int | None:
        self.poll_calls += 1
        if self.poll_calls == 1:
            raise OSError("synthetic poll failure")
        return None if self.alive else -9

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        self.killed = True
        self.alive = False

    def wait(self, timeout: float) -> int:
        if self.alive:
            raise subprocess.TimeoutExpired("synthetic-provider", timeout)
        return -9


def test_synthetic_provider_kills_after_terminate_error_and_closes_log(
    tmp_path: Path,
) -> None:
    provider = SyntheticEmailProvider(working_directory=tmp_path, shutdown_timeout=0.01)
    process = _TerminateFailureThenKill()
    log = io.BytesIO()
    provider._process = cast(subprocess.Popen[bytes], process)
    provider._log = cast(io.BufferedWriter, log)

    with pytest.raises(ExceptionGroup, match="synthetic provider cleanup failed"):
        provider.stop()

    assert process.killed
    assert provider._process is None
    assert provider._log is None
    assert log.closed


def test_synthetic_provider_poll_failure_cannot_skip_reaping_or_log_close(
    tmp_path: Path,
) -> None:
    provider = SyntheticEmailProvider(working_directory=tmp_path, shutdown_timeout=0.01)
    process = _PollFailureThenKill()
    log = io.BytesIO()
    provider._process = cast(subprocess.Popen[bytes], process)
    provider._log = cast(io.BufferedWriter, log)

    with pytest.raises(ExceptionGroup, match="synthetic provider cleanup failed"):
        provider.stop()

    assert process.killed
    assert provider._process is None
    assert provider._log is None
    assert log.closed


def test_renewal_demo_requires_explicit_approved_local_execution() -> None:
    executable = Path(sys.executable).parent / "openmagic-playground"

    completed = subprocess.run(
        [str(executable), "demo-renewal"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "--execute-approved-local-effect" in completed.stderr
