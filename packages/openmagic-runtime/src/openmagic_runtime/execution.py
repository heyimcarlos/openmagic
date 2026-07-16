"""One typed cancellable Executor seam for every Step kind."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from multiprocessing import get_context
from threading import Event, Thread
from time import monotonic
from typing import Any, Generic, Literal, Protocol, TypeVar
from uuid import UUID

from openmagic_runtime.agents import AgentExecutionInput


@dataclass(frozen=True)
class AttemptExecution:
    instance_id: UUID
    step_id: UUID
    attempt_id: UUID
    attempt_number: int
    template_key: str
    executor_key: str
    input: dict[str, Any]
    agent_input: AgentExecutionInput | None = None


@dataclass(frozen=True)
class AttemptObservation:
    value: dict[str, Any]


class CancellationToken:
    def __init__(self) -> None:
        self._event = Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()


class ExecutionAuthorityLost(RuntimeError):
    """Raised when execution is cancelled because its durable authority ended."""


AgentExecutionFailureReason = Literal[
    "cancelled",
    "missing_input",
    "bounded_timeout",
    "missing_result",
    "child_process_failure",
    "malformed_result",
]


class AgentExecutionFailure(RuntimeError):
    """A typed failure at the fresh Agent process boundary."""

    def __init__(self, reason: AgentExecutionFailureReason, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class Executor(Protocol):
    def execute(
        self, execution: AttemptExecution, cancellation: CancellationToken
    ) -> AttemptObservation: ...


def execute_with_renewable_authority(
    *,
    executor: Executor,
    execution: AttemptExecution,
    cancellation: CancellationToken,
    renew: Callable[[], object],
    lease_seconds: int,
    worker_shutdown: Event | None = None,
) -> AttemptObservation:
    """Execute while renewing durable authority and cancel immediately when it is lost."""
    if lease_seconds <= 0:
        raise ValueError("Attempt lease duration must be positive")
    if worker_shutdown is not None and worker_shutdown.is_set():
        cancellation.cancel()
        raise ExecutionAuthorityLost("Worker shutdown cancelled Attempt execution")
    try:
        renew()
    except BaseException as error:
        cancellation.cancel()
        raise ExecutionAuthorityLost("Attempt execution lost durable authority") from error
    stopped = Event()
    authority_failure: list[BaseException] = []
    interval = max(0.05, lease_seconds / 3)

    def maintain() -> None:
        while not stopped.wait(interval):
            if worker_shutdown is not None and worker_shutdown.is_set():
                authority_failure.append(
                    RuntimeError("Worker shutdown cancelled Attempt execution")
                )
                cancellation.cancel()
                return
            try:
                renew()
            except BaseException as error:
                authority_failure.append(error)
                cancellation.cancel()
                return

    maintainer = Thread(target=maintain, name="openmagic-attempt-lease", daemon=True)
    maintainer.start()
    try:
        try:
            result = executor.execute(execution, cancellation)
        except BaseException:
            if authority_failure:
                raise ExecutionAuthorityLost("Attempt execution lost durable authority") from (
                    authority_failure[0]
                )
            raise
    finally:
        stopped.set()
        maintainer.join(timeout=1)
    if authority_failure:
        raise ExecutionAuthorityLost("Attempt execution lost durable authority") from (
            authority_failure[0]
        )
    if worker_shutdown is not None and worker_shutdown.is_set():
        cancellation.cancel()
        raise ExecutionAuthorityLost("Worker shutdown cancelled Attempt execution")
    return result


class DeterministicExecutor:
    def __init__(self, operation: Callable[[dict[str, Any]], dict[str, Any]]) -> None:
        self._operation = operation

    def execute(
        self, execution: AttemptExecution, cancellation: CancellationToken
    ) -> AttemptObservation:
        if cancellation.cancelled:
            raise RuntimeError("Attempt execution was cancelled")
        result = self._operation(dict(execution.input))
        if cancellation.cancelled:
            raise RuntimeError("Attempt execution was cancelled")
        return AttemptObservation(value=result)


CandidateT = TypeVar("CandidateT")


def _run_agent_child(
    factory: Callable[[], Callable[[AgentExecutionInput], Any]],
    input_value: AgentExecutionInput,
    sender: Any,
) -> None:
    try:
        sender.send(("result", factory()(input_value)))
    except AgentExecutionFailure as error:
        sender.send(("failure", error.reason, str(error)))
    except BaseException as error:
        sender.send(
            (
                "failure",
                "child_process_failure",
                f"Agent execution failed: {type(error).__name__}: {error}",
            )
        )
    finally:
        sender.close()


class FreshAgentExecutor(Generic[CandidateT]):
    def __init__(
        self,
        factory: Callable[[], Callable[[AgentExecutionInput], CandidateT]],
        *,
        result_class: type[CandidateT],
        encoder: Callable[[CandidateT], dict[str, Any]],
        timeout_seconds: int,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("Agent execution timeout must be positive")
        self._factory = factory
        self._result_class = result_class
        self._encoder = encoder
        self._timeout_seconds = timeout_seconds

    def execute(
        self, execution: AttemptExecution, cancellation: CancellationToken
    ) -> AttemptObservation:
        if cancellation.cancelled:
            raise AgentExecutionFailure("cancelled", "Attempt execution was cancelled")
        if execution.agent_input is None:
            raise AgentExecutionFailure(
                "missing_input", "Agent execution requires its durable typed Run Input"
            )
        context = get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(
            target=_run_agent_child,
            args=(self._factory, execution.agent_input, sender),
            name="openmagic-agent-attempt",
        )
        process.start()
        sender.close()
        deadline = monotonic() + self._timeout_seconds
        try:
            while process.is_alive() and monotonic() < deadline and not cancellation.cancelled:
                process.join(timeout=0.01)
            if process.is_alive():
                process.terminate()
                process.join(timeout=1)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=1)
                if cancellation.cancelled:
                    raise AgentExecutionFailure("cancelled", "Attempt execution was cancelled")
                raise AgentExecutionFailure(
                    "bounded_timeout", "Agent execution exceeded its bounded timeout"
                )
            if not receiver.poll():
                raise AgentExecutionFailure(
                    "missing_result", "Agent process ended without a candidate"
                )
            try:
                message = receiver.recv()
            except EOFError as error:
                raise AgentExecutionFailure(
                    "missing_result", "Agent process ended without a candidate"
                ) from error
            if message[0] == "failure":
                raise AgentExecutionFailure(message[1], message[2])
            result = message[1]
        finally:
            receiver.close()
            if process.is_alive():
                process.kill()
                process.join(timeout=1)
        if cancellation.cancelled:
            raise AgentExecutionFailure("cancelled", "Attempt execution was cancelled")
        if type(result) is not self._result_class:
            raise AgentExecutionFailure(
                "malformed_result", "Agent returned a candidate outside its typed contract"
            )
        return AttemptObservation(value=self._encoder(result))


__all__ = [
    "AgentExecutionFailure",
    "AgentExecutionFailureReason",
    "AttemptExecution",
    "AttemptObservation",
    "CancellationToken",
    "DeterministicExecutor",
    "ExecutionAuthorityLost",
    "Executor",
    "FreshAgentExecutor",
    "execute_with_renewable_authority",
]
