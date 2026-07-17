from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import partial
from multiprocessing import active_children
from pathlib import Path
from threading import Thread
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
    AgentExecutionFailure,
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


def _failing_candidate_factory():
    def fail(_execution: AgentExecutionInput) -> Candidate:
        raise ValueError("synthetic child failure")

    return fail


def _slow_candidate_factory(marker: Path):
    def run(execution: AgentExecutionInput) -> Candidate:
        time.sleep(1.5)
        marker.write_text(str(execution.run_input.task.input.value("value")))
        return Candidate("late")

    return run


def _term_resistant_candidate_factory(pid_file: Path):
    def run(_execution: AgentExecutionInput) -> Candidate:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        descendant = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(10)",
            ]
        )
        pid_file.write_text(f"{os.getpid()} {descendant.pid}", encoding="utf-8")
        time.sleep(10)
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

    observation = executor.run(_execution(), CancellationToken())

    assert observation.value == {"value": "candidate"}


def test_fresh_agent_executor_rejects_malformed_candidate_type() -> None:
    executor = FreshAgentExecutor(
        _malformed_candidate_factory,
        result_class=Candidate,
        encoder=lambda candidate: {"value": candidate.value},
        timeout_seconds=1,
    )

    with pytest.raises(AgentExecutionFailure, match="outside its typed contract") as raised:
        executor.run(_execution(), CancellationToken())
    assert raised.value.reason == "malformed_result"


def test_fresh_agent_executor_preserves_typed_child_failure() -> None:
    executor = FreshAgentExecutor(
        _failing_candidate_factory,
        result_class=Candidate,
        encoder=lambda candidate: {"value": candidate.value},
        timeout_seconds=1,
    )

    with pytest.raises(AgentExecutionFailure, match="synthetic child failure") as raised:
        executor.run(_execution(), CancellationToken())
    assert raised.value.reason == "child_process_failure"


def test_fresh_agent_executor_terminates_work_after_timeout(tmp_path: Path) -> None:
    marker = tmp_path / "agent-finished"

    executor = FreshAgentExecutor(
        partial(_slow_candidate_factory, marker),
        result_class=Candidate,
        encoder=lambda candidate: {"value": candidate.value},
        timeout_seconds=1,
    )

    with pytest.raises(AgentExecutionFailure, match="bounded timeout") as raised:
        executor.run(_execution(), CancellationToken())
    assert raised.value.reason == "bounded_timeout"
    time.sleep(0.7)

    assert not marker.exists()


def test_fresh_agent_executor_kills_and_reaps_term_resistant_child(tmp_path: Path) -> None:
    pid_file = tmp_path / "agent-pid"
    executor = FreshAgentExecutor(
        partial(_term_resistant_candidate_factory, pid_file),
        result_class=Candidate,
        encoder=lambda candidate: {"value": candidate.value},
        timeout_seconds=1,
    )

    with pytest.raises(AgentExecutionFailure) as raised:
        executor.run(_execution(), CancellationToken())
    assert raised.value.reason == "bounded_timeout"
    process_ids = tuple(int(value) for value in pid_file.read_text(encoding="utf-8").split())
    for process_id in process_ids:
        stat = Path(f"/proc/{process_id}/stat")
        assert not stat.exists() or stat.read_text(encoding="utf-8").rpartition(")")[2].split()[
            0
        ] in {"X", "Z"}


def test_fresh_agent_executor_cancellation_reaps_ready_session_tree(tmp_path: Path) -> None:
    pid_file = tmp_path / "cancelled-agent-pids"
    cancellation = CancellationToken()
    executor = FreshAgentExecutor(
        partial(_term_resistant_candidate_factory, pid_file),
        result_class=Candidate,
        encoder=lambda candidate: {"value": candidate.value},
        timeout_seconds=5,
    )

    def cancel_when_candidate_starts() -> None:
        deadline = time.monotonic() + 3
        while not pid_file.is_file() and time.monotonic() < deadline:
            time.sleep(0.01)
        cancellation.cancel()

    canceller = Thread(target=cancel_when_candidate_starts)
    canceller.start()
    with pytest.raises(AgentExecutionFailure) as raised:
        executor.run(_execution(), cancellation)
    canceller.join(timeout=1)

    assert raised.value.reason == "cancelled"
    assert not canceller.is_alive()
    process_ids = tuple(int(value) for value in pid_file.read_text(encoding="utf-8").split())
    for process_id in process_ids:
        stat = Path(f"/proc/{process_id}/stat")
        assert not stat.exists() or stat.read_text(encoding="utf-8").rpartition(")")[2].split()[
            0
        ] in {"X", "Z"}


def test_fresh_agent_executor_cleans_scope_when_spawn_cannot_start() -> None:
    existing_children = {child.pid for child in active_children()}

    def local_factory():
        return _candidate_factory()

    executor = FreshAgentExecutor(
        local_factory,
        result_class=Candidate,
        encoder=lambda candidate: {"value": candidate.value},
        timeout_seconds=1,
    )

    with pytest.raises((AttributeError, TypeError), match=r"local object|pickle"):
        executor.run(_execution(), CancellationToken())

    assert {child.pid for child in active_children()} <= existing_children
