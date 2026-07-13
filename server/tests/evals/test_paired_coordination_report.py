from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from server.evals.coordination import (
    CoordinationDiagnostics,
    CoordinationTrial,
    build_coordination_report,
    write_coordination_report,
)


def test_report_gates_workflow_correctness_and_keeps_baseline_diagnostic(tmp_path) -> None:
    baseline = CoordinationTrial(
        scenario_id="unique-renewal",
        profile="legacy",
        model="test-model",
        application_build="build-1",
        outcome="delegated",
        correctness=None,
        response_digest="0" * 64,
        diagnostics=CoordinationDiagnostics(
            model_calls=1,
            tool_calls=("send_message_to_agent",),
            search_calls=0,
            packet_reads=0,
            max_context_bytes=12_000,
            approximate_context_tokens=3_000,
            model_duration_ms=25.0,
            local_tool_duration_ms=1.0,
        ),
    )
    workflow = CoordinationTrial(
        scenario_id="unique-renewal",
        profile="workflow",
        model="test-model",
        application_build="build-1",
        outcome="proposed",
        correctness=True,
        response_digest="1" * 64,
        selected_workflow_id="40000000-0000-0000-0000-000000000001",
        diagnostics=CoordinationDiagnostics(
            model_calls=4,
            tool_calls=(
                "search_workflows",
                "read_workflow_packet",
                "propose_renewal_email",
            ),
            search_calls=1,
            packet_reads=1,
            max_context_bytes=8_000,
            approximate_context_tokens=2_000,
            model_duration_ms=40.0,
            local_tool_duration_ms=6.0,
        ),
    )

    report = build_coordination_report(
        run_id="paired-test-run",
        generated_at=datetime(2026, 7, 13, tzinfo=UTC),
        trials=(baseline, workflow),
    )
    json_path, markdown_path = write_coordination_report(report, tmp_path)

    assert report.v0_passed is True
    assert report.workflow_trials == 1
    assert report.baseline_trials == 1
    payload = json.loads(json_path.read_text())
    assert payload["schema_version"] == 1
    assert payload["v0_passed"] is True
    assert payload["trials"][0]["correctness"] is None
    assert "prompt" not in json_path.read_text().casefold()
    trial_paths = sorted((json_path.parent / "trials").glob("*.json"))
    assert [path.name for path in trial_paths] == [
        "unique-renewal-legacy.json",
        "unique-renewal-workflow.json",
    ]
    assert json.loads(trial_paths[1].read_text())["profile"] == "workflow"
    markdown = markdown_path.read_text()
    assert "Strict V0 verdict: PASS" in markdown
    assert "Baseline outcomes and trajectory measurements are diagnostic" in markdown
    assert "| unique-renewal | legacy | delegated | diagnostic |" in markdown
    assert "| unique-renewal | workflow | proposed | pass |" in markdown

    with pytest.raises(FileExistsError):
        write_coordination_report(report, tmp_path)


def test_report_rejects_unpaired_scenario_results() -> None:
    diagnostics = CoordinationDiagnostics(
        model_calls=1,
        tool_calls=(),
        search_calls=0,
        packet_reads=0,
        max_context_bytes=1,
        approximate_context_tokens=1,
        model_duration_ms=1,
        local_tool_duration_ms=1,
    )
    baseline = CoordinationTrial(
        scenario_id="scenario-a",
        profile="legacy",
        model="test-model",
        application_build="build-1",
        outcome="delegated",
        correctness=None,
        response_digest="0" * 64,
        diagnostics=diagnostics,
    )
    workflow = CoordinationTrial(
        scenario_id="scenario-b",
        profile="workflow",
        model="test-model",
        application_build="build-1",
        outcome="no_match",
        correctness=True,
        response_digest="1" * 64,
        diagnostics=diagnostics,
    )

    with pytest.raises(ValueError, match="one legacy and one Workflow trial"):
        build_coordination_report(
            run_id="unpaired",
            generated_at=datetime(2026, 7, 13, tzinfo=UTC),
            trials=(baseline, workflow),
        )
