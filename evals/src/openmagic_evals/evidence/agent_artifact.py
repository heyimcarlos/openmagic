"""Pinned Agent corpus and quality artifact contracts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from openmagic_evals.evidence.agent_aggregation import (
    AgentQualitySummary,
    assess_agent_case,
    summarize_agent_quality,
)
from openmagic_evals.evidence.agent_trial_models import AgentCaseEvidence
from openmagic_evals.evidence.core_models import (
    EvidenceModel,
    merge_correlations,
    require_digest,
    validate_correlated_definitions,
)
from openmagic_evals.evidence.pins import ReproducibilityPin
from openmagic_evals.evidence.release_models import SCHEMA_VERSION


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
        configuration_keys = self._validate_case_set()
        expected_summary = summarize_agent_quality(self.cases, configuration_keys)
        if self.summary != expected_summary:
            raise ValueError("Agent summary contradicts its complete case evidence")
        validate_correlated_definitions(
            (case.correlations for case in self.cases),
            self.reproducibility.definition_digests,
        )
        return self

    def _validate_case_set(self) -> tuple[str, ...]:
        if not self.cases:
            raise ValueError("Agent quality requires versioned development or held-out cases")
        configuration_keys = tuple(item.agent_key for item in self.agent_configurations)
        if len(set(configuration_keys)) != len(configuration_keys):
            raise ValueError("Agent configuration keys must be unique")
        if any(case.configuration_key not in configuration_keys for case in self.cases):
            raise ValueError("Agent case references an unknown pinned configuration")
        if not any(case.split == "development" for case in self.cases) or not any(
            case.split == "held_out" for case in self.cases
        ):
            raise ValueError("Agent quality requires development and held-out cases")
        for case in self.cases:
            self._validate_case(case)
        return configuration_keys

    @staticmethod
    def _validate_case(case: AgentCaseEvidence) -> None:
        if case.observed_trials != case.expected_trials or case.observed_trials == 0:
            raise ValueError("Agent case must retain its complete predeclared denominator")
        assessment = assess_agent_case(
            case.agent_trials,
            expected_trials=case.expected_trials,
            pass_threshold=case.pass_threshold,
        )
        if (
            len(case.agent_trials) != case.observed_trials
            or len({trial.seed for trial in case.agent_trials}) != len(case.agent_trials)
            or tuple(sorted(trial.seed for trial in case.agent_trials)) != tuple(sorted(case.seeds))
            or case.passed_trials != assessment.aggregate.passed_trials
            or case.prohibited_actions != assessment.aggregate.prohibited_actions
            or case.observation_digests
            != tuple(trial.trajectory_digest for trial in case.agent_trials)
            or case.correlations
            != merge_correlations(trial.correlations for trial in case.agent_trials)
            or (case.verdict.status == "passed") != assessment.threshold_passed
        ):
            raise ValueError("Agent case contradicts its complete per-trial evidence")


__all__ = [
    "AgentConfigurationPin",
    "AgentCorpusPin",
    "AgentQualityArtifact",
]
