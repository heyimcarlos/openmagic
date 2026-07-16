"""Fresh-process contenders synchronized by a PostgreSQL-visible overlap gate."""

from __future__ import annotations

import hashlib
import os
import time
from contextlib import suppress
from multiprocessing import get_context
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import TypeVar

import psycopg

from openmagic_evals.evidence._race_operations import (
    AcceptSignalRace,
    AttemptResultRace,
    DeliveryClaimRace,
    RaceProtocolError,
    RaceRequest,
    RaceRequestValue,
    RouteActivationRace,
    RouteActivationRaceResult,
    StartRenewalOutreachRace,
    StepClaimRace,
    VerificationSubmissionRace,
    validate_race_pair,
    validate_race_request,
)
from openmagic_evals.evidence._race_transport import (
    ProcessRaceCompleted,
    ProcessRaceFailed,
    ProcessRaceFailure,
    ProcessRaceFatal,
    ProcessRacePair,
    ProcessRaceReady,
    ProcessRaceResult,
    ProcessRaceSucceeded,
    RaceBarrierStage,
    RaceControl,
    RaceFailureKind,
    RaceFailureReason,
    decode_fatal,
    decode_process_result,
    decode_ready,
)

ResultT = TypeVar("ResultT")


def _barrier_key(case_id: str, seed: int) -> int:
    digest = hashlib.sha256(f"{case_id}:{seed}".encode()).digest()
    return int.from_bytes(digest[:8], signed=True)


def _contend(
    database_url: str,
    barrier_key: int,
    jitter_microseconds: int,
    request: RaceRequestValue,
    control: Connection,
) -> None:
    process_id = os.getpid()
    try:
        with psycopg.connect(database_url, autocommit=True) as barrier:
            backend_id = barrier.info.backend_pid
            control.send(
                ProcessRaceReady(
                    stage=RaceBarrierStage.WAITING,
                    process_id=process_id,
                    backend_id=backend_id,
                )
            )
            barrier.execute("SELECT pg_advisory_lock_shared(%s)", (barrier_key,))
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
                value = validated.execute(database_url)
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
            finally:
                barrier.execute("SELECT pg_advisory_unlock_shared(%s)", (barrier_key,))
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


def _receive(connection: Connection, timeout: float = 15.0) -> object:
    if not connection.poll(timeout):
        raise TimeoutError("fresh race contender did not send its protocol message")
    return connection.recv()


def _receive_ready(connection: Connection, expected: RaceBarrierStage) -> ProcessRaceReady:
    message = _receive(connection)
    if type(message) is ProcessRaceFatal:
        failure = decode_fatal(message).failure
        raise RuntimeError(
            "fresh race contender failed before "
            f"{expected.value}: {failure.kind.value}/{failure.reason.value}"
        )
    return decode_ready(message, expected)


def _receive_result(
    connection: Connection, request: RaceRequest[ResultT]
) -> ProcessRaceResult[ResultT]:
    message = _receive(connection, timeout=30)
    if type(message) is ProcessRaceFatal:
        failure = decode_fatal(message).failure
        raise RuntimeError(
            "fresh race contender failed before completion: "
            f"{failure.kind.value}/{failure.reason.value}"
        )
    return decode_process_result(request, message)


def _reap_processes(processes: tuple[BaseProcess, ...], *, timeout_seconds: float = 5.0) -> None:
    """Reap every owned contender before reporting any cleanup failure."""

    errors: list[Exception] = []
    for process in processes:
        alive = True
        try:
            try:
                process.join(timeout=timeout_seconds)
            except Exception as error:
                errors.append(error)
            try:
                alive = process.is_alive()
            except Exception as error:
                errors.append(error)
            if alive:
                try:
                    process.terminate()
                except Exception as error:
                    errors.append(error)
                try:
                    process.join(timeout=timeout_seconds)
                except Exception as error:
                    errors.append(error)
                try:
                    alive = process.is_alive()
                except Exception as error:
                    errors.append(error)
            if alive:
                try:
                    process.kill()
                except Exception as error:
                    errors.append(error)
                try:
                    process.join(timeout=timeout_seconds)
                except Exception as error:
                    errors.append(error)
                try:
                    alive = process.is_alive()
                except Exception as error:
                    errors.append(error)
            if alive:
                errors.append(RuntimeError(f"race contender {process.pid} survived cleanup"))
        finally:
            if not alive:
                try:
                    process.close()
                except Exception as error:
                    errors.append(error)
    if errors:
        raise ExceptionGroup("race contender cleanup failed", errors)


