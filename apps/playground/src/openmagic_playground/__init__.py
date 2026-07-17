"""Public synthetic-only controls and demonstrations."""

from dataclasses import asdict, dataclass
from importlib.metadata import version

from openmagic_playground.demonstrations import (
    exercise_process_controls,
    run_renewal_demonstration,
    run_verification_demonstration,
)
from openmagic_playground.deployment import ManagedProcess, PlaygroundDeployment, ProcessRole
from openmagic_playground.reset import (
    ResetAssessment,
    ResetPreflightBlocked,
    assess_reset,
    mark_synthetic_deployment,
    reset_synthetic_deployment,
)
from openmagic_playground.responses import (
    ControlExerciseResponse,
    ExercisedControls,
    FailureScenarioObservation,
    PlaygroundAgentCorrelations,
    PlaygroundApplicationCorrelations,
    PlaygroundCorrelations,
    PlaygroundProcessCorrelations,
    PlaygroundProviderCorrelations,
    PlaygroundRuntimeCorrelations,
    PlaygroundScenarioCoverage,
    PostgresDeploymentObservation,
    RenewalDemonstrationObservation,
    RenewalDemonstrationResponse,
    SafeRenewalBoundaryObservation,
)
from openmagic_playground.verification_response import (
    VerificationDemonstrationObservation,
    VerificationDemonstrationResponse,
)

__version__ = version("openmagic-playground")


@dataclass(frozen=True)
class PlaygroundSafety:
    synthetic_data_only: bool = True
    external_effects_enabled: bool = False
    local_provider_only: bool = True
    deterministic_fixture_version: str = "issue-71.v1"
    process_control: str = "explicit"
    reset_requires_confirmation: bool = True
    contributes_to_correctness: bool = False

    def as_dict(self) -> dict[str, bool | str]:
        return asdict(self)


@dataclass(frozen=True)
class PlaygroundProcessControls:
    roles: tuple[str, ...] = ("api", "workflow-worker", "delivery-worker")
    actions: tuple[str, ...] = ("start", "drain", "reset", "restart", "stop")
    ownership: str = "explicit-local-processes"

    def as_dict(self) -> dict[str, tuple[str, ...] | str]:
        return asdict(self)


def safety_manifest() -> PlaygroundSafety:
    return PlaygroundSafety()


def process_controls() -> PlaygroundProcessControls:
    return PlaygroundProcessControls()


__all__ = [
    "ControlExerciseResponse",
    "ExercisedControls",
    "FailureScenarioObservation",
    "ManagedProcess",
    "PlaygroundAgentCorrelations",
    "PlaygroundApplicationCorrelations",
    "PlaygroundCorrelations",
    "PlaygroundDeployment",
    "PlaygroundProcessControls",
    "PlaygroundProcessCorrelations",
    "PlaygroundProviderCorrelations",
    "PlaygroundRuntimeCorrelations",
    "PlaygroundSafety",
    "PlaygroundScenarioCoverage",
    "PostgresDeploymentObservation",
    "ProcessRole",
    "RenewalDemonstrationObservation",
    "RenewalDemonstrationResponse",
    "ResetAssessment",
    "ResetPreflightBlocked",
    "SafeRenewalBoundaryObservation",
    "VerificationDemonstrationObservation",
    "VerificationDemonstrationResponse",
    "__version__",
    "assess_reset",
    "exercise_process_controls",
    "mark_synthetic_deployment",
    "process_controls",
    "reset_synthetic_deployment",
    "run_renewal_demonstration",
    "run_verification_demonstration",
    "safety_manifest",
]
