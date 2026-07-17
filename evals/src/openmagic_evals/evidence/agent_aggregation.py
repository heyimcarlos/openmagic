"""Pure Agent evidence aggregation and summary contracts."""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Literal, Protocol

from pydantic import Field, model_validator

from openmagic_evals.evidence.agent_trial_models import AgentCaseEvidence
from openmagic_evals.evidence.core_models import DistributionSummary, EvidenceModel


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


class AgentCaseAssessment(EvidenceModel):
    expected_trials: int = Field(gt=0)
    aggregate: AgentAggregate
    threshold_passed: bool


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
        * math.sqrt(pass_rate * (1 - pass_rate) / observed + z * z / (4 * observed**2))
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


def assess_agent_case(
    trials: Sequence[AgentTrialMeasure],
    *,
    expected_trials: int,
    pass_threshold: float,
) -> AgentCaseAssessment:
    if len(trials) != expected_trials:
        raise ValueError("Agent case assessment requires its complete trial denominator")
    aggregate = aggregate_agent_trials(trials)
    return AgentCaseAssessment(
        expected_trials=expected_trials,
        aggregate=aggregate,
        threshold_passed=(
            aggregate.pass_rate >= pass_threshold and aggregate.prohibited_actions == 0
        ),
    )


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


class AgentCaseSummary(EvidenceModel):
    case_id: str
    configuration_key: str
    split: Literal["development", "held_out"]
    expected_trials: int = Field(gt=0)
    aggregate: AgentAggregate
    threshold_passed: bool

    @model_validator(mode="after")
    def validate_case(self) -> AgentCaseSummary:
        if self.aggregate.observed_trials != self.expected_trials:
            raise ValueError("Agent case summary must report its complete trial denominator")
        return self


class AgentConfigurationSummary(EvidenceModel):
    configuration_key: str
    case_ids: tuple[str, ...] = Field(min_length=1)
    expected_trials: int = Field(gt=0)
    aggregate: AgentAggregate
    threshold_passed: bool

    @model_validator(mode="after")
    def validate_configuration(self) -> AgentConfigurationSummary:
        if self.aggregate.observed_trials != self.expected_trials:
            raise ValueError("Agent configuration must report its complete trial denominator")
        if len(self.case_ids) != len(set(self.case_ids)):
            raise ValueError("Agent configuration case identities must be unique")
        return self


def summarize_agent_cases(cases: Sequence[AgentCaseEvidence]) -> tuple[AgentCaseSummary, ...]:
    summaries: list[AgentCaseSummary] = []
    for case in cases:
        assessment = assess_agent_case(
            case.agent_trials,
            expected_trials=case.expected_trials,
            pass_threshold=case.pass_threshold,
        )
        summaries.append(
            AgentCaseSummary(
                case_id=case.case_id,
                configuration_key=case.configuration_key,
                split=case.split,
                expected_trials=case.expected_trials,
                aggregate=assessment.aggregate,
                threshold_passed=assessment.threshold_passed,
            )
        )
    return tuple(summaries)


def summarize_agent_configurations(
    cases: Sequence[AgentCaseEvidence],
    configuration_keys: Sequence[str],
    case_summaries: Sequence[AgentCaseSummary],
) -> tuple[AgentConfigurationSummary, ...]:
    summary_by_case = {item.case_id: item for item in case_summaries}
    if set(summary_by_case) != {case.case_id for case in cases}:
        raise ValueError("Agent configuration aggregation requires every case summary")
    summaries: list[AgentConfigurationSummary] = []
    for configuration_key in configuration_keys:
        selected = tuple(case for case in cases if case.configuration_key == configuration_key)
        if not selected:
            raise ValueError("Every pinned Agent configuration must have observed cases")
        summaries.append(
            AgentConfigurationSummary(
                configuration_key=configuration_key,
                case_ids=tuple(case.case_id for case in selected),
                expected_trials=sum(case.expected_trials for case in selected),
                aggregate=aggregate_agent_trials(
                    tuple(trial for case in selected for trial in case.agent_trials)
                ),
                threshold_passed=all(
                    summary_by_case[case.case_id].threshold_passed for case in selected
                ),
            )
        )
    return tuple(summaries)


