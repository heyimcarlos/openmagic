from __future__ import annotations

import asyncio

import pytest

from server.services.workflow_worker_fleet import InProcessWorkflowWorkerFleet


async def test_worker_fleet_polls_each_worker_concurrently() -> None:
    all_started = asyncio.Event()
    release = asyncio.Event()
    started: list[str] = []

    class Worker:
        def __init__(self, worker_id: str) -> None:
            self.worker_id = worker_id

        async def run_once(self) -> str:
            started.append(self.worker_id)
            if len(started) == 2:
                all_started.set()
            await release.wait()
            return self.worker_id

    fleet = InProcessWorkflowWorkerFleet(Worker, initial_capacity=2)

    poll = asyncio.create_task(fleet.run_once())
    await asyncio.wait_for(all_started.wait(), timeout=1)
    release.set()

    assert await poll == fleet.worker_ids


def test_worker_fleet_adds_distinct_workers_up_to_its_limit() -> None:
    class Worker:
        def __init__(self, worker_id: str) -> None:
            self.worker_id = worker_id

        async def run_once(self) -> str:
            return self.worker_id

    fleet = InProcessWorkflowWorkerFleet(Worker, max_capacity=2)

    added = fleet.add_worker()

    assert fleet.worker_ids[1] == added
    with pytest.raises(ValueError, match="limit of 2"):
        fleet.add_worker()


def test_worker_fleet_removes_exact_worker_but_preserves_minimum_capacity() -> None:
    class Worker:
        def __init__(self, worker_id: str) -> None:
            self.worker_id = worker_id

        async def run_once(self) -> str:
            return self.worker_id

    fleet = InProcessWorkflowWorkerFleet(Worker, initial_capacity=2)
    removed = fleet.worker_ids[0]
    remaining = fleet.worker_ids[1]

    fleet.remove_worker(removed)

    assert fleet.worker_ids == (remaining,)
    with pytest.raises(ValueError, match="at least one Worker"):
        fleet.remove_worker(fleet.worker_ids[0])
