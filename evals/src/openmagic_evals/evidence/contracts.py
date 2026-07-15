"""Canonical enterprise evidence contracts owned by the private eval package."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

SCHEMA_VERSION = "openmagic.enterprise-evidence.v1"
REQUIRED_NEGATIVE_CLAIMS = (
    "No exactly-once External Effect guarantee.",
    "No production SLO, availability, throughput, or fleet-scale guarantee.",
    "No correctness claim for multiple databases.",
    "No arbitrary durable Python guarantee.",
    "No parity claim with mature workflow engines.",
)

_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")
_GIT_SHA = re.compile(r"[0-9a-f]{40}")


class EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class BuildPin(EvidenceModel):
    git_sha: str
    checkout_clean: bool
    lock_digest: str
    distributions: dict[str, str]

    @model_validator(mode="after")
    def validate_build(self) -> BuildPin:
        if _GIT_SHA.fullmatch(self.git_sha) is None:
            raise ValueError("git_sha must be a full lowercase Git SHA")
        if not self.checkout_clean:
            raise ValueError("admissible evidence requires a clean checkout")
        _require_digest(self.lock_digest, "lock_digest")
        if not self.distributions:
            raise ValueError("distribution versions must be pinned")
        return self


class ReproducibilityPin(EvidenceModel):
    build: BuildPin
    suite_version: str
    command: tuple[str, ...]
    environment_allowlist: tuple[str, ...]
    started_at: datetime
    finished_at: datetime
    timeout_seconds: int = Field(gt=0)
    postgres_version: str
    postgres_configuration_digest: str
    migration_heads: dict[str, str]
    definition_digests: dict[str, str]
    case_corpus_digest: str | None = None
    sandbox_digest: str | None = None

    @model_validator(mode="after")
    def validate_reproducibility(self) -> ReproducibilityPin:
        if not self.suite_version or not self.command:
            raise ValueError("suite version and exact command are required")
        if self.finished_at < self.started_at:
            raise ValueError("finished_at cannot precede started_at")
        _require_digest(self.postgres_configuration_digest, "postgres_configuration_digest")
        if self.case_corpus_digest is not None:
            _require_digest(self.case_corpus_digest, "case_corpus_digest")
        if self.sandbox_digest is not None:
            _require_digest(self.sandbox_digest, "sandbox_digest")
        if not self.migration_heads or not self.definition_digests:
            raise ValueError("migration heads and Definition digests are required")
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


class QueueDepth(EvidenceModel):
    pending_steps: int = Field(ge=0)
    pending_deliveries: int = Field(ge=0)


class ProcessMetrics(EvidenceModel):
    queued_workflows: int = Field(gt=0)
    initial_queue: QueueDepth
    drained_queue: QueueDepth
    initial_capacity: dict[Literal["api", "workflow-worker", "delivery-worker"], int]
    started_processes: dict[Literal["api", "workflow-worker", "delivery-worker"], int]
    forced_losses: dict[Literal["workflow-worker", "delivery-worker"], int]
    fresh_interpreters: Literal[True]
    postgresql_only_reconstruction: Literal[True]
    elapsed_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_process_evidence(self) -> ProcessMetrics:
        roles = {"api", "workflow-worker", "delivery-worker"}
        if set(self.initial_capacity) != roles or set(self.started_processes) != roles:
            raise ValueError("process evidence must report every independent role")
        if set(self.forced_losses) != {"workflow-worker", "delivery-worker"}:
            raise ValueError("process evidence must report both forced Worker losses")
        if self.initial_queue.pending_steps != self.queued_workflows:
            raise ValueError("initial Step queue must match the submitted Workflow denominator")
        if self.drained_queue.pending_steps or self.drained_queue.pending_deliveries:
            raise ValueError("process evidence must finish with both durable queues drained")
        return self


class ArtifactCase(EvidenceModel):
    case_id: str
    case_schema_version: int = Field(gt=0)
    split: Literal["development", "held_out"] | None = None
    expected_trials: int = Field(gt=0)
    observed_trials: int = Field(ge=0)
    seeds: tuple[int, ...]
    correlations: Correlations
    observation_digests: tuple[str, ...]
    verdict: CaseVerdict
    process_metrics: ProcessMetrics | None = None

    @model_validator(mode="after")
    def validate_denominator(self) -> ArtifactCase:
        if self.observed_trials != self.expected_trials:
            raise ValueError("observed trials must equal the predeclared expected trials")
        if len(self.seeds) != self.observed_trials:
            raise ValueError("one recorded seed is required for every observed trial")
        if len(self.observation_digests) != self.observed_trials:
            raise ValueError("one observation digest is required for every observed trial")
        if len(set(self.seeds)) != len(self.seeds):
            raise ValueError("trial seeds must be unique")
        for digest in self.observation_digests:
            _require_digest(digest, "observation_digest")
        return self


class DeterministicSummary(EvidenceModel):
    expected_cases: int = Field(ge=0)
    observed_cases: int = Field(ge=0)
    passed_cases: int = Field(ge=0)
    failed_cases: int = Field(ge=0)
    infrastructure_errors: int = Field(ge=0)
    invariant_violations: int = Field(ge=0)
    strict_pass: bool


class AgentQualitySummary(EvidenceModel):
    development_cases: int = Field(ge=0)
    held_out_cases: int = Field(ge=0)
    expected_trials: int = Field(ge=0)
    observed_trials: int = Field(ge=0)
    passed_trials: int = Field(ge=0)
    prohibited_actions: int = Field(ge=0)
    threshold_passed: bool
    deterministic_release_pass: bool | None = None
    pass_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    wilson_lower: float = Field(default=0.0, ge=0.0, le=1.0)
    wilson_upper: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def keep_quality_separate(self) -> AgentQualitySummary:
        if self.deterministic_release_pass is not None:
            raise ValueError("Agent quality cannot determine deterministic release correctness")
        if self.observed_trials != self.expected_trials:
            raise ValueError("Agent quality must report the complete trial denominator")
        if self.passed_trials > self.observed_trials:
            raise ValueError("passed trials cannot exceed observed trials")
        return self


class AgentConfigurationPin(EvidenceModel):
    agent_key: str
    agent_version: int = Field(gt=0)
    instruction_digest: str
    tool_schema_digest: str
    provider: str
    model: str
    reasoning: str
    temperature: float

    @model_validator(mode="after")
    def validate_agent_pin(self) -> AgentConfigurationPin:
        _require_digest(self.instruction_digest, "instruction_digest")
        _require_digest(self.tool_schema_digest, "tool_schema_digest")
        return self


class AvailabilitySummary(EvidenceModel):
    attempted: bool
    available: bool
    reversible: bool

    @model_validator(mode="after")
    def validate_availability(self) -> AvailabilitySummary:
        if self.available and not self.attempted:
            raise ValueError("an unattempted live smoke cannot report availability")
        if self.attempted and not self.reversible:
            raise ValueError("live smoke input must be reversible")
        return self


class LiveProviderPin(EvidenceModel):
    provider: str
    model: str
    endpoint_digest: str
    configuration_digest: str
    synthetic_case_id: str
    reversible: Literal[True]

    @model_validator(mode="after")
    def validate_live_pin(self) -> LiveProviderPin:
        _require_digest(self.endpoint_digest, "endpoint_digest")
        _require_digest(self.configuration_digest, "configuration_digest")
        return self


class PlaygroundSummary(EvidenceModel):
    synthetic_data_only: Literal[True]
    effects_enabled_by_default: Literal[False]
    local_provider: Literal[True]
    reset_verified: bool
    process_controls_verified: bool
    contributes_to_correctness: Literal[False]


class DeterministicArtifact(EvidenceModel):
    schema_version: Literal["openmagic.enterprise-evidence.v1"] = SCHEMA_VERSION
    artifact_kind: Literal["deterministic_release"] = "deterministic_release"
    lane: Literal["deterministic_correctness"] = "deterministic_correctness"
    reproducibility: ReproducibilityPin
    cases: tuple[ArtifactCase, ...]
    summary: DeterministicSummary
    limitations: tuple[str, ...]
    negative_claims: tuple[str, ...]

    @model_validator(mode="after")
    def validate_release(self) -> DeterministicArtifact:
        statuses = [case.verdict.status for case in self.cases]
        violations = sum(len(case.verdict.invariant_violations) for case in self.cases)
        expected = len(self.cases)
        counts_match = (
            self.summary.expected_cases == expected
            and self.summary.observed_cases == expected
            and self.summary.passed_cases == statuses.count("passed")
            and self.summary.failed_cases == statuses.count("failed")
            and self.summary.infrastructure_errors == statuses.count("infrastructure_error")
            and self.summary.invariant_violations == violations
        )
        if not counts_match:
            raise ValueError("deterministic summary does not match its complete case denominator")
        should_pass = all(status == "passed" for status in statuses) and violations == 0
        if self.summary.strict_pass != should_pass:
            raise ValueError("strict deterministic verdict does not match case outcomes")
        missing = set(REQUIRED_NEGATIVE_CLAIMS).difference(self.negative_claims)
        if missing:
            raise ValueError("final report is missing required negative claims")
        return self


class AgentQualityArtifact(EvidenceModel):
    schema_version: Literal["openmagic.enterprise-evidence.v1"] = SCHEMA_VERSION
    artifact_kind: Literal["agent_quality"] = "agent_quality"
    lane: Literal["agent_quality"] = "agent_quality"
    reproducibility: ReproducibilityPin
    agent_configuration: AgentConfigurationPin
    cases: tuple[ArtifactCase, ...]
    summary: AgentQualitySummary
    limitations: tuple[str, ...]


class LiveSmokeArtifact(EvidenceModel):
    schema_version: Literal["openmagic.enterprise-evidence.v1"] = SCHEMA_VERSION
    artifact_kind: Literal["live_smoke"] = "live_smoke"
    lane: Literal["provider_availability"] = "provider_availability"
    reproducibility: ReproducibilityPin
    provider_configuration: LiveProviderPin
    cases: tuple[ArtifactCase, ...]
    summary: AvailabilitySummary
    limitations: tuple[str, ...]


class PlaygroundArtifact(EvidenceModel):
    schema_version: Literal["openmagic.enterprise-evidence.v1"] = SCHEMA_VERSION
    artifact_kind: Literal["playground"] = "playground"
    lane: Literal["demonstration"] = "demonstration"
    reproducibility: ReproducibilityPin
    cases: tuple[ArtifactCase, ...]
    summary: PlaygroundSummary
    limitations: tuple[str, ...]


Artifact = Annotated[
    DeterministicArtifact | AgentQualityArtifact | LiveSmokeArtifact | PlaygroundArtifact,
    Field(discriminator="artifact_kind"),
]
_ARTIFACT_ADAPTER = TypeAdapter(Artifact)


def parse_artifact(document: str | bytes) -> Artifact:
    return _ARTIFACT_ADAPTER.validate_json(document)


def canonical_artifact_json(artifact: Artifact) -> str:
    value = artifact.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def artifact_json_schema() -> dict[str, object]:
    return _ARTIFACT_ADAPTER.json_schema()


def _require_digest(value: str, field: str) -> None:
    if _SHA256.fullmatch(value) is None:
        raise ValueError(f"{field} must be a lowercase SHA-256 digest")


__all__ = [
    "REQUIRED_NEGATIVE_CLAIMS",
    "SCHEMA_VERSION",
    "AgentConfigurationPin",
    "AgentQualityArtifact",
    "AgentQualitySummary",
    "Artifact",
    "ArtifactCase",
    "AvailabilitySummary",
    "BuildPin",
    "CaseVerdict",
    "Correlations",
    "DeterministicArtifact",
    "DeterministicSummary",
    "LiveProviderPin",
    "LiveSmokeArtifact",
    "PlaygroundArtifact",
    "PlaygroundSummary",
    "ProcessMetrics",
    "QueueDepth",
    "ReproducibilityPin",
    "artifact_json_schema",
    "canonical_artifact_json",
    "parse_artifact",
]
