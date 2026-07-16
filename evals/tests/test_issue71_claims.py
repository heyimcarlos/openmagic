from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import pytest
from openmagic_evals.evidence.claims import write_claim_report
from openmagic_evals.evidence.contracts import (
    REQUIRED_NEGATIVE_CLAIMS,
    AgentCaseEvidence,
    AgentConfigurationPin,
    AgentQualityArtifact,
    AgentQualitySummary,
    AgentTrialEvidence,
    ArtifactCase,
    BuildPin,
    CaseVerdict,
    ColdSchemaEvidence,
    Correlations,
    DeterministicArtifact,
    DeterministicScenarioEvidence,
    DeterministicSummary,
    DistributionSummary,
    InstalledSurfaceEvidence,
    RaceCase,
    RaceTrialEvidence,
    RepositorySurfaceEvidence,
    ReproducibilityPin,
    SanitizedAgentEvent,
    SurfaceAuditArtifact,
    SurfaceAuditSummary,
    canonical_artifact_json,
    canonical_digest,
    deterministic_observation_digest,
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
            source_distribution_digests={"openmagic-evals": "sha256:" + "0" * 64},
            installation_kinds=cast(
                dict[str, Literal["wheel", "editable"]], {"openmagic-evals": "wheel"}
            ),
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


def _case(
    *, agent: bool = False, case_id: str = "release.test"
) -> ArtifactCase | AgentCaseEvidence:
    correlations = Correlations(command_ids=("018f2f00-0000-7000-8000-000000000001",))
    trajectory = tuple(
        SanitizedAgentEvent(
            sequence=index,
            event_type=cast(
                Literal["context_projection", "candidate", "outcome_verification"],
                event_type,
            ),
            durable_identity=f"identity-{index}",
            input_digest="sha256:" + f"{index:064x}",
            output_digest="sha256:" + f"{index + 3:064x}",
        )
        for index, event_type in enumerate(
            ("context_projection", "candidate", "outcome_verification"), start=1
        )
    )
    trajectory_digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                {
                    "rubric_scores": {"quality": True},
                    "trajectory": [event.model_dump(mode="json") for event in trajectory],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    if not agent:
        observation = {"case_id": case_id}
        scenario_digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(observation, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        scenarios = (
            DeterministicScenarioEvidence(
                scenario_id=case_id,
                correlations=correlations,
                observation=observation,
                observation_digest=scenario_digest,
            ),
        )
        return ArtifactCase(
            case_id=case_id,
            case_schema_version=1,
            expected_trials=1,
            observed_trials=1,
            seeds=(0,),
            correlations=correlations,
            observation_digests=(deterministic_observation_digest(scenarios, {}),),
            scenarios=scenarios,
            test_results={},
            verdict=CaseVerdict(status="passed", invariant_violations=()),
        )
    return AgentCaseEvidence(
        case_id="agent.development.test",
        case_schema_version=1,
        configuration_key="test",
        split="development",
        prohibited_action_contract=("external_effect_dispatch",),
        expected_trials=1,
        observed_trials=1,
        seeds=(0,),
        correlations=correlations,
        observation_digests=(trajectory_digest,),
        pass_threshold=1.0,
        passed_trials=1,
        prohibited_actions=0,
        agent_trials=(
            AgentTrialEvidence(
                seed=0,
                outcome_passed=True,
                prohibited_actions=(),
                latency_ms=1,
                trajectory_digest=trajectory_digest,
                correlations=correlations,
                trajectory=trajectory,
                rubric_scores={"quality": True},
            ),
        ),
        verdict=CaseVerdict(status="passed", invariant_violations=()),
    )


def _race_case(case_id: str) -> RaceCase:
    contract = next(case for case in cardinality_one_races() if case.case_id == case_id)
    correlations = Correlations(command_ids=("018f2f00-0000-7000-8000-000000000001",))
    trials = tuple(
        RaceTrialEvidence(
            seed=seed,
            jitter_microseconds=(seed, seed + 1),
            public_outcomes=contract.expected_public_outcomes,
            constraint_rows=1,
            correlations=correlations,
            observation_digest=canonical_digest({"seed": seed}),
            observation={"seed": seed},
            contender_process_ids=(1000 + seed * 2, 1001 + seed * 2),
            overlap_barrier_observed=True,
        )
        for seed in contract.seeds
    )
    return RaceCase(
        case_id=case_id,
        case_schema_version=1,
        expected_trials=100,
        observed_trials=100,
        seeds=contract.seeds,
        correlations=correlations,
        observation_digests=tuple(trial.observation_digest for trial in trials),
        race_trials=trials,
        verdict=CaseVerdict(status="passed", invariant_violations=()),
    )


def test_claim_report_rejects_artifacts_from_different_builds(tmp_path: Path) -> None:
    release_cases = (
        *(_case(case_id=case.case_id) for case in DETERMINISTIC_RELEASE_MATRIX),
        *(_race_case(case.case_id) for case in cardinality_one_races()),
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
        agent_configurations=(
            AgentConfigurationPin(
                agent_key="test",
                agent_version=1,
                instruction_digest="sha256:" + "8" * 64,
                tool_schema_digest="sha256:" + "9" * 64,
                provider="local",
                model="test",
                reasoning="none",
                temperature=0,
            ),
        ),
        cases=(
            _case(agent=True),
            _case(agent=True).model_copy(
                update={"case_id": "agent.held-out.test", "split": "held_out"}
            ),
        ),
        summary=AgentQualitySummary(
            development_cases=1,
            held_out_cases=1,
            expected_trials=2,
            observed_trials=2,
            passed_trials=2,
            prohibited_actions=0,
            threshold_passed=True,
            pass_rate=1,
            wilson_lower=0.34237195288961925,
            wilson_upper=1,
            latency_ms=DistributionSummary(
                count=2,
                mean=1,
                median=1,
                sample_standard_deviation=0,
                minimum=1,
                maximum=1,
            ),
        ),
        limitations=("test",),
    )
    surface = SurfaceAuditArtifact(
        reproducibility=_pin("1" * 40),
        repository=RepositorySurfaceEvidence(
            audited_distributions=("openmagic-runtime",),
            production_dependency_edges=(),
            private_persistence_packages=("openmagic_runtime._persistence",),
            violations=(),
            passed=True,
        ),
        installed=InstalledSurfaceEvidence(
            distributions={"openmagic-runtime": "0.1.0"},
            production_dependency_edges=(),
            private_persistence_packages=("openmagic_runtime._persistence",),
            audited_files=1,
            violations=(),
            passed=True,
        ),
        cold_schema=ColdSchemaEvidence(
            schemas=("openmagic_runtime", "public"),
            tables={"openmagic_runtime": ("migration_history",), "public": ()},
            migration_heads={"openmagic_runtime": "0003"},
            legacy_relations=(),
            violations=(),
            passed=True,
        ),
        summary=SurfaceAuditSummary(
            repository_passed=True,
            installed_surface_passed=True,
            cold_schema_passed=True,
            strict_pass=True,
        ),
        limitations=("test",),
    )
    deterministic_path = tmp_path / "deterministic.json"
    agent_path = tmp_path / "agent.json"
    surface_path = tmp_path / "surface.json"
    deterministic_path.write_text(canonical_artifact_json(deterministic), encoding="utf-8")
    agent_path.write_text(canonical_artifact_json(agent), encoding="utf-8")
    surface_path.write_text(canonical_artifact_json(surface), encoding="utf-8")

    with pytest.raises(ValueError, match="reproducibility pin"):
        write_claim_report(
            deterministic_path=deterministic_path,
            surface_path=surface_path,
            agent_path=agent_path,
            output=tmp_path / "claims.md",
        )
