"""Shared, lane-neutral models for canonical enterprise evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Literal, TypeVar
from uuid import UUID

from openmagic_runtime.kernel.definitions import DefinitionIdentity
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

CorrelationValue = TypeVar("CorrelationValue")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_STABLE_DEFINITION_KEY = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


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


class InstanceDefinitionCorrelation(EvidenceModel):
    """One durable Instance pinned to its exact registered Definition identity."""

    instance_id: UUID
    definition_key: str = Field(min_length=1)
    definition_version: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_identity(self) -> InstanceDefinitionCorrelation:
        if _STABLE_DEFINITION_KEY.fullmatch(self.definition_key) is None:
            raise ValueError("Definition correlation key must use the stable key grammar")
        return self

    @classmethod
    def from_identity(
        cls,
        instance_id: UUID,
        identity: DefinitionIdentity,
    ) -> InstanceDefinitionCorrelation:
        return cls(
            instance_id=instance_id,
            definition_key=identity.key,
            definition_version=identity.version,
        )

    @property
    def digest_key(self) -> str:
        return f"{self.definition_key}:{self.definition_version}"


class RuntimeCorrelations(EvidenceModel):
    command_ids: tuple[UUID, ...] = ()
    workflow_ids: tuple[UUID, ...] = ()
    instance_ids: tuple[UUID, ...] = ()
    step_ids: tuple[UUID, ...] = ()
    attempt_ids: tuple[UUID, ...] = ()
    wait_ids: tuple[UUID, ...] = ()
    signal_ids: tuple[UUID, ...] = ()
    trace_event_ids: tuple[UUID, ...] = ()
    instance_definitions: tuple[InstanceDefinitionCorrelation, ...] = ()

    @model_validator(mode="after")
    def validate_instance_definitions(self) -> RuntimeCorrelations:
        mapped_ids = tuple(item.instance_id for item in self.instance_definitions)
        if len(self.instance_ids) != len(set(self.instance_ids)):
            raise ValueError("runtime Instance identities must be unique")
        if len(mapped_ids) != len(set(mapped_ids)):
            raise ValueError("an Instance can correlate to only one Definition identity")
        if set(mapped_ids) != set(self.instance_ids):
            raise ValueError("every observed Instance must retain its exact Definition identity")
        return self


def validate_correlated_definitions(
    correlations: Iterable[Correlations],
    definition_digests: Mapping[str, str],
) -> None:
    missing = sorted(
        {
            item.digest_key
            for correlation in correlations
            for item in correlation.runtime.instance_definitions
            if item.digest_key not in definition_digests
        }
    )
    if missing:
        raise ValueError(f"Instance correlations reference unpinned Definitions: {missing}")


class ApplicationCorrelations(EvidenceModel):
    thread_ids: tuple[UUID, ...] = ()
    message_ids: tuple[UUID, ...] = ()
    domain_event_ids: tuple[UUID, ...] = ()
    delivery_ids: tuple[UUID, ...] = ()
    delivery_attempt_ids: tuple[UUID, ...] = ()
    external_effect_ids: tuple[UUID, ...] = ()
    approval_grant_ids: tuple[UUID, ...] = ()
    verification_challenge_ids: tuple[UUID, ...] = ()
    verification_session_ids: tuple[UUID, ...] = ()


class AgentCorrelations(EvidenceModel):
    agent_run_ids: tuple[UUID, ...] = ()


class ProcessCorrelations(EvidenceModel):
    worker_ids: tuple[str, ...] = ()
    process_ids: tuple[int, ...] = ()


class ProviderCorrelations(EvidenceModel):
    provider_request_ids: tuple[str, ...] = ()


class Correlations(EvidenceModel):
    runtime: RuntimeCorrelations = Field(default_factory=RuntimeCorrelations)
    application: ApplicationCorrelations = Field(default_factory=ApplicationCorrelations)
    agent: AgentCorrelations = Field(default_factory=AgentCorrelations)
    process: ProcessCorrelations = Field(default_factory=ProcessCorrelations)
    provider: ProviderCorrelations = Field(default_factory=ProviderCorrelations)


def has_correlations(value: Correlations) -> bool:
    return any(
        identities
        for group in (
            value.runtime,
            value.application,
            value.agent,
            value.process,
            value.provider,
        )
        for identities in group.model_dump(mode="python").values()
    )


def merge_correlations(values: Iterable[Correlations]) -> Correlations:
    items = tuple(values)

    def unique(source: Iterable[CorrelationValue]) -> tuple[CorrelationValue, ...]:
        return tuple(dict.fromkeys(source))

    return Correlations(
        runtime=RuntimeCorrelations(
            command_ids=unique(value for item in items for value in item.runtime.command_ids),
            workflow_ids=unique(value for item in items for value in item.runtime.workflow_ids),
            instance_ids=unique(value for item in items for value in item.runtime.instance_ids),
            step_ids=unique(value for item in items for value in item.runtime.step_ids),
            attempt_ids=unique(value for item in items for value in item.runtime.attempt_ids),
            wait_ids=unique(value for item in items for value in item.runtime.wait_ids),
            signal_ids=unique(value for item in items for value in item.runtime.signal_ids),
            trace_event_ids=unique(
                value for item in items for value in item.runtime.trace_event_ids
            ),
            instance_definitions=unique(
                value for item in items for value in item.runtime.instance_definitions
            ),
        ),
        application=ApplicationCorrelations(
            thread_ids=unique(value for item in items for value in item.application.thread_ids),
            message_ids=unique(value for item in items for value in item.application.message_ids),
            domain_event_ids=unique(
                value for item in items for value in item.application.domain_event_ids
            ),
            delivery_ids=unique(value for item in items for value in item.application.delivery_ids),
            delivery_attempt_ids=unique(
                value for item in items for value in item.application.delivery_attempt_ids
            ),
            external_effect_ids=unique(
                value for item in items for value in item.application.external_effect_ids
            ),
            approval_grant_ids=unique(
                value for item in items for value in item.application.approval_grant_ids
            ),
            verification_challenge_ids=unique(
                value for item in items for value in item.application.verification_challenge_ids
            ),
            verification_session_ids=unique(
                value for item in items for value in item.application.verification_session_ids
            ),
        ),
        agent=AgentCorrelations(
            agent_run_ids=unique(value for item in items for value in item.agent.agent_run_ids),
        ),
        process=ProcessCorrelations(
            worker_ids=unique(value for item in items for value in item.process.worker_ids),
            process_ids=unique(value for item in items for value in item.process.process_ids),
        ),
        provider=ProviderCorrelations(
            provider_request_ids=unique(
                value for item in items for value in item.provider.provider_request_ids
            ),
        ),
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


__all__ = [
    "AgentCorrelations",
    "ApplicationCorrelations",
    "ArtifactCaseBase",
    "CaseVerdict",
    "Correlations",
    "DistributionSummary",
    "EvidenceModel",
    "InstanceDefinitionCorrelation",
    "ProcessCorrelations",
    "ProviderCorrelations",
    "RuntimeCorrelations",
    "SanitizedObservation",
    "canonical_digest",
    "has_correlations",
    "merge_correlations",
    "require_digest",
    "validate_correlated_definitions",
]
