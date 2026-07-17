"""External observation of the installed playground application."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TypeVar

from pydantic import BaseModel

from openmagic_evals.evidence._owned_command import capture_owned_command
from openmagic_evals.evidence.reproducibility import fixed_execution_environment

ResponseT = TypeVar("ResponseT", bound=BaseModel)


def parse_playground_response(payload: str, *, response_type: type[ResponseT]) -> ResponseT:
    """Validate the complete versioned response before eval code observes it."""

    return response_type.model_validate_json(payload)


def invoke_playground(
    *arguments: str,
    timeout_seconds: int,
    response_type: type[ResponseT],
) -> ResponseT:
    executable = Path(sys.executable).parent / "openmagic-playground"
    if not executable.is_file():
        raise RuntimeError("installed playground entry point is missing")
    completed = capture_owned_command(
        (str(executable), *arguments),
        working_directory=Path.cwd(),
        environment=fixed_execution_environment(),
        timeout_seconds=timeout_seconds,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "playground command failed: "
            f"status={completed.returncode} stderr={completed.stderr.strip()}"
        )
    return parse_playground_response(completed.stdout, response_type=response_type)


__all__ = ["invoke_playground", "parse_playground_response"]
