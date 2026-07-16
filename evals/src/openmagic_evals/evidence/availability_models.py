"""Contracts for opt-in provider availability evidence."""

from __future__ import annotations

from typing import Literal

from pydantic import model_validator

from openmagic_evals.evidence.core_models import EvidenceModel, require_digest
from openmagic_evals.evidence.deterministic_models import ArtifactCase
from openmagic_evals.evidence.pins import ReproducibilityPin
from openmagic_evals.evidence.release_models import SCHEMA_VERSION


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


__all__ = ["AvailabilitySummary", "LiveProviderPin", "LiveSmokeArtifact"]
