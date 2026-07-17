"""Fresh child acquisition and typed race protocol transport."""

from __future__ import annotations

import os
import time
from contextlib import suppress
from multiprocessing.connection import Connection
from multiprocessing.context import SpawnContext
from typing import TypeVar

from openmagic_runtime.processes import OwnedProcess

from openmagic_evals.evidence._race_operations import (
    RaceProtocolError,
    RaceRequest,
    validate_race_request,
)
from openmagic_evals.evidence._race_persistence import RaceContenderBarrier
from openmagic_evals.evidence._race_transport import (
    ProcessRaceCompleted,
    ProcessRaceFailed,
    ProcessRaceFailure,
    ProcessRaceFatal,
    ProcessRaceReady,
    ProcessRaceResult,
    ProcessRaceSessionReady,
    ProcessRaceSucceeded,
    RaceBarrierStage,
    RaceControl,
    decode_fatal,
    decode_process_result,
    decode_ready,
    decode_session_ready,
)

ResultT = TypeVar("ResultT")


def contend(
    database_url: str,
    barrier_key: int,
    jitter_microseconds: int,
    request: RaceRequest[object],
    control: Connection,
) -> None:
    process_id = os.getpid()
    try:
        os.setsid()
        control.send(ProcessRaceSessionReady(process_id=process_id))
        with RaceContenderBarrier(database_url, barrier_key) as barrier:
            backend_id = barrier.backend_id
            control.send(
                ProcessRaceReady(
                    stage=RaceBarrierStage.WAITING,
                    process_id=process_id,
                    backend_id=backend_id,
                )
            )
            barrier.acquire()
            control.send(
                ProcessRaceReady(
                    stage=RaceBarrierStage.ACQUIRED,
                    process_id=process_id,
                    backend_id=backend_id,
                )
            )
            if control.recv() is not RaceControl.PROCEED:
                raise RuntimeError("race overlap gate received an invalid release")
            time.sleep(jitter_microseconds / 1_000_000)
            try:
                validated = validate_race_request(request)
                value = validated.perform(database_url)
                control.send(
                    ProcessRaceCompleted(
                        process_id=process_id,
                        outcome=ProcessRaceSucceeded(value),
                    )
                )
            except BaseException as error:
                control.send(
                    ProcessRaceCompleted(
                        process_id=process_id,
                        outcome=ProcessRaceFailed(ProcessRaceFailure.capture(error)),
                    )
                )
    except BaseException as error:
        with suppress(BrokenPipeError, EOFError, OSError):
            control.send(
                ProcessRaceFatal(
                    process_id=process_id,
                    failure=ProcessRaceFailure.capture(error),
                )
            )
    finally:
        control.close()


def receive(connection: Connection, timeout: float = 15.0) -> object:
    if not connection.poll(timeout):
        raise TimeoutError("fresh race contender did not send its protocol message")
    return connection.recv()


def receive_ready(connection: Connection, expected: RaceBarrierStage) -> ProcessRaceReady:
    message = receive(connection)
    if type(message) is ProcessRaceFatal:
        failure = decode_fatal(message).failure
        raise RuntimeError(
            "fresh race contender failed before "
            f"{expected.value}: {failure.kind.value}/{failure.reason.value}"
        )
    return decode_ready(message, expected)


def receive_result(
    connection: Connection,
    request: RaceRequest[ResultT],
) -> ProcessRaceResult[ResultT]:
    message = receive(connection, timeout=30)
    if type(message) is ProcessRaceFatal:
        failure = decode_fatal(message).failure
        raise RuntimeError(
            "fresh race contender failed before completion: "
            f"{failure.kind.value}/{failure.reason.value}"
        )
    return decode_process_result(request, message)


def reap_processes(
    processes: tuple[OwnedProcess, ...],
    *,
    timeout_seconds: float = 5.0,
) -> None:
    errors: list[BaseException] = []
    for process in processes:
        cleanup = process.reap(timeout_seconds=timeout_seconds)
        errors.extend(cleanup.errors)
    if errors:
        raise BaseExceptionGroup("race contender cleanup failed", errors)


def start_contender(
    *,
    context: SpawnContext,
    database_url: str,
    barrier_key: int,
    jitter_microseconds: int,
    request: RaceRequest[ResultT],
    name: str,
) -> tuple[Connection, OwnedProcess]:
    parent, child = context.Pipe(duplex=True)
    process = context.Process(
        target=contend,
        args=(database_url, barrier_key, jitter_microseconds, request, child),
        name=name,
    )
    owner: OwnedProcess | None = None
    try:
        process.start()
        process_id = process.pid
        if not isinstance(process_id, int):
            raise RaceProtocolError("started race contender has no process identity")
        ready = decode_session_ready(receive(parent))
        if ready.process_id != process_id:
            raise RaceProtocolError("race process-session identity does not match its child")
        owner = OwnedProcess.multiprocessing(process, resources=(parent, child))
        child.close()
        return parent, owner
    except BaseException as start_error:
        cleanup = (
            owner.reap(timeout_seconds=1)
            if owner is not None
            else OwnedProcess.cleanup_multiprocessing_start(
                process,
                resources=(parent, child),
                timeout_seconds=1,
            )
        )
        if cleanup.errors:
            raise BaseExceptionGroup(
                "race contender startup and cleanup failed",
                [start_error, *cleanup.errors],
            ) from start_error
        raise


__all__ = [
    "reap_processes",
    "receive_ready",
    "receive_result",
    "start_contender",
]
