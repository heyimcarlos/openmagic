from __future__ import annotations

import io
import os
import signal
import subprocess
import sys
import time
from multiprocessing import get_context
from pathlib import Path

import pytest
from openmagic_runtime.processes import OwnedProcess


def _ignore_terminate_without_session() -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(30)


def _exit_with_term_resistant_descendant(pid_file: Path) -> None:
    os.setsid()
    descendant = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)",
        ]
    )
    pid_file.write_text(str(descendant.pid), encoding="utf-8")


def _process_is_live(process_id: int) -> bool:
    try:
        value = Path(f"/proc/{process_id}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    _, _, suffix = value.rpartition(")")
    return suffix.split()[0] not in {"X", "Z"}


def test_partial_multiprocessing_acquisition_reaps_leader_and_closes_resources() -> None:
    context = get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    log = io.BytesIO()
    process = context.Process(target=_ignore_terminate_without_session)
    process.start()
    time.sleep(0.1)

    cleanup = OwnedProcess.cleanup_multiprocessing_start(
        process,
        resources=(parent, child, log),
        timeout_seconds=0.1,
    )

    assert cleanup.reaped
    assert cleanup.errors == ()
    assert parent.closed
    assert child.closed
    assert log.closed
    with pytest.raises(ValueError, match="process object is closed"):
        process.is_alive()


def test_unstarted_multiprocessing_acquisition_closes_every_resource() -> None:
    context = get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    log = io.BytesIO()
    process = context.Process(target=_ignore_terminate_without_session)

    cleanup = OwnedProcess.cleanup_multiprocessing_start(
        process,
        resources=(parent, child, log),
        timeout_seconds=0.1,
    )

    assert cleanup.reaped
    assert cleanup.errors == ()
    assert parent.closed
    assert child.closed
    assert log.closed
    with pytest.raises(ValueError, match="process object is closed"):
        process.is_alive()


def test_partial_acquisition_reaps_descendant_after_session_leader_exits(
    tmp_path: Path,
) -> None:
    context = get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    log = io.BytesIO()
    pid_file = tmp_path / "descendant.pid"
    process = context.Process(target=_exit_with_term_resistant_descendant, args=(pid_file,))
    process.start()
    deadline = time.monotonic() + 3
    while not pid_file.is_file() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert pid_file.is_file()
    descendant_id = int(pid_file.read_text(encoding="utf-8"))
    process.join(timeout=3)
    assert not process.is_alive()
    assert _process_is_live(descendant_id)

    cleanup = OwnedProcess.cleanup_multiprocessing_start(
        process,
        resources=(parent, child, log),
        timeout_seconds=0.1,
    )

    assert cleanup.reaped
    assert cleanup.errors == ()
    assert not _process_is_live(descendant_id)
    assert parent.closed
    assert child.closed
    assert log.closed
