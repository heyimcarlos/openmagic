from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime

import pytest

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
        application_build="0123456789abcdef0123456789abcdef01234567",
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
    observation_names = {observation.name for observation in notification.observations}
    assert "Job completion remains separate from Notification delivery" in observation_names
    assert (
        "Notification delivery remains separate from user-visible acknowledgement"
        in observation_names
    )
    live = next(lane for lane in report.lanes if lane.lane_id == "live_composio")
    live_observation_names = {observation.name for observation in live.observations}
    assert "Send Job completed" in live_observation_names
    assert "Notification delivered" in live_observation_names
    assert "User-visible acknowledgement recorded" in live_observation_names
    payload = json.loads(json_path.read_text())
    assert payload["application_build"] == "0123456789abcdef0123456789abcdef01234567"
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
        application_build="2" * 40,
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


def test_timed_out_lane_is_bounded_failure_evidence(tmp_path) -> None:
    def runner(command, **_kwargs):
        raise subprocess.TimeoutExpired(command, timeout=1, output="bounded output")

    report, _, _ = run_v0_evidence(
        output_directory=tmp_path,
        application_build="3" * 40,
        invocation=("evidence",),
        run_model_diagnostics=False,
        run_live_composio=False,
        runner=runner,
        now=datetime(2026, 7, 13, 0, 0, 2, tzinfo=UTC),
        lane_timeout_seconds=1,
    )

    assert report.v0_passed is False
    assert all(
        lane.status == "fail"
        for lane in report.lanes
        if lane.classification == "deterministic_gate"
    )


def test_unstartable_lane_is_bounded_failure_evidence(tmp_path) -> None:
    def runner(_command, **_kwargs):
        raise OSError("process unavailable")

    report, _, _ = run_v0_evidence(
        output_directory=tmp_path,
        application_build="4" * 40,
        invocation=("evidence",),
        run_model_diagnostics=False,
        run_live_composio=False,
        runner=runner,
        now=datetime(2026, 7, 13, 0, 0, 3, tzinfo=UTC),
    )

    assert report.v0_passed is False


def test_invalid_build_is_rejected_before_creating_output(tmp_path) -> None:
    with pytest.raises(ValueError, match="full lowercase Git SHA"):
        run_v0_evidence(
            output_directory=tmp_path,
            application_build="feature/bad-build",
            invocation=("evidence",),
            run_model_diagnostics=False,
            run_live_composio=False,
        )

    assert list(tmp_path.iterdir()) == []
