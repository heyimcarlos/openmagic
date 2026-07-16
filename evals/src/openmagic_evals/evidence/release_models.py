"""Shared release verdict contracts for deterministic evidence products."""

from __future__ import annotations

from collections.abc import Sequence

from pydantic import Field

from openmagic_evals.evidence.core_models import ArtifactCaseBase, EvidenceModel

SCHEMA_VERSION = "openmagic.enterprise-evidence.v1"
REQUIRED_NEGATIVE_CLAIMS = (
    "No exactly-once External Effect guarantee.",
    "No production SLO, availability, throughput, or fleet-scale guarantee.",
    "No correctness claim for multiple databases.",
    "No arbitrary durable Python guarantee.",
    "No parity claim with mature workflow engines.",
)


class DeterministicSummary(EvidenceModel):
    expected_cases: int = Field(gt=0)
    observed_cases: int = Field(gt=0)
    passed_cases: int = Field(ge=0)
    failed_cases: int = Field(ge=0)
    infrastructure_errors: int = Field(ge=0)
    invariant_violations: int = Field(ge=0)
    strict_pass: bool
    runner_exit_code: int


def validate_deterministic_summary(
    cases: Sequence[ArtifactCaseBase],
    summary: DeterministicSummary,
    negative_claims: tuple[str, ...],
) -> None:
    statuses = [case.verdict.status for case in cases]
    violations = sum(len(case.verdict.invariant_violations) for case in cases)
    expected = len(cases)
    if not (
        summary.expected_cases == expected
        and summary.observed_cases == expected
        and summary.passed_cases == statuses.count("passed")
        and summary.failed_cases == statuses.count("failed")
        and summary.infrastructure_errors == statuses.count("infrastructure_error")
        and summary.invariant_violations == violations
    ):
        raise ValueError("deterministic summary does not match its complete case denominator")
    should_pass = (
        summary.runner_exit_code == 0
        and all(status == "passed" for status in statuses)
        and violations == 0
    )
    if summary.strict_pass != should_pass:
        raise ValueError("strict deterministic verdict does not match case outcomes")
    if set(REQUIRED_NEGATIVE_CLAIMS).difference(negative_claims):
        raise ValueError("final report is missing required negative claims")


__all__ = [
    "REQUIRED_NEGATIVE_CLAIMS",
    "SCHEMA_VERSION",
    "DeterministicSummary",
    "validate_deterministic_summary",
]
