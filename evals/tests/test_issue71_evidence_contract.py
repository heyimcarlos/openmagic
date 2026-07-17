from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from typing import Literal, cast
from uuid import UUID

import pytest
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
    AttemptAuthorityEvidence,
    BoundaryAgentCandidateObservation,
    BoundaryAgentScorerContract,
    BuildPin,
    CaseVerdict,
    Correlations,
    DeliveryAuthorityEvidence,
    DeterministicArtifact,
    DeterministicScenarioEvidence,
    DeterministicSummary,
    DistributionSummary,
    EnvironmentVariablePin,
    ExecutablePin,
    ForcedProcessLoss,
    InstanceDefinitionCorrelation,
    PostgresDeploymentPin,
    ProcessCase,
    ProcessContract,
    ProcessIdentityEvidence,
    ProcessMetrics,
    ProcessObservation,
    QueueDepth,
    RaceTrialEvidence,
    RepositorySurfaceEvidence,
    ReproducibilityPin,
    RuntimeCorrelations,
    SanitizedAgentEvent,
    SanitizedObservation,
    WheelArchivePin,
    canonical_artifact_json,
    canonical_digest,
    deterministic_observation_digest,
    parse_artifact,
    race_trial_digest,
    summarize_agent_cases,
    summarize_agent_configurations,
)
from openmagic_evals.evidence.redaction import RedactionViolation, audit_redaction
from openmagic_evals.evidence.reproducibility import (
    fixed_executable_path,
    fixed_execution_environment,
)
from pydantic import JsonValue


def _pin() -> ReproducibilityPin:
    return ReproducibilityPin(
        build=BuildPin(
            git_sha="1f38698d73b7e609528861a0faf49e49acf617f2",
            checkout_clean=True,
            lock_digest="sha256:" + "a" * 64,
            distributions={
                "example-insurance": "0.1.0",
                "openmagic-api": "0.1.0",
                "openmagic-evals": "0.1.0",
                "openmagic-runtime": "0.1.0",
            },
            distribution_digests={
                "example-insurance": "sha256:" + "0" * 64,
                "openmagic-api": "sha256:" + "1" * 64,
                "openmagic-evals": "sha256:" + "2" * 64,
                "openmagic-runtime": "sha256:" + "3" * 64,
            },
            source_distribution_digests={
                "example-insurance": "sha256:" + "0" * 64,
                "openmagic-api": "sha256:" + "1" * 64,
                "openmagic-evals": "sha256:" + "2" * 64,
                "openmagic-runtime": "sha256:" + "3" * 64,
            },
            installation_kinds=cast(
                dict[str, Literal["wheel", "editable"]],
                {
                    "example-insurance": "wheel",
                    "openmagic-api": "wheel",
                    "openmagic-evals": "wheel",
                    "openmagic-runtime": "wheel",
                },
            ),
            wheel_archives={
                name: WheelArchivePin(
                    filename=f"{name}-0.1.0-py3-none-any.whl",
                    archive_digest="sha256:" + digest * 64,
                    record_digest="sha256:" + digest * 64,
                    metadata_digest="sha256:" + digest * 64,
                )
                for name, digest in zip(
                    (
                        "example-insurance",
                        "openmagic-api",
                        "openmagic-evals",
                        "openmagic-runtime",
                    ),
                    ("0", "1", "2", "3"),
                    strict=True,
                )
            },
        ),
        suite_version="issue-71.v1",
        command=("uv", "run", "openmagic-evidence", "deterministic"),
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
        started_at=datetime(2026, 7, 15, 20, 0, tzinfo=UTC),
        finished_at=datetime(2026, 7, 15, 20, 1, tzinfo=UTC),
        timeout_seconds=900,
        postgres_deployments=(
            PostgresDeploymentPin(
                deployment_id="sha256:" + "d" * 64,
                postgres_version="17.5",
                postgres_image="postgres@sha256:" + "1" * 64,
                postgres_configuration={
                    "default_transaction_isolation": "read committed",
                    "max_connections": "100",
                    "observer_transaction_isolation": "repeatable read",
                    "synchronous_commit": "on",
                    "timezone": "UTC",
                },
                postgres_configuration_digest=canonical_digest(
                    {
                        "default_transaction_isolation": "read committed",
                        "max_connections": "100",
                        "observer_transaction_isolation": "repeatable read",
                        "synchronous_commit": "on",
                        "timezone": "UTC",
                    }
                ),
                migration_heads={
                    "example_insurance": "0004_deterministic_verification",
                    "openmagic_runtime": "0003_fenced_effect_kernel",
                },
            ),
        ),
        definition_digests={"example_insurance.renewal:1": "sha256:" + "c" * 64},
    )


