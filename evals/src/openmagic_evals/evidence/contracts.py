"""Canonical enterprise evidence contracts owned by the private eval package."""

from __future__ import annotations

import hashlib
import json
import math
import statistics
from typing import Annotated, Literal

from pydantic import Field, JsonValue, TypeAdapter, model_validator

from openmagic_evals.evidence.agent_scoring import (
    AgentCandidateObservation,
    AgentScorerContract,
    BoundaryAgentCandidateObservation,
    BoundaryAgentScorerContract,
    RenewalAgentCandidateObservation,
    RenewalAgentScorerContract,
    agent_rubric_scores,
)
from openmagic_evals.evidence.core_models import (
    ArtifactCaseBase,
    CaseVerdict,
    Correlations,
    DistributionSummary,
    EvidenceModel,
    SanitizedObservation,
    canonical_digest,
    merge_correlations,
    require_digest,
)
from openmagic_evals.evidence.pins import BuildPin, ReproducibilityPin, WheelArchivePin
from openmagic_evals.evidence.process_models import (
    AttemptAuthorityEvidence,
    DeliveryAuthorityEvidence,
    ForcedProcessLoss,
    ProcessCase,
    ProcessContract,
    ProcessIdentityEvidence,
    ProcessMetrics,
    ProcessObservation,
    QueueDepth,
)
from openmagic_evals.evidence.race_models import RaceCase, RaceTrialEvidence, race_trial_digest
from openmagic_evals.evidence.surface_models import (
    ColdSchemaEvidence,
    InstalledSurfaceEvidence,
    RepositorySurfaceEvidence,
    SurfaceAuditSummary,
)

SCHEMA_VERSION = "openmagic.enterprise-evidence.v1"
REQUIRED_NEGATIVE_CLAIMS = (
    "No exactly-once External Effect guarantee.",
    "No production SLO, availability, throughput, or fleet-scale guarantee.",
    "No correctness claim for multiple databases.",
    "No arbitrary durable Python guarantee.",
    "No parity claim with mature workflow engines.",
)


class SanitizedAgentEvent(EvidenceModel):
    sequence: int = Field(gt=0)
    event_type: Literal["context_projection", "candidate", "outcome_verification"]
    durable_identity: str
    input_digest: str
    output_digest: str

    @model_validator(mode="after")
    def validate_event(self) -> SanitizedAgentEvent:
        if not self.durable_identity:
            raise ValueError("Agent trajectory event requires one durable identity")
        require_digest(self.input_digest, "Agent trajectory input digest")
        require_digest(self.output_digest, "Agent trajectory output digest")
        return self