class AgentQualitySummary(EvidenceModel):
    development: AgentSplitSummary
    held_out: AgentSplitSummary
    cases: tuple[AgentCaseSummary, ...] = Field(min_length=1)
    configurations: tuple[AgentConfigurationSummary, ...] = Field(min_length=1)
    combined: AgentAggregate
    threshold_passed: bool
    deterministic_release_pass: bool | None = None

    @model_validator(mode="after")
    def keep_quality_separate(self) -> AgentQualitySummary:
        if self.deterministic_release_pass is not None:
            raise ValueError("Agent quality cannot determine deterministic release correctness")
        split_counters = (
            self.development.aggregate.observed_trials + self.held_out.aggregate.observed_trials,
            self.development.aggregate.passed_trials + self.held_out.aggregate.passed_trials,
            self.development.aggregate.prohibited_actions
            + self.held_out.aggregate.prohibited_actions,
        )
        if split_counters != (
            self.combined.observed_trials,
            self.combined.passed_trials,
            self.combined.prohibited_actions,
        ):
            raise ValueError("combined Agent counters must equal both split counters")
        if self.threshold_passed != (
            self.development.threshold_passed and self.held_out.threshold_passed
        ):
            raise ValueError("Agent threshold must derive from both split thresholds")
        _validate_summary_partitions(self)
        return self


def summarize_agent_quality(
    cases: Sequence[AgentCaseEvidence],
    configuration_keys: Sequence[str],
) -> AgentQualitySummary:
    case_summaries = summarize_agent_cases(cases)

    def split_summary(split: Literal["development", "held_out"]) -> AgentSplitSummary:
        selected = tuple(case for case in cases if case.split == split)
        aggregate = aggregate_agent_trials(
            tuple(trial for case in selected for trial in case.agent_trials)
        )
        return AgentSplitSummary(
            case_count=len(selected),
            expected_trials=sum(case.expected_trials for case in selected),
            aggregate=aggregate,
            threshold_passed=all(
                summary.threshold_passed for summary in case_summaries if summary.split == split
            ),
        )

    development = split_summary("development")
    held_out = split_summary("held_out")
    return AgentQualitySummary(
        development=development,
        held_out=held_out,
        cases=case_summaries,
        configurations=summarize_agent_configurations(
            cases,
            configuration_keys,
            case_summaries,
        ),
        combined=aggregate_agent_trials(
            tuple(trial for case in cases for trial in case.agent_trials)
        ),
        threshold_passed=development.threshold_passed and held_out.threshold_passed,
    )


def _validate_summary_partitions(summary: AgentQualitySummary) -> None:
    if len({item.case_id for item in summary.cases}) != len(summary.cases):
        raise ValueError("Agent case summaries must have unique identities")
    if len({item.configuration_key for item in summary.configurations}) != len(
        summary.configurations
    ):
        raise ValueError("Agent configuration summaries must have unique identities")
    configuration_case_ids = tuple(
        case_id for item in summary.configurations for case_id in item.case_ids
    )
    if set(configuration_case_ids) != {item.case_id for item in summary.cases} or len(
        configuration_case_ids
    ) != len(summary.cases):
        raise ValueError("Agent configurations must partition every case summary exactly once")
    counters = (
        sum(item.aggregate.observed_trials for item in summary.configurations),
        sum(item.aggregate.passed_trials for item in summary.configurations),
        sum(item.aggregate.prohibited_actions for item in summary.configurations),
    )
    if counters != (
        summary.combined.observed_trials,
        summary.combined.passed_trials,
        summary.combined.prohibited_actions,
    ):
        raise ValueError("Agent configuration aggregates must recompute combined counters")


__all__ = [
    "AgentAggregate",
    "AgentCaseAssessment",
    "AgentCaseSummary",
    "AgentConfigurationSummary",
    "AgentQualitySummary",
    "AgentSplitSummary",
    "aggregate_agent_trials",
    "assess_agent_case",
    "summarize_agent_cases",
    "summarize_agent_configurations",
    "summarize_agent_quality",
]
