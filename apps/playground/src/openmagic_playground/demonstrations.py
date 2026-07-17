"""Public facade for executable synthetic playground demonstrations."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from openmagic_playground._control_scenario import run_control_scenario
from openmagic_playground._renewal_scenario import run_renewal_scenario
from openmagic_playground._verification_scenario import run_verification_scenario
from openmagic_playground.responses import (
    ControlExerciseResponse,
    RenewalDemonstrationResponse,
)
from openmagic_playground.verification_response import VerificationDemonstrationResponse


def run_renewal_demonstration(
    *,
    working_directory: Path,
    execute_approved_local_effect: Literal[True],
) -> RenewalDemonstrationResponse:
    """Run one explicitly approved effect through an owned local provider."""

    if execute_approved_local_effect is not True:
        raise ValueError("renewal demo requires explicit approved local effect execution")
    return run_renewal_scenario(working_directory=working_directory)


def run_verification_demonstration() -> VerificationDemonstrationResponse:
    """Run deterministic verification without an external provider."""

    return run_verification_scenario()


def exercise_process_controls(*, working_directory: Path) -> ControlExerciseResponse:
    """Exercise controls and all accepted synthetic playground scenarios."""

    return run_control_scenario(working_directory=working_directory)


__all__ = [
    "exercise_process_controls",
    "run_renewal_demonstration",
    "run_verification_demonstration",
]
