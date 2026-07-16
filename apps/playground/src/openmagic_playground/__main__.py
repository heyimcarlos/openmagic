from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel

from openmagic_playground import (
    exercise_process_controls,
    process_controls,
    run_renewal_demonstration,
    run_verification_demonstration,
    safety_manifest,
)


def _manifest(_arguments: argparse.Namespace) -> dict[str, object]:
    return dict(safety_manifest().as_dict())


def _controls(_arguments: argparse.Namespace) -> dict[str, object]:
    return dict(process_controls().as_dict())


def _renewal(_arguments: argparse.Namespace) -> BaseModel:
    return run_renewal_demonstration()


def _verification(_arguments: argparse.Namespace) -> BaseModel:
    return run_verification_demonstration()


def _exercise(arguments: argparse.Namespace) -> BaseModel:
    return exercise_process_controls(working_directory=arguments.working_directory)


def main() -> None:
    parser = argparse.ArgumentParser(prog="openmagic-playground")
    commands = parser.add_subparsers(dest="command", required=True)

    handlers: tuple[
        tuple[str, str, Callable[[argparse.Namespace], dict[str, object] | BaseModel]], ...
    ] = (
        ("manifest", "print the synthetic safety contract", _manifest),
        ("controls", "print explicit local role process controls", _controls),
        ("demo-renewal", "run the effects-disabled renewal demonstration", _renewal),
        ("demo-verification", "run deterministic verification", _verification),
    )
    for name, help_text, handler in handlers:
        command = commands.add_parser(name, help=help_text)
        command.set_defaults(handler=handler)
    exercise = commands.add_parser("exercise", help="exercise every process and reset control")
    exercise.add_argument("--working-directory", required=True, type=Path)
    exercise.set_defaults(handler=_exercise)

    arguments = parser.parse_args()
    result = arguments.handler(arguments)
    value = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
