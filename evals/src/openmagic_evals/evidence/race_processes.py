"""Fresh-process contenders synchronized by a PostgreSQL-visible overlap gate."""

from __future__ import annotations

import hashlib
import os
import time
from contextlib import suppress
from dataclasses import dataclass
from multiprocessing import get_context
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
from typing import Any, Literal, cast
from uuid import UUID

import psycopg
from example_insurance.renewals import (
    ExampleInsurance,
    StartRenewalOutreach,
    SubmitVerificationCode,
)
from openmagic_runtime.kernel.control import AcceptSignal, KernelControl
from openmagic_runtime.kernel.work import ClaimedAttempt, KernelWork

RaceOperation = Literal[
    "accept_signal",
    "attempt_result",
    "command_receipt",
    "delivery_claim",
    "route_activation",
    "step_claim",
    "verification_submission",
]


@dataclass(frozen=True)
class ProcessRaceFailure:
    exception_type: type[BaseException]
    reason: object | None
    message: str

    @classmethod
    def capture(cls, error: BaseException) -> ProcessRaceFailure:
        return cls(
            exception_type=type(error),
            reason=getattr(error, "reason", None),
            message=str(error),
        )


@dataclass(frozen=True)
class ProcessRaceResult:
    process_id: int
    value: object | None
    failure: ProcessRaceFailure | None

    def require_value(self) -> object:
        if self.failure is not None:
            raise RuntimeError(
                "race contender failed through its public operation: "
                f"{self.failure.exception_type.__name__}"
            )
        return self.value


@dataclass(frozen=True)
class ProcessRacePair:
    results: tuple[ProcessRaceResult, ProcessRaceResult]
    overlap_barrier_observed: Literal[True]

    @property
    def process_ids(self) -> tuple[int, int]:
        return self.results[0].process_id, self.results[1].process_id


def _barrier_key(case_id: str, seed: int) -> int:
    digest = hashlib.sha256(f"{case_id}:{seed}".encode()).digest()
    return int.from_bytes(digest[:8], signed=True)


def _execute(database_url: str, operation: RaceOperation, payload: object) -> object:
    if operation == "command_receipt":
        return ExampleInsurance(database_url=database_url).start_renewal_outreach(
            cast(StartRenewalOutreach, payload)
        )
    if operation == "step_claim":
        worker_id, claim_request_id = cast(tuple[str, UUID], payload)
        return ExampleInsurance(database_url=database_url).claim_workflow_attempt(
            worker_id=worker_id,
            claim_request_id=claim_request_id,
        )
    if operation == "delivery_claim":
        worker_id, claim_request_id = cast(tuple[str, UUID], payload)
        return ExampleInsurance(database_url=database_url).claim_delivery_attempt(
            worker_id=worker_id,
            claim_request_id=claim_request_id,
        )
    if operation == "verification_submission":
        command, secret = cast(tuple[SubmitVerificationCode, bytes], payload)
        return ExampleInsurance(
            database_url=database_url,
            verification_code_secret=secret,
        ).submit_verification_code(command)
    with psycopg.connect(database_url) as connection, connection.transaction():
        if operation == "accept_signal":
            return KernelControl(connection).accept_signal(cast(AcceptSignal, payload))
        if operation == "attempt_result":
            claim, worker_id, observation = cast(
                tuple[ClaimedAttempt, str, dict[str, Any]], payload
            )
            return KernelWork(connection).accept_result(
                claim,
                worker_id=worker_id,
                observation=observation,
            )
        if operation == "route_activation":
            claim, worker_id, observation = cast(
                tuple[ClaimedAttempt, str, dict[str, Any]], payload
            )
            required = KernelWork(connection).accept_result(
                claim,
                worker_id=worker_id,
                observation=observation,
            )
            steps, waits = KernelControl(connection).succeed(
                required,
                output=observation,
                outcome_route="finish_after_origin",
                route_input=observation,
            )
            return dict(steps), dict(waits), required.replayed
    raise AssertionError(f"unsupported race operation: {operation}")


def _contend(
    database_url: str,
    barrier_key: int,
    jitter_microseconds: int,
    operation: RaceOperation,
    payload: object,
    control: Connection,
) -> None:
    process_id = os.getpid()
    try:
        with psycopg.connect(database_url, autocommit=True) as barrier:
            backend_id = barrier.info.backend_pid
            control.send(("waiting", process_id, backend_id))
            barrier.execute("SELECT pg_advisory_lock_shared(%s)", (barrier_key,))
            control.send(("acquired", process_id, backend_id))
            if control.recv() != "proceed":
                raise RuntimeError("race overlap gate received an invalid release")
            time.sleep(jitter_microseconds / 1_000_000)
            try:
                value = _execute(database_url, operation, payload)
                control.send(("result", process_id, value, None))
            except BaseException as error:
                control.send(("result", process_id, None, ProcessRaceFailure.capture(error)))
            finally:
                barrier.execute("SELECT pg_advisory_unlock_shared(%s)", (barrier_key,))
    except BaseException as error:
        with suppress(BrokenPipeError, EOFError, OSError):
            control.send(("fatal", process_id, type(error).__name__, str(error)))
    finally:
        control.close()


def _receive(connection: Connection, expected: str, timeout: float = 15.0) -> tuple[object, ...]:
    if not connection.poll(timeout):
        raise TimeoutError(f"fresh race contender did not reach {expected}")
    message = connection.recv()
    if not isinstance(message, tuple) or not message or message[0] != expected:
        raise RuntimeError(f"fresh race contender failed before {expected}: {message!r}")
    return message


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
    operation: RaceOperation,
    payloads: tuple[object, object],
) -> ProcessRacePair:
    """Run two fresh interpreters inside one observed PostgreSQL overlap gate."""

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
                        operation,
                        payloads[index],
                        child,
                    ),
                    name=f"openmagic-race-{case_id}-{seed}-{index}",
                )
                process.start()
                child.close()
                parents.append(parent)
                processes.append(process)
            waiting = tuple(_receive(parent, "waiting") for parent in parents)
            backend_ids = tuple(cast(int, message[2]) for message in waiting)
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
            acquired = tuple(_receive(parent, "acquired") for parent in parents)
            acquired_backend_ids = tuple(cast(int, message[2]) for message in acquired)
            row = coordinator.execute(
                "SELECT count(*) FROM pg_locks WHERE pid = ANY(%s) "
                "AND locktype = 'advisory' AND granted",
                (list(acquired_backend_ids),),
            ).fetchone()
            overlap = row == (2,)
            if not overlap:
                raise AssertionError("PostgreSQL did not grant both shared overlap locks")
            for parent in parents:
                parent.send("proceed")
            messages = tuple(_receive(parent, "result", timeout=30) for parent in parents)
            results = tuple(
                ProcessRaceResult(
                    process_id=cast(int, message[1]),
                    value=message[2],
                    failure=cast(ProcessRaceFailure | None, message[3]),
                )
                for message in messages
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
    "ProcessRaceFailure",
    "ProcessRacePair",
    "ProcessRaceResult",
    "RaceOperation",
    "run_process_contenders",
]
