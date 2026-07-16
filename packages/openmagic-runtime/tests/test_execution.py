from __future__ import annotations

import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from uuid import uuid4

import pytest
from openmagic_runtime.agents import (
    AgentAudience,
    AgentConfiguration,
    AgentExecutionInput,
    AgentField,
    AgentRecord,
    AgentRunInput,
    AgentTask,
)
from openmagic_runtime.execution import (
    AttemptExecution,
    CancellationToken,
    FreshAgentExecutor,
)
from openmagic_runtime.threads import ThreadContext


@dataclass(frozen=True)
class Candidate:
    value: str


def _candidate_factory():
    return lambda execution: Candidate(str(execution.run_input.task.input.value("value")))


def _malformed_candidate_factory():
    return lambda _execution: "not-a-typed-candidate"


def _slow_candidate_factory(marker: Path):
    def run(execution: AgentExecutionInput) -> Candidate:
        time.sleep(1.5)
        marker.write_text(str(execution.run_input.task.input.value("value")))
        return Candidate("late")

    return run


def _execution() -> AttemptExecution:
    attempt_id = uuid4()
    thread_id = uuid4()
    run_input = AgentRunInput(
        configuration=AgentConfiguration("test.agent", 1, "test.agent.instructions.v1"),
        task=AgentTask(
            "test.task",
            1,
            AgentRecord("test.task.input", 1, (AgentField("value", "candidate"),)),
        ),
        thread_id=thread_id,
        context_through_sequence=0,
        domain_event_context=(),
        audience_context=AgentAudience("test", "recipient"),
        locale="en-CA",
    )
    return AttemptExecution(
        instance_id=uuid4(),
        step_id=uuid4(),
        attempt_id=attempt_id,
        attempt_number=1,
        template_key="test_agent",
        executor_key="test.agent.v1",
        input={"value": "candidate"},
        agent_input=AgentExecutionInput(
            agent_run_id=uuid4(),
            attempt_id=attempt_id,
            run_input=run_input,
            thread_context=ThreadContext(thread_id, 0, ()),
        ),
    )


def test_fresh_agent_executor_returns_only_its_typed_candidate() -> None:
    executor = FreshAgentExecutor(
        _candidate_factory,
        result_class=Candidate,
        encoder=lambda candidate: {"value": candidate.value},
        timeout_seconds=1,
    )

    observation = executor.execute(_execution(), CancellationToken())

    assert observation.value == {"value": "candidate"}


def test_fresh_agent_executor_rejects_malformed_candidate_type() -> None:
    executor = FreshAgentExecutor(
        _malformed_candidate_factory,
        result_class=Candidate,
        encoder=lambda candidate: {"value": candidate.value},
        timeout_seconds=1,
    )

    with pytest.raises(RuntimeError, match="outside its typed contract"):
        executor.execute(_execution(), CancellationToken())


def test_fresh_agent_executor_terminates_work_after_timeout(tmp_path: Path) -> None:
    marker = tmp_path / "agent-finished"

    executor = FreshAgentExecutor(
        partial(_slow_candidate_factory, marker),
        result_class=Candidate,
        encoder=lambda candidate: {"value": candidate.value},
        timeout_seconds=1,
    )

    with pytest.raises(RuntimeError, match="bounded timeout"):
        executor.execute(_execution(), CancellationToken())
    time.sleep(0.7)

    assert not marker.exists()
