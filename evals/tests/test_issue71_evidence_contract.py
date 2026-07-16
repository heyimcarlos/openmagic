from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Literal, cast

import pytest
from openmagic_evals.evidence.contracts import (
    REQUIRED_NEGATIVE_CLAIMS,
    AgentCaseEvidence,
    AgentConfigurationPin,
    AgentQualityArtifact,
    AgentQualitySummary,
    AgentTrialEvidence,
    ArtifactCase,
    BoundaryAgentCandidateObservation,
    BoundaryAgentScorerContract,
    BuildPin,
    CaseVerdict,
    Correlations,
    DeterministicArtifact,
    DeterministicScenarioEvidence,
    DeterministicSummary,
    DistributionSummary,
    ProcessMetrics,
    QueueDepth,
    RaceTrialEvidence,
    RepositorySurfaceEvidence,
    ReproducibilityPin,
    SanitizedAgentEvent,
    WheelArchivePin,
    canonical_artifact_json,
    deterministic_observation_digest,
    parse_artifact,
    race_trial_digest,
)
from openmagic_evals.evidence.redaction import RedactionViolation, audit_redaction
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
        environment_allowlist=("PATH", "PYTHONNOUSERSITE"),
        started_at=datetime(2026, 7, 15, 20, 0, tzinfo=UTC),
        finished_at=datetime(2026, 7, 15, 20, 1, tzinfo=UTC),
        timeout_seconds=900,
        postgres_version="17.5",
        postgres_image="postgres@sha256:" + "1" * 64,
        postgres_configuration={
            "synchronous_commit": "on",
            "transaction_isolation": "read committed",
        },
        postgres_configuration_digest="sha256:" + "b" * 64,
        migration_heads={
            "example_insurance": "0004_deterministic_verification",
            "openmagic_runtime": "0003_fenced_effect_kernel",
        },
        definition_digests={"example_insurance.renewal": "sha256:" + "c" * 64},
    )


def _case(case_id: str = "command.exact_replay") -> ArtifactCase:
    correlations = Correlations(command_ids=("018f2f00-0000-7000-8000-000000000001",))
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


def _agent_case() -> AgentCaseEvidence:
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
    candidate_observation = BoundaryAgentCandidateObservation(observed_boundary="malformed_result")
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
            correlations=Correlations(command_ids=("018f2f00-0000-7000-8000-000000000001",)),
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

    with pytest.raises(ValueError, match="Agent quality cannot determine"):
        AgentQualityArtifact(
            reproducibility=_pin(),
            agent_configurations=(_agent_configuration(),),
            cases=(_agent_case(),),
            summary=AgentQualitySummary(
                development_cases=1,
                held_out_cases=0,
                expected_trials=1,
                observed_trials=1,
                passed_trials=1,
                prohibited_actions=0,
                threshold_passed=True,
                deterministic_release_pass=True,
                latency_ms=DistributionSummary(
                    count=1,
                    mean=1,
                    median=1,
                    sample_standard_deviation=0,
                    minimum=1,
                    maximum=1,
                ),
            ),
            limitations=("local scripted Agent only",),
        )


def test_race_digest_binds_every_claim_bearing_field() -> None:
    correlations = Correlations(command_ids=("018f2f00-0000-7000-8000-000000000001",))
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
