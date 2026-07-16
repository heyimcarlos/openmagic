"""Contracts for the non-correctness playground evidence lane."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from openmagic_evals.evidence.core_models import EvidenceModel, validate_correlated_definitions
from openmagic_evals.evidence.deterministic_models import ArtifactCase
from openmagic_evals.evidence.pins import ReproducibilityPin
from openmagic_evals.evidence.release_models import SCHEMA_VERSION


class PlaygroundSummary(EvidenceModel):
    synthetic_data_only: Literal[True]
    effects_enabled_by_default: Literal[False]
    local_provider: Literal[True]
    reset_verified: bool
    repeated_run_verified: bool
    intentional_failure_verified: bool
    disconnected_provider_verified: bool
    process_controls_verified: bool
    contributes_to_correctness: Literal[False]


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
        validate_correlated_definitions(
            (case.correlations for case in self.cases),
            self.reproducibility.definition_digests,
        )
        return self


__all__ = ["PlaygroundArtifact", "PlaygroundSummary"]