def _case(case_id: str = "command.exact_replay") -> ArtifactCase:
    correlations = Correlations(runtime={"command_ids": ("018f2f00-0000-7000-8000-000000000001",)})
    observation = {"outcome": "passed"}
    observation_digest = (
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
            observation_digest=observation_digest,
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


def test_postgres_provenance_is_fail_closed() -> None:
    pin = _pin()
    deployment = pin.postgres_deployments[0]

    with pytest.raises(ValueError, match="exact deployment provenance"):
        ReproducibilityPin.model_validate(
            pin.model_copy(update={"postgres_deployments": ()}).model_dump()
        )

    with pytest.raises(ValueError, match="configuration digest"):
        PostgresDeploymentPin.model_validate(
            deployment.model_copy(
                update={"postgres_configuration_digest": "sha256:" + "0" * 64}
            ).model_dump()
        )

    with pytest.raises(ValueError, match="concrete migration heads"):
        ReproducibilityPin.model_validate(
            pin.model_copy(
                update={
                    "postgres_deployments": (
                        deployment.model_copy(
                            update={
                                "migration_heads": {
                                    "example_insurance": None,
                                    "openmagic_runtime": "0003_fenced_effect_kernel",
                                }
                            }
                        ),
                    )
                }
            ).model_dump()
        )


def test_execution_provenance_pins_environment_values_and_executable_content() -> None:
    pin = _pin()

    assert pin.environment["PATH"].value
    assert pin.environment["PATH"].digest == canonical_digest(pin.environment["PATH"].value)
    assert pin.executables["python"].path.startswith("/")

    with pytest.raises(ValueError, match="environment value digest"):
        EnvironmentVariablePin(value="/bin", digest="sha256:" + "0" * 64)
    with pytest.raises(ValueError, match="absolute path"):
        ExecutablePin(path="bin/python", content_digest="sha256:" + "0" * 64)

    assert fixed_execution_environment() == {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.defpath,
        "PYTHONNOUSERSITE": "1",
    }
    assert fixed_executable_path("git").is_absolute()
    assert fixed_executable_path("docker").is_absolute()


def test_runtime_correlations_reject_instance_without_exact_definition() -> None:
    instance_id = UUID("018f2f00-0000-7000-8000-000000000001")

    with pytest.raises(ValueError, match="every observed Instance"):
        RuntimeCorrelations(instance_ids=(instance_id,))
    with pytest.raises(ValueError, match="every observed Instance"):
        RuntimeCorrelations(
            instance_definitions=(
                InstanceDefinitionCorrelation(
                    instance_id=instance_id,
                    definition_key="example.workflow",
                    definition_version=1,
                ),
            )
        )

    mapping = InstanceDefinitionCorrelation(
        instance_id=instance_id,
        definition_key="example.workflow",
        definition_version=1,
    )
    with pytest.raises(ValueError, match="Instance identities must be unique"):
        RuntimeCorrelations(
            instance_ids=(instance_id, instance_id),
            instance_definitions=(mapping,),
        )
    with pytest.raises(ValueError, match="only one Definition"):
        RuntimeCorrelations(
            instance_ids=(instance_id,),
            instance_definitions=(
                mapping,
                mapping.model_copy(update={"definition_version": 2}),
            ),
        )

    with pytest.raises(ValueError, match="stable key grammar"):
        InstanceDefinitionCorrelation(
            instance_id=instance_id,
            definition_key="Invalid Definition",
            definition_version=1,
        )


def test_reproducibility_rejects_malformed_definition_pins() -> None:
    document = _pin().model_dump(mode="python")
    document["definition_digests"] = {"invalid": "sha256:" + "c" * 64}
    with pytest.raises(ValueError, match="stable key and positive version"):
        ReproducibilityPin.model_validate(document)

    document["definition_digests"] = {"example.workflow:1": "not-a-digest"}
    with pytest.raises(ValueError, match="Definition digest"):
        ReproducibilityPin.model_validate(document)


def test_artifact_rejects_instance_definition_missing_from_reproducibility_pin() -> None:
    instance_id = UUID("018f2f00-0000-7000-8000-000000000001")
    correlations = Correlations(
        runtime=RuntimeCorrelations(
            instance_ids=(instance_id,),
            instance_definitions=(
                InstanceDefinitionCorrelation(
                    instance_id=instance_id,
                    definition_key="unregistered.workflow",
                    definition_version=7,
                ),
            ),
        )
    )
    observation = {"connected": True}
    scenario = DeterministicScenarioEvidence(
        scenario_id="definition-pin",
        correlations=correlations,
        observation=observation,
        observation_digest=canonical_digest(observation),
    )
    case = ArtifactCase(
        case_id="definition.pin",
        case_schema_version=1,
        expected_trials=1,
        observed_trials=1,
        seeds=(0,),
        correlations=correlations,
        observation_digests=(deterministic_observation_digest((scenario,), {}),),
        scenarios=(scenario,),
        test_results={},
        verdict=CaseVerdict(status="passed", invariant_violations=()),
    )

    with pytest.raises(ValueError, match="unpinned Definitions"):
        DeterministicArtifact(
            reproducibility=_pin(),
            cases=(case,),
            summary=DeterministicSummary(
                expected_cases=1,
                observed_cases=1,
                passed_cases=1,
                failed_cases=0,
                infrastructure_errors=0,
                invariant_violations=0,
                strict_pass=True,
                runner_exit_code=0,
            ),
            limitations=("fixture",),
            negative_claims=REQUIRED_NEGATIVE_CLAIMS,
        )


def test_process_contract_requires_independent_burst_capacity() -> None:
    with pytest.raises(ValueError, match="must increase every role"):
        ProcessContract(
            scenario_version="process.loss-backpressure-recovery.v1",
            queued_workflows=4,
            initial_capacity={"api": 1, "workflow-worker": 1, "delivery-worker": 1},
            burst_capacity={"api": 1, "workflow-worker": 2, "delivery-worker": 2},
            provider_behavior="slow_success",
            provider_delay_seconds=1,
            forced_loss_points=(
                "api-readiness",
                "workflow-worker-provider-io",
                "delivery-worker-message-lock",
            ),
            queue_predicates=(
                "pending-steps-equal-workflow-denominator",
                "pending-steps-and-deliveries-drain-to-zero",
            ),
            recovery_timeout_seconds=10,
        )


def _agent_configuration() -> AgentConfigurationPin:
    return AgentConfigurationPin(
        agent_key="renewal_outreach",
        agent_version=1,
        instruction_digest="sha256:" + "e" * 64,
        tool_schema_digest="sha256:" + "f" * 64,
        provider="local",
        model="deterministic",
        reasoning="none",
        temperature=0.0,
    )


def _agent_corpus() -> AgentCorpusPin:
    return AgentCorpusPin(
        development_cases_digest="sha256:" + "a" * 64,
        held_out_corpus_version="test.v1",
        held_out_cases_digest="sha256:" + "b" * 64,
        held_out_sealed_at_commit="1" * 40,
        runner_frozen_at_commit="2" * 40,
        tuning_locked_roots=("example/agent",),
        tuning_locked_source_digest="sha256:" + "c" * 64,
        execution_phases=("development", "held_out"),
        tuning_unchanged_after_seal=True,
    )


def _agent_case() -> AgentCaseEvidence:
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
    trajectory_document = json.dumps(
        {
            "candidate_observation": candidate_observation.model_dump(mode="json"),
            "rubric_scores": rubric_scores,
            "trajectory": [event.model_dump(mode="json") for event in trajectory],
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    trajectory_digest = "sha256:" + hashlib.sha256(trajectory_document).hexdigest()
    return AgentCaseEvidence(
        case_id="agent.development.tool-choice",
        case_schema_version=1,
        configuration_key="renewal_outreach",
        split="development",
        prohibited_action_contract=("external_effect_dispatch",),
        scorer_contract=BoundaryAgentScorerContract(expected_boundary="malformed_result"),
        expected_trials=1,
        observed_trials=1,
        seeds=(0,),
        correlations=correlations,
        observation_digests=(trajectory_digest,),
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
        pass_threshold=0.75,
        passed_trials=1,
        prohibited_actions=0,
        verdict=CaseVerdict(status="passed", invariant_violations=()),
    )


def test_deterministic_artifact_round_trips_as_canonical_versioned_json() -> None:
    artifact = DeterministicArtifact(
        reproducibility=_pin(),
        cases=(_case(),),
        summary=DeterministicSummary(
            expected_cases=1,
            observed_cases=1,
            passed_cases=1,
            failed_cases=0,
            infrastructure_errors=0,
            invariant_violations=0,
            strict_pass=True,
            runner_exit_code=0,
        ),
        limitations=("single PostgreSQL deployment shape",),
        negative_claims=REQUIRED_NEGATIVE_CLAIMS,
    )

    encoded = canonical_artifact_json(artifact)
    decoded = parse_artifact(encoded)

    assert decoded == artifact
    assert encoded.endswith("\n")
    assert json.loads(encoded)["schema_version"] == "openmagic.enterprise-evidence.v1"
    assert audit_redaction(json.loads(encoded)).passed


def test_artifacts_reject_incomplete_denominators_and_lane_substitution() -> None:
    with pytest.raises(ValueError, match="observed trials"):
        ArtifactCase(
            case_id="agent.held-out.ambiguous",
            case_schema_version=1,
            expected_trials=5,
            observed_trials=4,
            seeds=(0, 1, 2, 3),
            correlations=Correlations(),
            observation_digests=("sha256:" + "e" * 64,) * 4,
            scenarios=_case().scenarios,
            test_results={},
            verdict=CaseVerdict(status="passed", invariant_violations=()),
        )

    with pytest.raises(ValueError, match="at least 1 item"):
        DeterministicArtifact(
            reproducibility=_pin(),
            cases=(),
            summary=DeterministicSummary(
                expected_cases=1,
                observed_cases=1,
                passed_cases=1,
                failed_cases=0,
                infrastructure_errors=0,
                invariant_violations=0,
                strict_pass=True,
                runner_exit_code=0,
            ),
            limitations=("invalid empty fixture",),
            negative_claims=REQUIRED_NEGATIVE_CLAIMS,
        )

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        ArtifactCase.model_validate(
            {
                **_case().model_dump(mode="python"),
                "split": "held_out",
                "pass_threshold": 0.75,
                "passed_trials": 1,
                "prohibited_actions": 7,
            }
        )


def test_race_trial_requires_cardinality_one_and_durable_correlations() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        RaceTrialEvidence(
            seed=0,
            jitter_microseconds=(1, 2),
            public_outcomes=("won", "lost"),
            constraint_rows=2,
            correlations=Correlations(
                runtime={"command_ids": ("018f2f00-0000-7000-8000-000000000001",)}
            ),
            observation_digest="sha256:" + "1" * 64,
            observation={"seed": 0},
            contender_process_ids=(101, 102),
            overlap_barrier_observed=True,
        )

    with pytest.raises(ValueError, match="correlate"):
        RaceTrialEvidence(
            seed=0,
            jitter_microseconds=(1, 2),
            public_outcomes=("won", "lost"),
            constraint_rows=1,
            correlations=Correlations(),
            observation_digest="sha256:" + "1" * 64,
            observation={"seed": 0},
            contender_process_ids=(101, 102),
            overlap_barrier_observed=True,
        )

    agent_case = _agent_case()
    with pytest.raises(ValueError, match="Agent quality cannot determine"):
        AgentQualityArtifact(
            reproducibility=_pin(),
            corpus=_agent_corpus(),
            agent_configurations=(_agent_configuration(),),
            cases=(agent_case,),
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
                cases=summarize_agent_cases((agent_case,)),
                configurations=summarize_agent_configurations(
                    (agent_case,),
                    ("renewal_outreach",),
                    summarize_agent_cases((agent_case,)),
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
                deterministic_release_pass=True,
            ),
            limitations=("local scripted Agent only",),
        )


def test_race_digest_binds_every_claim_bearing_field() -> None:
    correlations = Correlations(runtime={"command_ids": ("018f2f00-0000-7000-8000-000000000001",)})
    observation: dict[str, JsonValue] = {"durable_id": "018f2f00-0000-7000-8000-000000000001"}
    trial = RaceTrialEvidence(
        seed=7,
        jitter_microseconds=(10, 20),
        public_outcomes=("won", "lost"),
        constraint_rows=1,
        correlations=correlations,
        observation=observation,
        contender_process_ids=(101, 102),
        overlap_barrier_observed=True,
        observation_digest=race_trial_digest(
            seed=7,
            jitter_microseconds=(10, 20),
            public_outcomes=("won", "lost"),
            constraint_rows=1,
            correlations=correlations,
            observation=observation,
            contender_process_ids=(101, 102),
            overlap_barrier_observed=True,
        ),
    )
    document = trial.model_dump(mode="python")
    document["seed"] = 8

    with pytest.raises(ValueError, match="digest does not match"):
        RaceTrialEvidence.model_validate(document)


def test_artifact_requires_all_negative_claims() -> None:
    with pytest.raises(ValueError, match="negative claims"):
        DeterministicArtifact(
            reproducibility=_pin(),
            cases=(_case(),),
            summary=DeterministicSummary(
                expected_cases=1,
                observed_cases=1,
                passed_cases=1,
                failed_cases=0,
                infrastructure_errors=0,
                invariant_violations=0,
                strict_pass=True,
                runner_exit_code=0,
            ),
            limitations=("single PostgreSQL deployment shape",),
            negative_claims=REQUIRED_NEGATIVE_CLAIMS[:-1],
        )


def test_process_metrics_require_independent_roles_losses_and_drained_queues() -> None:
    metrics = ProcessMetrics(
        queued_workflows=12,
        initial_queue=QueueDepth(pending_steps=12, pending_deliveries=0),
        drained_queue=QueueDepth(pending_steps=0, pending_deliveries=0),
        initial_capacity={"api": 1, "workflow-worker": 1, "delivery-worker": 1},
        started_processes={"api": 1, "workflow-worker": 4, "delivery-worker": 3},
        forced_losses={"api": 1, "workflow-worker": 1, "delivery-worker": 1},
        fresh_interpreters=True,
        postgresql_only_reconstruction=True,
        elapsed_ms=250,
        claim_latency_ms=DistributionSummary(
            count=1,
            mean=10,
            median=10,
            sample_standard_deviation=0,
            minimum=10,
            maximum=10,
        ),
        recovery_time_ms=DistributionSummary(
            count=3,
            mean=20,
            median=20,
            sample_standard_deviation=0,
            minimum=20,
            maximum=20,
        ),
        lock_wait_lower_bound_ms=DistributionSummary(
            count=1,
            mean=5,
            median=5,
            sample_standard_deviation=0,
            minimum=5,
            maximum=5,
        ),
        observed_throughput_per_second=4.0,
    )

    assert metrics.initial_queue.pending_steps == metrics.queued_workflows

    with pytest.raises(ValueError, match="queues drained"):
        metrics.model_copy(
            update={"drained_queue": QueueDepth(pending_steps=0, pending_deliveries=1)}
        ).model_validate(
            metrics.model_copy(
                update={"drained_queue": QueueDepth(pending_steps=0, pending_deliveries=1)}
            ).model_dump()
        )


def _process_case() -> ProcessCase:
    workload = tuple(
        SanitizedObservation(
            document={"ordinal": ordinal}, digest=canonical_digest({"ordinal": ordinal})
        )
        for ordinal in range(4)
    )
    api = SanitizedObservation(document={"role": "api"}, digest=canonical_digest({"role": "api"}))
    observation = ProcessObservation(
        initial_processes=(
            ProcessIdentityEvidence(role="api", pid=10, worker_id=None),
            ProcessIdentityEvidence(role="workflow-worker", pid=11, worker_id="workflow-old"),
            ProcessIdentityEvidence(role="delivery-worker", pid=12, worker_id="delivery-old"),
        ),
        replacement_processes=(
            ProcessIdentityEvidence(role="api", pid=20, worker_id=None),
            ProcessIdentityEvidence(role="workflow-worker", pid=21, worker_id="workflow-lost"),
            ProcessIdentityEvidence(role="delivery-worker", pid=22, worker_id="delivery-lost"),
            ProcessIdentityEvidence(role="api", pid=23, worker_id=None),
            ProcessIdentityEvidence(role="workflow-worker", pid=24, worker_id="workflow-burst"),
            ProcessIdentityEvidence(role="delivery-worker", pid=25, worker_id="delivery-burst"),
        ),
        forced_losses=(
            ForcedProcessLoss(role="api", pid=20),
            ForcedProcessLoss(role="workflow-worker", pid=21),
            ForcedProcessLoss(role="delivery-worker", pid=22),
        ),
        lost_attempt=AttemptAuthorityEvidence(
            instance_id=UUID(int=1),
            instance_definition=InstanceDefinitionCorrelation(
                instance_id=UUID(int=1),
                definition_key="example_insurance.renewal_outreach",
                definition_version=2,
            ),
            step_id=UUID(int=2),
            attempt_id=UUID(int=3),
            worker_id="workflow-lost",
        ),
        lost_delivery=DeliveryAuthorityEvidence(
            delivery_id=UUID(int=4),
            delivery_attempt_id=UUID(int=5),
            thread_id=UUID(int=6),
            worker_id="delivery-lost",
        ),
        workload_correlations=Correlations(),
        workload_observations=workload,
        api_observations=(api, api),
    )
    contract = ProcessContract(
        scenario_version="process.loss-backpressure-recovery.v1",
        queued_workflows=4,
        initial_capacity={"api": 1, "workflow-worker": 1, "delivery-worker": 1},
        burst_capacity={"api": 2, "workflow-worker": 2, "delivery-worker": 2},
        provider_behavior="slow_success",
        provider_delay_seconds=1,
        forced_loss_points=(
            "api-readiness",
            "workflow-worker-provider-io",
            "delivery-worker-message-lock",
        ),
        queue_predicates=(
            "pending-steps-equal-workflow-denominator",
            "pending-steps-and-deliveries-drain-to-zero",
        ),
        recovery_timeout_seconds=10,
    )
    one_sample = DistributionSummary(
        count=1,
        mean=1,
        median=1,
        sample_standard_deviation=0,
        minimum=1,
        maximum=1,
    )
    metrics = ProcessMetrics(
        queued_workflows=4,
        initial_queue=QueueDepth(pending_steps=4, pending_deliveries=0),
        drained_queue=QueueDepth(pending_steps=0, pending_deliveries=0),
        initial_capacity={"api": 1, "workflow-worker": 1, "delivery-worker": 1},
        started_processes={"api": 2, "workflow-worker": 2, "delivery-worker": 2},
        forced_losses={"api": 1, "workflow-worker": 1, "delivery-worker": 1},
        fresh_interpreters=True,
        postgresql_only_reconstruction=True,
        elapsed_ms=1,
        claim_latency_ms=one_sample,
        recovery_time_ms=one_sample.model_copy(update={"count": 3}),
        lock_wait_lower_bound_ms=one_sample,
        observed_throughput_per_second=1,
    )
    correlations = Correlations(
        runtime={
            "instance_ids": (UUID(int=1),),
            "instance_definitions": (
                InstanceDefinitionCorrelation(
                    instance_id=UUID(int=1),
                    definition_key="example_insurance.renewal_outreach",
                    definition_version=2,
                ),
            ),
            "step_ids": (UUID(int=2),),
            "attempt_ids": (UUID(int=3),),
        },
        application={
            "thread_ids": (UUID(int=6),),
            "delivery_ids": (UUID(int=4),),
            "delivery_attempt_ids": (UUID(int=5),),
        },
        process={
            "worker_ids": (
                "workflow-old",
                "delivery-old",
                "workflow-lost",
                "delivery-lost",
                "workflow-burst",
                "delivery-burst",
            ),
            "process_ids": (10, 11, 12, 20, 21, 22, 23, 24, 25),
        },
    )
    proof = {
        "contract": contract.model_dump(mode="json"),
        "metrics": metrics.model_dump(mode="json"),
        "observation": observation.model_dump(mode="json"),
        "correlations": correlations.model_dump(mode="json"),
    }
    return ProcessCase(
        case_id="process.contract-test",
        case_schema_version=1,
        expected_trials=1,
        observed_trials=1,
        seeds=(0,),
        correlations=correlations,
        observation_digests=(canonical_digest(proof),),
        verdict=CaseVerdict(status="passed", invariant_violations=()),
        process_metrics=metrics,
        process_contract=contract,
        process_observation=observation,
    )


def _rehash_process_payload(payload: dict[str, object]) -> None:
    payload["observation_digests"] = (
        canonical_digest(
            {
                "contract": payload["process_contract"],
                "metrics": payload["process_metrics"],
                "observation": payload["process_observation"],
                "correlations": payload["correlations"],
            }
        ),
    )


def test_process_case_derives_loss_counts_from_typed_identities() -> None:
    payload = _process_case().model_dump(mode="json")
    payload["process_metrics"]["forced_losses"] = {
        "api": 2,
        "workflow-worker": 2,
        "delivery-worker": 2,
    }
    payload["process_metrics"]["recovery_time_ms"]["count"] = 6
    _rehash_process_payload(payload)

    with pytest.raises(ValueError, match="loss metrics must derive"):
        ProcessCase.model_validate(payload)


def test_process_case_binds_lost_authorities_to_role_correct_workers() -> None:
    payload = _process_case().model_dump(mode="json")
    payload["process_observation"]["lost_attempt"]["worker_id"] = "delivery-lost"
    _rehash_process_payload(payload)

    with pytest.raises(ValueError, match="forced Workflow Worker"):
        ProcessCase.model_validate(payload)


def test_process_observation_rejects_duplicate_worker_identities() -> None:
    payload = _process_case().process_observation.model_dump(mode="python")
    payload["replacement_processes"][2]["worker_id"] = "workflow-lost"

    with pytest.raises(ValueError, match="unique worker identity"):
        ProcessObservation.model_validate(payload)


def test_process_case_derives_correlations_from_complete_proof() -> None:
    payload = _process_case().model_dump(mode="json")
    payload["correlations"] = Correlations().model_dump(mode="json")
    _rehash_process_payload(payload)

    with pytest.raises(ValueError, match="correlations must derive"):
        ProcessCase.model_validate(payload)


def test_surface_verdict_cannot_hide_recorded_violations() -> None:
    with pytest.raises(ValueError, match="derive from recorded violations"):
        RepositorySurfaceEvidence(
            audited_distributions=("openmagic-runtime",),
            production_dependency_edges=(),
            private_persistence_packages=("openmagic_runtime._persistence",),
            violations=("unexpected public export",),
            passed=True,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"provider_token": "secret-value"},
        {"database_url": "postgresql://user:password@db/openmagic"},
        {"raw_message_content": "A real customer renewal request"},
        {"authorization": "Bearer credential"},
        {"verification_code": "123456"},
        {"api_key": "configured-elsewhere"},
        {"credential": "configured-elsewhere"},
        {"value": "sk-proj-abcdefghijklmnopqrstuvwxyz"},
    ],
)
def test_redaction_audit_rejects_secret_and_sensitive_raw_content(payload: object) -> None:
    with pytest.raises(RedactionViolation):
        audit_redaction(payload)


def test_redaction_audit_accepts_predeclared_pytest_node_identities() -> None:
    result = {
        "status": "passed",
        "duration_seconds": 1.0,
        "detail_digest": "sha256:" + "a" * 64,
    }

    assert audit_redaction(
        {
            "cases": [
                {
                    "test_results": {
                        "evals/tests/test_verification_contract.py::test_verification_code_is_single_use_replay_safe_and_serialized": result
                    }
                }
            ]
        }
    ).passed
