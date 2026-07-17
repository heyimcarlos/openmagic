"""Contracts for the non-correctness playground evidence lane."""

from __future__ import annotations

from typing import Literal

from openmagic_playground.verification_response import VerificationDurableChain
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
    verification_chains: tuple[VerificationDurableChain, ...] = ()
    limitations: tuple[str, ...]

    @model_validator(mode="after")
    def validate_demonstration_lane(self) -> PlaygroundArtifact:
        if not self.cases or any(case.verdict.status != "passed" for case in self.cases):
            raise ValueError("demonstration artifacts require isolated passing cases")
        validate_correlated_definitions(
            (case.correlations for case in self.cases),
            self.reproducibility.definition_digests,
        )
        case_ids = tuple(case.case_id for case in self.cases)
        if case_ids == ("playground.synthetic-reset-and-process-control",):
            validate_playground_control_artifact(self)
        elif case_ids == ("demo.deterministic-verification",):
            if len(self.verification_chains) != 1:
                raise ValueError("verification demo must retain one typed durable chain")
        elif self.verification_chains:
            raise ValueError("only the verification demo may retain a verification chain")
        return self

    def _validate_control_case(self, case: ArtifactCase) -> None:
        scenario_by_id = {scenario.scenario_id: scenario for scenario in case.scenarios}
        if set(scenario_by_id) != {
            "safe-reset",
            "repeated-run",
            "intentional-failure",
            "disconnected-provider",
        }:
            raise ValueError("playground control evidence requires every exact scenario")
        safe_reset = scenario_by_id["safe-reset"].observation
        expected_controls = {"start": 3, "drain": 3, "reset": True, "restart": 3, "stop": True}
        if (
            safe_reset.get("controls") != expected_controls
            or safe_reset.get("reset_reproduced") is not True
            or safe_reset.get("effects_enabled_by_default") is not False
            or scenario_by_id["repeated-run"].observation != {"reproduced": True}
        ):
            raise ValueError("playground reset and process controls are incomplete")
        intentional = scenario_by_id["intentional-failure"].observation
        disconnected = scenario_by_id["disconnected-provider"].observation
        if (
            intentional.get("scenario") != "intentional-failure"
            or intentional.get("external_effect_certainty") != "not_applied"
            or intentional.get("provider_connected") is not True
            or disconnected.get("scenario") != "disconnected-provider"
            or disconnected.get("external_effect_certainty") != "uncertain"
            or disconnected.get("provider_connected") is not False
        ):
            raise ValueError("playground failure scenarios are incomplete")
        if not all(
            (
                self.summary.reset_verified,
                self.summary.repeated_run_verified,
                self.summary.intentional_failure_verified,
                self.summary.disconnected_provider_verified,
                self.summary.process_controls_verified,
            )
        ):
            raise ValueError("playground summary must derive from every control scenario")


def validate_playground_control_artifact(artifact: PlaygroundArtifact) -> None:
    if tuple(case.case_id for case in artifact.cases) != (
        "playground.synthetic-reset-and-process-control",
    ):
        raise ValueError("playground control evidence requires its canonical case identity")
    artifact._validate_control_case(artifact.cases[0])


__all__ = [
    "PlaygroundArtifact",
    "PlaygroundSummary",
    "validate_playground_control_artifact",
]
