from __future__ import annotations

import io
import signal
import time
from multiprocessing import get_context

import pytest
from openmagic_runtime.processes import OwnedProcess


def _ignore_terminate_without_session() -> None:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(30)


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
