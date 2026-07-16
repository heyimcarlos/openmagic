"""Contracts for deterministic correctness evidence."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, JsonValue, model_validator

from openmagic_evals.evidence.core_models import (
    ArtifactCaseBase,
    Correlations,
    EvidenceModel,
    canonical_digest,
    merge_correlations,
)
from openmagic_evals.evidence.pins import ReproducibilityPin
from openmagic_evals.evidence.race_models import RaceCase
from openmagic_evals.evidence.release_models import (
    REQUIRED_NEGATIVE_CLAIMS,
    SCHEMA_VERSION,
    DeterministicSummary,
    validate_deterministic_summary,
)


class DeterministicScenarioEvidence(EvidenceModel):
    scenario_id: str
    correlations: Correlations
    observation: dict[str, object]
    observation_digest: str

    @model_validator(mode="after")
    def validate_observation(self) -> DeterministicScenarioEvidence:
        if not self.scenario_id:
            raise ValueError("deterministic scenario identity is required")
        if self.observation_digest != canonical_digest(self.observation):
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
        validate_deterministic_summary(self.cases, self.summary, self.negative_claims)
        return self


__all__ = [
    "REQUIRED_NEGATIVE_CLAIMS",
    "SCHEMA_VERSION",
    "ArtifactCase",
    "DeterministicArtifact",
    "DeterministicScenarioEvidence",
    "DeterministicSummary",
    "deterministic_observation_digest",
    "validate_deterministic_summary",
]
