"""Bounded evidence for the V0 restart and Worker-loss recovery proof."""

from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from server.workflows import WorkflowTrace

RecoveryScenario = Literal[
    "duplicate-cause",
    "restart-awaiting-approval",
    "worker-loss-before-dispatch",
    "worker-loss-after-dispatch",
]
RECOVERY_SCENARIOS = frozenset(
    {
        "duplicate-cause",
        "restart-awaiting-approval",
        "worker-loss-before-dispatch",
        "worker-loss-after-dispatch",
    }
)


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RecoveryJobEvidence(_EvidenceModel):
    job_id: UUID
    kind: str
    status: str
    attempts: int = Field(ge=0)
    waiting_reasons: tuple[str, ...]


class RecoveryRunEvidence(_EvidenceModel):
    run_id: UUID
    job_id: UUID
    status: str
    application_build: str
    runtime_instance_id: UUID | None
    result_outcome: str | None


class RecoveryEventEvidence(_EvidenceModel):
    event_id: UUID
    event_type: str
    job_id: UUID | None
    run_id: UUID | None
    approval_grant_id: UUID | None
    cause_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class RecoveryTraceEvidence(_EvidenceModel):
    workflow_id: UUID
    workflow_kind: str
    workflow_status: str
    jobs: tuple[RecoveryJobEvidence, ...]
    runs: tuple[RecoveryRunEvidence, ...]
    events: tuple[RecoveryEventEvidence, ...]


class RecoveryCaseEvidence(_EvidenceModel):
    scenario_id: RecoveryScenario
    passed: bool
    restart_boundaries: int = Field(ge=0)
    adapter_invocations: int = Field(ge=0)
    duplicate_deliveries: int = Field(default=1, ge=1)
    stable_replay_observed: bool = False
    conflict_rejected: bool = False
    stale_command_rejected: bool = False
    trace: RecoveryTraceEvidence

    @model_validator(mode="after")
    def require_trace_backed_verdict(self) -> RecoveryCaseEvidence:
        expected = _case_passed(
            self.scenario_id,
            self.trace,
            restart_boundaries=self.restart_boundaries,
            adapter_invocations=self.adapter_invocations,
            duplicate_deliveries=self.duplicate_deliveries,
            stable_replay_observed=self.stable_replay_observed,
            conflict_rejected=self.conflict_rejected,
            stale_command_rejected=self.stale_command_rejected,
        )
        if self.passed != expected:
            raise ValueError("Recovery case verdict does not match its durable trace")
        return self


class RecoveryReport(_EvidenceModel):
    schema_version: Literal[1] = 1
    suite_id: Literal["workflow-recovery.v1"] = "workflow-recovery.v1"
    run_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
    application_build: str = Field(min_length=1, max_length=255)
    generated_at: datetime
    v0_passed: bool
    cases: tuple[RecoveryCaseEvidence, ...]

    @model_validator(mode="after")
    def require_complete_suite(self) -> RecoveryReport:
        scenario_ids = [case.scenario_id for case in self.cases]
        if set(scenario_ids) != RECOVERY_SCENARIOS or len(scenario_ids) != len(RECOVERY_SCENARIOS):
            raise ValueError("Report requires the complete workflow-recovery.v1 suite")
        if self.v0_passed != all(case.passed for case in self.cases):
            raise ValueError("Recovery report verdict does not match its case evidence")
        run_builds = {run.application_build for case in self.cases for run in case.trace.runs}
        if run_builds != {self.application_build}:
            raise ValueError("Recovery report build does not match durable Run provenance")
        return self


def build_recovery_case(
    scenario_id: RecoveryScenario,
    trace: WorkflowTrace,
    *,
    restart_boundaries: int = 0,
    adapter_invocations: int = 0,
    duplicate_deliveries: int = 1,
    stable_replay_observed: bool = False,
    conflict_rejected: bool = False,
    stale_command_rejected: bool = False,
) -> RecoveryCaseEvidence:
    """Project one durable trace and apply the named recovery assertion."""

    evidence = _project_trace(trace)
    return RecoveryCaseEvidence(
        scenario_id=scenario_id,
        passed=_case_passed(
            scenario_id,
            evidence,
            restart_boundaries=restart_boundaries,
            adapter_invocations=adapter_invocations,
            duplicate_deliveries=duplicate_deliveries,
            stable_replay_observed=stable_replay_observed,
            conflict_rejected=conflict_rejected,
            stale_command_rejected=stale_command_rejected,
        ),
        restart_boundaries=restart_boundaries,
        adapter_invocations=adapter_invocations,
        duplicate_deliveries=duplicate_deliveries,
        stable_replay_observed=stable_replay_observed,
        conflict_rejected=conflict_rejected,
        stale_command_rejected=stale_command_rejected,
        trace=evidence,
    )


def build_recovery_report(
    *,
    run_id: str,
    application_build: str,
    generated_at: datetime,
    cases: tuple[RecoveryCaseEvidence, ...],
) -> RecoveryReport:
    return RecoveryReport(
        run_id=run_id,
        application_build=application_build,
        generated_at=generated_at,
        v0_passed=all(case.passed for case in cases),
        cases=cases,
    )


