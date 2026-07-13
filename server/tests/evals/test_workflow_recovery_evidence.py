from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

from server.evals import build_recovery_report, write_recovery_report
from server.tests.evals.recovery_scenarios import run_recovery_scenarios


async def test_recovery_suite_proves_restart_and_worker_loss_boundaries(
    migrated_postgres_url: str,
    clean_workflow_database,
    tmp_path,
) -> None:
    application_build = os.getenv("OPENMAGIC_RECOVERY_EVAL_APPLICATION_BUILD", "test-build")
    configured_output = os.getenv("OPENMAGIC_RECOVERY_EVAL_OUTPUT_DIR")
    output_directory = Path(configured_output) if configured_output else tmp_path
    run_id = (
        f"recovery-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
        if configured_output
        else "recovery-test-run"
    )
    cases = await run_recovery_scenarios(migrated_postgres_url, application_build)
    report = build_recovery_report(
        run_id=run_id,
        application_build=application_build,
        generated_at=datetime(2026, 7, 13, tzinfo=UTC),
        cases=cases,
    )

    json_path, markdown_path = write_recovery_report(report, output_directory)

    assert report.v0_passed is True
    assert all(case.passed for case in report.cases)
    assert {case.scenario_id for case in report.cases} == {
        "duplicate-cause",
        "restart-awaiting-approval",
        "worker-loss-before-dispatch",
        "worker-loss-after-dispatch",
    }
    payload = json.loads(json_path.read_text())
    assert payload["suite_id"] == "workflow-recovery.v1"
    assert "approval_granted" in json_path.read_text()
    assert "external_effect_dispatch_started" in json_path.read_text()
    assert "Yes, send" not in json_path.read_text()
    assert "@" not in json_path.read_text()
    assert "Strict V0 verdict: PASS" in markdown_path.read_text()
