from __future__ import annotations

import time
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from uuid import uuid4

import pytest
from openmagic_runtime.execution import (
    AttemptExecution,
    CancellationToken,
    FreshAgentExecutor,
)


@dataclass(frozen=True)
class Candidate:
    value: str


def _candidate_factory():  # type: ignore[no-untyped-def]
    return lambda value: Candidate(value["value"])


def _slow_candidate_factory(marker: Path):  # type: ignore[no-untyped-def]
    def run(value: dict[str, object]) -> Candidate:
        time.sleep(1.5)
        marker.write_text(str(value["value"]))
        return Candidate("late")

    return run


def _execution() -> AttemptExecution:
    return AttemptExecution(
        instance_id=uuid4(),
        step_id=uuid4(),
        attempt_id=uuid4(),
        attempt_number=1,
        template_key="test_agent",
        executor_key="test.agent.v1",
        input={"value": "candidate"},
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
