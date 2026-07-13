"""Bounded in-process Workflow Worker concurrency for the local runtime."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Protocol
from uuid import uuid4


class PollingWorkflowWorker(Protocol):
    async def run_once(self) -> object: ...


class InProcessWorkflowWorkerFleet:
    """Own a bounded set of independently identified async claim loops."""

    def __init__(
        self,
        worker_factory: Callable[[str], PollingWorkflowWorker],
        *,
        initial_capacity: int = 1,
        max_capacity: int = 8,
    ) -> None:
        if initial_capacity < 1 or max_capacity < initial_capacity:
            raise ValueError("Worker fleet capacity is invalid")
        self._worker_factory = worker_factory
        self._max_capacity = max_capacity
        self._workers: dict[str, PollingWorkflowWorker] = {}
        for _ in range(initial_capacity):
            self.add_worker()

    @property
    def worker_ids(self) -> tuple[str, ...]:
        return tuple(self._workers)

    @property
    def max_capacity(self) -> int:
        return self._max_capacity

    def add_worker(self) -> str:
        if len(self._workers) >= self._max_capacity:
            raise ValueError(f"Worker fleet is already at its limit of {self._max_capacity}")
        worker_id = f"workflow-worker:{uuid4()}"
        self._workers[worker_id] = self._worker_factory(worker_id)
        return worker_id

    def remove_worker(self, worker_id: str) -> None:
        if worker_id not in self._workers:
            raise ValueError("Worker is not part of this fleet")
        if len(self._workers) == 1:
            raise ValueError("The fleet must keep at least one Worker")
        del self._workers[worker_id]

    async def run_once(self) -> tuple[object, ...]:
        return tuple(
            await asyncio.gather(*(worker.run_once() for worker in self._workers.values()))
        )


__all__ = ["InProcessWorkflowWorkerFleet"]
