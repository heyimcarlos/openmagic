from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast

import pytest
from openmagic_evals.evidence.__main__ import _parser
from openmagic_evals.evidence.claims import (
    _validate_common_reproducibility,
    _validate_release_matrix,
)
from openmagic_evals.evidence.contracts import (
    REQUIRED_NEGATIVE_CLAIMS,
    AgentAggregate,
    AgentCaseEvidence,
    AgentConfigurationPin,
    AgentCorpusPin,
    AgentQualityArtifact,
    AgentQualitySummary,
    AgentSplitSummary,
    AgentTrialEvidence,
    ArtifactCase,
    BoundaryAgentCandidateObservation,
    BoundaryAgentScorerContract,
    BuildPin,
    CaseVerdict,
    ColdSchemaEvidence,
    Correlations,
    DeterministicArtifact,
    DeterministicScenarioEvidence,
    DeterministicSummary,
    DistributionSummary,
    EnvironmentVariablePin,
    ExecutablePin,
    InstalledSurfaceEvidence,
    PostgresDeploymentPin,
    RaceCase,
    RaceTrialEvidence,
    RepositorySurfaceEvidence,
    ReproducibilityPin,
    SanitizedAgentEvent,
    SurfaceAuditArtifact,
    SurfaceAuditSummary,
    WheelArchivePin,
    aggregate_agent_trials,
    canonical_artifact_json,
    canonical_digest,
    deterministic_observation_digest,
    race_trial_digest,
)
from openmagic_evals.evidence.matrix import DETERMINISTIC_RELEASE_MATRIX, cardinality_one_races
from pydantic import JsonValue


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
            wheel_archives={
                "openmagic-evals": WheelArchivePin(
                    filename="openmagic_evals-0.1.0-py3-none-any.whl",
                    archive_digest="sha256:" + "7" * 64,
                    record_digest="sha256:" + "8" * 64,
                    metadata_digest="sha256:" + "9" * 64,
                )
            },
        ),
        suite_version="issue-71.v1",
        command=("openmagic-evidence", "test"),
        environment={
            "GIT_CONFIG_GLOBAL": EnvironmentVariablePin(
                value=os.devnull, digest=canonical_digest(os.devnull)
            ),
            "GIT_CONFIG_NOSYSTEM": EnvironmentVariablePin(value="1", digest=canonical_digest("1")),
            "LANG": EnvironmentVariablePin(value="C.UTF-8", digest=canonical_digest("C.UTF-8")),
            "LC_ALL": EnvironmentVariablePin(value="C.UTF-8", digest=canonical_digest("C.UTF-8")),
            "PATH": EnvironmentVariablePin(
                value="/bin:/usr/bin", digest=canonical_digest("/bin:/usr/bin")
            ),
            "PYTHONNOUSERSITE": EnvironmentVariablePin(value="1", digest=canonical_digest("1")),
        },
        executables={
            name: ExecutablePin(path=f"/fixture/{name}", content_digest="sha256:" + "e" * 64)
            for name in (
                "docker",
                "git",
                "openmagic-api",
                "openmagic-delivery-worker",
                "openmagic-evidence",
                "openmagic-local-email-provider",
                "openmagic-playground",
                "openmagic-workflow-worker",
                "python",
            )
        },
        started_at=datetime(2026, 7, 15, tzinfo=UTC),
        finished_at=datetime(2026, 7, 15, 0, 1, tzinfo=UTC),
        timeout_seconds=60,
        postgres_deployments=(
            PostgresDeploymentPin(
                deployment_id="sha256:" + "a" * 64,
                postgres_version="17.5",
                postgres_image="postgres@sha256:" + "2" * 64,
                postgres_configuration={"transaction_isolation": "read committed"},
                postgres_configuration_digest=canonical_digest(
                    {"transaction_isolation": "read committed"}
                ),
                migration_heads={
                    "example_insurance": "0004",
                    "openmagic_runtime": "0003",
                },
            ),
        ),
        definition_digests={"example_insurance.renewal_outreach:2": "sha256:" + "4" * 64},
        case_corpus_digest="sha256:" + "5" * 64,
        sandbox_digest="sha256:" + "6" * 64,
    )


def _case(
    *, agent: bool = False, case_id: str = "release.test"
) -> ArtifactCase | AgentCaseEvidence:
    correlations = Correlations(runtime={"command_ids": ("018f2f00-0000-7000-8000-000000000001",)})
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
    candidate_observation = BoundaryAgentCandidateObservation(
        observed_boundary="malformed_result",
        execution_failure_reason="malformed_result",
    )
    rubric_scores = {
        "expected_boundary_rejection": True,
        "no_candidate_accepted": True,
        "safety_boundary": True,
    }
    trajectory_digest = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(
                {
                    "candidate_observation": candidate_observation.model_dump(mode="json"),
                    "rubric_scores": rubric_scores,
                    "trajectory": [event.model_dump(mode="json") for event in trajectory],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
    )
    if not agent:
        contract = next(case for case in DETERMINISTIC_RELEASE_MATRIX if case.case_id == case_id)
        scenarios = tuple(
            DeterministicScenarioEvidence(
                scenario_id=scenario_id,
                correlations=correlations,
                observation={"case_id": case_id, "scenario_id": scenario_id},
                observation_digest=canonical_digest(
                    {"case_id": case_id, "scenario_id": scenario_id}
                ),
            )
            for scenario_id in sorted(contract.required_scenarios)
        )
        test_results: dict[str, dict[str, JsonValue]] = {
            node: {
                "status": "passed",
                "duration_seconds": 0.1,
                "detail_digest": "sha256:" + "a" * 64,
            }
            for node in contract.pytest_nodes
        }
        return ArtifactCase(
            case_id=case_id,
            case_schema_version=1,
            expected_trials=1,
            observed_trials=1,
            seeds=(0,),
            correlations=correlations,
            observation_digests=(deterministic_observation_digest(scenarios, test_results),),
            scenarios=scenarios,
            test_results=test_results,
            verdict=CaseVerdict(status="passed", invariant_violations=()),
        )
    return AgentCaseEvidence(
        case_id="agent.development.test",
        case_schema_version=1,
        configuration_key="test",
        split="development",
        prohibited_action_contract=("external_effect_dispatch",),
        scorer_contract=BoundaryAgentScorerContract(expected_boundary="malformed_result"),
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
                candidate_observation=candidate_observation,
                rubric_scores=rubric_scores,
            ),
        ),
        verdict=CaseVerdict(status="passed", invariant_violations=()),
    )


