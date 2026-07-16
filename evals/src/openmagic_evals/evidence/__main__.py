from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from openmagic_evals.evidence.agent_quality import run_local_agent_quality
from openmagic_evals.evidence.audit import audit_repository
from openmagic_evals.evidence.claims import write_claim_report
from openmagic_evals.evidence.contracts import artifact_json_schema
from openmagic_evals.evidence.demos import run_renewal_demo, run_verification_demo
from openmagic_evals.evidence.installed_audit import audit_installed_environment
from openmagic_evals.evidence.live_smoke import run_live_smoke
from openmagic_evals.evidence.playground import verify_playground
from openmagic_evals.evidence.processes import run_process_release
from openmagic_evals.evidence.release import run_deterministic_release, run_race_release


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="openmagic-evidence")
    subcommands = parser.add_subparsers(dest="command", required=True)
    schema = subcommands.add_parser("schema", help="write the versioned canonical JSON schema")
    schema.add_argument("--output", type=Path)
    audit = subcommands.add_parser("audit-surface", help="audit repository public surfaces")
    audit.add_argument("--repository-root", type=Path, required=True)
    subcommands.add_parser("audit-installed", help="audit the installed wheel surface")
    release_commands = {"deterministic", "races"}
    for name, help_text in (
        ("deterministic", "run the deterministic release matrix"),
        ("races", "run the recorded cardinality-one race corpus"),
        ("processes", "run process-loss and backpressure evidence"),
        ("agent-quality", "run versioned Agent quality experiments"),
        ("live-smoke", "run explicit opt-in provider availability smoke"),
        ("playground", "verify the synthetic playground"),
        ("claim-report", "assemble the supported claim report"),
        ("demo-renewal", "run the full synthetic renewal demonstration"),
        ("demo-verification", "run deterministic verification demonstration"),
    ):
        command = subcommands.add_parser(name, help=help_text)
        if name in release_commands:
            command.add_argument("--repository-root", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
            command.add_argument(
                "--timeout-seconds",
                type=int,
                default=1800 if name == "deterministic" else 900,
            )
        elif name == "processes":
            command.add_argument("--repository-root", type=Path, required=True)
            command.add_argument("--working-directory", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--timeout-seconds", type=int, default=120)
        elif name == "agent-quality":
            command.add_argument("--repository-root", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--timeout-seconds", type=int, default=300)
        elif name == "live-smoke":
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
        elif name == "playground":
            command.add_argument("--repository-root", type=Path, required=True)
            command.add_argument("--working-directory", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--timeout-seconds", type=int, default=120)
        elif name == "claim-report":
            command.add_argument("--deterministic", type=Path, required=True)
            command.add_argument("--agent-quality", type=Path)
            command.add_argument("--live-smoke", type=Path)
            command.add_argument("--playground", type=Path)
            command.add_argument("--processes", type=Path)
            command.add_argument("--races", type=Path)
            command.add_argument("--renewal-demo", type=Path)
            command.add_argument("--verification-demo", type=Path)
            command.add_argument("--output", type=Path, required=True)
        elif name == "demo-renewal":
            command.add_argument("--repository-root", type=Path, required=True)
            command.add_argument("--working-directory", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--timeout-seconds", type=int, default=120)
        elif name == "demo-verification":
            command.add_argument("--repository-root", type=Path, required=True)
            command.add_argument("--output", type=Path, required=True)
            command.add_argument("--timeout-seconds", type=int, default=120)
    return parser


def main() -> None:
    parser = _parser()
    arguments = parser.parse_args()
    if arguments.command == "schema":
        document = json.dumps(artifact_json_schema(), sort_keys=True, separators=(",", ":"))
        if arguments.output is None:
            print(document)
        else:
            arguments.output.parent.mkdir(parents=True, exist_ok=True)
            arguments.output.write_text(document + "\n", encoding="utf-8")
            print(json.dumps({"schema": str(arguments.output.resolve())}, sort_keys=True))
        return
    if arguments.command == "audit-surface":
        report = audit_repository(arguments.repository_root)
        print(json.dumps(asdict(report), sort_keys=True, separators=(",", ":")))
        if not report.passed:
            raise SystemExit(1)
        return
    if arguments.command == "audit-installed":
        report = audit_installed_environment()
        print(json.dumps(asdict(report), sort_keys=True, separators=(",", ":")))
        if not report.passed:
            raise SystemExit(1)
        return
    if arguments.command in {"deterministic", "races"}:
        runner = (
            run_deterministic_release if arguments.command == "deterministic" else run_race_release
        )
        artifact = runner(
            repository_root=arguments.repository_root,
            output=arguments.output,
            timeout_seconds=arguments.timeout_seconds,
        )
        print(
            json.dumps(
                {
                    "artifact": str(arguments.output.resolve()),
                    "cases": artifact.summary.observed_cases,
                    "invariant_violations": artifact.summary.invariant_violations,
                    "strict_pass": artifact.summary.strict_pass,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        return
    if arguments.command == "processes":
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
        return
    if arguments.command == "agent-quality":
        artifact = run_local_agent_quality(
            repository_root=arguments.repository_root,
            output=arguments.output,
            timeout_seconds=arguments.timeout_seconds,
        )
        print(json.dumps(artifact.summary.model_dump(mode="json"), sort_keys=True))
        return
    if arguments.command == "live-smoke":
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
        return
    if arguments.command == "playground":
        artifact = verify_playground(
            repository_root=arguments.repository_root,
            working_directory=arguments.working_directory,
            output=arguments.output,
            timeout_seconds=arguments.timeout_seconds,
        )
        print(json.dumps(artifact.summary.model_dump(mode="json"), sort_keys=True))
        return
    if arguments.command == "claim-report":
        write_claim_report(
            deterministic_path=arguments.deterministic,
            agent_path=arguments.agent_quality,
            live_path=arguments.live_smoke,
            playground_path=arguments.playground,
            process_path=arguments.processes,
            race_path=arguments.races,
            renewal_demo_path=arguments.renewal_demo,
            verification_demo_path=arguments.verification_demo,
            output=arguments.output,
        )
        print(json.dumps({"claim_report": str(arguments.output.resolve())}, sort_keys=True))
        return
    if arguments.command == "demo-renewal":
        artifact = run_renewal_demo(
            repository_root=arguments.repository_root,
            working_directory=arguments.working_directory,
            output=arguments.output,
        )
        print(json.dumps(artifact.summary.model_dump(mode="json"), sort_keys=True))
        return
    if arguments.command == "demo-verification":
        artifact = run_verification_demo(
            repository_root=arguments.repository_root,
            output=arguments.output,
            timeout_seconds=arguments.timeout_seconds,
        )
        print(json.dumps(artifact.summary.model_dump(mode="json"), sort_keys=True))
        return
    parser.error(f"{arguments.command} requires its explicit run configuration")


if __name__ == "__main__":
    main()
