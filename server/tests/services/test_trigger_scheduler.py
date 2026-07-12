from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from server.services import trigger_scheduler as scheduler_module
from server.services.trigger_scheduler import TriggerScheduler
from server.services.triggers import TriggerRecord


class _DueTriggerService:
    def __init__(self) -> None:
        self.trigger = TriggerRecord(
            id=1,
            agent_name="legacy reminder",
            payload="do legacy work",
            next_trigger="2026-07-12T20:00:00Z",
            status="active",
            created_at="2026-07-12T19:00:00Z",
            updated_at="2026-07-12T19:00:00Z",
        )

    def get_due_triggers(self, *, before):
        return [self.trigger]


async def test_workflow_mode_never_launches_legacy_trigger_execution(
    monkeypatch: pytest.MonkeyPatch,
):
    scheduler = TriggerScheduler(poll_interval_seconds=0.01)
    scheduler._service = _DueTriggerService()
    monkeypatch.setattr(
        scheduler_module,
        "get_settings",
        lambda: SimpleNamespace(interaction_mode="workflow"),
    )

    class ForbiddenExecutionManager:
        def __init__(self):
            raise AssertionError("legacy execution manager must remain unreachable")

    monkeypatch.setattr(
        scheduler_module,
        "ExecutionBatchManager",
        ForbiddenExecutionManager,
    )

    await scheduler.start()
    await scheduler._poll_once()

    assert scheduler._task is None
    assert scheduler._execution_tasks == set()


async def test_legacy_trigger_tasks_are_retained_and_drained_on_stop(
    monkeypatch: pytest.MonkeyPatch,
):
    scheduler = TriggerScheduler(poll_interval_seconds=60)
    scheduler._service = _DueTriggerService()
    monkeypatch.setattr(
        scheduler_module,
        "get_settings",
        lambda: SimpleNamespace(interaction_mode="legacy"),
    )
    started = asyncio.Event()

    class BlockingExecutionManager:
        async def execute_agent(self, agent_name: str, instructions: str):
            started.set()
            await asyncio.Event().wait()

    monkeypatch.setattr(
        scheduler_module,
        "ExecutionBatchManager",
        BlockingExecutionManager,
    )

    await scheduler._poll_once()
    await started.wait()
    assert len(scheduler._execution_tasks) == 1

    await scheduler.stop()

    assert scheduler._execution_tasks == set()
    assert scheduler._in_flight == set()