def _race_case(case_id: str) -> RaceCase:
    contract = next(case for case in cardinality_one_races() if case.case_id == case_id)
    correlations = Correlations(runtime={"command_ids": ("018f2f00-0000-7000-8000-000000000001",)})
    trials = tuple(
        RaceTrialEvidence(
            seed=seed,
            jitter_microseconds=(seed, seed + 1),
            public_outcomes=contract.expected_public_outcomes,
            constraint_rows=1,
            correlations=correlations,
            observation_digest=race_trial_digest(
                seed=seed,
                jitter_microseconds=(seed, seed + 1),
                public_outcomes=contract.expected_public_outcomes,
                constraint_rows=1,
                correlations=correlations,
                observation={"seed": seed},
                contender_process_ids=(1000 + seed * 2, 1001 + seed * 2),
                overlap_barrier_observed=True,
            ),
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
    first = deterministic.cases[0]
    assert isinstance(first, ArtifactCase)
    incomplete = deterministic.model_copy(
        update={"cases": (first.model_copy(update={"test_results": {}}), *deterministic.cases[1:])}
    )
    with pytest.raises(ValueError, match="deterministic proof is incomplete"):
        _validate_release_matrix(incomplete)
    agent = AgentQualityArtifact(
        reproducibility=_pin("2" * 40),
        corpus=AgentCorpusPin(
            development_cases_digest="sha256:" + "a" * 64,
            held_out_corpus_version="test.v1",
            held_out_cases_digest="sha256:" + "b" * 64,
            held_out_sealed_at_commit="1" * 40,
            runner_frozen_at_commit="2" * 40,
            tuning_locked_roots=("example/agent",),
            tuning_locked_source_digest="sha256:" + "c" * 64,
            execution_phases=("development", "held_out"),
            tuning_unchanged_after_seal=True,
        ),
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
            development=AgentSplitSummary(
                case_count=1,
                expected_trials=1,
                aggregate=AgentAggregate(
                    observed_trials=1,
                    passed_trials=1,
                    prohibited_actions=0,
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
                threshold_passed=True,
            ),
            held_out=AgentSplitSummary(
                case_count=1,
                expected_trials=1,
                aggregate=AgentAggregate(
                    observed_trials=1,
                    passed_trials=1,
                    prohibited_actions=0,
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
                threshold_passed=True,
            ),
            combined=AgentAggregate(
                observed_trials=2,
                passed_trials=2,
                prohibited_actions=0,
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
            threshold_passed=True,
        ),
        limitations=("test",),
    )
    development_trials = tuple(
        trial for case in agent.cases if case.split == "development" for trial in case.agent_trials
    )
    held_out_trials = tuple(
        trial for case in agent.cases if case.split == "held_out" for trial in case.agent_trials
    )
    development_aggregate = aggregate_agent_trials(development_trials)
    held_out_aggregate = aggregate_agent_trials(held_out_trials)
    assert agent.summary.development.aggregate == development_aggregate
    assert agent.summary.development.aggregate.observed_trials == len(development_trials)
    assert agent.summary.held_out.aggregate == held_out_aggregate
    assert agent.summary.held_out.aggregate.observed_trials == len(held_out_trials)
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
        _validate_common_reproducibility(
            (
                ("deterministic", deterministic_path, deterministic),
                ("surface-audit", surface_path, surface),
                ("agent-quality", agent_path, agent),
            )
        )

    changed_executables = dict(deterministic.reproducibility.executables)
    changed_executables["docker"] = ExecutablePin(
        path="/different/docker",
        content_digest="sha256:" + "f" * 64,
    )
    changed = deterministic.model_copy(
        update={
            "reproducibility": deterministic.reproducibility.model_copy(
                update={"executables": changed_executables}
            )
        }
    )
    with pytest.raises(ValueError, match="reproducibility pin"):
        _validate_common_reproducibility(
            (
                ("first", deterministic_path, deterministic),
                ("different-helper", deterministic_path, changed),
            )
        )


def test_claim_report_cli_rejects_an_incomplete_evidence_package(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args(
            [
                "claim-report",
                "--deterministic",
                str(tmp_path / "deterministic.json"),
                "--surface-audit",
                str(tmp_path / "surface.json"),
                "--output",
                str(tmp_path / "claims.md"),
            ]
        )
