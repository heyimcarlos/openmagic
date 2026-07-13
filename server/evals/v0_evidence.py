"""One bounded index over the V0 evaluation and provider evidence lanes."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

LaneStatus = Literal["pass", "fail", "not_run"]
LaneClassification = Literal["deterministic_gate", "model_diagnostic", "live_smoke"]
LaneId = Literal[
    "workflow_correctness",
    "workflow_recovery",
    "notification_recovery",
    "deterministic_composio",
    "model_diagnostics",
    "live_composio",
]
Runner = Callable[..., subprocess.CompletedProcess[str]]
_LANE_CLASSIFICATIONS: dict[LaneId, LaneClassification] = {
    "workflow_correctness": "deterministic_gate",
    "workflow_recovery": "deterministic_gate",
    "notification_recovery": "deterministic_gate",
    "deterministic_composio": "deterministic_gate",
    "model_diagnostics": "model_diagnostic",
    "live_composio": "live_smoke",
}


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class V0EvidenceObservation(_EvidenceModel):
    name: str = Field(min_length=1, max_length=255)
    status: LaneStatus
    source: str = Field(min_length=1, max_length=1_000)


class V0EvidenceArtifact(_EvidenceModel):
    path: str = Field(min_length=1, max_length=2_000)
    digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class V0EvidenceLane(_EvidenceModel):
    lane_id: LaneId
    classification: LaneClassification
    status: LaneStatus
    environment: tuple[str, ...]
    command: tuple[str, ...]
    observations: tuple[V0EvidenceObservation, ...]
    artifacts: tuple[V0EvidenceArtifact, ...]
    duration_ms: float = Field(ge=0)
    output_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class V0EvidenceReport(_EvidenceModel):
    schema_version: Literal[1] = 1
    suite_id: Literal["openmagic-v0-evidence.v1"] = "openmagic-v0-evidence.v1"
    run_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
    application_build: str = Field(pattern=r"^[0-9a-f]{40}$")
    generated_at: datetime
    invocation: tuple[str, ...]
    v0_passed: bool
    lanes: tuple[V0EvidenceLane, ...]

    @model_validator(mode="after")
    def require_complete_and_traceable_lanes(self) -> V0EvidenceReport:
        expected = set(_LANE_CLASSIFICATIONS)
        lane_ids = [lane.lane_id for lane in self.lanes]
        if set(lane_ids) != expected or len(lane_ids) != len(expected):
            raise ValueError("V0 evidence report requires every named lane exactly once")
        if any(lane.classification != _LANE_CLASSIFICATIONS[lane.lane_id] for lane in self.lanes):
            raise ValueError("V0 evidence lane classification does not match its contract")
        gates = [
            lane
            for lane in self.lanes
            if _LANE_CLASSIFICATIONS[lane.lane_id] == "deterministic_gate"
        ]
        if any(lane.status == "not_run" for lane in gates):
            raise ValueError("Deterministic gates may not be omitted")
        if self.v0_passed != all(lane.status == "pass" for lane in gates):
            raise ValueError("V0 verdict must equal the deterministic gate verdict")
        return self


class _LaneSpec(_EvidenceModel):
    lane_id: LaneId
    classification: LaneClassification
    pytest_targets: tuple[str, ...]
    observations: tuple[str, ...]
    opt_in_environment: tuple[tuple[str, str], ...] = ()

    @model_validator(mode="after")
    def require_authoritative_classification(self) -> _LaneSpec:
        if self.classification != _LANE_CLASSIFICATIONS[self.lane_id]:
            raise ValueError("Lane classification does not match its contract")
        return self


_LANES = (
    _LaneSpec(
        lane_id="workflow_correctness",
        classification="deterministic_gate",
        pytest_targets=(
            "server/tests/evals/test_paired_coordination_report.py",
            "server/tests/evals/test_paired_coordination_eval.py",
            "server/tests/evals/test_workflow_retrieval_eval.py",
        ),
        observations=(
            "Workflow correctness is gated independently from baseline diagnostics",
            "Ambiguous and missing retrieval produce no mutation",
            "Packet reads remain bounded",
        ),
    ),
    _LaneSpec(
        lane_id="workflow_recovery",
        classification="deterministic_gate",
        pytest_targets=("server/tests/evals/test_workflow_recovery_evidence.py",),
        observations=(
            "Restart awaiting approval",
            "Safe retry before dispatch",
            "No automatic retry after dispatch",
        ),
    ),
    _LaneSpec(
        lane_id="notification_recovery",
        classification="deterministic_gate",
        pytest_targets=("server/tests/workflows/test_notification_fault_recovery.py",),
        observations=(
            "Job completion remains separate from Notification delivery",
            "Notification delivery remains separate from user-visible acknowledgement",
            "Lost acknowledgement does not duplicate the correlated reply",
            "Delayed, stale, exhausted, and restarted delivery paths are durable",
        ),
    ),
    _LaneSpec(
        lane_id="deterministic_composio",
        classification="deterministic_gate",
        pytest_targets=("server/tests/workflows/test_approved_email_effect.py",),
        observations=(
            "Success, known failure, and uncertain provider branches use one adapter contract",
            "Dispatch authority and exact approval are enforced before provider execution",
        ),
    ),
    _LaneSpec(
        lane_id="model_diagnostics",
        classification="model_diagnostic",
        pytest_targets=("server/tests/evals/test_live_paired_coordination.py",),
        observations=(
            "Real-model baseline and Workflow trajectories are diagnostic",
            "Model diagnostics never override deterministic correctness gates",
        ),
        opt_in_environment=(("OPENMAGIC_RUN_PAIRED_COORDINATION_EVAL", "1"),),
    ),
    _LaneSpec(
        lane_id="live_composio",
        classification="live_smoke",
        pytest_targets=("server/tests/live/test_composio_email_smoke.py",),
        observations=(
            "Send Job completed",
            "Notification delivered",
            "User-visible acknowledgement recorded",
            "Recipient independently observed exactly one correlated email",
        ),
        opt_in_environment=(("OPENMAGIC_RUN_LIVE_EMAIL_SMOKE", "1"),),
    ),
)


def run_v0_evidence(
    *,
    output_directory: Path,
    application_build: str | None,
    invocation: Sequence[str],
    run_model_diagnostics: bool,
    run_live_composio: bool,
    runner: Runner = subprocess.run,
    now: datetime | None = None,
    lane_timeout_seconds: float = 360,
    build_verifier: Callable[[str | None], str] | None = None,
) -> tuple[V0EvidenceReport, Path, Path]:
    """Run every deterministic lane and optional external lanes, then write one index."""

    application_build = (build_verifier or _verified_current_build)(application_build)
    _require_build_sha(application_build)
    run_at = now or datetime.now(UTC)
    run_id = f"v0-{run_at.strftime('%Y%m%dT%H%M%SZ')}-{application_build[:12]}"
    run_directory = output_directory / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    lanes = tuple(
        _run_lane(
            spec,
            application_build=application_build,
            enabled=(
                spec.classification == "deterministic_gate"
                or (spec.lane_id == "model_diagnostics" and run_model_diagnostics)
                or (spec.lane_id == "live_composio" and run_live_composio)
            ),
            runner=runner,
            run_directory=run_directory,
            timeout_seconds=lane_timeout_seconds,
        )
        for spec in _LANES
    )
    report = V0EvidenceReport(
        run_id=run_id,
        application_build=application_build,
        generated_at=run_at,
        invocation=tuple(invocation),
        v0_passed=all(
            lane.status == "pass" for lane in lanes if lane.classification == "deterministic_gate"
        ),
        lanes=lanes,
    )
    json_path = run_directory / "report.json"
    markdown_path = run_directory / "report.md"
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return report, json_path, markdown_path


def _run_lane(
    spec: _LaneSpec,
    *,
    application_build: str,
    enabled: bool,
    runner: Runner,
    run_directory: Path,
    timeout_seconds: float,
) -> V0EvidenceLane:
    lane_directory = run_directory / spec.lane_id
    junit_path = lane_directory / "junit.xml"
    live_evidence_path = lane_directory / "live-evidence.json"
    pytest_evidence_arguments = (
        () if spec.lane_id == "live_composio" else (f"--junitxml={junit_path}",)
    )
    command = (
        sys.executable,
        "-m",
        "pytest",
        "-q",
        *spec.pytest_targets,
        *pytest_evidence_arguments,
    )
    public_environment = tuple(
        f"{name}={value}"
        for name, value in (
            *spec.opt_in_environment,
            ("OPENMAGIC_EVAL_APPLICATION_BUILD", application_build),
            ("OPENMAGIC_RECOVERY_EVAL_APPLICATION_BUILD", application_build),
            ("OPENMAGIC_RECOVERY_EVAL_OUTPUT_DIR", str(lane_directory)),
            ("OPENMAGIC_PAIRED_EVAL_OUTPUT_DIR", str(lane_directory)),
            ("OPENMAGIC_LIVE_EVIDENCE_PATH", str(live_evidence_path)),
        )
    )
    lane_directory.mkdir()
    if not enabled:
        return V0EvidenceLane(
            lane_id=spec.lane_id,
            classification=spec.classification,
            status="not_run",
            environment=public_environment,
            command=command,
            observations=tuple(
                V0EvidenceObservation(name=name, status="not_run", source="lane not requested")
                for name in spec.observations
            ),
            artifacts=(),
            duration_ms=0,
        )
    environment = os.environ.copy()
    environment.update(spec.opt_in_environment)
    environment["OPENMAGIC_EVAL_APPLICATION_BUILD"] = application_build
    environment["OPENMAGIC_RECOVERY_EVAL_APPLICATION_BUILD"] = application_build
    environment["OPENMAGIC_RECOVERY_EVAL_OUTPUT_DIR"] = str(lane_directory)
    environment["OPENMAGIC_PAIRED_EVAL_OUTPUT_DIR"] = str(lane_directory)
    environment["OPENMAGIC_LIVE_EVIDENCE_PATH"] = str(live_evidence_path)
    started = time.perf_counter()
    try:
        result = runner(
            command,
            cwd=Path.cwd(),
            env=environment,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        return_code = result.returncode
        stdout = result.stdout
        stderr = result.stderr
    except subprocess.TimeoutExpired as exc:
        return_code = 124
        stdout = _bounded_subprocess_text(exc.stdout)
        stderr = "lane timed out"
    except OSError as exc:
        return_code = 126
        stdout = ""
        stderr = f"lane could not start: {type(exc).__name__}"
    duration_ms = (time.perf_counter() - started) * 1_000
    output = f"{stdout}\n{stderr}".encode()
    if return_code != 0:
        sys.stderr.write(stdout)
        sys.stderr.write(stderr)
    status: LaneStatus = "pass" if return_code == 0 else "fail"
    artifacts = _artifacts(lane_directory)
    source = next(
        (str(path) for path in (live_evidence_path, junit_path) if path.exists()),
        "lane process outcome",
    )
    return V0EvidenceLane(
        lane_id=spec.lane_id,
        classification=spec.classification,
        status=status,
        environment=public_environment,
        command=command,
        observations=tuple(
            V0EvidenceObservation(name=name, status=status, source=source)
            for name in spec.observations
        ),
        artifacts=artifacts,
        duration_ms=duration_ms,
        output_digest=hashlib.sha256(output).hexdigest(),
    )


def _artifacts(lane_directory: Path) -> tuple[V0EvidenceArtifact, ...]:
    return tuple(
        V0EvidenceArtifact(
            path=str(path),
            digest=hashlib.sha256(path.read_bytes()).hexdigest(),
        )
        for path in sorted(lane_directory.rglob("*"))
        if path.is_file()
    )


def _bounded_subprocess_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    text = value.decode(errors="replace") if isinstance(value, bytes) else value
    return text[-4_000:]


def _render_markdown(report: V0EvidenceReport) -> str:
    verdict = "PASS" if report.v0_passed else "FAIL"
    lines = [
        f"# OpenMagic V0 evidence: {report.run_id}",
        "",
        f"Deterministic V0 verdict: {verdict}",
        "",
        f"Application build: `{report.application_build}`",
        "",
        "Model diagnostics and the live provider smoke are reported separately. They do not override deterministic safety gates.",
        "",
        "| Lane | Classification | Status |",
        "| --- | --- | --- |",
    ]
    for lane in report.lanes:
        lines.append(f"| {lane.lane_id} | {lane.classification} | {lane.status} |")
    lines.extend(
        [
            "",
            "## Typed observations",
            "",
            "| Lane | Observation | Status | Evidence source |",
            "| --- | --- | --- | --- |",
        ]
    )
    for lane in report.lanes:
        for observation in lane.observations:
            lines.append(
                f"| {lane.lane_id} | {observation.name} | "
                f"{observation.status} | `{observation.source}` |"
            )
    lines.extend(["", "## Exact commands", ""])
    for lane in report.lanes:
        lines.extend(
            [
                f"### {lane.lane_id}",
                "",
                "```text",
                shlex.join(("env", *lane.environment, *lane.command)),
                "```",
                "",
            ]
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("/tmp/openmagic-v0-evidence"))
    parser.add_argument("--build")
    parser.add_argument("--run-model-diagnostics", action="store_true")
    parser.add_argument("--run-live-composio", action="store_true")
    args = parser.parse_args(argv)
    invocation = (sys.executable, "-m", "server.evals.v0_evidence", *(argv or sys.argv[1:]))
    report, json_path, markdown_path = run_v0_evidence(
        output_directory=args.output,
        application_build=args.build,
        invocation=invocation,
        run_model_diagnostics=args.run_model_diagnostics,
        run_live_composio=args.run_live_composio,
    )
    print(json.dumps({"report": str(json_path), "summary": str(markdown_path)}))
    return 0 if report.v0_passed else 1


def _verified_current_build(requested_build: str | None) -> str:
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = subprocess.run(
        ("git", "status", "--porcelain"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if dirty:
        raise RuntimeError("V0 evidence requires a clean worktree")
    if requested_build is not None and requested_build != head:
        raise RuntimeError("Requested build does not match the checked-out Git HEAD")
    return _require_build_sha(head)


def _require_build_sha(application_build: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", application_build) is None:
        raise ValueError("Application build must be one full lowercase Git SHA")
    return application_build


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "V0EvidenceArtifact",
    "V0EvidenceLane",
    "V0EvidenceObservation",
    "V0EvidenceReport",
    "run_v0_evidence",
]
