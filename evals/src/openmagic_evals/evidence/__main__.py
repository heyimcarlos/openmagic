from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from openmagic_evals.evidence.agent_quality import run_local_agent_quality
from openmagic_evals.evidence.claims import EvidencePackagePaths, write_claim_report
from openmagic_evals.evidence.contracts import artifact_json_schema
from openmagic_evals.evidence.demos import run_renewal_demo, run_verification_demo
from openmagic_evals.evidence.installed_audit import audit_installed_environment
from openmagic_evals.evidence.live_smoke import run_live_smoke
from openmagic_evals.evidence.playground import verify_playground
from openmagic_evals.evidence.processes import run_process_release
from openmagic_evals.evidence.release import run_deterministic_release, run_race_release
from openmagic_evals.evidence.surface import run_surface_audit

CommandHandler = Callable[[argparse.Namespace], None]
CommandRegistrar = Callable[[argparse._SubParsersAction], None]


def _write_summary(output: Path, artifact: Any) -> None:
    print(
        json.dumps(
            {
                "artifact": str(output.resolve()),
                "cases": artifact.summary.observed_cases,
                "invariant_violations": artifact.summary.invariant_violations,
                "strict_pass": artifact.summary.strict_pass,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _handle_schema(arguments: argparse.Namespace) -> None:
    document = json.dumps(artifact_json_schema(), sort_keys=True, separators=(",", ":"))
    if arguments.output is None:
        print(document)
        return
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(document + "\n", encoding="utf-8")
    print(json.dumps({"schema": str(arguments.output.resolve())}, sort_keys=True))


def _register_schema(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser("schema", help="write the versioned canonical JSON schema")
    command.add_argument("--output", type=Path)
    command.set_defaults(handler=_handle_schema)


def _handle_surface(arguments: argparse.Namespace) -> None:
    artifact = run_surface_audit(
        repository_root=arguments.repository_root,
        output=arguments.output,
        timeout_seconds=arguments.timeout_seconds,
    )
    print(json.dumps(artifact.summary.model_dump(mode="json"), sort_keys=True))


def _register_surface(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser("audit-surface", help="audit repository public surfaces")
    command.add_argument("--repository-root", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--timeout-seconds", type=int, default=120)
    command.set_defaults(handler=_handle_surface)


def _handle_installed(arguments: argparse.Namespace) -> None:
    del arguments
    report = audit_installed_environment()
    print(json.dumps(asdict(report), sort_keys=True, separators=(",", ":")))
    if not report.passed:
        raise SystemExit(1)


def _register_installed(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser("audit-installed", help="audit the installed wheel surface")
    command.set_defaults(handler=_handle_installed)


def _handle_deterministic(arguments: argparse.Namespace) -> None:
    artifact = run_deterministic_release(
        repository_root=arguments.repository_root,
        output=arguments.output,
        timeout_seconds=arguments.timeout_seconds,
    )
    _write_summary(arguments.output, artifact)


def _register_deterministic(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser("deterministic", help="run the deterministic release matrix")
    command.add_argument("--repository-root", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--timeout-seconds", type=int, default=1800)
    command.set_defaults(handler=_handle_deterministic)


def _handle_races(arguments: argparse.Namespace) -> None:
    artifact = run_race_release(
        repository_root=arguments.repository_root,
        output=arguments.output,
        timeout_seconds=arguments.timeout_seconds,
    )
    _write_summary(arguments.output, artifact)


def _register_races(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser("races", help="run the recorded cardinality-one race corpus")
    command.add_argument("--repository-root", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--timeout-seconds", type=int, default=900)
    command.set_defaults(handler=_handle_races)


def _handle_processes(arguments: argparse.Namespace) -> None:
    artifact = run_process_release(
        repository_root=arguments.repository_root,
        working_directory=arguments.working_directory,
        output=arguments.output,
        timeout_seconds=arguments.timeout_seconds,
    )
    print(
        json.dumps(
            {
                "artifact": str(arguments.output.resolve()),
                "cases": artifact.summary.observed_cases,
                "strict_pass": artifact.summary.strict_pass,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _register_processes(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser("processes", help="run process-loss and backpressure evidence")
    command.add_argument("--repository-root", type=Path, required=True)
    command.add_argument("--working-directory", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--timeout-seconds", type=int, default=120)
    command.set_defaults(handler=_handle_processes)


def _handle_agent_quality(arguments: argparse.Namespace) -> None:
    artifact = run_local_agent_quality(
        repository_root=arguments.repository_root,
        output=arguments.output,
        timeout_seconds=arguments.timeout_seconds,
    )
    print(json.dumps(artifact.summary.model_dump(mode="json"), sort_keys=True))


def _register_agent_quality(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser("agent-quality", help="run versioned Agent experiments")
    command.add_argument("--repository-root", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--timeout-seconds", type=int, default=300)
    command.set_defaults(handler=_handle_agent_quality)


def _handle_live_smoke(arguments: argparse.Namespace) -> None:
    artifact = run_live_smoke(
        repository_root=arguments.repository_root,
        output=arguments.output,
        provider=arguments.provider,
        model=arguments.model,
        endpoint=arguments.endpoint,
        configuration_digest=arguments.configuration_digest,
        synthetic_case_id=arguments.synthetic_case_id,
        credential_file=arguments.credential_file,
        allow_live=arguments.allow_live,
        timeout_seconds=arguments.timeout_seconds,
    )
    print(json.dumps(artifact.summary.model_dump(mode="json"), sort_keys=True))


def _register_live_smoke(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser("live-smoke", help="run explicit opt-in provider smoke")
    command.add_argument("--repository-root", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--provider", required=True)
    command.add_argument("--model", required=True)
    command.add_argument("--endpoint", required=True)
    command.add_argument("--configuration-digest")
    command.add_argument("--synthetic-case-id", required=True)
    command.add_argument("--credential-file", type=Path)
    command.add_argument("--allow-live", action="store_true")
    command.add_argument("--timeout-seconds", type=int, default=10)
    command.set_defaults(handler=_handle_live_smoke)


def _handle_playground(arguments: argparse.Namespace) -> None:
    artifact = verify_playground(
        repository_root=arguments.repository_root,
        working_directory=arguments.working_directory,
        output=arguments.output,
        timeout_seconds=arguments.timeout_seconds,
    )
    print(json.dumps(artifact.summary.model_dump(mode="json"), sort_keys=True))


def _register_playground(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser("playground", help="verify the synthetic playground")
    command.add_argument("--repository-root", type=Path, required=True)
    command.add_argument("--working-directory", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--timeout-seconds", type=int, default=120)
    command.set_defaults(handler=_handle_playground)


def _handle_claim_report(arguments: argparse.Namespace) -> None:
    write_claim_report(
        package=EvidencePackagePaths(
            deterministic=arguments.deterministic,
            surface_audit=arguments.surface_audit,
            agent_quality=arguments.agent_quality,
            live_smoke=arguments.live_smoke,
            playground=arguments.playground,
            processes=arguments.processes,
            races=arguments.races,
            renewal_demo=arguments.renewal_demo,
            verification_demo=arguments.verification_demo,
        ),
        output=arguments.output,
    )
    print(json.dumps({"claim_report": str(arguments.output.resolve())}, sort_keys=True))


def _register_claim_report(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser("claim-report", help="assemble the supported claim report")
    command.add_argument("--deterministic", type=Path, required=True)
    command.add_argument("--surface-audit", type=Path, required=True)
    command.add_argument("--agent-quality", type=Path, required=True)
    command.add_argument("--live-smoke", type=Path, required=True)
    command.add_argument("--playground", type=Path, required=True)
    command.add_argument("--processes", type=Path, required=True)
    command.add_argument("--races", type=Path, required=True)
    command.add_argument("--renewal-demo", type=Path, required=True)
    command.add_argument("--verification-demo", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.set_defaults(handler=_handle_claim_report)


def _handle_renewal_demo(arguments: argparse.Namespace) -> None:
    artifact = run_renewal_demo(
        repository_root=arguments.repository_root,
        working_directory=arguments.working_directory,
        output=arguments.output,
        timeout_seconds=arguments.timeout_seconds,
    )
    print(json.dumps(artifact.summary.model_dump(mode="json"), sort_keys=True))


def _register_renewal_demo(commands: argparse._SubParsersAction) -> None:
    command = commands.add_parser("demo-renewal", help="run the synthetic renewal demonstration")
    command.add_argument("--repository-root", type=Path, required=True)
    command.add_argument("--working-directory", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--timeout-seconds", type=int, default=120)
    command.set_defaults(handler=_handle_renewal_demo)


def _handle_verification_demo(arguments: argparse.Namespace) -> None:
    artifact = run_verification_demo(
        repository_root=arguments.repository_root,
        output=arguments.output,
        timeout_seconds=arguments.timeout_seconds,
    )
    print(json.dumps(artifact.summary.model_dump(mode="json"), sort_keys=True))


def _register_verification_demo(
    commands: argparse._SubParsersAction,
) -> None:
    command = commands.add_parser(
        "demo-verification", help="run deterministic verification demonstration"
    )
    command.add_argument("--repository-root", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--timeout-seconds", type=int, default=120)
    command.set_defaults(handler=_handle_verification_demo)


_COMMANDS: tuple[CommandRegistrar, ...] = (
    _register_schema,
    _register_surface,
    _register_installed,
    _register_deterministic,
    _register_races,
    _register_processes,
    _register_agent_quality,
    _register_live_smoke,
    _register_playground,
    _register_claim_report,
    _register_renewal_demo,
    _register_verification_demo,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openmagic-evidence")
    commands = parser.add_subparsers(required=True)
    for register in _COMMANDS:
        register(commands)
    return parser


def main() -> None:
    arguments = _parser().parse_args()
    handler: CommandHandler = arguments.handler
    handler(arguments)


if __name__ == "__main__":
    main()
