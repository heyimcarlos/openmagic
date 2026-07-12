"""Production entry points must honor the configured Interaction Agent profile."""

from __future__ import annotations

import asyncio

from server.agents import interaction_agent
from server.agents.execution_agent.batch_manager import ExecutionBatchManager
from server.services.gmail.importance_watcher import _resolve_interaction_runtime


async def test_background_entrypoints_use_mode_aware_runtime_factory(monkeypatch):
    delivered: list[str] = []

    class FakeRuntime:
        async def handle_agent_message(self, payload: str) -> None:
            delivered.append(payload)

    runtime = FakeRuntime()
    monkeypatch.setattr(interaction_agent, "create_interaction_runtime", lambda: runtime)

    assert _resolve_interaction_runtime() is runtime
    await ExecutionBatchManager()._dispatch_to_interaction_agent("execution result")
    await asyncio.sleep(0)

    assert delivered == ["execution result"]
