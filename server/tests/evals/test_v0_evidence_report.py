from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime

from server.evals.v0_evidence import run_v0_evidence


class _SuccessfulRunner:
    def __init__(self) -> None:
        self.commands: list[tuple[str, ...]] = []

    def __call__(self, command, **_kwargs) -> subprocess.CompletedProcess[str]:
        self.commands.append(tuple(command))
        return subprocess.CompletedProcess(command, 0, stdout="7 passed\n", stderr="")


def test_report_separates_deterministic_diagnostic_and_live_evidence(tmp_path) -> None:
    runner = _SuccessfulRunner()
    report, json_path, markdown_path = run_v0_evidence(
        output_directory=tmp_path,
        application_build="0123456789abcdef",
        invocation=("python", "-m", "server.evals.v0_evidence"),
        run_model_diagnostics=False,
        run_live_composio=False,
        runner=runner,
        now=datetime(2026, 7, 13, tzinfo=UTC),
    )

    assert report.v0_passed is True
    assert len(runner.commands) == 4
    statuses = {lane.lane_id: lane.status for lane in report.lanes}
    assert statuses == {
        "workflow_correctness": "pass",
        "workflow_recovery": "pass",
        "notification_recovery": "pass",
        "deterministic_composio": "pass",
        "model_diagnostics": "not_run",
        "live_composio": "not_run",
    }
    notification = next(lane for lane in report.lanes if lane.lane_id == "notification_recovery")
    assert "Job completion remains separate from Notification delivery" in notification.observations
    assert (
        "Notification delivery remains separate from user-visible acknowledgement"
        in notification.observations
    )
    live = next(lane for lane in report.lanes if lane.lane_id == "live_composio")
    assert "Send Job completed" in live.observations
    assert "Notification delivered" in live.observations
    assert "User-visible acknowledgement recorded" in live.observations
    payload = json.loads(json_path.read_text())
    assert payload["application_build"] == "0123456789abcdef"
    assert "Deterministic V0 verdict: PASS" in markdown_path.read_text()
    serialized = json_path.read_text().casefold()
    assert "api_key" not in serialized
    assert "@gmail" not in serialized


def test_failed_deterministic_lane_fails_only_the_strict_gate(tmp_path) -> None:
    calls = 0

    def runner(command, **_kwargs):
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess(
            command,
            1 if calls == 2 else 0,
            stdout="bounded output",
            stderr="",
        )

    report, _, _ = run_v0_evidence(
        output_directory=tmp_path,
        application_build="build-2",
        invocation=("evidence",),
        run_model_diagnostics=True,
        run_live_composio=True,
        runner=runner,
        now=datetime(2026, 7, 13, 0, 0, 1, tzinfo=UTC),
    )

    assert report.v0_passed is False
    assert len(report.lanes) == 6
    assert (
        next(lane for lane in report.lanes if lane.lane_id == "workflow_recovery").status == "fail"
    )
