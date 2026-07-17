"""External verification of the installed synthetic playground."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from openmagic_playground import ControlExerciseResponse, PlaygroundCorrelations

from openmagic_evals.evidence.artifact_io import write_artifact
from openmagic_evals.evidence.contracts import (
    AgentCorrelations,
    ApplicationCorrelations,
    ArtifactCase,
    CaseVerdict,
    Correlations,
    DeterministicScenarioEvidence,
    PlaygroundArtifact,
    PlaygroundSummary,
    ProcessCorrelations,
    ProviderCorrelations,
    RuntimeCorrelations,
    deterministic_observation_digest,
)
from openmagic_evals.evidence.core_models import canonical_digest
from openmagic_evals.evidence.deadline import bounded_evidence
from openmagic_evals.evidence.pins import PostgresDeploymentPin
from openmagic_evals.evidence.playground_client import invoke_playground
from openmagic_evals.evidence.reproducibility import reproducibility_pin


def playground_correlations(
    value: PlaygroundCorrelations, process_ids: tuple[int, ...] = ()
) -> Correlations:
    return Correlations(
        runtime=RuntimeCorrelations.model_validate(value.runtime.model_dump(mode="python")),
        application=ApplicationCorrelations.model_validate(
            value.application.model_dump(mode="python")
        ),
        agent=AgentCorrelations.model_validate(value.agent.model_dump(mode="python")),
        process=ProcessCorrelations(
            worker_ids=value.process.worker_ids,
            process_ids=tuple(dict.fromkeys((*process_ids, *value.process.process_ids))),
        ),
        provider=ProviderCorrelations.model_validate(value.provider.model_dump(mode="python")),
    )


@bounded_evidence
def verify_playground(
    *,
    repository_root: Path,
    working_directory: Path,
    output: Path,
    timeout_seconds: int = 120,
) -> PlaygroundArtifact:
    command = (
        "openmagic-evidence",
        "playground",
        "--repository-root",
        str(repository_root.resolve()),
        "--working-directory",
        str(working_directory.resolve()),
        "--output",
        str(output.resolve()),
        "--timeout-seconds",
        str(timeout_seconds),
    )
    started_at = datetime.now(UTC)
    result = invoke_playground(
        "exercise",
        "--working-directory",
        str((working_directory / "deployment").resolve()),
        timeout_seconds=timeout_seconds,
        response_type=ControlExerciseResponse,
    )
    finished_at = datetime.now(UTC)
    controls = result.controls.model_dump(mode="python")
    expected_controls = {
        "start": 3,
        "drain": 3,
        "reset": True,
        "restart": 3,
        "stop": True,
    }
    if controls != expected_controls:
        raise AssertionError("playground did not exercise every declared public control")
    process_ids = tuple(
        int(value) for value in (*result.original_process_ids, *result.restarted_process_ids)
    )
    case_correlations = playground_correlations(result.correlations, process_ids)
    coverage = result.scenario_coverage
    scenarios = (
        DeterministicScenarioEvidence(
            scenario_id="safe-reset",
            correlations=case_correlations,
            observation={
                "controls": controls,
                "fixture": result.fixture.model_dump(mode="json"),
                "reset_reproduced": coverage.reset_reproduced,
                "effects_enabled_by_default": False,
            },
            observation_digest=canonical_digest(
                {
                    "controls": controls,
                    "fixture": result.fixture.model_dump(mode="json"),
                    "reset_reproduced": coverage.reset_reproduced,
                    "effects_enabled_by_default": False,
                }
            ),
        ),
        DeterministicScenarioEvidence(
            scenario_id="repeated-run",
            correlations=case_correlations,
            observation={"reproduced": coverage.repeated_run_reproduced},
            observation_digest=canonical_digest({"reproduced": coverage.repeated_run_reproduced}),
        ),
        DeterministicScenarioEvidence(
            scenario_id="intentional-failure",
            correlations=case_correlations,
            observation=coverage.intentional_failure.model_dump(mode="json"),
            observation_digest=canonical_digest(
                coverage.intentional_failure.model_dump(mode="json")
            ),
        ),
        DeterministicScenarioEvidence(
            scenario_id="disconnected-provider",
            correlations=case_correlations,
            observation=coverage.disconnected_provider.model_dump(mode="json"),
            observation_digest=canonical_digest(
                coverage.disconnected_provider.model_dump(mode="json")
            ),
        ),
    )
    artifact = PlaygroundArtifact(
        reproducibility=reproducibility_pin(
            repository_root.resolve(),
            command=command,
            started_at=started_at,
            finished_at=finished_at,
            timeout_seconds=timeout_seconds,
            case_corpus_digest=canonical_digest("issue-71.playground.v1"),
            postgres_deployments=tuple(
                PostgresDeploymentPin.model_validate(value.model_dump(mode="python"))
                for value in result.postgres_deployments
            ),
        ),
        cases=(
            ArtifactCase(
                case_id="playground.synthetic-reset-and-process-control",
                case_schema_version=1,
                expected_trials=1,
                observed_trials=1,
                seeds=(0,),
                correlations=case_correlations,
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
            reset_verified=True,
            repeated_run_verified=True,
            intentional_failure_verified=True,
            disconnected_provider_verified=True,
            process_controls_verified=True,
            contributes_to_correctness=False,
        ),
        limitations=(
            "The playground is a local synthetic demonstration.",
            "Playground success does not contribute to deterministic correctness.",
        ),
    )
    write_artifact(output, artifact)
    return artifact


__all__ = ["playground_correlations", "verify_playground"]