def run_process_contenders(
    database_url: str,
    *,
    case_id: str,
    seed: int,
    jitter_microseconds: tuple[int, int],
    requests: tuple[RaceRequest[ResultT], RaceRequest[ResultT]],
) -> ProcessRacePair[ResultT]:
    """Run two fresh interpreters inside one observed PostgreSQL overlap gate."""

    validate_race_pair(requests)
    key = _barrier_key(case_id, seed)
    context = get_context("spawn")
    parents: list[Connection] = []
    processes: list[BaseProcess] = []
    with psycopg.connect(database_url, autocommit=True) as coordinator:
        coordinator.execute("SELECT pg_advisory_lock(%s)", (key,))
        try:
            for index in range(2):
                parent, child = context.Pipe(duplex=True)
                process = context.Process(
                    target=_contend,
                    args=(
                        database_url,
                        key,
                        jitter_microseconds[index],
                        requests[index],
                        child,
                    ),
                    name=f"openmagic-race-{case_id}-{seed}-{index}",
                )
                process.start()
                child.close()
                parents.append(parent)
                processes.append(process)
            waiting = tuple(_receive_ready(parent, RaceBarrierStage.WAITING) for parent in parents)
            backend_ids = tuple(message.backend_id for message in waiting)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                row = coordinator.execute(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE pid = ANY(%s) AND wait_event_type = 'Lock' "
                    "AND wait_event = 'advisory'",
                    (list(backend_ids),),
                ).fetchone()
                if row == (2,):
                    break
                time.sleep(0.01)
            else:
                observed = coordinator.execute(
                    "SELECT pid, state, wait_event_type, wait_event FROM pg_stat_activity "
                    "WHERE pid = ANY(%s) ORDER BY pid",
                    (list(backend_ids),),
                ).fetchall()
                raise TimeoutError(
                    f"PostgreSQL did not observe both race contenders waiting: {observed!r}"
                )
            coordinator.execute("SELECT pg_advisory_unlock(%s)", (key,))
            acquired = tuple(
                _receive_ready(parent, RaceBarrierStage.ACQUIRED) for parent in parents
            )
            acquired_backend_ids = tuple(message.backend_id for message in acquired)
            row = coordinator.execute(
                "SELECT count(*) FROM pg_locks WHERE pid = ANY(%s) "
                "AND locktype = 'advisory' AND granted",
                (list(acquired_backend_ids),),
            ).fetchone()
            overlap = row == (2,)
            if not overlap:
                raise AssertionError("PostgreSQL did not grant both shared overlap locks")
            for parent in parents:
                parent.send(RaceControl.PROCEED)
            results = (
                _receive_result(parents[0], requests[0]),
                _receive_result(parents[1], requests[1]),
            )
            if len({result.process_id for result in results}) != 2:
                raise AssertionError("race contenders did not use distinct fresh interpreters")
            return ProcessRacePair(
                results=(results[0], results[1]),
                overlap_barrier_observed=True,
            )
        finally:
            cleanup_errors: list[Exception] = []
            try:
                coordinator.execute("SELECT pg_advisory_unlock(%s)", (key,))
            except Exception as error:
                cleanup_errors.append(error)
            for parent in parents:
                try:
                    parent.close()
                except Exception as error:
                    cleanup_errors.append(error)
            try:
                _reap_processes(tuple(processes))
            except Exception as error:
                cleanup_errors.append(error)
            if cleanup_errors:
                raise ExceptionGroup("race process cleanup failed", cleanup_errors)


__all__ = [
    "AcceptSignalRace",
    "AttemptResultRace",
    "DeliveryClaimRace",
    "ProcessRaceCompleted",
    "ProcessRaceFailed",
    "ProcessRaceFailure",
    "ProcessRacePair",
    "ProcessRaceResult",
    "ProcessRaceSucceeded",
    "RaceFailureKind",
    "RaceFailureReason",
    "RaceProtocolError",
    "RouteActivationRace",
    "RouteActivationRaceResult",
    "StartRenewalOutreachRace",
    "StepClaimRace",
    "VerificationSubmissionRace",
    "decode_process_result",
    "run_process_contenders",
    "validate_race_pair",
    "validate_race_request",
]
