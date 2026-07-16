from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from openmagic_evals.evidence.claims import write_claim_report
from openmagic_evals.evidence.contracts import (
    REQUIRED_NEGATIVE_CLAIMS,
    AgentConfigurationPin,
    AgentQualityArtifact,
    AgentQualitySummary,
    AgentTrialEvidence,
    ArtifactCase,
    BuildPin,
    CaseVerdict,
    Correlations,
    DeterministicArtifact,
    DeterministicSummary,
    DistributionSummary,
    ReproducibilityPin,
    canonical_artifact_json,
)
from openmagic_evals.evidence.matrix import DETERMINISTIC_RELEASE_MATRIX, cardinality_one_races


def _pin(git_sha: str) -> ReproducibilityPin:
    return ReproducibilityPin(
        build=BuildPin(
            git_sha=git_sha,
            checkout_clean=True,
            lock_digest="sha256:" + "1" * 64,
            distributions={"openmagic-evals": "0.1.0"},
            distribution_digests={"openmagic-evals": "sha256:" + "0" * 64},
        ),
        suite_version="issue-71.v1",
        command=("openmagic-evidence", "test"),
        environment_allowlist=("PATH",),
        started_at=datetime(2026, 7, 15, tzinfo=UTC),
        finished_at=datetime(2026, 7, 15, 0, 1, tzinfo=UTC),
        timeout_seconds=60,
        postgres_version="17.5",
        postgres_image="postgres@sha256:" + "2" * 64,
        postgres_configuration={"transaction_isolation": "read committed"},
        postgres_configuration_digest="sha256:" + "3" * 64,
        migration_heads={"openmagic_runtime": "0003"},
        definition_digests={"definition": "sha256:" + "4" * 64},
        case_corpus_digest="sha256:" + "5" * 64,
        sandbox_digest="sha256:" + "6" * 64,
    )


def _case(*, agent: bool = False, case_id: str = "release.test") -> ArtifactCase:
    correlations = Correlations(command_ids=("018f2f00-0000-7000-8000-000000000001",))
    return ArtifactCase(
        case_id="agent.development.test" if agent else case_id,
        case_schema_version=1,
        split="development" if agent else None,
        expected_trials=1,
        observed_trials=1,
        seeds=(0,),
        correlations=correlations,
        observation_digests=("sha256:" + "7" * 64,),
        pass_threshold=1.0 if agent else None,
        passed_trials=1 if agent else None,
        agent_trials=(
            (
                AgentTrialEvidence(
                    seed=0,
                    outcome_passed=True,
                    prohibited_actions=(),
                    latency_ms=1,
                    trajectory_digest="sha256:" + "7" * 64,
                    correlations=correlations,
                ),
            )
            if agent
            else ()
        ),
        verdict=CaseVerdict(status="passed", invariant_violations=()),
    )


def test_claim_report_rejects_artifacts_from_different_builds(tmp_path: Path) -> None:
    release_cases = tuple(
        _case(case_id=case_id)
        for case_id in (
            *(case.case_id for case in DETERMINISTIC_RELEASE_MATRIX),
            *(case.case_id for case in cardinality_one_races()),
        )
    )
    deterministic = DeterministicArtifact(
        reproducibility=_pin("1" * 40),
        cases=release_cases,
        summary=DeterministicSummary(
            expected_cases=len(release_cases),
            observed_cases=len(release_cases),
            passed_cases=len(release_cases),
            failed_cases=0,
            infrastructure_errors=0,
            invariant_violations=0,
            strict_pass=True,
            runner_exit_code=0,
        ),
        limitations=("test",),
        negative_claims=REQUIRED_NEGATIVE_CLAIMS,
    )
    agent = AgentQualityArtifact(
        reproducibility=_pin("2" * 40),
        agent_configuration=AgentConfigurationPin(
            agent_key="test",
            agent_version=1,
            instruction_digest="sha256:" + "8" * 64,
            tool_schema_digest="sha256:" + "9" * 64,
            provider="local",
            model="test",
            reasoning="none",
            temperature=0,
        ),
        cases=(_case(agent=True),),
        summary=AgentQualitySummary(
            development_cases=1,
            held_out_cases=0,
            expected_trials=1,
            observed_trials=1,
            passed_trials=1,
            prohibited_actions=0,
            threshold_passed=True,
            pass_rate=1,
            wilson_lower=0.20654329147389294,
            wilson_upper=1,
            latency_ms=DistributionSummary(
                count=1,
                mean=1,
                median=1,
                sample_standard_deviation=0,
                minimum=1,
                maximum=1,
            ),
        ),
        limitations=("test",),
    )
    deterministic_path = tmp_path / "deterministic.json"
    agent_path = tmp_path / "agent.json"
    deterministic_path.write_text(canonical_artifact_json(deterministic), encoding="utf-8")
    agent_path.write_text(canonical_artifact_json(agent), encoding="utf-8")

    with pytest.raises(ValueError, match="reproducibility pin"):
        write_claim_report(
            deterministic_path=deterministic_path,
            agent_path=agent_path,
            output=tmp_path / "claims.md",
        )
