"""Evidence wrappers around installed playground demonstrations."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from openmagic_playground import (
    RenewalDemonstrationResponse,
    VerificationDemonstrationResponse,
)

from openmagic_evals.evidence.artifact_io import write_artifact
from openmagic_evals.evidence.contracts import (
    ArtifactCase,
    CaseVerdict,
    Correlations,
    DeterministicScenarioEvidence,
    PlaygroundArtifact,
    PlaygroundSummary,
    deterministic_observation_digest,
)
from openmagic_evals.evidence.core_models import canonical_digest
from openmagic_evals.evidence.deadline import bounded_evidence
from openmagic_evals.evidence.pins import PostgresDeploymentPin
from openmagic_evals.evidence.playground import playground_correlations
from openmagic_evals.evidence.playground_client import invoke_playground
from openmagic_evals.evidence.reproducibility import reproducibility_pin

_RENEWAL_DEMONSTRATION_CASE_ID = "demo.renewal-safe-wait"
_VERIFICATION_DEMONSTRATION_CASE_ID = "demo.deterministic-verification"


def _demo_artifact(
    *,
    repository_root: Path,
    output: Path,
    command: tuple[str, ...],
    case_id: str,
    started_at: datetime,
    correlations: Correlations,
    observation: dict[str, object],
    postgres_deployments: tuple[PostgresDeploymentPin, ...],
    timeout_seconds: int,
) -> PlaygroundArtifact:
    finished_at = datetime.now(UTC)
    scenarios = (
        DeterministicScenarioEvidence(
            scenario_id=case_id,
            correlations=correlations,
            observation=observation,
            observation_digest=canonical_digest(observation),
        ),
    )
    artifact = PlaygroundArtifact(
        reproducibility=reproducibility_pin(
            repository_root.resolve(),
            command=command,
            started_at=started_at,
            finished_at=finished_at,
            timeout_seconds=timeout_seconds,
            case_corpus_digest=canonical_digest(case_id),
            postgres_deployments=postgres_deployments,
        ),
        cases=(
            ArtifactCase(
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
            ),
        ),
        summary=PlaygroundSummary(
            synthetic_data_only=True,
            effects_enabled_by_default=False,
            local_provider=True,
            reset_verified=False,
            repeated_run_verified=False,
            intentional_failure_verified=False,
            disconnected_provider_verified=False,
            process_controls_verified=False,
            contributes_to_correctness=False,
        ),
        limitations=(
            "This is a synthetic demonstration and not correctness evidence.",
            "The result applies only to the pinned local provider and build.",
        ),
    )
    write_artifact(output, artifact)
    return artifact


@bounded_evidence
def run_renewal_demo(
    *,
    repository_root: Path,
    working_directory: Path,
    execute_approved_local_effect: bool,
    output: Path,
    timeout_seconds: int = 120,
) -> PlaygroundArtifact:
    if not execute_approved_local_effect:
        raise ValueError("renewal demo requires explicit approval for its local effect")
    started_at = datetime.now(UTC)
    command_line = (
        "openmagic-evidence",
        "demo-renewal",
        "--repository-root",
        str(repository_root.resolve()),
        "--working-directory",
        str(working_directory.resolve()),
        "--execute-approved-local-effect",
        "--output",
        str(output.resolve()),
        "--timeout-seconds",
        str(timeout_seconds),
    )
    result = invoke_playground(
        "demo-renewal",
        "--working-directory",
        str(working_directory.resolve()),
        "--execute-approved-local-effect",
        timeout_seconds=timeout_seconds,
        response_type=RenewalDemonstrationResponse,
    )
    return _demo_artifact(
        repository_root=repository_root,
        output=output,
        command=command_line,
        case_id=_RENEWAL_DEMONSTRATION_CASE_ID,
        started_at=started_at,
        correlations=playground_correlations(result.correlations),
        observation=result.observation.model_dump(mode="json"),
        postgres_deployments=tuple(
            PostgresDeploymentPin.model_validate(value.model_dump(mode="python"))
            for value in result.postgres_deployments
        ),
        timeout_seconds=timeout_seconds,
    )


@bounded_evidence
def run_verification_demo(
    *, repository_root: Path, output: Path, timeout_seconds: int = 120
) -> PlaygroundArtifact:
    started_at = datetime.now(UTC)
    command_line = (
        "openmagic-evidence",
        "demo-verification",
        "--repository-root",
        str(repository_root.resolve()),
        "--output",
        str(output.resolve()),
        "--timeout-seconds",
        str(timeout_seconds),
    )
    result = invoke_playground(
        "demo-verification",
        timeout_seconds=timeout_seconds,
        response_type=VerificationDemonstrationResponse,
    )
    return _demo_artifact(
        repository_root=repository_root,
        output=output,
        command=command_line,
        case_id=_VERIFICATION_DEMONSTRATION_CASE_ID,
        started_at=started_at,
        correlations=playground_correlations(result.correlations),
        observation=result.observation.model_dump(mode="json"),
        postgres_deployments=tuple(
            PostgresDeploymentPin.model_validate(value.model_dump(mode="python"))
            for value in result.postgres_deployments
        ),
        timeout_seconds=timeout_seconds,
    )


__all__ = ["run_renewal_demo", "run_verification_demo"]
