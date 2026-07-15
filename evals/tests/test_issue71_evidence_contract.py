from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from openmagic_evals.evidence.contracts import (
    REQUIRED_NEGATIVE_CLAIMS,
    AgentConfigurationPin,
    AgentQualityArtifact,
    AgentQualitySummary,
    ArtifactCase,
    BuildPin,
    CaseVerdict,
    Correlations,
    DeterministicArtifact,
    DeterministicSummary,
    ProcessMetrics,
    QueueDepth,
    ReproducibilityPin,
    canonical_artifact_json,
    parse_artifact,
)
from openmagic_evals.evidence.redaction import RedactionViolation, audit_redaction


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
        ),
        suite_version="issue-71.v1",
        command=("uv", "run", "openmagic-evidence", "deterministic"),
        environment_allowlist=("PATH", "PYTHONNOUSERSITE"),
        started_at=datetime(2026, 7, 15, 20, 0, tzinfo=UTC),
        finished_at=datetime(2026, 7, 15, 20, 1, tzinfo=UTC),
        timeout_seconds=900,
        postgres_version="17.5",
        postgres_configuration_digest="sha256:" + "b" * 64,
        migration_heads={
            "example_insurance": "0004_deterministic_verification",
            "openmagic_runtime": "0003_fenced_effect_kernel",
        },
        definition_digests={"example_insurance.renewal": "sha256:" + "c" * 64},
    )


def _case(case_id: str = "command.exact_replay") -> ArtifactCase:
    return ArtifactCase(
        case_id=case_id,
        case_schema_version=1,
        expected_trials=1,
        observed_trials=1,
        seeds=(0,),
        correlations=Correlations(command_ids=("018f2f00-0000-7000-8000-000000000001",)),
        observation_digests=("sha256:" + "d" * 64,),
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
            verdict=CaseVerdict(status="passed", invariant_violations=()),
        )

    with pytest.raises(ValueError, match="Agent quality cannot determine"):
        AgentQualityArtifact(
            reproducibility=_pin(),
            agent_configuration=_agent_configuration(),
            cases=(_case("agent.development.tool-choice"),),
            summary=AgentQualitySummary(
                development_cases=1,
                held_out_cases=0,
                expected_trials=1,
                observed_trials=1,
                passed_trials=1,
                prohibited_actions=0,
                threshold_passed=True,
                deterministic_release_pass=True,
            ),
            limitations=("local scripted Agent only",),
        )


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
        started_processes={"api": 0, "workflow-worker": 4, "delivery-worker": 3},
        forced_losses={"workflow-worker": 1, "delivery-worker": 1},
        fresh_interpreters=True,
        postgresql_only_reconstruction=True,
        elapsed_ms=250,
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


@pytest.mark.parametrize(
    "payload",
    [
        {"provider_token": "secret-value"},
        {"database_url": "postgresql://user:password@db/openmagic"},
        {"raw_message_content": "A real customer renewal request"},
        {"authorization": "Bearer credential"},
        {"verification_code": "123456"},
    ],
)
def test_redaction_audit_rejects_secret_and_sensitive_raw_content(payload: object) -> None:
    with pytest.raises(RedactionViolation):
        audit_redaction(payload)
