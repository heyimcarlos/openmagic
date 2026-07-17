"""PostgreSQL overlap coordination for fresh-process race contenders."""

from __future__ import annotations

import hashlib
from multiprocessing import get_context
from multiprocessing.connection import Connection
from typing import TypeVar

from openmagic_runtime.processes import OwnedProcess, owned_cleanup_scope

from openmagic_evals.evidence._race_contender_process import (
    reap_processes,
    receive_ready,
    receive_result,
)
from openmagic_evals.evidence._race_contender_process import (
    start_contender as _start_contender,
)
from openmagic_evals.evidence._race_operations import RaceRequest, validate_race_pair
from openmagic_evals.evidence._race_persistence import RaceCoordinatorBarrier
from openmagic_evals.evidence._race_transport import (
    ProcessRacePair,
    RaceBarrierStage,
    RaceControl,
)

ResultT = TypeVar("ResultT")


def _barrier_key(case_id: str, seed: int) -> int:
    digest = hashlib.sha256(f"{case_id}:{seed}".encode()).digest()
    return int.from_bytes(digest[:8], signed=True)


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
    processes: list[OwnedProcess] = []
    with RaceCoordinatorBarrier(database_url, key) as coordinator:

        def cleanup() -> None:
            cleanup_errors: list[BaseException] = []
            try:
                coordinator.release()
            except BaseException as error:
                cleanup_errors.append(error)
            try:
                reap_processes(tuple(processes))
            except BaseException as error:
                cleanup_errors.append(error)
            if cleanup_errors:
                raise BaseExceptionGroup("race process cleanup failed", cleanup_errors)

        with owned_cleanup_scope(cleanup, message="race execution and cleanup failed"):
            for index in range(2):
                parent, process = _start_contender(
                    context=context,
                    database_url=database_url,
                    barrier_key=key,
                    jitter_microseconds=jitter_microseconds[index],
                    request=requests[index],
                    name=f"openmagic-race-{case_id}-{seed}-{index}",
                )
                parents.append(parent)
                processes.append(process)
            backend_ids = tuple(
                receive_ready(parent, RaceBarrierStage.WAITING).backend_id for parent in parents
            )
            coordinator.await_waiters(backend_ids)
            coordinator.release()
            acquired_backend_ids = tuple(
                receive_ready(parent, RaceBarrierStage.ACQUIRED).backend_id for parent in parents
            )
            coordinator.require_overlap(acquired_backend_ids)
            for parent in parents:
                parent.send(RaceControl.PROCEED)
            results = (
                receive_result(parents[0], requests[0]),
                receive_result(parents[1], requests[1]),
            )
            if len({result.process_id for result in results}) != 2:
                raise AssertionError("race contenders did not use distinct fresh interpreters")
            return ProcessRacePair(results=results, overlap_barrier_observed=True)


__all__ = ["run_process_contenders"]