class AgentTrialEvidence(EvidenceModel):
    seed: int = Field(ge=0)
    outcome_passed: bool
    prohibited_actions: tuple[str, ...]
    latency_ms: int = Field(ge=0)
    trajectory_digest: str
    correlations: Correlations
    trajectory: tuple[SanitizedAgentEvent, ...] = Field(min_length=3)
    candidate_observation: AgentCandidateObservation
    rubric_scores: dict[str, bool]

    @model_validator(mode="after")
    def validate_trial(self) -> AgentTrialEvidence:
        require_digest(self.trajectory_digest, "Agent trajectory digest")
        if not any(self.correlations.model_dump(mode="python").values()):
            raise ValueError("Agent trial must retain durable correlations")
        if tuple(event.sequence for event in self.trajectory) != tuple(
            range(1, len(self.trajectory) + 1)
        ) or tuple(event.event_type for event in self.trajectory) != (
            "context_projection",
            "candidate",
            "outcome_verification",
        ):
            raise ValueError("Agent trajectory must retain its complete ordered lifecycle")
        if not self.rubric_scores or self.outcome_passed != all(self.rubric_scores.values()):
            raise ValueError("Agent outcome must derive from every recorded rubric score")
        document = json.dumps(
            {
                "candidate_observation": self.candidate_observation.model_dump(mode="json"),
                "rubric_scores": dict(sorted(self.rubric_scores.items())),
                "trajectory": [event.model_dump(mode="json") for event in self.trajectory],
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        if self.trajectory_digest != "sha256:" + hashlib.sha256(document).hexdigest():
            raise ValueError("Agent trajectory digest does not match its sanitized events")
        return self


class DeterministicScenarioEvidence(EvidenceModel):
    scenario_id: str
    correlations: Correlations
    observation: dict[str, object]
    observation_digest: str

    @model_validator(mode="after")
    def validate_observation(self) -> DeterministicScenarioEvidence:
        if not self.scenario_id:
            raise ValueError("deterministic scenario identity is required")
        expected = canonical_digest(self.observation)
        if self.observation_digest != expected:
            raise ValueError("deterministic scenario digest does not match its observation")
        return self


def deterministic_observation_digest(
    scenarios: tuple[DeterministicScenarioEvidence, ...],
    test_results: dict[str, dict[str, JsonValue]],
) -> str:
    return canonical_digest(
        {
            "scenarios": [scenario.model_dump(mode="json") for scenario in scenarios],
            "test_results": test_results,
        }
    )


class ArtifactCase(ArtifactCaseBase):
    case_kind: Literal["deterministic"] = "deterministic"
    scenarios: tuple[DeterministicScenarioEvidence, ...] = Field(min_length=1)
    test_results: dict[str, dict[str, JsonValue]]

    @model_validator(mode="after")
    def validate_scenarios(self) -> ArtifactCase:
        scenario_ids = tuple(item.scenario_id for item in self.scenarios)
        if len(set(scenario_ids)) != len(scenario_ids):
            raise ValueError("deterministic scenario identities must be unique")
        if self.correlations != merge_correlations(
            scenario.correlations for scenario in self.scenarios
        ):
            raise ValueError("deterministic case correlations must derive from its scenarios")
        if self.observation_digests != (
            deterministic_observation_digest(self.scenarios, self.test_results),
        ):
            raise ValueError("deterministic case digest must derive from its canonical payload")
        return self


class AgentCaseEvidence(ArtifactCaseBase):
    case_kind: Literal["agent"] = "agent"
    configuration_key: str
    split: Literal["development", "held_out"]
    prohibited_action_contract: tuple[str, ...] = Field(min_length=1)
    scorer_contract: AgentScorerContract
    agent_trials: tuple[AgentTrialEvidence, ...] = Field(min_length=1)
    pass_threshold: float = Field(ge=0.0, le=1.0)
    passed_trials: int = Field(ge=0)
    prohibited_actions: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_trials(self) -> AgentCaseEvidence:
        if tuple(trial.seed for trial in self.agent_trials) != self.seeds:
            raise ValueError("Agent trials must follow the predeclared seed corpus")
        if tuple(trial.trajectory_digest for trial in self.agent_trials) != (
            self.observation_digests
        ):
            raise ValueError("Agent trials must own every recorded trajectory digest")
        if self.passed_trials > self.observed_trials:
            raise ValueError("Agent case pass count exceeds its denominator")
        if any(
            set(trial.prohibited_actions).difference(self.prohibited_action_contract)
            for trial in self.agent_trials
        ):
            raise ValueError("Agent trial contains an action outside its predeclared contract")
        if any(
            trial.rubric_scores
            != agent_rubric_scores(
                self.scorer_contract,
                trial.candidate_observation,
                trial.prohibited_actions,
            )
            for trial in self.agent_trials
        ):
            raise ValueError("Agent trial scores must be recomputable from sanitized evidence")
        return self


class DeterministicSummary(EvidenceModel):
    expected_cases: int = Field(gt=0)
    observed_cases: int = Field(gt=0)
    passed_cases: int = Field(ge=0)
    failed_cases: int = Field(ge=0)
    infrastructure_errors: int = Field(ge=0)
    invariant_violations: int = Field(ge=0)
    strict_pass: bool
    runner_exit_code: int


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
    latency_ms: DistributionSummary

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
        require_digest(self.instruction_digest, "instruction_digest")
        require_digest(self.tool_schema_digest, "tool_schema_digest")
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
        require_digest(self.endpoint_digest, "endpoint_digest")
        require_digest(self.configuration_digest, "configuration_digest")
        return self


class PlaygroundSummary(EvidenceModel):
    synthetic_data_only: Literal[True]
    effects_enabled_by_default: Literal[False]
    local_provider: Literal[True]
    reset_verified: bool
    process_controls_verified: bool
    contributes_to_correctness: Literal[False]


DeterministicEvidenceCase = Annotated[ArtifactCase | RaceCase, Field(discriminator="case_kind")]


class DeterministicArtifact(EvidenceModel):
    schema_version: Literal["openmagic.enterprise-evidence.v1"] = SCHEMA_VERSION
    artifact_kind: Literal["deterministic_release"] = "deterministic_release"
    lane: Literal["deterministic_correctness"] = "deterministic_correctness"
    reproducibility: ReproducibilityPin
    cases: tuple[DeterministicEvidenceCase, ...] = Field(min_length=1)
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
        should_pass = (
            self.summary.runner_exit_code == 0
            and all(status == "passed" for status in statuses)
            and violations == 0
        )
        if self.summary.strict_pass != should_pass:
            raise ValueError("strict deterministic verdict does not match case outcomes")
        missing = set(REQUIRED_NEGATIVE_CLAIMS).difference(self.negative_claims)
        if missing:
            raise ValueError("final report is missing required negative claims")
        return self


class RaceArtifact(DeterministicArtifact):
    artifact_kind: Literal["race_corpus"] = "race_corpus"
    cases: tuple[RaceCase, ...] = Field(min_length=1)


class ProcessArtifact(DeterministicArtifact):
    artifact_kind: Literal["process_recovery"] = "process_recovery"
    cases: tuple[ProcessCase, ...] = Field(min_length=1)


class AgentQualityArtifact(EvidenceModel):
    schema_version: Literal["openmagic.enterprise-evidence.v1"] = SCHEMA_VERSION
    artifact_kind: Literal["agent_quality"] = "agent_quality"
    lane: Literal["agent_quality"] = "agent_quality"
    reproducibility: ReproducibilityPin
    agent_configurations: tuple[AgentConfigurationPin, ...] = Field(min_length=1)
    cases: tuple[AgentCaseEvidence, ...]
    summary: AgentQualitySummary
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def validate_agent_quality(self) -> AgentQualityArtifact:
        if not self.cases:
            raise ValueError("Agent quality requires versioned development or held-out cases")
        configuration_keys = tuple(item.agent_key for item in self.agent_configurations)
        if len(set(configuration_keys)) != len(configuration_keys):
            raise ValueError("Agent configuration keys must be unique")
        if any(case.configuration_key not in configuration_keys for case in self.cases):
            raise ValueError("Agent case references an unknown pinned configuration")
        if any(len(case.agent_trials) != case.observed_trials for case in self.cases):
            raise ValueError("Agent quality requires every sanitized per-trial trajectory")
        development = sum(case.split == "development" for case in self.cases)
        held_out = sum(case.split == "held_out" for case in self.cases)
        if development == 0 or held_out == 0:
            raise ValueError("Agent quality requires development and held-out cases")
        for case in self.cases:
            if case.observed_trials != case.expected_trials or case.observed_trials == 0:
                raise ValueError("Agent case must retain its complete predeclared denominator")
            trial_seeds = tuple(trial.seed for trial in case.agent_trials)
            trial_passes = sum(trial.outcome_passed for trial in case.agent_trials)
            trial_prohibited = sum(len(trial.prohibited_actions) for trial in case.agent_trials)
            threshold_passed = (
                trial_passes / case.observed_trials >= case.pass_threshold and trial_prohibited == 0
            )
            if (
                len(set(trial_seeds)) != len(trial_seeds)
                or tuple(sorted(trial_seeds)) != tuple(sorted(case.seeds))
                or case.passed_trials != trial_passes
                or case.prohibited_actions != trial_prohibited
                or case.observation_digests
                != tuple(trial.trajectory_digest for trial in case.agent_trials)
                or case.correlations
                != merge_correlations(trial.correlations for trial in case.agent_trials)
                or (case.verdict.status == "passed") != threshold_passed
            ):
                raise ValueError("Agent case contradicts its complete per-trial evidence")
        expected = sum(case.expected_trials for case in self.cases)
        observed = sum(case.observed_trials for case in self.cases)
        all_trials = tuple(trial for case in self.cases for trial in case.agent_trials)
        passed = sum(trial.outcome_passed for trial in all_trials)
        prohibited = sum(len(trial.prohibited_actions) for trial in all_trials)
        threshold_passed = all(
            case.verdict.status == "passed"
            and case.passed_trials / case.observed_trials >= case.pass_threshold
            and case.prohibited_actions == 0
            for case in self.cases
        )
        latencies = tuple(trial.latency_ms for trial in all_trials)
        latency_mean = statistics.mean(latencies)
        latency_median = statistics.median(latencies)
        latency_deviation = statistics.stdev(latencies) if len(latencies) > 1 else 0.0
        pass_rate = passed / observed
        z = 1.96
        denominator = 1 + z * z / observed
        centre = (pass_rate + z * z / (2 * observed)) / denominator
        margin = (
            z
            * math.sqrt(pass_rate * (1 - pass_rate) / observed + z * z / (4 * observed * observed))
            / denominator
        )
        wilson_lower = max(0.0, centre - margin)
        wilson_upper = 1.0 if passed == observed else min(1.0, centre + margin)
        if (
            self.summary.expected_trials != expected
            or self.summary.observed_trials != observed
            or self.summary.passed_trials != passed
            or self.summary.prohibited_actions != prohibited
            or self.summary.threshold_passed != threshold_passed
            or self.summary.development_cases != development
            or self.summary.held_out_cases != held_out
            or self.summary.latency_ms.count != observed
            or not math.isclose(self.summary.latency_ms.mean, latency_mean)
            or not math.isclose(self.summary.latency_ms.median, latency_median)
            or not math.isclose(
                self.summary.latency_ms.sample_standard_deviation,
                latency_deviation,
            )
            or self.summary.latency_ms.minimum != min(latencies)
            or self.summary.latency_ms.maximum != max(latencies)
            or not math.isclose(self.summary.pass_rate, pass_rate)
            or not math.isclose(self.summary.wilson_lower, wilson_lower)
            or not math.isclose(self.summary.wilson_upper, wilson_upper)
        ):
            raise ValueError("Agent summary contradicts its complete case evidence")
        return self


class LiveSmokeArtifact(EvidenceModel):
    schema_version: Literal["openmagic.enterprise-evidence.v1"] = SCHEMA_VERSION
    artifact_kind: Literal["live_smoke"] = "live_smoke"
    lane: Literal["provider_availability"] = "provider_availability"
    reproducibility: ReproducibilityPin
    provider_configuration: LiveProviderPin
    cases: tuple[ArtifactCase, ...]
    summary: AvailabilitySummary
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def validate_live_lane(self) -> LiveSmokeArtifact:
        if len(self.cases) != 1:
            raise ValueError("live availability requires one isolated live case")
        expected_status = (
            "passed"
            if self.summary.available
            else "infrastructure_error"
            if self.summary.attempted
            else "unavailable"
        )
        if self.cases[0].verdict.status != expected_status:
            raise ValueError("live summary contradicts its case outcome")
        return self


class PlaygroundArtifact(EvidenceModel):
    schema_version: Literal["openmagic.enterprise-evidence.v1"] = SCHEMA_VERSION
    artifact_kind: Literal["playground"] = "playground"
    lane: Literal["demonstration"] = "demonstration"
    reproducibility: ReproducibilityPin
    cases: tuple[ArtifactCase, ...]
    summary: PlaygroundSummary
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def validate_demonstration_lane(self) -> PlaygroundArtifact:
        if not self.cases or any(case.verdict.status != "passed" for case in self.cases):
            raise ValueError("demonstration artifacts require isolated passing cases")
        return self


class SurfaceAuditArtifact(EvidenceModel):
    schema_version: Literal["openmagic.enterprise-evidence.v1"] = SCHEMA_VERSION
    artifact_kind: Literal["surface_audit"] = "surface_audit"
    lane: Literal["installable_surface"] = "installable_surface"
    reproducibility: ReproducibilityPin
    repository: RepositorySurfaceEvidence
    installed: InstalledSurfaceEvidence
    cold_schema: ColdSchemaEvidence
    summary: SurfaceAuditSummary
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def validate_surface_audit(self) -> SurfaceAuditArtifact:
        if self.summary.repository_passed != self.repository.passed:
            raise ValueError("repository audit summary contradicts its evidence")
        if self.summary.installed_surface_passed != self.installed.passed:
            raise ValueError("installed audit summary contradicts its evidence")
        if self.summary.cold_schema_passed != self.cold_schema.passed:
            raise ValueError("cold schema summary contradicts its evidence")
        return self


Artifact = Annotated[
    DeterministicArtifact
    | RaceArtifact
    | ProcessArtifact
    | AgentQualityArtifact
    | LiveSmokeArtifact
    | PlaygroundArtifact
    | SurfaceAuditArtifact,
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


__all__ = [
    "REQUIRED_NEGATIVE_CLAIMS",
    "SCHEMA_VERSION",
    "AgentCandidateObservation",
    "AgentCaseEvidence",
    "AgentConfigurationPin",
    "AgentQualityArtifact",
    "AgentQualitySummary",
    "AgentScorerContract",
    "AgentTrialEvidence",
    "Artifact",
    "ArtifactCase",
    "AttemptAuthorityEvidence",
    "AvailabilitySummary",
    "BoundaryAgentCandidateObservation",
    "BoundaryAgentScorerContract",
    "BuildPin",
    "CaseVerdict",
    "ColdSchemaEvidence",
    "Correlations",
    "DeliveryAuthorityEvidence",
    "DeterministicArtifact",
    "DeterministicScenarioEvidence",
    "DeterministicSummary",
    "DistributionSummary",
    "ForcedProcessLoss",
    "InstalledSurfaceEvidence",
    "LiveProviderPin",
    "LiveSmokeArtifact",
    "PlaygroundArtifact",
    "PlaygroundSummary",
    "ProcessArtifact",
    "ProcessCase",
    "ProcessContract",
    "ProcessIdentityEvidence",
    "ProcessMetrics",
    "ProcessObservation",
    "QueueDepth",
    "RaceArtifact",
    "RaceCase",
    "RaceTrialEvidence",
    "RenewalAgentCandidateObservation",
    "RenewalAgentScorerContract",
    "RepositorySurfaceEvidence",
    "ReproducibilityPin",
    "SanitizedAgentEvent",
    "SanitizedObservation",
    "SurfaceAuditArtifact",
    "SurfaceAuditSummary",
    "WheelArchivePin",
    "agent_rubric_scores",
    "artifact_json_schema",
    "canonical_artifact_json",
    "canonical_digest",
    "deterministic_observation_digest",
    "merge_correlations",
    "parse_artifact",
    "race_trial_digest",
]
