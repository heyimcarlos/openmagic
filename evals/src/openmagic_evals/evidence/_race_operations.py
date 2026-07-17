"""Closed typed operations executed by fresh race contenders."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeGuard, TypeVar
from uuid import UUID

import psycopg
from example_insurance.renewals import (
    ExampleInsurance,
    StartRenewalOutreach,
    StartRenewalOutreachResult,
    SubmitVerificationCode,
    SubmitVerificationCodeResult,
)
from openmagic_runtime.commands import CommandReceipt
from openmagic_runtime.delivery import ClaimedDelivery
from openmagic_runtime.kernel.control import AcceptSignal, KernelControl, SignalReceipt
from openmagic_runtime.kernel.work import ClaimedAttempt, DispositionRequired, KernelWork


class RaceProtocolError(RuntimeError):
    """Raised when a process sends data outside the closed race protocol."""


ResultT = TypeVar("ResultT")
ResultT_co = TypeVar("ResultT_co", covariant=True)


class RaceRequest(Protocol[ResultT_co]):
    """One typed operation whose result decoder guards the process boundary."""

    def perform(self, database_url: str) -> ResultT_co: ...

    def decode_result(self, value: object) -> ResultT_co: ...

    def validate(self) -> None: ...


def _require_uuid(value: object, field: str) -> None:
    if not isinstance(value, UUID):
        raise RaceProtocolError(f"{field} must be a UUID")


def _require_worker(value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        raise RaceProtocolError("race worker ID must be non-empty text")


def _require_observation(value: object) -> None:
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in value.items()
    ):
        raise RaceProtocolError("race observation must contain text keys and values")


def _is_start_receipt(
    value: object,
) -> TypeGuard[CommandReceipt[StartRenewalOutreachResult]]:
    return type(value) is CommandReceipt and type(value.result) is StartRenewalOutreachResult


def _is_verification_receipt(
    value: object,
) -> TypeGuard[CommandReceipt[SubmitVerificationCodeResult]]:
    return type(value) is CommandReceipt and type(value.result) is SubmitVerificationCodeResult


@dataclass(frozen=True)
class StartRenewalOutreachRace:
    command: StartRenewalOutreach

    def validate(self) -> None:
        if type(self.command) is not StartRenewalOutreach:
            raise RaceProtocolError("renewal race requires a StartRenewalOutreach command")

    def perform(self, database_url: str) -> CommandReceipt[StartRenewalOutreachResult]:
        self.validate()
        return ExampleInsurance(database_url=database_url).start_renewal_outreach(self.command)

    def decode_result(self, value: object) -> CommandReceipt[StartRenewalOutreachResult]:
        if not _is_start_receipt(value):
            raise RaceProtocolError("renewal race returned an invalid Command receipt")
        return value


@dataclass(frozen=True)
class StepClaimRace:
    worker_id: str
    claim_request_id: UUID

    def validate(self) -> None:
        _require_worker(self.worker_id)
        _require_uuid(self.claim_request_id, "Step claim request ID")

    def perform(self, database_url: str) -> ClaimedAttempt | None:
        self.validate()
        return ExampleInsurance(database_url=database_url).claim_workflow_attempt(
            worker_id=self.worker_id,
            claim_request_id=self.claim_request_id,
        )

    def decode_result(self, value: object) -> ClaimedAttempt | None:
        if value is not None and type(value) is not ClaimedAttempt:
            raise RaceProtocolError("Step claim race returned an invalid claim")
        return value


@dataclass(frozen=True)
class DeliveryClaimRace:
    worker_id: str
    claim_request_id: UUID

    def validate(self) -> None:
        _require_worker(self.worker_id)
        _require_uuid(self.claim_request_id, "Delivery claim request ID")

    def perform(self, database_url: str) -> ClaimedDelivery | None:
        self.validate()
        return ExampleInsurance(database_url=database_url).claim_delivery_attempt(
            worker_id=self.worker_id,
            claim_request_id=self.claim_request_id,
        )

    def decode_result(self, value: object) -> ClaimedDelivery | None:
        if value is not None and type(value) is not ClaimedDelivery:
            raise RaceProtocolError("Delivery claim race returned an invalid claim")
        return value


@dataclass(frozen=True)
class VerificationSubmissionRace:
    command: SubmitVerificationCode
    secret: bytes

    def validate(self) -> None:
        if type(self.command) is not SubmitVerificationCode:
            raise RaceProtocolError("verification race requires a typed command")
        if not isinstance(self.secret, bytes) or not self.secret:
            raise RaceProtocolError("verification race requires a non-empty secret")

    def perform(self, database_url: str) -> CommandReceipt[SubmitVerificationCodeResult]:
        self.validate()
        return ExampleInsurance(
            database_url=database_url,
            verification_code_secret=self.secret,
        ).submit_verification_code(self.command)

    def decode_result(self, value: object) -> CommandReceipt[SubmitVerificationCodeResult]:
        if not _is_verification_receipt(value):
            raise RaceProtocolError("verification race returned an invalid Command receipt")
        return value


@dataclass(frozen=True)
class AcceptSignalRace:
    request: AcceptSignal

    def validate(self) -> None:
        if type(self.request) is not AcceptSignal:
            raise RaceProtocolError("Signal race requires an AcceptSignal request")

    def perform(self, database_url: str) -> SignalReceipt:
        self.validate()
        with psycopg.connect(database_url) as connection, connection.transaction():
            return KernelControl(connection).accept_signal(self.request)

    def decode_result(self, value: object) -> SignalReceipt:
        if type(value) is not SignalReceipt:
            raise RaceProtocolError("Signal race returned an invalid Signal receipt")
        return value


@dataclass(frozen=True)
class AttemptResultRace:
    claim: ClaimedAttempt
    worker_id: str
    observation: dict[str, str]

    def validate(self) -> None:
        if type(self.claim) is not ClaimedAttempt:
            raise RaceProtocolError("Attempt result race requires a typed claim")
        _require_worker(self.worker_id)
        _require_observation(self.observation)

    def perform(self, database_url: str) -> DispositionRequired:
        self.validate()
        with psycopg.connect(database_url) as connection, connection.transaction():
            return KernelWork(connection).accept_result(
                self.claim,
                worker_id=self.worker_id,
                observation=self.observation,
            )

    def decode_result(self, value: object) -> DispositionRequired:
        if type(value) is not DispositionRequired:
            raise RaceProtocolError("Attempt result race returned an invalid disposition")
        return value


@dataclass(frozen=True)
class RouteActivationRaceResult:
    steps: dict[str, UUID]
    waits: dict[str, UUID]
    replayed: bool


@dataclass(frozen=True)
class RouteActivationRace:
    claim: ClaimedAttempt
    worker_id: str
    observation: dict[str, str]

    def validate(self) -> None:
        if type(self.claim) is not ClaimedAttempt:
            raise RaceProtocolError("Route activation race requires a typed claim")
        _require_worker(self.worker_id)
        _require_observation(self.observation)

    def perform(self, database_url: str) -> RouteActivationRaceResult:
        self.validate()
        with psycopg.connect(database_url) as connection, connection.transaction():
            required = KernelWork(connection).accept_result(
                self.claim,
                worker_id=self.worker_id,
                observation=self.observation,
            )
            steps, waits = KernelControl(connection).succeed(
                required,
                output=self.observation,
                outcome_route="finish_after_origin",
                route_input=self.observation,
            )
        return RouteActivationRaceResult(
            steps=dict(steps),
            waits=dict(waits),
            replayed=required.replayed,
        )

    def decode_result(self, value: object) -> RouteActivationRaceResult:
        if type(value) is not RouteActivationRaceResult:
            raise RaceProtocolError("Route activation race returned an invalid receipt")
        if not isinstance(value.steps, dict) or not all(
            isinstance(key, str) and isinstance(item, UUID) for key, item in value.steps.items()
        ):
            raise RaceProtocolError("Route activation Step receipt is invalid")
        if not isinstance(value.waits, dict) or not all(
            isinstance(key, str) and isinstance(item, UUID) for key, item in value.waits.items()
        ):
            raise RaceProtocolError("Route activation Wait receipt is invalid")
        if not isinstance(value.replayed, bool):
            raise RaceProtocolError("Route activation replay flag is invalid")
        return value


RaceRequestValue = (
    StartRenewalOutreachRace
    | StepClaimRace
    | DeliveryClaimRace
    | VerificationSubmissionRace
    | AcceptSignalRace
    | AttemptResultRace
    | RouteActivationRace
)

_RACE_REQUEST_TYPES = (
    StartRenewalOutreachRace,
    StepClaimRace,
    DeliveryClaimRace,
    VerificationSubmissionRace,
    AcceptSignalRace,
    AttemptResultRace,
    RouteActivationRace,
)


def _is_race_request(request: object) -> TypeGuard[RaceRequestValue]:
    return type(request) in _RACE_REQUEST_TYPES


def validate_race_request(request: object) -> RaceRequestValue:
    if not _is_race_request(request):
        raise RaceProtocolError("race request kind is outside the closed protocol")
    request.validate()
    return request


def validate_race_pair(
    requests: tuple[RaceRequest[ResultT], RaceRequest[ResultT]],
) -> None:
    first, second = requests
    validate_race_request(first)
    validate_race_request(second)
    if type(first) is not type(second):
        raise RaceProtocolError("race contenders must use the same typed operation")


__all__ = [
    "AcceptSignalRace",
    "AttemptResultRace",
    "DeliveryClaimRace",
    "RaceProtocolError",
    "RaceRequest",
    "RaceRequestValue",
    "RouteActivationRace",
    "RouteActivationRaceResult",
    "StartRenewalOutreachRace",
    "StepClaimRace",
    "VerificationSubmissionRace",
    "validate_race_pair",
    "validate_race_request",
]
