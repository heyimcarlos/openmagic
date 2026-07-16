"""Contracts and canonical aggregation for Agent quality evidence."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal, Protocol

from pydantic import Field, model_validator

from openmagic_evals.evidence.agent_scoring import (
    AgentCandidateObservation,
    AgentScorerContract,
    agent_rubric_scores,
)
from openmagic_evals.evidence.core_models import (
    ArtifactCaseBase,
    Correlations,
    DistributionSummary,
    EvidenceModel,
    canonical_digest,
    has_correlations,
    merge_correlations,
    require_digest,
    validate_correlated_definitions,
)
from openmagic_evals.evidence.pins import ReproducibilityPin
from openmagic_evals.evidence.release_models import SCHEMA_VERSION


class AgentTrialMeasure(Protocol):
    @property
    def outcome_passed(self) -> bool: ...

    @property
    def prohibited_actions(self) -> tuple[str, ...]: ...

    @property
    def latency_ms(self) -> int: ...


class AgentAggregate(EvidenceModel):
    observed_trials: int = Field(gt=0)
    passed_trials: int = Field(ge=0)
    prohibited_actions: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    wilson_lower: float = Field(ge=0.0, le=1.0)
    wilson_upper: float = Field(ge=0.0, le=1.0)
    latency_ms: DistributionSummary


def aggregate_agent_trials(trials: Sequence[AgentTrialMeasure]) -> AgentAggregate:
    if not trials:
        raise ValueError("Agent aggregation requires at least one trial")
    observed = len(trials)
    passed = sum(trial.outcome_passed for trial in trials)
    prohibited = sum(len(trial.prohibited_actions) for trial in trials)
    latencies = tuple(trial.latency_ms for trial in trials)
    mean = sum(latencies) / observed
    ordered = tuple(sorted(latencies))
    middle = observed // 2
    median = float(ordered[middle]) if observed % 2 else (ordered[middle - 1] + ordered[middle]) / 2
    deviation = (
        math.sqrt(sum((value - mean) ** 2 for value in latencies) / (observed - 1))
        if observed > 1
        else 0.0
    )
    pass_rate = passed / observed
    z = 1.96
    denominator = 1 + z * z / observed
    centre = (pass_rate + z * z / (2 * observed)) / denominator
    margin = (
        z
        * math.sqrt(pass_rate * (1 - pass_rate) / observed + z * z / (4 * observed * observed))
        / denominator
    )
    return AgentAggregate(
        observed_trials=observed,
        passed_trials=passed,
        prohibited_actions=prohibited,
        pass_rate=pass_rate,
        wilson_lower=max(0.0, centre - margin),
        wilson_upper=1.0 if passed == observed else min(1.0, centre + margin),
        latency_ms=DistributionSummary(
            count=observed,
            mean=mean,
            median=median,
            sample_standard_deviation=deviation,
            minimum=min(latencies),
            maximum=max(latencies),
        ),
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
        if not has_correlations(self.correlations):
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
        document = {
            "candidate_observation": self.candidate_observation.model_dump(mode="json"),
            "rubric_scores": dict(sorted(self.rubric_scores.items())),
            "trajectory": [event.model_dump(mode="json") for event in self.trajectory],
        }
        if self.trajectory_digest != canonical_digest(document):
            raise ValueError("Agent trajectory digest does not match its sanitized events")
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


class AgentSplitSummary(EvidenceModel):
    case_count: int = Field(gt=0)
    expected_trials: int = Field(gt=0)
    aggregate: AgentAggregate
    threshold_passed: bool

    @model_validator(mode="after")
    def validate_split(self) -> AgentSplitSummary:
        if self.aggregate.observed_trials != self.expected_trials:
            raise ValueError("Agent split must report its complete trial denominator")
        return self


class AgentQualitySummary(EvidenceModel):
    development: AgentSplitSummary
    held_out: AgentSplitSummary
    combined: AgentAggregate
    threshold_passed: bool
    deterministic_release_pass: bool | None = None

    @model_validator(mode="after")
    def keep_quality_separate(self) -> AgentQualitySummary:
        if self.deterministic_release_pass is not None:
            raise ValueError("Agent quality cannot determine deterministic release correctness")
        if self.combined.observed_trials != (
            self.development.aggregate.observed_trials + self.held_out.aggregate.observed_trials
        ):
            raise ValueError("combined Agent denominator must equal both split denominators")
        if self.combined.passed_trials != (
            self.development.aggregate.passed_trials + self.held_out.aggregate.passed_trials
        ) or self.combined.prohibited_actions != (
            self.development.aggregate.prohibited_actions
            + self.held_out.aggregate.prohibited_actions
        ):
            raise ValueError("combined Agent aggregate must equal both split aggregates")
        if self.threshold_passed != (
            self.development.threshold_passed and self.held_out.threshold_passed
        ):
            raise ValueError("Agent threshold must derive from both split thresholds")
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


class AgentCorpusPin(EvidenceModel):
    development_cases_digest: str
    held_out_corpus_version: str
    held_out_cases_digest: str
    held_out_sealed_at_commit: str
    runner_frozen_at_commit: str
    tuning_locked_roots: tuple[str, ...] = Field(min_length=1)
    tuning_locked_source_digest: str
    execution_phases: tuple[Literal["development", "held_out"], ...]
    tuning_unchanged_after_seal: Literal[True]

    @model_validator(mode="after")
    def validate_corpus_pin(self) -> AgentCorpusPin:
        require_digest(self.development_cases_digest, "development cases digest")
        require_digest(self.held_out_cases_digest, "held-out cases digest")
        if not self.held_out_corpus_version:
            raise ValueError("held-out corpus version is required")
        if re.fullmatch(r"[0-9a-f]{40}", self.held_out_sealed_at_commit) is None:
            raise ValueError("held-out corpus seal must be an exact Git commit")
        if re.fullmatch(r"[0-9a-f]{40}", self.runner_frozen_at_commit) is None:
            raise ValueError("Agent runner freeze must be an exact Git commit")
        require_digest(self.tuning_locked_source_digest, "tuning-locked source digest")
        if self.execution_phases != ("development", "held_out"):
            raise ValueError("held-out cases must execute only after development cases")
        if any(
            Path(path).is_absolute() or ".." in Path(path).parts
            for path in self.tuning_locked_roots
        ):
            raise ValueError("tuning lock paths must be repository-relative")
        return self


class AgentQualityArtifact(EvidenceModel):
    schema_version: Literal["openmagic.enterprise-evidence.v1"] = SCHEMA_VERSION
    artifact_kind: Literal["agent_quality"] = "agent_quality"
    lane: Literal["agent_quality"] = "agent_quality"
    reproducibility: ReproducibilityPin
    corpus: AgentCorpusPin
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
        development = sum(case.split == "development" for case in self.cases)
        held_out = sum(case.split == "held_out" for case in self.cases)
        if development == 0 or held_out == 0:
            raise ValueError("Agent quality requires development and held-out cases")
        for case in self.cases:
            if case.observed_trials != case.expected_trials or case.observed_trials == 0:
                raise ValueError("Agent case must retain its complete predeclared denominator")
            aggregate = aggregate_agent_trials(case.agent_trials)
            threshold_passed = (
                aggregate.pass_rate >= case.pass_threshold and aggregate.prohibited_actions == 0
            )
            if (
                len(case.agent_trials) != case.observed_trials
                or len({trial.seed for trial in case.agent_trials}) != len(case.agent_trials)
                or tuple(sorted(trial.seed for trial in case.agent_trials))
                != tuple(sorted(case.seeds))
                or case.passed_trials != aggregate.passed_trials
                or case.prohibited_actions != aggregate.prohibited_actions
                or case.observation_digests
                != tuple(trial.trajectory_digest for trial in case.agent_trials)
                or case.correlations
                != merge_correlations(trial.correlations for trial in case.agent_trials)
                or (case.verdict.status == "passed") != threshold_passed
            ):
                raise ValueError("Agent case contradicts its complete per-trial evidence")
        split_cases = {
            split: tuple(case for case in self.cases if case.split == split)
            for split in ("development", "held_out")
        }

        def split_summary(split: Literal["development", "held_out"]) -> AgentSplitSummary:
            cases = split_cases[split]
            aggregate = aggregate_agent_trials(
                tuple(trial for case in cases for trial in case.agent_trials)
            )
            return AgentSplitSummary(
                case_count=len(cases),
                expected_trials=sum(case.expected_trials for case in cases),
                aggregate=aggregate,
                threshold_passed=all(case.verdict.status == "passed" for case in cases),
            )

        aggregate = aggregate_agent_trials(
            tuple(trial for case in self.cases for trial in case.agent_trials)
        )
        development_summary = split_summary("development")
        held_out_summary = split_summary("held_out")
        expected_summary = AgentQualitySummary(
            development=development_summary,
            held_out=held_out_summary,
            combined=aggregate,
            threshold_passed=(
                development_summary.threshold_passed and held_out_summary.threshold_passed
            ),
        )
        if self.summary != expected_summary:
            raise ValueError("Agent summary contradicts its complete case evidence")
        validate_correlated_definitions(
            (case.correlations for case in self.cases),
            self.reproducibility.definition_digests,
        )
        return self


__all__ = [
    "AgentAggregate",
    "AgentCaseEvidence",
    "AgentConfigurationPin",
    "AgentCorpusPin",
    "AgentQualityArtifact",
    "AgentQualitySummary",
    "AgentSplitSummary",
    "AgentTrialEvidence",
    "SanitizedAgentEvent",
    "aggregate_agent_trials",
]
