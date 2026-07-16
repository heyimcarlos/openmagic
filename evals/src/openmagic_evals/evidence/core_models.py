"""Shared, lane-neutral models for canonical enterprise evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

CorrelationValue = TypeVar("CorrelationValue")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def canonical_digest(value: object) -> str:
    document = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(document).hexdigest()


def require_digest(value: str, field: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


class SanitizedObservation(EvidenceModel):
    document: dict[str, JsonValue]
    digest: str

    @model_validator(mode="after")
    def validate_digest(self) -> SanitizedObservation:
        if self.digest != canonical_digest(self.document):
            raise ValueError("sanitized observation digest does not match its canonical document")
        return self


class Correlations(EvidenceModel):
    command_ids: tuple[UUID, ...] = ()
    workflow_ids: tuple[UUID, ...] = ()
    instance_ids: tuple[UUID, ...] = ()
    step_ids: tuple[UUID, ...] = ()
    attempt_ids: tuple[UUID, ...] = ()
    wait_ids: tuple[UUID, ...] = ()
    signal_ids: tuple[UUID, ...] = ()
    trace_event_ids: tuple[UUID, ...] = ()
    thread_ids: tuple[UUID, ...] = ()
    message_ids: tuple[UUID, ...] = ()
    agent_run_ids: tuple[UUID, ...] = ()
    domain_event_ids: tuple[UUID, ...] = ()
    delivery_ids: tuple[UUID, ...] = ()
    delivery_attempt_ids: tuple[UUID, ...] = ()
    external_effect_ids: tuple[UUID, ...] = ()
    approval_grant_ids: tuple[UUID, ...] = ()
    verification_challenge_ids: tuple[UUID, ...] = ()
    verification_session_ids: tuple[UUID, ...] = ()
    worker_ids: tuple[str, ...] = ()
    process_ids: tuple[int, ...] = ()
    provider_request_ids: tuple[str, ...] = ()


def merge_correlations(values: Iterable[Correlations]) -> Correlations:
    items = tuple(values)

    def unique(source: Iterable[CorrelationValue]) -> tuple[CorrelationValue, ...]:
        return tuple(dict.fromkeys(source))

    return Correlations(
        command_ids=unique(value for item in items for value in item.command_ids),
        workflow_ids=unique(value for item in items for value in item.workflow_ids),
        instance_ids=unique(value for item in items for value in item.instance_ids),
        step_ids=unique(value for item in items for value in item.step_ids),
        attempt_ids=unique(value for item in items for value in item.attempt_ids),
        wait_ids=unique(value for item in items for value in item.wait_ids),
        signal_ids=unique(value for item in items for value in item.signal_ids),
        trace_event_ids=unique(value for item in items for value in item.trace_event_ids),
        thread_ids=unique(value for item in items for value in item.thread_ids),
        message_ids=unique(value for item in items for value in item.message_ids),
        agent_run_ids=unique(value for item in items for value in item.agent_run_ids),
        domain_event_ids=unique(value for item in items for value in item.domain_event_ids),
        delivery_ids=unique(value for item in items for value in item.delivery_ids),
        delivery_attempt_ids=unique(value for item in items for value in item.delivery_attempt_ids),
        external_effect_ids=unique(value for item in items for value in item.external_effect_ids),
        approval_grant_ids=unique(value for item in items for value in item.approval_grant_ids),
        verification_challenge_ids=unique(
            value for item in items for value in item.verification_challenge_ids
        ),
        verification_session_ids=unique(
            value for item in items for value in item.verification_session_ids
        ),
        worker_ids=unique(value for item in items for value in item.worker_ids),
        process_ids=unique(value for item in items for value in item.process_ids),
        provider_request_ids=unique(value for item in items for value in item.provider_request_ids),
    )


class CaseVerdict(EvidenceModel):
    status: Literal["passed", "failed", "infrastructure_error", "unavailable"]
    invariant_violations: tuple[str, ...]
    verifier_version: str = "issue-71.v1"

    @model_validator(mode="after")
    def validate_verdict(self) -> CaseVerdict:
        if self.status == "passed" and self.invariant_violations:
            raise ValueError("a passed case cannot contain invariant violations")
        if self.status == "failed" and not self.invariant_violations:
            raise ValueError("a failed case must name an invariant violation")
        return self


class DistributionSummary(EvidenceModel):
    count: int = Field(gt=0)
    mean: float = Field(ge=0)
    median: float = Field(ge=0)
    sample_standard_deviation: float = Field(ge=0)
    minimum: int = Field(ge=0)
    maximum: int = Field(ge=0)


class ArtifactCaseBase(EvidenceModel):
    case_id: str
    case_schema_version: int = Field(gt=0)
    expected_trials: int = Field(gt=0)
    observed_trials: int = Field(ge=0)
    seeds: tuple[int, ...]
    correlations: Correlations
    observation_digests: tuple[str, ...]
    verdict: CaseVerdict

    @model_validator(mode="after")
    def validate_denominator(self) -> ArtifactCaseBase:
        if self.observed_trials != self.expected_trials:
            raise ValueError("observed trials must equal the predeclared expected trials")
        if len(self.seeds) != self.observed_trials:
            raise ValueError("one recorded seed is required for every observed trial")
        if len(self.observation_digests) != self.observed_trials:
            raise ValueError("one observation digest is required for every observed trial")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("trial seeds must be unique")
        for digest in self.observation_digests:
            require_digest(digest, "observation_digest")
        return self