def write_recovery_report(report: RecoveryReport, output_directory: Path) -> tuple[Path, Path]:
    """Write one exclusive JSON trace and readable summary."""

    output_directory.mkdir(parents=True, exist_ok=True)
    run_directory = output_directory / report.run_id
    run_directory.mkdir(exist_ok=False)
    json_path = run_directory / "report.json"
    markdown_path = run_directory / "report.md"
    json_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    markdown_path.write_text(_render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def _project_trace(trace: WorkflowTrace) -> RecoveryTraceEvidence:
    return RecoveryTraceEvidence(
        workflow_id=trace.workflow.id,
        workflow_kind=trace.workflow.kind,
        workflow_status=trace.workflow.status,
        jobs=tuple(
            RecoveryJobEvidence(
                job_id=job.id,
                kind=job.kind,
                status=job.status,
                attempts=job.attempts,
                waiting_reasons=job.waiting_reasons,
            )
            for job in trace.jobs
        ),
        runs=tuple(
            RecoveryRunEvidence(
                run_id=run.id,
                job_id=run.job_id,
                status=run.status,
                application_build=run.application_build,
                runtime_instance_id=run.runtime_instance_id,
                result_outcome=(
                    run.result.get("outcome") if isinstance(run.result, dict) else None
                ),
            )
            for run in trace.runs
        ),
        events=tuple(
            RecoveryEventEvidence(
                event_id=event.id,
                event_type=event.event_type,
                job_id=event.job_id,
                run_id=event.run_id,
                approval_grant_id=event.approval_grant_id,
                cause_digest=hashlib.sha256(
                    f"{event.cause_type}:{event.cause_id}".encode()
                ).hexdigest(),
            )
            for event in trace.events
        ),
    )


def _case_passed(
    scenario_id: RecoveryScenario,
    trace: RecoveryTraceEvidence,
    *,
    restart_boundaries: int,
    adapter_invocations: int,
    duplicate_deliveries: int,
    stable_replay_observed: bool,
    conflict_rejected: bool,
    stale_command_rejected: bool,
) -> bool:
    event_types = [event.event_type for event in trace.events]
    run_statuses = [run.status for run in trace.runs]
    if scenario_id == "duplicate-cause":
        return (
            duplicate_deliveries == 2
            and stable_replay_observed
            and conflict_rejected
            and len(trace.jobs) == 2
            and event_types.count("workflow_jobs_proposed") == 1
        )
    if scenario_id == "restart-awaiting-approval":
        send_jobs = [job for job in trace.jobs if job.kind == "gmail.send_email.v1"]
        return (
            restart_boundaries == 1
            and len(send_jobs) == 1
            and send_jobs[0].status == "queued"
            and event_types.count("approval_granted") == 1
        )
    if scenario_id == "worker-loss-before-dispatch":
        return (
            restart_boundaries == 1
            and stale_command_rejected
            and run_statuses == ["abandoned", "running"]
            and event_types.count("run_abandoned") == 1
            and event_types.count("external_effect_dispatch_started") == 0
        )
    send_jobs = [job for job in trace.jobs if job.kind == "gmail.send_email.v1"]
    send_runs = [run for run in trace.runs if send_jobs and run.job_id == send_jobs[0].job_id]
    send_run_ids = {run.run_id for run in send_runs}
    approvals = [event for event in trace.events if event.event_type == "approval_granted"]
    dispatches = [
        event for event in trace.events if event.event_type == "external_effect_dispatch_started"
    ]
    abandonments = [event for event in trace.events if event.event_type == "run_abandoned"]
    return (
        restart_boundaries == 1
        and adapter_invocations == 1
        and stale_command_rejected
        and len(send_jobs) == 1
        and len(send_runs) == 1
        and send_runs[0].status == "abandoned"
        and send_jobs[0].status == "waiting"
        and len(approvals) == 1
        and len(dispatches) == 1
        and dispatches[0].run_id in send_run_ids
        and dispatches[0].approval_grant_id == approvals[0].event_id
        and len(abandonments) == 1
        and abandonments[0].run_id == send_runs[0].run_id
    )


def _render_markdown(report: RecoveryReport) -> str:
    verdict = "PASS" if report.v0_passed else "FAIL"
    lines = [
        f"# Workflow recovery evaluation: {report.run_id}",
        "",
        f"Strict V0 verdict: {verdict}",
        "",
        "| Scenario | Verdict | Restarts | Adapter invocations |",
        "| --- | --- | ---: | ---: |",
    ]
    for case in report.cases:
        lines.append(
            f"| {case.scenario_id} | {'pass' if case.passed else 'fail'} | "
            f"{case.restart_boundaries} | {case.adapter_invocations} |"
        )
    return "\n".join(lines) + "\n"


__all__ = [
    "RECOVERY_SCENARIOS",
    "RecoveryCaseEvidence",
    "RecoveryReport",
    "build_recovery_case",
    "build_recovery_report",
    "write_recovery_report",
]
