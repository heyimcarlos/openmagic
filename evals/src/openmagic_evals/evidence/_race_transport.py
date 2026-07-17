"""Validated wire envelopes for fresh-process race contenders."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Generic, Literal, TypeGuard, TypeVar

from openmagic_runtime.kernel.control import SignalConflict

from openmagic_evals.evidence._race_operations import (
    RaceProtocolError,
    RaceRequest,
    validate_race_request,
)


class RaceFailureKind(StrEnum):
    SIGNAL_CONFLICT = "signal_conflict"
    UNEXPECTED_EXCEPTION = "unexpected_exception"


class RaceFailureReason(StrEnum):
    INSTANCE_NOT_FOUND = "instance_not_found"
    WAIT_NOT_FOUND = "wait_not_found"
    WAIT_ALREADY_SATISFIED = "wait_already_satisfied"
    IDENTITY_REUSED = "identity_reused"
    UNCLASSIFIED = "unclassified"


_SIGNAL_FAILURE_REASONS = frozenset(
    {
        RaceFailureReason.INSTANCE_NOT_FOUND,
        RaceFailureReason.WAIT_NOT_FOUND,
        RaceFailureReason.WAIT_ALREADY_SATISFIED,
        RaceFailureReason.IDENTITY_REUSED,
    }
)


@dataclass(frozen=True)
class ProcessRaceFailure:
    kind: RaceFailureKind
    reason: RaceFailureReason
    message: str

    def __post_init__(self) -> None:
        self.validate()

    def validate(self) -> None:
        if not isinstance(self.kind, RaceFailureKind):
            raise RaceProtocolError("race failure kind is outside the stable protocol")
        if not isinstance(self.reason, RaceFailureReason):
            raise RaceProtocolError("race failure reason is outside the stable protocol")
        if not isinstance(self.message, str):
            raise RaceProtocolError("race failure message must be text")
        if self.kind is RaceFailureKind.SIGNAL_CONFLICT:
            if self.reason not in _SIGNAL_FAILURE_REASONS:
                raise RaceProtocolError("Signal conflict has an invalid failure reason")
        elif self.reason is not RaceFailureReason.UNCLASSIFIED:
            raise RaceProtocolError("unexpected exception has an invalid failure reason")

    @classmethod
    def capture(cls, error: BaseException) -> ProcessRaceFailure:
        if isinstance(error, SignalConflict):
            try:
                reason = RaceFailureReason(error.reason.value)
            except ValueError:
                return cls(
                    kind=RaceFailureKind.UNEXPECTED_EXCEPTION,
                    reason=RaceFailureReason.UNCLASSIFIED,
                    message=str(error),
                )
            return cls(
                kind=RaceFailureKind.SIGNAL_CONFLICT,
                reason=reason,
                message=str(error),
            )
        return cls(
            kind=RaceFailureKind.UNEXPECTED_EXCEPTION,
            reason=RaceFailureReason.UNCLASSIFIED,
            message=str(error),
        )


ResultT = TypeVar("ResultT")


class RaceBarrierStage(StrEnum):
    WAITING = "waiting"
    ACQUIRED = "acquired"


class RaceControl(StrEnum):
    PROCEED = "proceed"


@dataclass(frozen=True)
class ProcessRaceSessionReady:
    process_id: int


@dataclass(frozen=True)
class ProcessRaceReady:
    stage: RaceBarrierStage
    process_id: int
    backend_id: int


@dataclass(frozen=True)
class ProcessRaceSucceeded(Generic[ResultT]):
    value: ResultT


@dataclass(frozen=True)
class ProcessRaceFailed:
    failure: ProcessRaceFailure


@dataclass(frozen=True)
class ProcessRaceCompleted(Generic[ResultT]):
    process_id: int
    outcome: ProcessRaceSucceeded[ResultT] | ProcessRaceFailed


@dataclass(frozen=True)
class ProcessRaceFatal:
    process_id: int
    failure: ProcessRaceFailure


@dataclass(frozen=True)
class ProcessRaceResult(Generic[ResultT]):
    process_id: int
    outcome: ProcessRaceSucceeded[ResultT] | ProcessRaceFailed

    @property
    def failure(self) -> ProcessRaceFailure | None:
        if isinstance(self.outcome, ProcessRaceFailed):
            return self.outcome.failure
        return None

    def require_value(self) -> ResultT:
        if isinstance(self.outcome, ProcessRaceFailed):
            raise RuntimeError(
                "race contender failed through its public operation: "
                f"{self.outcome.failure.kind.value}/{self.outcome.failure.reason.value}"
            )
        return self.outcome.value


@dataclass(frozen=True)
class ProcessRacePair(Generic[ResultT]):
    results: tuple[ProcessRaceResult[ResultT], ProcessRaceResult[ResultT]]
    overlap_barrier_observed: Literal[True]

    def __post_init__(self) -> None:
        if self.overlap_barrier_observed is not True:
            raise RaceProtocolError("race pair requires an observed overlap barrier")

    @property
    def process_ids(self) -> tuple[int, int]:
        return self.results[0].process_id, self.results[1].process_id


def _valid_process_id(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def decode_ready(message: object, expected: RaceBarrierStage) -> ProcessRaceReady:
    if type(message) is not ProcessRaceReady:
        raise RaceProtocolError(f"race contender sent an invalid {expected.value} envelope")
    if not isinstance(message.stage, RaceBarrierStage):
        raise RaceProtocolError("race contender sent an invalid barrier stage")
    if message.stage is not expected:
        raise RaceProtocolError(
            f"race contender sent {message.stage.value} while {expected.value} was required"
        )
    if not _valid_process_id(message.process_id) or not _valid_process_id(message.backend_id):
        raise RaceProtocolError("race contender sent invalid process identities")
    return message


def decode_session_ready(message: object) -> ProcessRaceSessionReady:
    if type(message) is not ProcessRaceSessionReady:
        raise RaceProtocolError("race contender sent an invalid process-session envelope")
    if not _valid_process_id(message.process_id):
        raise RaceProtocolError("race contender sent an invalid process-session identity")
    return message


def decode_process_result(
    request: RaceRequest[ResultT], message: object
) -> ProcessRaceResult[ResultT]:
    validate_race_request(request)
    if type(message) is not ProcessRaceCompleted:
        raise RaceProtocolError("race contender sent an invalid completion envelope")
    if not _valid_process_id(message.process_id):
        raise RaceProtocolError("race contender sent an invalid process identity")
    outcome = message.outcome
    if type(outcome) is ProcessRaceFailed:
        if type(outcome.failure) is not ProcessRaceFailure:
            raise RaceProtocolError("race contender sent an invalid failure envelope")
        outcome.failure.validate()
        return ProcessRaceResult(process_id=message.process_id, outcome=outcome)
    if type(outcome) is not ProcessRaceSucceeded:
        raise RaceProtocolError("race contender sent an invalid result outcome")
    value = request.decode_result(outcome.value)
    return ProcessRaceResult(
        process_id=message.process_id,
        outcome=ProcessRaceSucceeded(value),
    )


def decode_fatal(message: object) -> ProcessRaceFatal:
    if type(message) is not ProcessRaceFatal:
        raise RaceProtocolError("race contender sent an invalid fatal envelope")
    if not _valid_process_id(message.process_id):
        raise RaceProtocolError("race contender sent an invalid process identity")
    if type(message.failure) is not ProcessRaceFailure:
        raise RaceProtocolError("race contender sent an invalid fatal failure")
    message.failure.validate()
    return message


__all__ = [
    "ProcessRaceCompleted",
    "ProcessRaceFailed",
    "ProcessRaceFailure",
    "ProcessRaceFatal",
    "ProcessRacePair",
    "ProcessRaceReady",
    "ProcessRaceResult",
    "ProcessRaceSessionReady",
    "ProcessRaceSucceeded",
    "RaceBarrierStage",
    "RaceControl",
    "RaceFailureKind",
    "RaceFailureReason",
    "decode_fatal",
    "decode_process_result",
    "decode_ready",
    "decode_session_ready",
]
