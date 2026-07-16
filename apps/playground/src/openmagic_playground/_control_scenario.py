"""Synthetic process controls, reset reproduction, and failure coverage."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path

from example_insurance.renewals import ExampleInsurance
from openmagic_runtime.processes import owned_cleanup_scope
from openmagic_runtime.threads import ThreadStore

from openmagic_playground._failure_scenario import FailureScenarioPhase, run_failure_scenario
from openmagic_playground._scenario_support import (
    SafeRenewalPhase,
    create_renewal_fixture,
    observe_safe_renewal,
)
from openmagic_playground.deployment import ManagedProcess, PlaygroundDeployment
from openmagic_playground.deployment_observation import observe_postgres
from openmagic_playground.responses import (
    ControlExerciseResponse,
    ExercisedControls,
    PlaygroundCorrelations,
    PlaygroundScenarioCoverage,
    PostgresDeploymentObservation,
)
from openmagic_playground.synthetic_provider import SyntheticEmailProvider


@dataclass(frozen=True)
class ResetReproductionPhase:
    first: SafeRenewalPhase
    second: SafeRenewalPhase


@dataclass(frozen=True)
class FailureCoveragePhase:
    intentional: FailureScenarioPhase
    disconnected: FailureScenarioPhase


@dataclass(frozen=True)
class ProcessControlPhase:
    original: tuple[ManagedProcess, ...]
    drained: tuple[ManagedProcess, ...]
    restarted: tuple[ManagedProcess, ...]


def _safe_fixture(database_url: str) -> SafeRenewalPhase:
    application = ExampleInsurance(database_url=database_url)
    application.prepare()
    threads = ThreadStore(database_url=database_url)
    fixture = create_renewal_fixture(application, threads, "control")
    return observe_safe_renewal(fixture)


def _exercise_reset(deployment: PlaygroundDeployment) -> ResetReproductionPhase:
    first = _safe_fixture(deployment.database_url)
    deployment.reset()
    second = _safe_fixture(deployment.database_url)
    if first.observation != second.observation:
        raise AssertionError("playground reset did not reproduce its deterministic fixture")
    return ResetReproductionPhase(first=first, second=second)


def _exercise_failures(
    deployment: PlaygroundDeployment,
    working_directory: Path,
) -> FailureCoveragePhase:
    with SyntheticEmailProvider(
        working_directory=working_directory / "intentional-failure-provider",
        behavior="not_applied",
    ) as provider:
        intentional = run_failure_scenario(
            database_url=deployment.database_url,
            scenario="intentional-failure",
            provider_url=provider.url,
            provider_connected=True,
            provider_process_id=provider.pid,
        )
    deployment.reset()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as disconnected:
        disconnected.bind(("127.0.0.1", 0))
        disconnected_port = int(disconnected.getsockname()[1])
        disconnected_phase = run_failure_scenario(
            database_url=deployment.database_url,
            scenario="disconnected-provider",
            provider_url=f"http://127.0.0.1:{disconnected_port}",
            provider_connected=False,
        )
    return FailureCoveragePhase(
        intentional=intentional,
        disconnected=disconnected_phase,
    )


def _restart_roles(deployment: PlaygroundDeployment) -> tuple[ManagedProcess, ...]:
    return tuple(
        process
        for role in ("api", "workflow-worker", "delivery-worker")
        for process in deployment.scale_role(role, capacity=1)
    )


def _assemble_response(
    deployment: PlaygroundDeployment,
    controls: ProcessControlPhase,
    reset: ResetReproductionPhase,
    failures: FailureCoveragePhase,
) -> ControlExerciseResponse:
    return ControlExerciseResponse(
        controls=ExercisedControls(
            start=len(controls.original),
            drain=len(controls.drained),
            reset=True,
            restart=len(controls.restarted),
            stop=True,
        ),
        correlations=PlaygroundCorrelations.merge(
            (
                reset.first.correlations,
                reset.second.correlations,
                failures.intentional.correlations,
                failures.disconnected.correlations,
            )
        ),
        fixture=reset.first.observation,
        scenario_coverage=PlaygroundScenarioCoverage(
            reset_reproduced=True,
            repeated_run_reproduced=True,
            intentional_failure=failures.intentional.observation,
            disconnected_provider=failures.disconnected.observation,
        ),
        original_process_ids=tuple(item.pid for item in controls.original),
        restarted_process_ids=tuple(item.pid for item in controls.restarted),
        postgres_deployments=(
            PostgresDeploymentObservation.model_validate(observe_postgres(deployment.database_url)),
        ),
    )


def run_control_scenario(*, working_directory: Path) -> ControlExerciseResponse:
    deployment = PlaygroundDeployment(working_directory=working_directory)
    with owned_cleanup_scope(
        deployment.stop,
        message="playground control execution and cleanup failed",
    ):
        original = deployment.start()
        drained = deployment.drain()
        reset = _exercise_reset(deployment)
        failures = _exercise_failures(deployment, working_directory)
        restarted = _restart_roles(deployment)
        if {item.pid for item in original} & {item.pid for item in restarted}:
            raise AssertionError("playground restart did not use fresh interpreters")
        return _assemble_response(
            deployment,
            ProcessControlPhase(original, drained, restarted),
            reset,
            failures,
        )


__all__: list[str] = []
